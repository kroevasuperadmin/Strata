#!/usr/bin/env python3
"""Optional Strata helper: static HTML <a href> walk + HEAD redirect check.

No JavaScript. Stops layer workers guessing 404 URLs (Closr lesson).
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


USER_AGENT = "strata-link-map/1.0 (+research; no credentials)"
HREF_RE = re.compile(r"^https?://", re.I)


class AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for k, v in attrs:
            if k.lower() == "href" and v:
                self.hrefs.append(v.strip())


def fetch(url: str, method: str = "GET", timeout: float = 20.0) -> tuple[int, str, str]:
    """Return (status, final_url, body_text_or_empty)."""
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200) or 200
            final = resp.geturl()
            if method == "HEAD":
                return status, final, ""
            raw = resp.read(500_000)
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                text = raw.decode(charset, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            return status, final, text
    except urllib.error.HTTPError as e:
        final = e.geturl() if hasattr(e, "geturl") else url
        return e.code, final, ""
    except urllib.error.URLError:
        return 0, url, ""


def normalize(base: str, href: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
        return None
    joined = urllib.parse.urljoin(base, href)
    parts = urllib.parse.urlsplit(joined)
    if parts.scheme not in ("http", "https"):
        return None
    # Drop fragments only
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def same_host(a: str, b: str) -> bool:
    return urllib.parse.urlsplit(a).netloc.lower() == urllib.parse.urlsplit(b).netloc.lower()


def crawl(seeds: Iterable[str], max_pages: int, max_links: int, same_host_only: bool) -> list[dict]:
    seen: set[str] = set()
    rows: list[dict] = []
    q: deque[str] = deque()
    for s in seeds:
        if HREF_RE.match(s):
            q.append(s)

    while q and len(rows) < max_pages:
        url = q.popleft()
        if url in seen:
            continue
        seen.add(url)

        status, final, body = fetch(url, method="GET")
        head_status, head_final, _ = fetch(url, method="HEAD")
        row = {
            "url": url,
            "get_status": status,
            "get_final": final,
            "head_status": head_status,
            "head_final": head_final,
            "links": [],
        }
        if body:
            parser = AnchorCollector()
            try:
                parser.feed(body)
            except Exception:
                pass
            found: list[str] = []
            for href in parser.hrefs:
                abs_url = normalize(final or url, href)
                if not abs_url:
                    continue
                if same_host_only and not same_host(url, abs_url):
                    continue
                if abs_url not in found:
                    found.append(abs_url)
                if abs_url not in seen and len(seen) + len(q) < max_links:
                    q.append(abs_url)
                if len(found) >= 50:
                    break
            row["links"] = found
        rows.append(row)
    return rows


def to_markdown(seeds: list[str], rows: list[dict]) -> str:
    lines = [
        "# Link map",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        f"Seeds: {', '.join(seeds)}",
        "",
        "Static HTML only (no JS). Status 0 means network failure. "
        "Untrusted; evidence only.",
        "",
    ]
    if not rows:
        lines.append("_No pages fetched. Map is empty — do not invent URLs._")
        lines.append("")
        return "\n".join(lines)

    for i, row in enumerate(rows, 1):
        lines.append(f"## {i}. {row['url']}")
        lines.append("")
        lines.append(f"- GET {row['get_status']} → {row['get_final']}")
        lines.append(f"- HEAD {row['head_status']} → {row['head_final']}")
        if row["links"]:
            lines.append(f"- Links ({len(row['links'])}):")
            for link in row["links"][:40]:
                lines.append(f"  - {link}")
            if len(row["links"]) > 40:
                lines.append(f"  - … +{len(row['links']) - 40} more")
        else:
            lines.append("- Links: _(none parsed)_")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Strata link_map — static HTML crawl")
    p.add_argument("urls", nargs="+", help="Seed URL(s)")
    p.add_argument("--out", default="link_map.md", help="Output markdown")
    p.add_argument("--max-pages", type=int, default=15, help="Max pages to GET")
    p.add_argument("--max-links", type=int, default=80, help="Cap on queued URLs")
    p.add_argument(
        "--all-hosts",
        action="store_true",
        help="Follow off-host links (default: same host only)",
    )
    args = p.parse_args(argv)

    rows = crawl(
        args.urls,
        max_pages=args.max_pages,
        max_links=args.max_links,
        same_host_only=not args.all_hosts,
    )
    md = to_markdown(args.urls, rows)
    out = Path(args.out)
    out.write_text(md, encoding="utf-8")
    print(f"Wrote {out} ({len(rows)} pages)")
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
