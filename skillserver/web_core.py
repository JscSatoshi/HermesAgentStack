"""
Shared web core for both FastAPI and MCP adapters.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright


@dataclass
class CoreConfig:
    searxng_url: str = os.environ.get("SEARXNG_URL", "http://searxng:8080").rstrip("/")
    searxng_timeout: float = float(os.environ.get("SEARXNG_TIMEOUT", "15"))
    page_timeout: int = int(os.environ.get("PAGE_TIMEOUT", "30000"))
    fetch_concurrency: int = int(os.environ.get("FETCH_CONCURRENCY", "3"))
    allow_private_network: bool = os.environ.get("ALLOW_PRIVATE_NETWORK", "false").lower() == "true"


class WebCore:
    """Transport-agnostic web capabilities shared by HTTP and MCP adapters."""

    _GENERAL_ENGINES = "bing,duckduckgo,brave,yahoo,mojeek,wikipedia"
    _NEWS_ENGINES = "bing news,duckduckgo news,yahoo news,brave news,wikinews"
    _ALLOWED_SCHEMES = {"http", "https"}
    _BLOCKED_HOSTS = {"localhost", "127.0.0.1", "::1"}
    _BLOCK_TYPES = {"image", "media", "font", "stylesheet", "ping", "websocket", "manifest", "other"}

    def __init__(self, config: Optional[CoreConfig] = None) -> None:
        self.config = config or CoreConfig()
        self._http_client: Optional[httpx.AsyncClient] = None
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    async def start(self) -> None:
        await self._warmup_browser()

    async def stop(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._http_client = None
        self._context = None
        self._browser = None
        self._pw = None

    async def search(
        self,
        query: str,
        categories: str = "general",
        language: str = "auto",
        safe_search: int = 0,
        page: int = 1,
        max_results: int = 10,
    ) -> dict[str, Any]:
        max_results = max(1, min(max_results, 20))
        if language == "auto":
            language = self._detect_lang(query)
        data = await self._searxng_query_with_retry(
            {
                "q": query,
                "categories": categories,
                "language": language,
                "safesearch": safe_search,
                "pageno": page,
            },
            category=categories,
        )
        results = self._dedup(data.get("results", []))[:max_results]
        unresponsive = data.get("unresponsive_engines", [])
        payload: dict[str, Any] = {
            "query": query,
            "total": data.get("number_of_results", len(results)),
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", "")[:500],
                    "published": r.get("publishedDate", "") or r.get("published_date", ""),
                    "engines": r.get("engines", []),
                    "score": r.get("score", 0),
                }
                for r in results
            ],
        }
        if unresponsive and not results:
            payload["unresponsive_engines"] = [[e[0], e[1]] for e in unresponsive]
        return payload

    async def deep_search(
        self,
        query: str,
        categories: str = "general",
        language: str = "auto",
        safe_search: int = 0,
        max_results: int = 5,
    ) -> dict[str, Any]:
        max_results = max(1, min(max_results, 10))
        if language == "auto":
            language = self._detect_lang(query)

        data = await self._searxng_query_with_retry(
            {
                "q": query,
                "categories": categories,
                "language": language,
                "safesearch": safe_search,
            },
            category=categories,
        )

        results = self._dedup(data.get("results", []))[:max_results]
        if not results:
            return {"query": query, "pages": []}

        semaphore = asyncio.Semaphore(self.config.fetch_concurrency)

        async def fetch_one(r: dict[str, Any]) -> dict[str, str]:
            url = r.get("url", "")
            title = r.get("title", url)
            if err := self.validate_url(url):
                return {"title": title, "url": url, "content": f"[blocked url: {err}]"}
            async with semaphore:
                text = await self._page_text(url, 8000)
            return {"title": title, "url": url, "content": text}

        pages = await asyncio.wait_for(
            asyncio.gather(*[fetch_one(r) for r in results]),
            timeout=45,
        )
        return {"query": query, "pages": list(pages)}

    async def navigate(self, url: str, wait_until: str = "domcontentloaded", format: str = "text") -> dict[str, str]:
        if err := self.validate_url(url):
            return {"url": url, "error": err}

        if format == "html":
            page = await self._new_text_page()
            try:
                await page.goto(url, wait_until=wait_until, timeout=self.config.page_timeout)
                html = await page.content()
                if len(html) > 50000:
                    html = html[:50000] + "\n<!-- truncated at 50000 chars -->"
                return {"url": url, "content": html, "format": "html"}
            except Exception as exc:
                return {"url": url, "error": str(exc)}
            finally:
                await page.close()

        text = await self._page_text(url, 20000, wait_until)
        if text.startswith("[fetch error:"):
            return {"url": url, "error": text[13:].strip(" []")}
        return {"url": url, "content": text, "format": "text"}

    async def extract_text(
        self,
        url: str,
        selector: str = "body",
        wait_until: str = "domcontentloaded",
    ) -> dict[str, Any]:
        if err := self.validate_url(url):
            return {"url": url, "error": err}
        page = await self._new_text_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=self.config.page_timeout)
            element = page.locator(selector).first
            text = await element.inner_text(timeout=5000)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            content = "\n".join(lines)
            if len(content) > 20000:
                content = content[:20000] + "\n\n[... truncated at 20000 chars]"
            return {"url": url, "selector": selector, "content": content}
        except Exception as exc:
            return {"url": url, "selector": selector, "error": str(exc)}
        finally:
            await page.close()

    async def extract_links(self, url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
        if err := self.validate_url(url):
            return {"url": url, "error": err}
        page = await self._new_text_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=self.config.page_timeout)
            links = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => ({ text: e.innerText.trim(), href: e.href }))"
                ".filter(l => l.href && !l.href.startsWith('javascript:'))",
            )
            return {"url": url, "count": len(links), "links": links[:200]}
        except Exception as exc:
            return {"url": url, "error": str(exc)}
        finally:
            await page.close()

    async def headlines(self, url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
        if err := self.validate_url(url):
            return {"url": url, "error": err}
        page = await self._new_text_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=self.config.page_timeout)
            items = await page.eval_on_selector_all(
                "h1, h2, h3, h4, h5, h6",
                "els => els.map(e => ({ level: parseInt(e.tagName[1]), text: e.innerText.trim() }))"
                ".filter(h => h.text.length > 0)",
            )
            return {"url": url, "count": len(items), "headlines": items[:200]}
        except Exception as exc:
            return {"url": url, "error": str(exc)}
        finally:
            await page.close()

    async def screenshot(self, url: str, full_page: bool = False) -> bytes:
        if err := self.validate_url(url):
            raise RuntimeError(f"Blocked URL: {err}")
        browser = await self._get_browser()
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            try:
                await page.goto(url, wait_until="load", timeout=self.config.page_timeout)
            except Exception:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.config.page_timeout)
            await page.wait_for_timeout(1000)
            return await page.screenshot(full_page=full_page)
        finally:
            await page.close()

    def validate_url(self, url: str) -> Optional[str]:
        parsed = urlparse(url)
        if parsed.scheme not in self._ALLOWED_SCHEMES:
            return f"URL scheme '{parsed.scheme}' not allowed; use http or https"
        if not parsed.hostname:
            return "URL must include a valid hostname"

        if self.config.allow_private_network:
            return None

        host = parsed.hostname.strip().lower()
        if host in self._BLOCKED_HOSTS:
            return f"Host '{host}' is not allowed"

        try:
            ip = ipaddress.ip_address(host)
            if any(
                (
                    ip.is_private,
                    ip.is_loopback,
                    ip.is_link_local,
                    ip.is_multicast,
                    ip.is_reserved,
                    ip.is_unspecified,
                )
            ):
                return f"IP '{host}' is not allowed"
        except ValueError:
            pass
        return None

    async def _warmup_browser(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

        self._pw = await async_playwright().start()
        pw = self._pw
        self._browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--no-first-run",
            ],
        )
        browser = self._browser
        self._context = await browser.new_context(
            java_script_enabled=True,
            ignore_https_errors=True,
        )

    async def _get_browser(self) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            await self._warmup_browser()
        if self._browser is None:
            raise RuntimeError("Browser warmup failed")
        return self._browser

    async def _get_context(self) -> BrowserContext:
        if self._browser is None or not self._browser.is_connected():
            await self._warmup_browser()
        if self._context is None:
            browser = await self._get_browser()
            self._context = await browser.new_context(
                java_script_enabled=True,
                ignore_https_errors=True,
            )
        return self._context

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=self.config.searxng_url,
                timeout=self.config.searxng_timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._http_client

    async def _searxng_query(self, params: dict[str, Any]) -> dict[str, Any]:
        params.setdefault("format", "json")
        resp = await self._get_http_client().get("/search", params=params)
        resp.raise_for_status()
        return resp.json()

    async def _searxng_query_with_retry(self, params: dict[str, Any], category: str = "general") -> dict[str, Any]:
        data = await self._searxng_query(params)
        results = data.get("results", [])
        if results:
            return data

        engines = self._NEWS_ENGINES if category == "news" else self._GENERAL_ENGINES
        retry_params = {**params, "engines": engines}
        retry_params.pop("categories", None)
        return await self._searxng_query(retry_params)

    def _dedup(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for item in results:
            url = item.get("url", "")
            if url and url in seen:
                continue
            seen.add(url)
            out.append(item)
        return out

    def _detect_lang(self, text: str) -> str:
        cjk = 0
        latin = 0
        for ch in text:
            cp = ord(ch)
            if (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF) or (0x20000 <= cp <= 0x2A6DF):
                cjk += 1
            elif 0x3040 <= cp <= 0x30FF:
                cjk += 1
            elif (0xAC00 <= cp <= 0xD7AF) or (0x1100 <= cp <= 0x11FF):
                cjk += 1
            elif ch.isalpha() and cp < 0x300:
                latin += 1

        if cjk == 0:
            return "en"
        if latin == 0:
            for ch in text:
                cp = ord(ch)
                if 0x3040 <= cp <= 0x30FF:
                    return "ja"
                if (0xAC00 <= cp <= 0xD7AF) or (0x1100 <= cp <= 0x11FF):
                    return "ko"
            return "zh"
        return "all"

    async def _new_text_page(self):
        async def _block(route):
            if route.request.resource_type in self._BLOCK_TYPES:
                await route.abort()
            else:
                await route.continue_()

        for attempt in range(2):
            ctx = await self._get_context()
            try:
                page = await ctx.new_page()
                await page.route("**/*", _block)
                return page
            except Exception:
                self._context = None
                if attempt == 1:
                    raise

        raise RuntimeError("Failed to create page")

    async def _page_text(self, url: str, limit: int, wait_until: str = "domcontentloaded") -> str:
        page = None
        try:
            page = await self._new_text_page()
            await page.goto(url, wait_until=wait_until, timeout=self.config.page_timeout)
            text = await page.inner_text("body")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            content = "\n".join(lines)
            if len(content) > limit:
                content = content[:limit] + f"\n\n[... truncated at {limit} chars]"
            return content
        except Exception as exc:
            return f"[fetch error: {exc}]"
        finally:
            if page:
                await page.close()
