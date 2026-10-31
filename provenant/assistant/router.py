"""Prompt router: decides WHAT to do with the owner's message.

IMPORTANT: parsing never grants authority. The Intent only picks a code path (pay, read, balance...).
The spending Policy is always built by firewall.policy.make_policy from the raw owner text, so a
wrong or manipulated parse cannot widen what the wallet is allowed to do.

Modes (env ASSISTANT_ROUTER): "auto" (default: LLM if an API key exists, regex otherwise),
"llm" (LLM first, regex on failure), "regex" (never calls an API). Any LLM error -> regex.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass

from .textutil import ADDRESS_RE, expand_encoded, parse_amount

INTENTS = ("pay", "read", "balance", "history", "explain", "confirm", "cancel", "help", "chat")

VERBS = r"(?:pay|send|transfer|give|settle|reimburse)"
_PAY = re.compile(rf"\b{VERBS}\b", re.I)
_CONFIRM = re.compile(
    r"^\s*(?:yes|y|yep|yeah|confirm|confirmed|go ahead|do it|proceed|ok|okay)\s*[.!]*\s*$", re.I
)
_CANCEL = re.compile(r"^\s*(?:no|n|nope|cancel|stop|abort|never ?mind|don'?t)\s*[.!]*\s*$", re.I)
_HELP = re.compile(r"^\s*(?:/?help|\?|what can you do\??|commands)\s*$", re.I)
_EXPLAIN_START = re.compile(r"^\s*(?:why|explain|what happened|how come)\b", re.I)
_EXPLAIN = re.compile(r"\b(?:why|explain|what happened|how come|reason)\b", re.I)
_HISTORY = re.compile(
    r"\b(?:history|recent|last|latest|previous|past|show (?:me )?(?:my )?(?:all )?)\b.*"
    r"\b(?:payments?|transactions?|transfers?|decisions?|verdicts?)\b"
    r"|\b(?:payments?|transactions?)\b.*\b(?:history|so far)\b",
    re.I,
)
_BALANCE = re.compile(r"\b(?:balance|how much (?:usdc|money|do i have)|funds? (?:left|available))\b", re.I)
_READ = re.compile(r"\b(?:summari[sz]e|summary|read|open|look at|fetch|check|what'?s (?:in|on))\b", re.I)
_TARGET = re.compile(r"\b(?:page|url|link|invoice|bill|site|website|article|guide|docs?|document)\b|https?://", re.I)
_URL = re.compile(r"https?://[^\s)>\]\"']+", re.I)
_INVOICE_NAME = re.compile(r"\b([a-z]+_[a-z]+_[a-z]+)\b", re.I)
_REF = re.compile(r"#\s*(\d+)|\b(?:payment|transaction|decision|verdict|number|no\.?)\s*(\d+)\b", re.I)
_STOP = {"to", "the", "my", "a", "an", "me", "him", "her", "them", "usdc", "usd", "of", "for", "from"}


@dataclass
class Intent:
    intent: str = "chat"
    payee: str | None = None
    amount: float | None = None
    invoice: str | None = None
    url: str | None = None
    ref: int | None = None
    source: str = "regex"  # "regex" | "llm"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- helpers

def looks_like_payment(text: str) -> bool:
    """Does this text read like an instruction to move money? (Also looks inside base64.)"""
    expanded = expand_encoded(text or "")
    return bool(_PAY.search(expanded) and (re.search(r"\d", expanded) or ADDRESS_RE.search(expanded)))


def _known_names() -> list[str]:
    try:
        from provenant import records

        return list(records.contacts()) + list(records.vendors())
    except Exception:
        return []


def _find_payee(text: str) -> str | None:
    low = text.lower()
    for name in sorted(_known_names(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(name.lower())}\b", low):
            return name.title()
    m = re.search(
        rf"\b{VERBS}\s+(?:\$?\d[\d,.]*\s*(?:usdc|usd)?\s*(?:to\s+)?)?([A-Za-z][\w'-]*)", text, re.I
    )
    if m and m.group(1).lower() not in _STOP:
        return m.group(1).title()
    return None


def _find_ref(text: str) -> int | None:
    m = _REF.search(text)
    return int(m.group(1) or m.group(2)) if m else None


# ---------------------------------------------------------------- regex parser (always available)

def regex_parse(text: str) -> Intent:
    t = (text or "").strip()
    url = (_URL.search(t) or [None])[0] if _URL.search(t) else None
    if _CONFIRM.match(t):
        return Intent("confirm")
    if _CANCEL.match(t):
        return Intent("cancel")
    if _HELP.match(t):
        return Intent("help")
    if _EXPLAIN_START.match(t):
        return Intent("explain", ref=_find_ref(t))

    has_pay_verb = bool(_PAY.search(t))
    mentions_target = bool(re.search(r"\binvoice|\bbill\b", t, re.I))
    if has_pay_verb and (
        re.search(r"\d", t) or ADDRESS_RE.search(t) or mentions_target or _find_known_payee(t)
    ):
        inv = _INVOICE_NAME.search(t)
        return Intent(
            "pay", payee=_find_payee(t), amount=parse_amount(t), invoice=inv.group(1) if inv else None, url=url
        )
    if _EXPLAIN.search(t) and not _READ.search(t):
        return Intent("explain", ref=_find_ref(t))
    if _HISTORY.search(t):
        return Intent("history")
    if _BALANCE.search(t):
        return Intent("balance")
    if (_READ.search(t) and _TARGET.search(t)) or url:
        inv = _INVOICE_NAME.search(t)
        return Intent("read", invoice=inv.group(1) if inv else None, url=url)
    return Intent("chat")


def _find_known_payee(text: str) -> bool:
    low = text.lower()
    return any(re.search(rf"\b{re.escape(n.lower())}\b", low) for n in _known_names())


# ---------------------------------------------------------------- optional LLM parser

_SYSTEM = (
    "You classify one message sent by a crypto wallet owner to their payment assistant. "
    "Reply with a single JSON object and nothing else: "
    '{"intent": "pay|read|balance|history|explain|help|chat", "payee": string|null, '
    '"amount": number|null, "invoice": string|null, "url": string|null, "ref": integer|null}. '
    "pay = the owner wants money sent. read = the owner wants a page or invoice read or summarised. "
    "balance = wallet balance. history = list recent payments. explain = why a payment was allowed or blocked "
    "(ref = which payment number, if any). help = what can you do. chat = anything else. "
    "You only classify. You never decide whether a payment is allowed."
)


def _llm_json(text: str) -> dict | None:
    model_hint = os.environ.get("ASSISTANT_MODEL")
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        msg = anthropic.Anthropic().messages.create(
            model=model_hint or "claude-haiku-5-5",
            max_tokens=200,
            system=_SYSTEM,
            messages=[{"role": "user", "content": text}],
            timeout=15,
        )
        raw = msg.content[0].text
    elif os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI

        r = OpenAI(timeout=15).chat.completions.create(
            model=model_hint or "gpt-4.1-mini",
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": text}],
            temperature=0,
        )
        raw = r.choices[0].message.content or ""
    else:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else None


def llm_parse(text: str) -> Intent | None:
    """LLM classification, validated hard. Returns None on any problem so the caller falls back."""
    try:
        data = _llm_json(text)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("intent") not in INTENTS:
        return None

    def _opt_str(key: str, must_appear_in_text: bool = False) -> str | None:
        v = data.get(key)
        if not isinstance(v, str) or not v.strip():
            return None
        v = v.strip()
        return v if (not must_appear_in_text or v.lower() in text.lower()) else None  # no hallucinated urls

    amount = data.get("amount")
    ref = data.get("ref")
    return Intent(
        intent=data["intent"],
        payee=_opt_str("payee"),
        amount=float(amount) if isinstance(amount, (int, float)) and not isinstance(amount, bool) else None,
        invoice=_opt_str("invoice", must_appear_in_text=True),
        url=_opt_str("url", must_appear_in_text=True),
        ref=int(ref) if isinstance(ref, int) and not isinstance(ref, bool) else None,
        source="llm",
    )


# ---------------------------------------------------------------- the router

class Router:
    def __init__(self, mode: str | None = None) -> None:
        self.mode = (mode or os.environ.get("ASSISTANT_ROUTER") or "auto").lower()
        if self.mode not in ("auto", "llm", "regex"):
            raise ValueError("ASSISTANT_ROUTER must be auto, llm or regex")

    def parse(self, text: str) -> Intent:
        first = regex_parse(text)
        if first.intent in ("confirm", "cancel", "help"):  # cheap and unambiguous: never spend an API call
            return first
        if self.mode in ("auto", "llm"):
            parsed = llm_parse(text)
            if parsed is not None:
                return parsed
        return first
