"""Small text helpers shared by the router, the inbox and the offline agent.

Nothing in here grants authority. These helpers only look at text.
"""
from __future__ import annotations

import base64
import binascii
import re
from html.parser import HTMLParser

ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_B64_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=])")
_ADDR_PREFIX = re.compile(r"0x[0-9a-fA-F]{6,}")
_MONEY = re.compile(
    r"(?:\$\s*|usdc\s*)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:usdc|usd|dollars|\$)", re.I
)


def find_addresses(text: str) -> list[str]:
    """Every 0x + 40 hex address in `text`, lowercase, in reading order (duplicates kept)."""
    return [m.group(0).lower() for m in ADDRESS_RE.finditer(text or "")]


def decode_base64_segments(text: str) -> list[str]:
    """Decode base64-looking chunks that turn into readable text (the 'encoded command' trick)."""
    out: list[str] = []
    for m in _B64_RE.finditer(text or ""):
        seg = m.group(0)
        try:
            decoded = base64.b64decode(seg + "=" * (-len(seg) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if decoded and decoded.isprintable():
            readable = sum(ch.isalnum() or ch.isspace() for ch in decoded)
            if readable / len(decoded) >= 0.7:
                out.append(decoded)
    return out


def expand_encoded(text: str) -> str:
    """The text plus anything hidden in it as base64, so detection can see both."""
    extra = decode_base64_segments(text)
    return text if not extra else text + "\n" + "\n".join(extra)


def _local_amount(text: str) -> float | None:
    # Mirrors firewall.policy._find_amount so the assistant works even if that private helper moves.
    cleaned = _ADDR_PREFIX.sub(" ", text or "")
    money = _MONEY.search(cleaned)
    if money:
        return float(money.group(1) or money.group(2))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\b", cleaned):
        n = float(m.group(1))
        if 1900 <= n <= 2100 and "." not in m.group(1):
            continue  # looks like a year, not an amount
        return n
    return None


def parse_amount(text: str) -> float | None:
    """Amount mentioned in `text`. Uses the firewall's own parser when available."""
    try:
        from provenant.firewall.policy import _find_amount  # type: ignore

        return _find_amount(text)
    except Exception:
        return _local_amount(text)


# ---------------------------------------------------------------- visible text of a web page

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_ALWAYS_HIDDEN = {"script", "style", "noscript", "template", "head"}


class _Visible(HTMLParser):
    """Collects only the text a human would see (skips display:none, hidden, scripts...)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._stack: list[tuple[str, bool]] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        a = {k: (v or "") for k, v in attrs}
        style = a.get("style", "").replace(" ", "").lower()
        hidden = (
            tag in _ALWAYS_HIDDEN
            or "display:none" in style
            or "visibility:hidden" in style
            or "font-size:0" in style
            or "hidden" in a
            or a.get("aria-hidden", "").lower() == "true"
        )
        self._stack.append((tag, hidden))
        if hidden:
            self._hidden_depth += 1

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                for _, was_hidden in self._stack[i:]:
                    if was_hidden:
                        self._hidden_depth -= 1
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._hidden_depth == 0 and data.strip():
            self.parts.append(data.strip())


def visible_text(html: str) -> str:
    p = _Visible()
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html or "")
    return " ".join(p.parts)
