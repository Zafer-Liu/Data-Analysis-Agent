# -*- coding: utf-8 -*-
"""Playwright-based headed browser service for visible web research.

A single ``BrowserService`` instance runs a dedicated browser thread so that
sync Playwright (which is not thread-safe) can be used safely under waitress
multi-threading.  Flask request threads submit commands via a queue and block
until the browser thread responds.

The browser launches **headed** (visible window) so the user can see the agent
operate and manually solve CAPTCHA / human-verification challenges when they
appear.
"""
from __future__ import annotations

import atexit
import os
import json
import logging
import queue
import threading
import time
import urllib.parse

log = logging.getLogger(__name__)

_SEARCH_URLS = {
    "google": "https://www.google.com/search?q={}&hl=zh-CN",
    "bing": "https://www.bing.com/search?q={}",
    "baidu": "https://www.baidu.com/s?wd={}",
}
_RESULT_SELECTORS = {
    "google": "div.g div.yuRUbf > a",
    "bing": "li.b_algo h2 > a",
    "baidu": "div.result.c-container h3.t > a, div.result-op h3 > a",
}
_SKIP_HREF_PREFIXES = ("javascript:", "#", "mailto:")
_IDLE_TIMEOUT = 300  # 5 minutes

# ── Verification (CAPTCHA / bot-check) detection ───────────────────────────

_VERIF_URL_PATTERNS = (
    "/sorry/",
    "/cdn-cgi/challenge",
    "recaptcha",
    "/verify",
    "/secure/",
    "challenge",
)

_VERIF_TEXT_PATTERNS = (
    "unusual traffic",
    "checking your browser",
    "are you a robot",
    "verify you are human",
    "please verify",
    "please complete the security check",
    "\u8bf7\u5b8c\u6210\u9a8c\u8bc1",
    "\u9a8c\u8bc1\u7801",
    "\u4eba\u673a\u9a8c\u8bc1",
    "\u5b89\u5168\u9a8c\u8bc1",
    "\u8bf7\u5b8c\u6210\u5b89\u5168\u9a8c\u8bc1",
)

_VERIF_SELECTORS = (
    'iframe[src*="recaptcha"]',
    'iframe[src*="captcha"]',
    'iframe[src*="challenge"]',
    ".g-recaptcha",
    ".h-captcha",
    "#challenge-form",
    "#captcha",
    "#bfr_research",
    "#ev_tob",
)

_VERIF_WAIT_TIMEOUT = 60
_VERIF_POLL_INTERVAL = 2


def _detect_verification(page) -> bool:
    """Return True if the current page looks like a CAPTCHA / bot challenge."""
    try:
        url = (page.url or "").lower()
        for pat in _VERIF_URL_PATTERNS:
            if pat in url:
                return True
    except Exception:
        pass
    try:
        for sel in _VERIF_SELECTORS:
            if page.query_selector(sel):
                return True
    except Exception:
        pass
    try:
        body = page.inner_text("body")[:2000].lower()
        for pat in _VERIF_TEXT_PATTERNS:
            if pat.lower() in body:
                return True
    except Exception:
        pass
    return False


class _Cmd:
    __slots__ = ("name", "params", "reply")

    def __init__(self, name: str, params: dict):
        self.name = name
        self.params = params
        self.reply: queue.Queue = queue.Queue()


_EXTRACT_TABLES_JS = """() => {
    const out = [];
    const MAX_TABLES = 10, MAX_ROWS = 10000;
    for (const t of document.querySelectorAll('table')) {
        if (out.length >= MAX_TABLES) break;
        const rows = [];
        for (const tr of t.querySelectorAll('tr')) {
            const cells = Array.from(tr.querySelectorAll('th,td'))
                .map(c => (c.innerText || '').trim());
            if (cells.some(c => c !== '')) rows.push(cells);
        }
        if (rows.length < 2) continue;
        let width = 0;
        for (const r of rows) width = Math.max(width, r.length);
        if (width < 2) continue;
        if (rows.length > MAX_ROWS) rows.length = MAX_ROWS;
        let label = (t.caption && t.caption.innerText || '').trim();
        if (!label) label = (t.getAttribute('aria-label') || '').trim();
        if (!label) {
            let el = t.previousElementSibling;
            for (let i = 0; i < 4 && el; i++, el = el.previousElementSibling) {
                if (/^H[1-6]$/.test(el.tagName)) {
                    label = (el.innerText || '').trim();
                    break;
                }
            }
        }
        out.push({name: label, rows: rows});
    }
    return out;
}"""


