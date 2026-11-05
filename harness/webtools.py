"""Web and browser tools for the Spark Agent.

Nine tools, exposed to the model as OpenAI-style function schemas:

- ``web_search(query, count, topic)`` - search the web. Uses Tavily when
  ``TAVILY_API_KEY`` is set, otherwise falls back to scraping a search
  engine's HTML result page.
- ``web_fetch(url, max_chars)`` - fetch one page and return its readable
  text. Uses Firecrawl when ``FIRECRAWL_API_KEY`` is set, otherwise fetches
  the HTML directly and strips it with BeautifulSoup.
- ``browser_navigate(url)`` / ``browser_text()`` / ``browser_click(selector)``
  / ``browser_type(selector, text)`` / ``browser_press(key)`` /
  ``browser_screenshot(path)`` / ``browser_close()`` - drive a single shared
  headless Chromium (Playwright) page that is started lazily on the first
  browser call and reused afterwards.

Every URL that the agent can make the machine fetch is checked against an
SSRF guard first: only ``http`` and ``https`` are allowed, and the host
must not resolve to a loopback, private, link-local or otherwise reserved
address. That blocks the usual "fetch http://127.0.0.1:8081/" probes and
anything reachable only from inside the machine.

No tool prints anything: they all return plain text, so the agent can pass
the result straight back to the model.
"""

from __future__ import annotations

import atexit
import base64
import ipaddress
import os
import re
import socket
import urllib.parse
from typing import NamedTuple

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Every network call gets an explicit timeout. Never None.
_TIMEOUT = 20

# Milliseconds, for the Playwright side of things.
_TIMEOUT_MS = 20_000

# How much text a browser read may return.
_MAX_BROWSER_TEXT = 8000

# How much of one search snippet to keep.
_MAX_SNIPPET = 300

# Default and ceiling for web_fetch.
_DEFAULT_MAX_CHARS = 8000
_MAX_MAX_CHARS = 100_000

# How many search results to ask for at most.
_MAX_RESULTS = 20

# How many HTTP redirects to follow while fetching.
_MAX_REDIRECTS = 5

_TAVILY_URL = "https://api.tavily.com/search"
_FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"
_DDG_URL = "https://html.duckduckgo.com/html/"
_BING_URL = "https://www.bing.com/search"


class _Hit(NamedTuple):
    """One search result."""

    title: str
    url: str
    snippet: str


