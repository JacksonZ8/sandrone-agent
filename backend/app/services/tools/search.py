from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

import httpx
from sqlmodel import Session

from ...models import ToolConfig
from .audit import _record_tool_call


class _DuckHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_link = False
        self._href = ""
        self._title: list[str] = []
        self._in_snippet = False
        self._snippet: list[str] = []
        self._link_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "result-link" in (attrs.get("class") or ""):
            self._in_link = True
            self._href = attrs.get("href", "")
            self._title = []
        elif tag in ("td", "div") and "result-snippet" in (attrs.get("class") or ""):
            self._in_snippet = True
            self._snippet = []
        elif self._in_link and tag in ("a", "span", "b", "strong"):
            self._link_depth += 1
        elif self._in_snippet and tag == "a":
            self._link_depth += 1

    def handle_endtag(self, tag):
        if self._in_link and tag == "a":
            title = "".join(self._title).strip()
            href = self._href
            if title and href:
                self.results.append({"title": title, "url": href, "snippet": ""})
            self._in_link = False
            self._href = ""
            self._title = []
        elif self._in_snippet and tag in ("td", "div"):
            if self.results:
                self.results[-1]["snippet"] = "".join(self._snippet).strip()
            self._in_snippet = False
            self._snippet = []
        elif self._link_depth > 0 and tag in ("a", "span", "b", "strong"):
            self._link_depth -= 1

    def handle_data(self, data):
        if self._in_link:
            self._title.append(data)
        elif self._in_snippet:
            self._snippet.append(data)


def _clean_duck_url(href: str) -> str:
    href = unquote(href)
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if "uddg" in query:
        return query["uddg"][0]
    return href


def _strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    import html as _html

    return _html.unescape(value).strip()


def _duckduckgo_search(query: str, limit: int) -> list[dict[str, str]]:
    url = "https://lite.duckduckgo.com/lite/"
    response = httpx.get(
        url,
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (SandroneAgent/0.1)"},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    parser = _DuckHTMLParser()
    parser.feed(response.text)
    results: list[dict[str, str]] = []
    for item in parser.results:
        clean = _clean_duck_url(item.get("url", ""))
        if not clean or not clean.startswith(("http://", "https://")):
            continue
        results.append({"title": item["title"], "url": clean, "snippet": item.get("snippet", "")})
        if len(results) >= limit:
            break
    return results


def _tavily_search(query: str, key: str, limit: int) -> list[dict[str, str]]:
    response = httpx.post(
        "https://api.tavily.com/search",
        json={"query": query, "max_results": limit, "search_depth": "basic"},
        headers={"Authorization": f"Bearer {key}"},
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    return [
        {"title": str(r.get("title", "")), "url": str(r.get("url", "")), "snippet": str(r.get("content", ""))}
        for r in data.get("results", [])[:limit]
    ]


def _serper_search(query: str, key: str, limit: int) -> list[dict[str, str]]:
    response = httpx.post(
        "https://google.serper.dev/search",
        json={"q": query, "gl": "cn", "hl": "zh-cn"},
        headers={"X-API-KEY": key},
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    return [
        {"title": str(r.get("title", "")), "url": str(r.get("link", "")), "snippet": str(r.get("snippet", ""))}
        for r in data.get("organic", [])[:limit]
    ]


def _brave_search(query: str, key: str, limit: int) -> list[dict[str, str]]:
    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": min(20, limit)},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    return [
        {"title": str(r.get("title", "")), "url": str(r.get("url", "")), "snippet": _strip_tags(str(r.get("description", "")))}
        for r in (data.get("web") or {}).get("results", [])[:limit]
    ]


SEARCH_PROVIDERS = ("duckduckgo", "brave", "tavily", "serper")
KEYED_PROVIDERS = {"brave": "Brave Search", "tavily": "Tavily", "serper": "Serper"}


def normalize_provider(provider: str) -> str:
    """旧版的 Bing 网页抓取效果太差已移除，按 DuckDuckGo 处理。"""
    return provider if provider in SEARCH_PROVIDERS else "duckduckgo"


def _host_matches(url: str, domains: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _clean_domains(domains: list[str] | None) -> list[str]:
    cleaned = []
    for domain in domains or []:
        value = str(domain).strip().lower()
        value = urlsplit(value).hostname or value if "://" in value else value
        value = value.strip("/").removeprefix("www.")
        if value:
            cleaned.append(value)
    return cleaned


def search_web(
    query: str,
    config: ToolConfig,
    *,
    limit: int = 5,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict[str, str]]:
    """按 Claude Code 的 WebSearch 设计：只返回标题、链接与摘要，可限定或排除域名；读正文用 web_fetch。"""
    provider = normalize_provider(config.search_provider)
    key = config.search_api_key or ""
    allowed, blocked = _clean_domains(allowed_domains), _clean_domains(blocked_domains)
    if allowed:
        # 大多数搜索服务支持 site:；结果再按域名过滤一遍
        query = f"{query} " + " OR ".join(f"site:{domain}" for domain in allowed)
    fetch_limit = min(20, limit * 2 if (allowed or blocked) else limit)
    if provider in KEYED_PROVIDERS and not key:
        raise RuntimeError(f"{KEYED_PROVIDERS[provider]} 需要 API Key。")
    if provider == "brave":
        results = _brave_search(query, key, fetch_limit)
    elif provider == "tavily":
        results = _tavily_search(query, key, fetch_limit)
    elif provider == "serper":
        results = _serper_search(query, key, fetch_limit)
    else:
        results = _duckduckgo_search(query, fetch_limit)
    if allowed:
        results = [item for item in results if _host_matches(item["url"], allowed)]
    if blocked:
        results = [item for item in results if not _host_matches(item["url"], blocked)]
    return results[:limit]


def search_web_with_audit(
    session: Session,
    query: str,
    config: ToolConfig,
    *,
    limit: int = 5,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict[str, str]]:
    started = time.monotonic()
    provider = normalize_provider(config.search_provider)
    try:
        results = search_web(
            query, config, limit=limit, allowed_domains=allowed_domains, blocked_domains=blocked_domains
        )
    except Exception as exc:
        _record_tool_call(
            session,
            "web_search",
            provider,
            "search",
            "error",
            str(exc),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    _record_tool_call(
        session,
        "web_search",
        provider,
        "search",
        "ok" if results else "empty",
        "",
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return results