class BrowserService:
    """Singleton bridging Flask threads and a dedicated Playwright thread."""

    _instance: BrowserService | None = None
    _lock = threading.Lock()

    def __new__(cls) -> BrowserService:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._cmd_q: queue.Queue[_Cmd | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._thread_lock = threading.Lock()

    def _send(self, name: str, **params) -> dict:
        self._ensure_thread()
        cmd = _Cmd(name, params)
        self._cmd_q.put(cmd)
        try:
            return cmd.reply.get(timeout=300)
        except queue.Empty:
            return {"error": "browser command timed out (300s)"}

    def _ensure_thread(self) -> None:
        if self._started:
            return
        with self._thread_lock:
            if self._started:
                return
            self._thread = threading.Thread(
                target=self._run, name="browser-service", daemon=True)
            self._thread.start()
            self._started = True
            atexit.register(self._shutdown)

    def _shutdown(self) -> None:
        if not self._started:
            return
        self._cmd_q.put(None)
        if self._thread:
            self._thread.join(timeout=5)
        self._started = False

    # -- public API ---------------------------------------------------------

    def search(self, query: str, engine: str = "google") -> str:
        return json.dumps(self._send("search", query=query, engine=engine),
                          ensure_ascii=False)

    def navigate(self, url: str) -> str:
        return json.dumps(self._send("navigate", url=url), ensure_ascii=False)

    def click(self, selector: str, text: str = "") -> str:
        return json.dumps(self._send("click", selector=selector, text=text),
                          ensure_ascii=False)

    def fill(self, selector: str, value: str) -> str:
        return json.dumps(self._send("fill", selector=selector, value=value),
                          ensure_ascii=False)

    def extract(self, max_chars: int = 8000) -> str:
        return json.dumps(self._send("extract", max_chars=max_chars),
                          ensure_ascii=False)

    def extract_tables(self) -> str:
        return json.dumps(self._send("extract_tables"), ensure_ascii=False)

    def screenshot(self, output_path: str) -> str:
        return json.dumps(self._send("screenshot", output_path=output_path),
                          ensure_ascii=False)

    def close(self) -> str:
        return json.dumps(self._send("close"), ensure_ascii=False)

    def check_verification(self) -> str:
        return json.dumps(self._send("check_verification"), ensure_ascii=False)

    def recheck(self, tool_name: str = "", **params) -> str:
        return json.dumps(self._send("recheck", tool_name=tool_name, **params),
                          ensure_ascii=False)

    # -- browser thread -----------------------------------------------------

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        pw = browser = page = None
        last_active = time.monotonic()

        def _cleanup():
            nonlocal pw, browser, page
            for obj in (page, browser):
                if obj:
                    try: obj.close()
                    except Exception: pass
            if pw:
                try: pw.stop()
                except Exception: pass
            page = browser = pw = None

        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=False)
            page = browser.new_page()
            page.set_default_timeout(30000)

            while True:
                if time.monotonic() - last_active >= _IDLE_TIMEOUT:
                    break
                try:
                    cmd = self._cmd_q.get(timeout=1.0)
                except queue.Empty:
                    continue
                if cmd is None:
                    break
                last_active = time.monotonic()
                handler = {
                    "search":             self._do_search,
                    "navigate":           self._do_navigate,
                    "click":              self._do_click,
                    "fill":               self._do_fill,
                    "extract":            self._do_extract,
                    "extract_tables":     self._do_extract_tables,
                    "screenshot":         self._do_screenshot,
                    "close":              self._do_close,
                    "check_verification": self._do_check_verification,
                    "recheck":            self._do_recheck,
                }.get(cmd.name)
                if handler:
                    try:
                        result = handler(page, **cmd.params)
                    except Exception as exc:
                        log.exception("browser %s failed", cmd.name)
                        result = {"error": str(exc)}
                        if "Target page" in str(exc) or "closed" in str(exc):
                            page = browser.new_page()
                            page.set_default_timeout(30000)
                else:
                    result = {"error": f"unknown command: {cmd.name}"}
                cmd.reply.put(result)
                if cmd.name == "close":
                    break
        except Exception:
            log.exception("browser thread crashed")
        finally:
            _cleanup()
            self._started = False

    # -- command handlers ---------------------------------------------------

    @staticmethod
    def _do_search(page, *, query: str, engine: str = "google") -> dict:
        engine = engine if engine in _SEARCH_URLS else "google"
        url = _SEARCH_URLS[engine].format(urllib.parse.quote(query))
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        if _detect_verification(page):
            return {"engine": engine, "query": query, "results": [],
                    "count": 0, "page_url": page.url,
                    "verification_needed": True}
        return BrowserService._extract_search_results(page, engine, query)

    @staticmethod
    def _extract_search_results(page, engine: str, query: str) -> dict:
        links = page.query_selector_all(_RESULT_SELECTORS[engine])
        results = []
        for el in links[:10]:
            href = el.get_attribute("href") or ""
            if not href or href.startswith(_SKIP_HREF_PREFIXES):
                continue
            title = el.inner_text().strip()
            if title:
                results.append({"title": title[:200], "url": href})
        return {"engine": engine, "query": query, "results": results,
                "count": len(results), "page_url": page.url}

    @staticmethod
    def _do_navigate(page, *, url: str) -> dict:
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        if _detect_verification(page):
            return {"title": page.title(), "url": page.url,
                    "verification_needed": True}
        return {"title": page.title(), "url": page.url}

    @staticmethod
    def _do_click(page, *, selector: str, text: str = "") -> dict:
        if text:
            page.get_by_text(text, exact=False).first.click()
        else:
            page.click(selector)
        page.wait_for_timeout(1000)
        return {"clicked": text or selector, "page_url": page.url,
                "page_title": page.title()}

    @staticmethod
    def _do_fill(page, *, selector: str, value: str) -> dict:
        page.fill(selector, value)
        page.press(selector, "Enter")
        page.wait_for_timeout(1500)
        return {"filled": selector, "value": value,
                "page_url": page.url, "page_title": page.title()}

    @staticmethod
    def _do_extract(page, *, max_chars: int = 8000) -> dict:
        max_chars = max(500, min(int(max_chars or 8000), 20000))
        text = page.inner_text("body")
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]"
        return {"title": page.title(), "url": page.url,
                "text": text, "length": len(text)}

    @staticmethod
    def _do_extract_tables(page, **_kw) -> dict:
        tables = page.evaluate(_EXTRACT_TABLES_JS)
        return {"title": page.title(), "url": page.url, "tables": tables or []}

    @staticmethod
    def _do_screenshot(page, *, output_path: str) -> dict:
        page.screenshot(path=output_path, full_page=False)
        return {"path": output_path}

    @staticmethod
    def _do_close(page, **_kw) -> dict:
        return {"closed": True}

    @staticmethod
    def _do_recheck(page, *, tool_name: str = "", **params) -> dict:
        """After verification is solved, extract results from the CURRENT page.

        Does NOT re-navigate — the user already solved the challenge and the
        page should now show the original content (search results, etc.).
        """
        if _detect_verification(page):
            return {"verification_needed": True,
                    "page_url": page.url,
                    "reason": "still_on_verification_page"}
        if tool_name == "browser_search":
            engine = params.get("engine", "google")
            query = params.get("query", "")
            return BrowserService._extract_search_results(page, engine, query)
        if tool_name == "browser_navigate":
            return {"title": page.title(), "url": page.url}
        if tool_name == "browser_extract_tables":
            return BrowserService._do_extract_tables(page)
        # For click/fill/extract the page is already in the right state
        # — just extract body text as a best-effort fallback.
        try:
            text = page.inner_text("body")[:8000]
        except Exception:
            text = ""
        return {"title": page.title(), "url": page.url,
                "text": text, "length": len(text)}

    @staticmethod
    def _do_check_verification(page, **_kw) -> dict:
        """Wait for user to solve verification challenge, up to 60s."""
        if not _detect_verification(page):
            return {"resolved": True, "waited_seconds": 0}
        start = time.monotonic()
        while time.monotonic() - start < _VERIF_WAIT_TIMEOUT:
            time.sleep(_VERIF_POLL_INTERVAL)
            if not _detect_verification(page):
                return {"resolved": True,
                        "waited_seconds": round(time.monotonic() - start)}
        return {"resolved": False, "waited_seconds": _VERIF_WAIT_TIMEOUT,
                "reason": "verification_timeout"}


# -- module-level wrappers (called from agent.py dispatch) ------------------

def browser_search(query: str, engine: str = "google") -> str:
    return BrowserService().search(query, engine=engine)

def browser_navigate(url: str) -> str:
    return BrowserService().navigate(url)

def browser_click(selector: str, text: str = "") -> str:
    return BrowserService().click(selector, text=text)

def browser_fill(selector: str, value: str) -> str:
    return BrowserService().fill(selector, value=value)

def browser_extract(max_chars: int = 8000) -> str:
    return BrowserService().extract(max_chars=max_chars)

def browser_extract_tables() -> str:
    return BrowserService().extract_tables()

def browser_screenshot(output_path: str) -> str:
    return BrowserService().screenshot(output_path=output_path)

def browser_close() -> str:
    return BrowserService().close()

def browser_check_verification() -> str:
    return BrowserService().check_verification()

def browser_recheck(tool_name: str = "", **params) -> str:
    return BrowserService().recheck(tool_name=tool_name, **params)