class _BlockedUrl(Exception):
    """Raised when a URL is refused by the SSRF guard."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _redact(text: str) -> str:
    """Remove any API key that might show up in an error message."""
    out = text
    for var in ("TAVILY_API_KEY", "FIRECRAWL_API_KEY"):
        key = os.environ.get(var, "")
        if key:
            out = out.replace(key, "***")
    return out


def _tavily_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "").strip()


def _firecrawl_key() -> str:
    return os.environ.get("FIRECRAWL_API_KEY", "").strip()


def _one_line(text: str) -> str:
    """Collapse whitespace so a snippet fits on one line."""
    return " ".join(text.split())


def _clean_text(text: str) -> str:
    """Tidy scraped text: no runaway blank lines, no leading/trailing space."""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate(text: str, limit: int) -> tuple[str, int]:
    """Cut ``text`` to ``limit`` characters; return the cut text and how many
    characters were dropped."""
    if limit <= 0:
        return "", len(text)
    if len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit


# --------------------------------------------------------------------------
# SSRF guard
# --------------------------------------------------------------------------


def _check_url(url: str) -> str | None:
    """Return a reason string when ``url`` must not be fetched, else None.

    Refuses every scheme except http and https, and refuses hosts that
    resolve to loopback, private, link-local, multicast or otherwise
    reserved addresses.
    """
    raw = (url or "").strip()
    if not raw:
        return "the url is empty"

    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        return f"the url could not be parsed ({exc})"

    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        shown = scheme if scheme else "(none)"
        return f"the scheme {shown} is not allowed, only http and https are"

    try:
        host = parts.hostname
    except ValueError as exc:
        return f"the url has an invalid host ({exc})"
    if not host:
        return "the url has no host"

    port = parts.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return f"the host {host!r} could not be resolved ({exc.strerror or exc})"
    except OSError as exc:
        return f"the host {host!r} could not be resolved ({exc})"

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return f"the host {host!r} resolved to an address that is not an IP"
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return (
                f"the host {host!r} resolves to the non-public address {address}, "
                "so it was refused"
            )
    return None


def _require_fetchable(url: str) -> None:
    """Raise :class:`_BlockedUrl` when ``url`` may not be fetched."""
    reason = _check_url(url)
    if reason:
        raise _BlockedUrl(reason)


# --------------------------------------------------------------------------
# Search backends
# --------------------------------------------------------------------------


def _search_tavily(query: str, count: int, topic: str) -> list[_Hit] | None:
    """Search with Tavily. Returns None when the API is unusable."""
    key = _tavily_key()
    if not key:
        return None

    payload = {
        "api_key": key,
        "query": query,
        "max_results": count,
        "search_depth": "basic",
        "topic": topic,
    }
    try:
        response = requests.post(
            _TAVILY_URL,
            json=payload,
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Tavily request failed: {_redact(str(exc))}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"Tavily returned HTTP {response.status_code}: "
            f"{_redact(response.text[:200])}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Tavily sent a response that is not JSON: {exc}") from exc

    hits: list[_Hit] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = _one_line(str(item.get("url") or ""))
        if not url.startswith(("http://", "https://")):
            continue
        hits.append(
            _Hit(
                title=_one_line(str(item.get("title") or url)),
                url=url,
                snippet=_one_line(str(item.get("content") or "")),
            )
        )
    return hits


def _ddg_result_url(href: str) -> str:
    """Unwrap the redirect link DuckDuckGo puts on its result anchors."""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parts = urllib.parse.urlsplit(href)
    except ValueError:
        return ""
    target = urllib.parse.parse_qs(parts.query).get("uddg")
    if target:
        return urllib.parse.unquote(target[0])
    if parts.scheme in ("http", "https"):
        return href
    return ""


def _search_ddg(query: str) -> list[_Hit]:
    """Scrape the DuckDuckGo HTML result page."""
    response = requests.get(
        _DDG_URL,
        params={"q": query},
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
    )
    if response.status_code != 200:
        raise RuntimeError(f"DuckDuckGo returned HTTP {response.status_code}")

    soup = BeautifulSoup(response.text, "html.parser")
    hits: list[_Hit] = []
    for anchor in soup.select("a.result__a"):
        url = _ddg_result_url(anchor.get("href") or "")
        if not url.startswith(("http://", "https://")):
            continue
        title = _one_line(anchor.get_text(" ", strip=True))
        snippet = ""
        container = anchor.find_parent(class_="result")
        if container is not None:
            node = container.select_one(".result__snippet")
            if node is not None:
                snippet = _one_line(node.get_text(" ", strip=True))
        hits.append(_Hit(title=title or url, url=url, snippet=snippet))
    return hits


def _bing_result_url(href: str) -> str:
    """Decode the redirect link Bing puts on its result anchors."""
    if not href:
        return ""
    try:
        parts = urllib.parse.urlsplit(href)
    except ValueError:
        return ""
    if parts.netloc.endswith("bing.com"):
        packed = urllib.parse.parse_qs(parts.query).get("u")
        if packed:
            data = packed[0]
            if data.startswith("a1"):
                data = data[2:]
            data = data.replace("-", "+").replace("_", "/")
            data += "=" * (-len(data) % 4)
            try:
                decoded = base64.b64decode(data).decode("utf-8", "replace")
            except (ValueError, TypeError):
                decoded = ""
            if decoded.startswith(("http://", "https://")):
                return decoded
    if parts.scheme in ("http", "https"):
        return href
    return ""


def _search_bing(query: str) -> list[_Hit]:
    """Scrape the Bing HTML result page."""
    response = requests.get(
        _BING_URL,
        params={"q": query},
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Bing returned HTTP {response.status_code}")

    soup = BeautifulSoup(response.text, "html.parser")
    hits: list[_Hit] = []
    for item in soup.select("li.b_algo"):
        anchor = item.select_one("h2 a")
        if anchor is None:
            continue
        url = _bing_result_url(anchor.get("href") or "")
        if not url.startswith(("http://", "https://")):
            continue
        title = _one_line(anchor.get_text(" ", strip=True))
        snippet = ""
        node = item.select_one(".b_caption p") or item.select_one("p")
        if node is not None:
            snippet = _one_line(node.get_text(" ", strip=True))
        hits.append(_Hit(title=title or url, url=url, snippet=snippet))
    return hits


def _format_hits(hits: list[_Hit], query: str) -> str:
    """Render search results as a numbered list."""
    if not hits:
        return f"No results found for {query!r}."

    lines: list[str] = []
    for index, hit in enumerate(hits, start=1):
        title = _one_line(hit.title) or hit.url
        snippet = _one_line(hit.snippet)
        if len(snippet) > _MAX_SNIPPET:
            snippet = snippet[: _MAX_SNIPPET - 3].rstrip() + "..."
        lines.append(f"{index}. {title}\n   {hit.url}\n   {snippet}")
    return "\n".join(lines)


def tool_web_search(query: str, count: int = 5, topic: str = "general") -> str:
    """Search the web and return a numbered list of results."""
    query = str(query or "").strip()
    if not query:
        return "error: web_search needs a query."

    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(count, _MAX_RESULTS))

    topic = str(topic or "general").strip().lower()
    if topic not in ("general", "news"):
        topic = "general"

    problems: list[str] = []
    hits: list[_Hit] = []

    if _tavily_key():
        try:
            found = _search_tavily(query, count, topic)
        except Exception as exc:  # noqa: BLE001 - fall through to the scrape
            problems.append(str(exc))
            found = None
        if found:
            hits = found[:count]
    else:
        problems.append("TAVILY_API_KEY is not set")

    if not hits:
        for scraper in (_search_ddg, _search_bing):
            try:
                found = scraper(query)
            except Exception as exc:  # noqa: BLE001 - try the next scraper
                problems.append(f"{scraper.__name__}: {_redact(str(exc))}")
                continue
            if found:
                hits = found[:count]
                break

    if not hits:
        detail = "; ".join(problems) if problems else "no backend returned results"
        return f"error: web_search found no results for {query!r} ({_redact(detail)})."

    return _format_hits(hits, query)


# --------------------------------------------------------------------------
# Page fetching
# --------------------------------------------------------------------------


def _http_get(url: str, headers: dict | None = None) -> requests.Response:
    """GET a url, validating every hop of the redirect chain."""
    current = url
    for _ in range(_MAX_REDIRECTS):
        _require_fetchable(current)
        response = requests.get(
            current,
            headers=headers or {"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
            allow_redirects=False,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                return response
            current = urllib.parse.urljoin(current, location)
            continue
        return response
    raise RuntimeError(f"too many redirects (more than {_MAX_REDIRECTS})")


def _html_to_text(html: str) -> tuple[str, str]:
    """Strip a HTML page down to its title and readable text.

    The title is returned separately because plenty of pages carry it only
    in the <title> element, and dropping it would leave the caller without
    any idea which page it is looking at.
    """
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title is not None:
        title = _one_line(soup.title.get_text(" ", strip=True))

    for tag in soup(
        ["script", "style", "noscript", "nav", "footer", "header", "aside", "svg", "title"]
    ):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return title, _clean_text(text)


def _fetch_with_firecrawl(url: str) -> tuple[str, str]:
    """Scrape a page with Firecrawl. Returns its title and markdown."""
    key = _firecrawl_key()
    response = requests.post(
        _FIRECRAWL_URL,
        json={"url": url, "formats": ["markdown"]},
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Firecrawl returned HTTP {response.status_code}: "
            f"{_redact(response.text[:200])}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Firecrawl sent a response that is not JSON: {exc}") from exc
    if not data.get("success", False):
        raise RuntimeError(f"Firecrawl reported a failure: {_redact(str(data)[:200])}")
    payload = data.get("data") or {}
    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise RuntimeError("Firecrawl returned no markdown")
    metadata = payload.get("metadata") or {}
    title = _one_line(str(metadata.get("title") or "")) if isinstance(metadata, dict) else ""
    return title, _clean_text(markdown)


def tool_web_fetch(url: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Fetch one page and return its readable text."""
    url = str(url or "").strip()
    if not url:
        return "error: web_fetch needs a url."

    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = _DEFAULT_MAX_CHARS
    max_chars = max(1, min(max_chars, _MAX_MAX_CHARS))

    try:
        _require_fetchable(url)
    except _BlockedUrl as exc:
        return f"error: web_fetch refused this url: {exc}."

    text = ""
    title = ""
    notes: list[str] = []

    if _firecrawl_key():
        try:
            title, text = _fetch_with_firecrawl(url)
        except Exception as exc:  # noqa: BLE001 - fall back to a direct fetch
            notes.append(f"Firecrawl failed ({_redact(str(exc))})")
    else:
        notes.append("FIRECRAWL_API_KEY is not set")

    if not text:
        try:
            response = _http_get(url)
        except _BlockedUrl as exc:
            return f"error: web_fetch refused this url: {exc}."
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            return f"error: web_fetch failed for {url}: {_redact(str(exc))}."
        if response.status_code >= 400:
            return f"error: web_fetch got HTTP {response.status_code} for {url}."
        title, text = _html_to_text(response.text)
        if not text:
            return f"error: web_fetch found no readable text at {url}."

    # The title is worth keeping even when the body does not repeat it.
    if title and title.lower() not in text.lower():
        text = f"{title}\n\n{text}"

    cut_text, dropped = _truncate(text, max_chars)
    if dropped:
        note = f"\n\n[truncated {dropped} more characters]"
        if notes:
            note = f" [{'; '.join(notes)}]" + note
        return cut_text + note
    if notes:
        return f"{cut_text}\n\n[{' ; '.join(notes)}]"
    return cut_text


