"""Assistant: the prompt-driven front door that replaces the Telegram bot.

    Assistant.chat(text, role="owner" | "stranger", sender=None) -> Reply

Trust comes from the CHANNEL, not the text:
  role="owner"    -> the owner's chat box: requester="owner", trusted ContentItem
  role="stranger" -> the simulated inbox: untrusted ContentItem; can never authorise a payment
Everything that can move money goes through PaymentEngine -> firewall.check. Read-only intents
(balance, history, explain, help) never sign anything, so they skip the firewall.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from provenant.schemas import ContentItem, Decision

from .engine import PaymentEngine, RunResult
from .inbox import Inbox, InboxError, safe_sender
from .replies import format_logged_row, format_result
from .router import Router, looks_like_payment
from .textutil import visible_text

HELP = """I can help you with:
  pay Rahul 500            send a payment (checked by the firewall first)
  pay Acme's October invoice   pay a vendor (load the invoice into the inbox first)
  summarise this page      read a page or invoice from the inbox
  show my last payments    recent decisions
  why was that blocked     explain the latest decision (or: explain #2)
  what's my balance        wallet balance
Messages sent as a stranger are untrusted: the agent can read them, they can never authorise a payment."""


@dataclass
class Pending:
    request: str
    amount: float | None
    payee_label: str
    expires_at: float


@dataclass
class Reply:
    text: str
    intent: str = "chat"
    result: RunResult | None = None
    extra: str = ""  # UI-only preface, e.g. a page summary
    needs_confirmation: bool = False

    @property
    def verdicts(self) -> list:
        return self.result.verdicts if self.result else []

    @property
    def decision(self) -> str | None:
        return self.result.decision if self.result else None

    def __str__(self) -> str:
        return self.text


class Assistant:
    def __init__(
        self,
        inbox: Inbox | None = None,
        engine: PaymentEngine | None = None,
        owner_id: str = "owner",
        router: Router | None = None,
    ) -> None:
        self.owner_id = owner_id
        self.inbox = inbox or Inbox()
        self.engine = engine or PaymentEngine(owner_id=owner_id)
        self.router = router or Router()
        self.pending: Pending | None = None

    # ------------------------------------------------------------------ entry point

    def chat(self, text: str, role: str = "owner", sender: str | None = None) -> Reply:
        if role not in ("owner", "stranger"):
            raise ValueError("role must be 'owner' or 'stranger'")
        text = (text or "").strip()
        if not text:
            return Reply("Type a request, for example: pay Rahul 500   (or 'help').", intent="help")
        return self._stranger(text, sender) if role == "stranger" else self._owner(text)

    def run_pending_expired(self) -> bool:
        return bool(self.pending and time.time() > self.pending.expires_at)

    # ------------------------------------------------------------------ stranger channel

    def _stranger(self, text: str, sender: str | None) -> Reply:
        sender = safe_sender(sender, self.owner_id)  # a stranger can never be called "owner"
        item = self.inbox.add_message(sender, text, self.owner_id)
        if not looks_like_payment(text):
            return Reply(
                f"Added to the inbox as an untrusted message from {sender} ({item.id}). "
                "The agent will see it, but it cannot authorise a payment.",
                intent="inbox",
            )
        # The message reads like a payment command (maybe hidden in base64): let the agent process it,
        # exactly like the Grok-style attack, and let the firewall decide.
        result = self.engine.run(text, sender, self.inbox.contents())
        body = format_result(result)
        return Reply(
            f"{sender} is not the owner, so this request is untrusted ({item.id}).\n{body}",
            intent="pay", result=result,
        )

    # ------------------------------------------------------------------ owner channel

    def _owner(self, text: str) -> Reply:
        intent = self.router.parse(text)
        kind = intent.intent
        if kind in ("confirm", "cancel"):
            if self.pending:
                return self._resolve_pending(kind)
            return Reply("Nothing is waiting for confirmation.\n" + HELP, intent="help")
        if kind == "pay":
            return self._pay(text, intent)
        if kind == "read":
            return self._read(text, intent)
        if kind == "history":
            return self._history()
        if kind == "explain":
            return self._explain(intent.ref)
        if kind == "balance":
            return self._balance()
        if kind == "help":
            return Reply(HELP, intent="help")
        return Reply("I didn't understand that.\n" + HELP, intent="chat")

    def _owner_item(self, text: str) -> ContentItem:
        return ContentItem(id=self.inbox.new_id(), source="telegram:owner", trust="trusted", text=text)

    def _load_referenced(self, intent) -> str:
        """Load an invoice / page the owner referred to, if it exists. Returns a note (maybe empty)."""
        notes = []
        if intent.url and not self.inbox.has_source(f"web:{intent.url}"):
            try:
                self.inbox.add_page(intent.url)
                notes.append(f"Loaded {intent.url} into the inbox (untrusted).")
            except InboxError as e:
                notes.append(str(e))
        if intent.invoice and not self.inbox.has_source(f"invoice:{intent.invoice}"):
            try:
                self.inbox.add_invoice(intent.invoice)
                notes.append(f"Loaded invoice {intent.invoice} into the inbox (untrusted).")
            except InboxError:
                pass  # the token may not have been an invoice name at all
        return " ".join(notes)

    # ---- pay -----------------------------------------------------------

    def _pay(self, text: str, intent) -> Reply:
        note = self._load_referenced(intent)
        contents = [self._owner_item(text)] + self.inbox.contents()
        result = self.engine.run(text, self.owner_id, contents)  # policy comes from the RAW text
        body = format_result(result)
        reply = Reply((note + "\n" if note else "") + body, intent="pay", result=result, extra=note)

        last = result.verdicts[-1] if result.verdicts else None
        if last is not None and last.decision == Decision.ESCALATE and result.policy.allowed_recipients:
            from .engine import SendOutcome  # noqa: F401  (keeps type checkers quiet)

            ttl = _policy_ttl()
            label = result.policy.payee_label or "the payee"
            self.pending = Pending(text, intent.amount, label, time.time() + ttl)
            reply.needs_confirmation = True
            reply.text += (
                f"\nReply 'yes' to pay exactly what you asked, to {label}'s verified address only, "
                f"or 'no' to cancel. (Expires in {ttl // 60} minutes.)"
            )
        else:
            self.pending = None
        return reply

    def _resolve_pending(self, kind: str) -> Reply:
        p = self.pending
        assert p is not None
        self.pending = None
        if time.time() > p.expires_at:
            return Reply("That confirmation expired. Please send the request again.", intent="cancel")
        if kind == "cancel":
            return Reply("Cancelled. Nothing was sent.", intent="cancel")
        if p.amount is None:
            return Reply(
                f"I can't pay {p.payee_label} without an explicit amount in your request. "
                "Please say it again with an amount, e.g. 'pay Acme 500'.",
                intent="cancel",
            )
        contents = [self._owner_item(p.request)] + self.inbox.contents()
        result = self.engine.run_confirmed(p.request, self.owner_id, contents, p.amount)
        return Reply(
            "Confirmed. Re-checking the payment you originally asked for (verified address only):\n"
            + format_result(result),
            intent="pay", result=result,
        )

    # ---- read ----------------------------------------------------------

    def _read(self, text: str, intent) -> Reply:
        note = self._load_referenced(intent)
        if not self.inbox.items:
            hint = "There is nothing in the inbox to read yet. Add a page or invoice (CLI: /inbox add page defi_guide)."
            return Reply((note + "\n" if note else "") + hint, intent="read")
        summary = self._summarise_latest()
        contents = [self._owner_item(text)] + self.inbox.contents()
        # The agent reads the content too. If it gets hijacked into proposing a payment, the firewall decides.
        result = self.engine.run(text, self.owner_id, contents)
        parts = [p for p in (note, summary) if p]
        if result.verdicts:
            parts.append("While reading, the agent tried to move money on its own. The firewall checked it:")
            parts.append(format_result(result))
        else:
            parts.append("The agent made no transaction proposals while reading, so nothing needed checking.")
        return Reply("\n".join(parts), intent="read", result=result if result.verdicts else None, extra=summary)

    def _summarise_latest(self) -> str:
        item = next((i for i in reversed(self.inbox.items) if i.source.startswith(("web:", "invoice:"))), None)
        if item is None:
            item = self.inbox.items[-1]
        if item.source.startswith("web:"):
            shown = visible_text(item.text)
            label = "page text a human would see"
        else:
            shown = item.text
            label = "content"
        shown = re.sub(r"\s+", " ", shown).strip()
        if len(shown) > 400:
            shown = shown[:400].rstrip() + "..."
        return f"Summary of {item.source} ({label}): {shown}"

    # ---- read-only intents --------------------------------------------

    def _rows(self) -> list[dict]:
        from provenant.verdict_log import read_verdicts

        return read_verdicts()

    def _history(self, n: int = 5) -> Reply:
        rows = self._rows()
        if not rows:
            return Reply("No decisions have been logged yet.", intent="history")
        start = max(0, len(rows) - n)
        lines = [f"Last {len(rows) - start} of {len(rows)} decisions (use 'explain #N' for details):"]
        for i in range(start, len(rows)):
            r = rows[i]
            lines.append(
                f"  #{i + 1} {str(r.get('ts', ''))[11:19]} {r.get('decision', '?'):<9} "
                f"risk {float(r.get('risk', 0)):.2f}  {r.get('request', '')}"
            )
        return Reply("\n".join(lines), intent="history")

    def _explain(self, ref: int | None) -> Reply:
        rows = self._rows()
        if not rows:
            return Reply("There is nothing to explain yet: no decisions have been logged.", intent="explain")
        n = ref if ref is not None else len(rows)
        if not 1 <= n <= len(rows):
            return Reply(f"I only have decisions #1 to #{len(rows)}.", intent="explain")
        return Reply(format_logged_row(n, rows[n - 1]), intent="explain")

    def _balance(self) -> Reply:
        value, note = _lookup_balance()
        if value is None:
            return Reply(note, intent="balance")
        return Reply(f"Wallet balance: {value:g} USDC. {note}".strip(), intent="balance")


def _policy_ttl() -> int:
    try:
        from provenant.firewall import policy as policy_mod

        return int(getattr(policy_mod, "POLICY_TTL_SECONDS", 600))
    except Exception:
        return 600


def _lookup_balance() -> tuple[float | None, str]:
    """Read the owner's USDC balance through chain.wallet (read-only; no key is used to sign)."""
    import inspect
    import os

    dry = os.environ.get("DRY_RUN", "1") != "0"
    dry_note = "(dry-run mode: payments are simulated.)" if dry else ""
    try:
        from provenant.chain import wallet
    except Exception:
        wallet = None
    for name in ("get_usdc_balance", "usdc_balance", "get_balance", "balance_of"):
        fn = getattr(wallet, name, None) if wallet else None
        if not callable(fn):
            continue
        try:
            needs_address = any(
                p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                for p in inspect.signature(fn).parameters.values()
            )
        except (TypeError, ValueError):
            needs_address = False
        try:
            if needs_address:
                lister = getattr(wallet, "_wallet_addresses", None)
                owner = (lister() if callable(lister) else {}).get("owner")
                if not owner:
                    return None, "I need the owner wallet to check its balance: set OWNER_PRIVATE_KEY in .env."
                return float(fn(owner)), dry_note
            return float(fn()), dry_note
        except NotImplementedError:
            break
        except Exception as e:
            return None, f"Could not read the balance: {type(e).__name__}: {e}"
    return None, (
        "Balance lookup isn't wired yet (Person 1: add chain.wallet.get_usdc_balance(address)). "
        + ("Dry-run mode is on, so no real funds move." if dry else "")
    ).strip()