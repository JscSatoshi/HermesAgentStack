---
name: web
description: "MANDATORY: Use this skill for ALL web access. DO NOT call browser, web_search, or web_fetch — they are disabled and will always fail. Instead use exec+curl to http://skillserver:3000/. Provides search, navigate, deep_search, screenshot via local SearXNG + Playwright."
triggers:
  - web
  - browser
  - search
  - navigate
  - website
  - url
  - internet
  - online
  - fetch
  - crawl
  - screenshot
---

# Web Search & Browser

> ## ⛔ CRITICAL: Built-in tools are DISABLED — do NOT use them
>
> The following built-in tools are **permanently disabled** and will **always fail** with an error:
> - **`browser`** — DISABLED. Will fail: "Browser control is disabled."
> - **`web_search`** — DISABLED. Will fail immediately.
> - **`web_fetch`** — DISABLED. Will fail immediately.
>
> **Do NOT attempt to call `browser`, `web_search`, or `web_fetch` under any circumstances.**
> Attempting to use them wastes a turn and returns an error — you cannot recover by retrying them.
>
> **✅ The ONLY way to access the web is: `exec` → `curl http://skillserver:3000/...`**
> This is always available and always works. Use it immediately.

## Mandatory Use Cases

You MUST use this skill first before answering whenever the user asks about:

- current events, latest news, recent releases, or anything time-sensitive
- websites, products, prices, changelogs, docs, API behavior, or external services
- anything that requires checking live internet content rather than model memory
- requests containing words like `latest`, `current`, `today`, `now`, `recent`, `news`, `search`, `look up`, `find online`, `check website`

If the request depends on external facts, do not answer from memory first. Search first, then answer.

If search results are weak or ambiguous, run another search with a better query or use `deep_search`.

If the user asks for sources, links, verification, or confirmation, you MUST search first.

Base URL: `http://skillserver:3000` — always use `curl -s --max-time <seconds>`.

---

## 1. Search (snippets)

```bash
curl -s --max-time 15 "http://skillserver:3000/search?q=YOUR+QUERY" | head -c 8000
```

Returns: `{ query, total, results: [{title, url, content, published, engines, score}] }`

| Param | Default | Notes |
|-------|---------|-------|
| `q` | required | `+` for spaces, URL-encode special chars |
| `categories` | `general` | `general`, `news`, `science`, `it`, `images`, `videos` |
| `language` | `auto` | `en-US`, `zh-CN`, `ja-JP`, `all` |
| `safe_search` | `0` | `0`=off, `1`=moderate, `2`=strict |
| `max_results` | `10` | 1–20 |
| `page` | `1` | Pagination |

News example: `...?q=AI+news&categories=news&language=en-US`

## 2. Deep Search (full page text, slower)

```bash
curl -s --max-time 60 "http://skillserver:3000/deep_search?q=YOUR+QUERY&max_results=3" | head -c 16000
```

Returns: `{ query, pages: [{title, url, content}] }` — use when snippets aren't enough.

## 3. Navigate (single page)

```bash
curl -s --max-time 30 "http://skillserver:3000/navigate?url=https%3A%2F%2Fexample.com" | head -c 12000
```

Raw HTML example:

```bash
curl -s --max-time 30 "http://skillserver:3000/navigate?url=https%3A%2F%2Fexample.com&format=html" | head -c 12000
```

## 4. Extract Text (CSS selector)

```bash
curl -s --max-time 20 "http://skillserver:3000/extract_text?url=https%3A%2F%2Fexample.com&selector=article" | head -c 8000
```

## 5. Extract Links

```bash
curl -s --max-time 20 "http://skillserver:3000/extract_links?url=https%3A%2F%2Fexample.com" | head -c 8000
```

## 6. Headlines (h1–h6)

```bash
curl -s --max-time 20 "http://skillserver:3000/headlines?url=https%3A%2F%2Fexample.com" | head -c 8000
```

Returns: `{ url, count, headlines: [{level, text}] }`

## 7. Screenshot (visual capture)

```bash
curl -s --max-time 30 "http://skillserver:3000/screenshot?url=https%3A%2F%2Fexample.com&full_page=false"
```

Returns: `{ url, format: "png", media: "MEDIA:/home/node/.openclaw/media/browser/screenshot_xxx.png" }`

**CRITICAL**: After calling `/screenshot`, extract the `media` field from the JSON response and output it **verbatim as-is** in your reply on its own line. The `MEDIA:` prefix is a special tag that the chat UI recognizes to render images inline. Example:

```
MEDIA:/home/node/.openclaw/media/browser/screenshot_1234_abc123.png
```

Do NOT wrap it in markdown image syntax. Do NOT modify it. Just paste the raw `MEDIA:...` string.

---

## Rules

1. **URL-encode** all query params (`+` for spaces, `%3A` for `:`, `%2F` for `/`)
2. **Always** include `--max-time` on every curl call
3. Use `search` for quick lookups; `deep_search` for comprehensive research
4. **Cite sources** with URLs; include current date for time-sensitive results
5. Chinese queries: add `&language=zh-CN`; news: add `&categories=news`
6. Parse JSON — never dump raw JSON to the user
7. For any request involving live or changing information, perform at least one `search` call before answering
8. If the user asks about a specific site or page, use `navigate` or `extract_text` instead of guessing
9. If the first search returns low-quality results, refine the query and search again before answering
10. Prefer short, targeted queries first, then broaden only if needed
11. If user asks "show me", "what it looks like", or needs visual verification, call `/screenshot`

## Query Optimization

- **Keep queries short**: 2–5 keywords work best. Avoid full sentences.
- **Use English keywords** even for Chinese users — English queries return more results. Translate key terms.
- **Remove filler words**: "what is the", "how to", "please find" → just use the core keywords.
- **No special operators**: `site:`, `filetype:`, quotes — these may cause empty results. Use plain keywords.
- **Split complex questions** into 2 separate searches rather than one long query.

### Retry Strategy (when results are empty or weak)

If a search returns 0 results or only irrelevant hits:
1. **Simplify the query** — remove adjectives, dates, reduce to 2–3 core words
2. **Switch language** — try `&language=en-US` if Chinese query failed, or vice versa
3. **Try news category** — add `&categories=news` for time-sensitive topics
4. **Use deep_search** — falls back to full page content extraction
5. **Navigate directly** — if you know the likely URL, use `/navigate?url=...`

### Examples

| User asks | Good query | Bad query |
|-----------|-----------|-----------|
| 最新的 Claude 模型 | `Claude+model+latest+2026` | `最新的Claude模型是什么` |
| iPhone 17 价格 | `iPhone+17+price` | `iPhone 17 售价是多少钱` |
| 今天的新闻 | `news+today` with `&categories=news` | `今天有什么新闻` |

## Execution Policy

- Do not say you cannot browse the web. This skill is the web access path.
- Do not answer live-information questions from memory unless search fails.
- When search succeeds, summarize the findings and include the relevant URLs.
- When search fails, explicitly say search failed and why.

## Required Behavior Examples

User: `What is the latest OpenAI model?`
Action: run `search` first, then answer with links.

User: `Check the price of Claude Code Max plan.`
Action: run `search` or `navigate` first, then answer with the source URL.

User: `Summarize this documentation page: https://...`
Action: run `navigate` first, then summarize.

User: `Who won the game today?`
Action: run `search` first, then answer.