# --------------------------------------------------------------------------
# Shared browser
# --------------------------------------------------------------------------

_playwright = None
_browser = None
_page = None
_screenshot_counter = 0


def _close_browser() -> None:
    """Close the shared browser and release Playwright. Safe to call twice."""
    global _playwright, _browser, _page

    page, browser = _page, _browser
    _page = None
    _browser = None
    for obj in (page, browser):
        if obj is None:
            continue
        try:
            obj.close()
        except Exception:  # noqa: BLE001 - shutting down, ignore
            pass

    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:  # noqa: BLE001 - shutting down, ignore
            pass
        _playwright = None


atexit.register(_close_browser)


def _get_page(create: bool = True):
    """Return the shared Playwright page, starting Chromium if needed."""
    global _playwright, _browser, _page

    if _page is not None:
        try:
            if not _page.is_closed():
                return _page
        except Exception:  # noqa: BLE001 - fall through and rebuild
            pass
        _close_browser()

    if not create:
        return None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(f"Playwright is not installed: {exc}") from exc

    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(headless=True)
    _page = _browser.new_page()
    _page.set_default_timeout(_TIMEOUT_MS)
    return _page


def _require_page():
    """Return the shared page, or raise when no page has been opened yet."""
    page = _get_page(create=False)
    if page is None:
        raise RuntimeError("no page is open yet, call browser_navigate first")
    return page


