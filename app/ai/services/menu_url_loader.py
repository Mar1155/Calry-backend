import asyncio
import base64
import html
import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

logger = logging.getLogger(__name__)

MAX_MENU_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_MENU_TEXT_CHARS = 24_000
MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 12.0

_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_IGNORED_TAGS = frozenset({"style", "noscript", "svg"})
_SUPPORTED_IMAGE_TYPES = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
)


class MenuUrlError(Exception):
    """Raised when a digital menu URL cannot be safely fetched or parsed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class LoadedMenuContent:
    source_url: str
    text_content: str | None = None
    data_uris: list[str] = field(default_factory=list)


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_disallowed_ip(ip.ipv4_mapped)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_http_url(url: str) -> str:
    """Validates that a URL is a public http/https URL and does not resolve to private/local IPs."""
    candidate = (url or "").strip()
    if not candidate:
        raise MenuUrlError("MENU_URL_UNREACHABLE", "Please provide a valid menu URL.")

    parsed = urlsplit(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise MenuUrlError("MENU_URL_UNREACHABLE", "Menu URL must use http or https.")

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise MenuUrlError("MENU_URL_UNREACHABLE", "Menu URL is missing a valid host.")

    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
    ):
        raise MenuUrlError("MENU_URL_UNREACHABLE", "Local or internal menu URLs are not allowed.")

    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        ip_literal = None

    if ip_literal is not None:
        if _is_disallowed_ip(ip_literal):
            raise MenuUrlError("MENU_URL_UNREACHABLE", "Private or loopback menu URLs are not allowed.")
        return candidate

    try:
        addr_info = socket.getaddrinfo(hostname, parsed.port or (443 if scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        raise MenuUrlError("MENU_URL_UNREACHABLE", "Could not reach the digital menu host.")

    if not addr_info:
        raise MenuUrlError("MENU_URL_UNREACHABLE", "Could not resolve the digital menu host.")

    for entry in addr_info:
        sockaddr = entry[4]
        if not sockaddr:
            continue
        raw_ip = str(sockaddr[0]).split("%", 1)[0]
        try:
            resolved_ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if _is_disallowed_ip(resolved_ip):
            raise MenuUrlError("MENU_URL_UNREACHABLE", "Private or internal network addresses are not allowed.")

    return candidate


def _collect_json_strings(obj: Any, out: list[str], *, depth: int = 0) -> None:
    if depth > 14 or len(out) > 600:
        return
    if isinstance(obj, str):
        cleaned = re.sub(r"\s+", " ", obj).strip()
        if len(cleaned) >= 2 and not cleaned.startswith(("http://", "https://", "/")):
            out.append(cleaned)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(str(obj))
    elif isinstance(obj, list):
        for item in obj[:150]:
            _collect_json_strings(item, out, depth=depth + 1)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"@context", "props", "page", "buildId", "assetPrefix"}:
                continue
            _collect_json_strings(value, out, depth=depth + 1)


class _MenuHTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._title_parts: list[str] = []
        self._ignored_depth = 0
        self._in_head = False
        self._in_title = False
        self._capture_script_mode: str | None = None
        self._script_buf: list[str] = []
        self._structured_snippets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}

        if tag_l == "head":
            self._in_head = True
            return
        if tag_l == "title":
            self._in_title = True
            return
        if tag_l == "script":
            script_type = attr_map.get("type", "").lower()
            script_id = attr_map.get("id", "")
            if "ld+json" in script_type:
                self._capture_script_mode = "ld+json"
                self._script_buf = []
            elif script_id == "__NEXT_DATA__":
                self._capture_script_mode = "next_data"
                self._script_buf = []
            else:
                self._ignored_depth += 1
            return
        if tag_l in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth == 0 and not self._in_head and tag_l in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l == "head":
            self._in_head = False
            return
        if tag_l == "title":
            self._in_title = False
            return
        if tag_l == "script":
            if self._capture_script_mode is not None:
                raw_script = "".join(self._script_buf).strip()
                mode = self._capture_script_mode
                self._capture_script_mode = None
                self._script_buf = []
                self._extract_structured_script(raw_script, mode)
            elif self._ignored_depth > 0:
                self._ignored_depth -= 1
            return
        if tag_l in _IGNORED_TAGS:
            if self._ignored_depth > 0:
                self._ignored_depth -= 1
            return
        if self._ignored_depth == 0 and not self._in_head and tag_l in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._capture_script_mode is not None:
            self._script_buf.append(data)
            return
        if self._in_title:
            cleaned = re.sub(r"\s+", " ", data).strip()
            if cleaned:
                self._title_parts.append(cleaned)
            return
        if self._ignored_depth > 0 or self._in_head:
            return
        self._parts.append(data)

    def _extract_structured_script(self, raw_script: str, mode: str) -> None:
        if not raw_script or len(raw_script) > 300_000:
            return
        try:
            payload = json.loads(raw_script)
        except json.JSONDecodeError:
            return
        strings: list[str] = []
        if mode == "next_data" and isinstance(payload, dict):
            payload = payload.get("props", payload)
        _collect_json_strings(payload, strings)
        if strings:
            self._structured_snippets.append(" | ".join(strings))

    def get_text(self) -> str:
        body_raw = "".join(self._parts)
        lines: list[str] = []
        title = " ".join(self._title_parts).strip()
        if title:
            lines.append(html.unescape(title))
        for raw_line in body_raw.splitlines():
            collapsed = re.sub(r"[ \t\r\f\v]+", " ", raw_line).strip()
            if collapsed:
                lines.append(collapsed)
        body_text = "\n".join(lines)
        if self._structured_snippets and len(body_text) < 400:
            structured = "\n".join(self._structured_snippets)
            body_text = f"{body_text}\n{structured}".strip() if body_text else structured
        return body_text[:MAX_MENU_TEXT_CHARS].strip()


def extract_text_from_html(raw_html: str) -> str:
    parser = _MenuHTMLTextExtractor()
    parser.feed(raw_html)
    parser.close()
    return parser.get_text()


def _format_json_menu(raw_json: str) -> str:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return raw_json[:MAX_MENU_TEXT_CHARS].strip()
    strings: list[str] = []
    _collect_json_strings(parsed, strings)
    if strings:
        return "\n".join(strings)[:MAX_MENU_TEXT_CHARS].strip()
    return json.dumps(parsed, ensure_ascii=False)[:MAX_MENU_TEXT_CHARS].strip()


async def fetch_digital_menu(url: str) -> LoadedMenuContent:
    """Safely fetches a restaurant digital menu URL (HTML, text, JSON, image, or PDF)."""
    current_url = await asyncio.to_thread(validate_public_http_url, url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/pdf,image/*,application/json,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response: httpx.Response | None = None
            for _ in range(MAX_REDIRECTS + 1):
                response = await client.get(current_url, headers=headers)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        break
                    next_url = urljoin(current_url, location)
                    current_url = await asyncio.to_thread(validate_public_http_url, next_url)
                    continue
                break

            if response is None or response.is_redirect:
                raise MenuUrlError("MENU_URL_UNREACHABLE", "Too many redirects while loading the menu link.")

            if response.status_code >= 400:
                raise MenuUrlError(
                    "MENU_URL_UNREACHABLE",
                    f"The digital menu link returned HTTP {response.status_code}.",
                )

            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > MAX_MENU_BYTES:
                raise MenuUrlError("MENU_UNREADABLE", "The digital menu file is too large to analyze.")

            raw_bytes = response.content
            if len(raw_bytes) > MAX_MENU_BYTES:
                raise MenuUrlError("MENU_UNREADABLE", "The digital menu file is too large to analyze.")

    except MenuUrlError:
        raise
    except httpx.HTTPError as exc:
        logger.warning("Digital menu fetch failed for %s: %s", current_url, exc)
        raise MenuUrlError("MENU_URL_UNREACHABLE", "Could not load the digital menu from this URL.")

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    url_path = urlsplit(current_url).path.lower()

    if content_type in _SUPPORTED_IMAGE_TYPES or (
        content_type.startswith("image/") and "svg" not in content_type
    ):
        mime = "image/jpeg" if content_type == "image/jpg" else content_type
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        return LoadedMenuContent(source_url=current_url, data_uris=[f"data:{mime};base64,{b64}"])

    if content_type == "application/pdf" or (not content_type and url_path.endswith(".pdf")) or raw_bytes.startswith(b"%PDF"):
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        return LoadedMenuContent(
            source_url=current_url,
            data_uris=[f"data:application/pdf;base64,{b64}"],
        )

    if content_type in {"text/html", "application/xhtml+xml", ""} or "<html" in raw_bytes[:512].decode(
        "utf-8", errors="ignore"
    ).lower():
        decoded = raw_bytes.decode(response.encoding or "utf-8", errors="replace")
        extracted = extract_text_from_html(decoded)
        if len(extracted) < 20:
            raise MenuUrlError(
                "MENU_UNREADABLE",
                "This digital menu page did not expose readable text. Try taking a screenshot or photo of the menu.",
            )
        return LoadedMenuContent(source_url=current_url, text_content=extracted)

    if content_type == "application/json":
        decoded = raw_bytes.decode(response.encoding or "utf-8", errors="replace")
        extracted = _format_json_menu(decoded)
        if len(extracted) < 20:
            raise MenuUrlError("MENU_UNREADABLE", "The digital menu link did not contain readable dishes.")
        return LoadedMenuContent(source_url=current_url, text_content=extracted)

    if content_type.startswith("text/"):
        decoded = raw_bytes.decode(response.encoding or "utf-8", errors="replace")
        cleaned = re.sub(r"\n{3,}", "\n\n", decoded).strip()[:MAX_MENU_TEXT_CHARS]
        if len(cleaned) < 20:
            raise MenuUrlError("MENU_UNREADABLE", "The digital menu link did not contain readable text.")
        return LoadedMenuContent(source_url=current_url, text_content=cleaned)

    raise MenuUrlError(
        "MENU_UNREADABLE",
        "This link format is not supported as a menu. Try a webpage, PDF, or photo of the menu.",
    )
