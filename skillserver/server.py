"""FastAPI adapter for shared web core."""

import asyncio
import hashlib
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from web_core import WebCore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HOST              = os.environ.get("HOST", "0.0.0.0")
PORT              = int(os.environ.get("PORT", "3000"))
MEDIA_DIR         = os.environ.get("MEDIA_DIR", "/tmp/screenshots")

core = WebCore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await core.start()
    yield
    await core.stop()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Web Service", lifespan=lifespan)

# Serve screenshots as static files
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/search")
async def search(
    q: str,
    categories: str = "general",
    language: str = "auto",
    safe_search: int = 0,
    page: int = 1,
    max_results: int = Query(default=10, ge=1, le=20),
):
    """Quick SearXNG search — returns titles, URLs and snippets."""
    try:
        return await core.search(
            query=q,
            categories=categories,
            language=language,
            safe_search=safe_search,
            page=page,
            max_results=max_results,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/deep_search")
async def deep_search(
    q: str,
    categories: str = "general",
    language: str = "auto",
    safe_search: int = 0,
    max_results: int = Query(default=5, ge=1, le=10),
):
    """Search SearXNG then fetch full page content via Playwright."""
    try:
        return await core.deep_search(
            query=q,
            categories=categories,
            language=language,
            safe_search=safe_search,
            max_results=max_results,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "deep_search timed out"}, status_code=504)


@app.get("/navigate")
async def navigate(url: str, wait_until: str = "domcontentloaded", format: str = "text"):
    """Fetch visible text content of a rendered page via Playwright."""
    result = await core.navigate(url=url, wait_until=wait_until, format=format)
    if "error" in result:
        return JSONResponse({"error": result["error"], "url": url}, status_code=400)
    return result


@app.get("/extract_text")
async def extract_text(
    url: str,
    selector: str = "body",
    wait_until: str = "domcontentloaded",
):
    """Extract text from a CSS selector on a rendered page."""
    result = await core.extract_text(url=url, selector=selector, wait_until=wait_until)
    if "error" in result:
        status = 400 if "not allowed" in str(result["error"]) else 500
        return JSONResponse({"error": result["error"], "url": url, "selector": selector}, status_code=status)
    return result


@app.get("/extract_links")
async def extract_links(url: str, wait_until: str = "domcontentloaded"):
    """Extract all hyperlinks from a rendered page."""
    result = await core.extract_links(url=url, wait_until=wait_until)
    if "error" in result:
        status = 400 if "not allowed" in str(result["error"]) else 500
        return JSONResponse({"error": result["error"], "url": url}, status_code=status)
    return result


@app.get("/headlines")
async def headlines(url: str, wait_until: str = "domcontentloaded"):
    """Extract all headings (h1–h6) from a rendered page."""
    result = await core.headlines(url=url, wait_until=wait_until)
    if "error" in result:
        status = 400 if "not allowed" in str(result["error"]) else 500
        return JSONResponse({"error": result["error"], "url": url}, status_code=status)
    return result


@app.get("/screenshot")
async def screenshot(url: str, full_page: bool = False):
    """Capture a screenshot, save to shared media dir, return MEDIA: path."""
    try:
        data = await core.screenshot(url=url, full_page=full_page)
        media = Path(MEDIA_DIR)
        media.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        slug = hashlib.md5(url.encode()).hexdigest()[:8]
        filename = f"screenshot_{ts}_{slug}.png"
        (media / filename).write_bytes(data)
        media_ref = f"MEDIA:/home/node/.openclaw/media/browser/{filename}"
        return {
            "url": url,
            "format": "png",
            "media": media_ref,
        }
    except Exception as exc:
        message = str(exc)
        status = 400 if message.startswith("Blocked URL") else 500
        return JSONResponse({"error": message, "url": url}, status_code=status)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