def _page_title(page) -> str:
    """Title of the current page."""
    try:
        return _one_line(page.title())
    except Exception:  # noqa: BLE001
        return ""


def _page_text(page, with_title: bool = True) -> str:
    """Readable text of the current page, capped at _MAX_BROWSER_TEXT.

    The title is included by default: many pages carry it only in the
    <title> element, so without it the caller cannot tell which page this
    is. Callers that already show the title separately pass False.
    """
    try:
        text = page.inner_text("body")
    except Exception:  # noqa: BLE001 - a page without a body, fall back
        try:
            _, text = _html_to_text(page.content())
        except Exception:  # noqa: BLE001
            text = ""
    text = _clean_text(text)
    if with_title:
        title = _page_title(page)
        if title and title.lower() not in text.lower():
            text = f"{title}\n\n{text}"
    return text


def _capped(text: str) -> str:
    """Cap browser output and say how much was cut."""
    cut_text, dropped = _truncate(text, _MAX_BROWSER_TEXT)
    if dropped:
        return cut_text + f"\n\n[truncated {dropped} more characters]"
    return cut_text


def tool_browser_navigate(url: str) -> str:
    """Open a url in the shared browser page."""
    url = str(url or "").strip()
    if not url:
        return "error: browser_navigate needs a url."

    try:
        _require_fetchable(url)
    except _BlockedUrl as exc:
        return f"error: browser_navigate refused this url: {exc}."

    try:
        page = _get_page()
        page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001 - report, do not raise
        return f"error: browser_navigate failed for {url}: {_redact(str(exc))}."

    try:
        title = page.title()
    except Exception:  # noqa: BLE001
        title = ""
    current = url
    try:
        current = page.url
    except Exception:  # noqa: BLE001
        pass

    # A redirect may have landed on a host that is not publicly reachable.
    landed = _check_url(current)
    if landed:
        return (
            f"error: browser_navigate landed on {current!r} which is not allowed "
            f"({landed})."
        )

    return f"Title: {title}\nURL: {current}\n\n{_capped(_page_text(page, with_title=False))}"


def tool_browser_text() -> str:
    """Return the visible text of the current page."""
    try:
        page = _require_page()
    except Exception as exc:  # noqa: BLE001
        return f"error: browser_text failed: {_redact(str(exc))}."
    return _capped(_page_text(page))


def tool_browser_click(selector: str) -> str:
    """Click an element and report the page afterwards."""
    selector = str(selector or "").strip()
    if not selector:
        return "error: browser_click needs a selector."

    try:
        page = _require_page()
        page.click(selector, timeout=_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        return f"error: browser_click failed for {selector!r}: {_redact(str(exc))}."

    try:
        title = page.title()
    except Exception:  # noqa: BLE001
        title = ""
    try:
        current = page.url
    except Exception:  # noqa: BLE001
        current = ""
    return f"Title: {title}\nURL: {current}\n\n{_capped(_page_text(page, with_title=False))}"


def tool_browser_type(selector: str, text: str) -> str:
    """Focus an element and type text into it."""
    selector = str(selector or "").strip()
    if not selector:
        return "error: browser_type needs a selector."

    value = "" if text is None else str(text)

    try:
        page = _require_page()
        page.click(selector, timeout=_TIMEOUT_MS)
        page.keyboard.type(value)
    except Exception as exc:  # noqa: BLE001
        return f"error: browser_type failed for {selector!r}: {_redact(str(exc))}."

    return f"Typed {len(value)} characters into {selector!r}."


def tool_browser_press(key: str) -> str:
    """Press a key on the current page."""
    key = str(key or "").strip()
    if not key:
        return "error: browser_press needs a key, for example Enter or Tab."

    try:
        page = _require_page()
        page.keyboard.press(key)
    except Exception as exc:  # noqa: BLE001
        return f"error: browser_press failed for {key!r}: {_redact(str(exc))}."

    return f"Pressed {key!r}."


def tool_browser_screenshot(path: str = "") -> str:
    """Save a PNG of the current page and return where it went."""
    global _screenshot_counter

    try:
        page = _get_page()
    except Exception as exc:  # noqa: BLE001
        return f"error: browser_screenshot failed: {_redact(str(exc))}."

    target = str(path or "").strip()
    if not target:
        _screenshot_counter += 1
        target = f"browser_screenshot_{_screenshot_counter}.png"

    try:
        folder = os.path.dirname(os.path.abspath(target))
        if folder:
            os.makedirs(folder, exist_ok=True)
        page.screenshot(path=target, timeout=_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        return f"error: browser_screenshot failed: {_redact(str(exc))}."

    if not os.path.isfile(target) or os.path.getsize(target) == 0:
        return f"error: browser_screenshot did not write {target}."

    return os.path.abspath(target)


def tool_browser_close() -> str:
    """Close the shared browser."""
    _close_browser()
    return "Closed the browser."


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def build_web_tools() -> list[dict]:
    """Return OpenAI-style function schemas for every web tool."""
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the web and get back a numbered list of results with "
                    "title, url and a short snippet. Use it to look up facts, "
                    "documentation, versions or errors."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The words to search for.",
                        },
                        "count": {
                            "type": "integer",
                            "description": "How many results to return (1-20).",
                            "default": 5,
                        },
                        "topic": {
                            "type": "string",
                            "description": "'general' for web pages, 'news' for recent events.",
                            "enum": ["general", "news"],
                            "default": "general",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": (
                    "Fetch one web page and return its readable text. Use it after "
                    "web_search to read a specific result."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The http or https url to read.",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum number of characters to return.",
                            "default": _DEFAULT_MAX_CHARS,
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_navigate",
                "description": (
                    "Open a url in a headless browser and return the page title, "
                    "url and visible text. Use it for pages that need JavaScript."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The http or https url to open.",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_text",
                "description": "Return the visible text of the page the browser is on.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_click",
                "description": (
                    "Click an element on the current page, given a CSS selector, "
                    "and return the new title and text."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector of the element to click.",
                        },
                    },
                    "required": ["selector"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_type",
                "description": (
                    "Click an input element and type text into it. Use it to fill "
                    "in a search box or a form field."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector of the input to focus.",
                        },
                        "text": {
                            "type": "string",
                            "description": "The text to type.",
                        },
                    },
                    "required": ["selector", "text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_press",
                "description": (
                    "Press a key on the current page, for example Enter, Tab or "
                    "ArrowDown."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "The key to press, for example Enter.",
                        },
                    },
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_screenshot",
                "description": (
                    "Save a PNG screenshot of the current page and return the file "
                    "path. Leave the path empty for an automatic name."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Where to save the PNG. Defaults to "
                                "browser_screenshot_<n>.png in the current folder."
                            ),
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_close",
                "description": "Close the browser and free the page for later use.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    ]


def execute_web_tool(name: str, arguments: dict) -> str:
    """Run one web tool by name and return plain text.

    Never raises for an expected failure: return a string starting with
    "error: " instead. Never returns an empty string on failure.
    """
    if not isinstance(arguments, dict):
        arguments = {}

    try:
        if name == "web_search":
            return tool_web_search(
                arguments.get("query", ""),
                arguments.get("count", 5),
                arguments.get("topic", "general"),
            )
        if name == "web_fetch":
            return tool_web_fetch(
                arguments.get("url", ""),
                arguments.get("max_chars", _DEFAULT_MAX_CHARS),
            )
        if name == "browser_navigate":
            return tool_browser_navigate(arguments.get("url", ""))
        if name == "browser_text":
            return tool_browser_text()
        if name == "browser_click":
            return tool_browser_click(arguments.get("selector", ""))
        if name == "browser_type":
            return tool_browser_type(
                arguments.get("selector", ""),
                arguments.get("text", ""),
            )
        if name == "browser_press":
            return tool_browser_press(arguments.get("key", ""))
        if name == "browser_screenshot":
            return tool_browser_screenshot(arguments.get("path", ""))
        if name == "browser_close":
            return tool_browser_close()
    except Exception as exc:  # noqa: BLE001 - a tool failure is a result, not a crash
        return f"error: {name} failed: {_redact(str(exc))}"

    return f"error: unknown tool {name!r}"
