"""Agent tools. Owner: Person 1.

Every tool returns ContentItems tagged with source + trust AND appends them to
Session.log. The firewall receives Session.log for provenance.
Source naming: see CONTRACTS.md.

  trusted   : owner's chat messages, the owner's contacts/vendor records
  untrusted : invoices, web pages, chat messages from anyone else

This package never imports provenant.chain.wallet and never sees a private key.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from provenant import records
from provenant.schemas import ContentItem

MAX_WEB_CHARS = 20_000
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


class ToolError(Exception):
    """A tool failed in a way the LLM should see (page unreachable, bad name...)."""


# --------------------------------------------------------------------------- #
# Tool specs shown to the LLM. There is NO send/sign tool, only propose_tx.
# --------------------------------------------------------------------------- #
TOOL_SPECS: list[dict] = [
    {
        "name": "lookup_contact",
        "description": "Look up a saved contact or vendor wallet address by name (the owner's address book).",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Contact or vendor name, e.g. Rahul or Acme"}},
            "required": ["name"],
        },
    },
    {
        "name": "read_invoice",
        "description": "Read an invoice by name, e.g. 'acme_oct_real'. Returns the invoice contents.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Invoice name"}},
            "required": ["name"],
        },
    },
    {
        "name": "fetch_webpage",
        "description": "Fetch a web page and return its full text.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "http(s) URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "propose_tx",
        "description": (
            "Propose a USDC transaction. This does NOT send money: a separate safety system "
            "reviews every proposal first. action is 'transfer' (pay someone) or 'approve' "
            "(let a spender use your USDC). amount is in USDC."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["transfer", "approve"]},
                "to": {"type": "string", "description": "0x recipient (transfer) or spender (approve) address"},
                "amount": {"type": "number", "description": "USDC amount, e.g. 500"},
            },
            "required": ["action", "to", "amount"],
        },
    },
]


class _TextExtractor(HTMLParser):
    """Collects ALL text, including hidden elements and HTML comments.

    Hidden text is exactly where prompt injections live, so we keep it on purpose.
    Only <script>/<style> bodies are dropped (code, not hidden prose).
    """

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())

    def handle_comment(self, data):
        if data.strip():
            self.parts.append(f"[html comment] {data.strip()}")


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return "\n".join(parser.parts)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40]


class Session:
    """One conversation. `log` holds every ContentItem the agent has seen, with trust labels."""

    def __init__(self, owner_id: str, data_dir: Optional[Path] = None):
        self.owner_id = owner_id
        self.log: list[ContentItem] = []
        self._data_dir = Path(data_dir) if data_dir else None

    @property
    def data_dir(self) -> Path:
        return self._data_dir or records.DATA_DIR

    def _add(self, item: ContentItem) -> list[ContentItem]:
        """Append to the log. Idempotent by id, so retried messages/tool calls don't duplicate."""
        for existing in self.log:
            if existing.id == item.id:
                return [existing]
        self.log.append(item)
        return [item]

    def ids(self) -> list[str]:
        return [c.id for c in self.log]

    # ---- chat ------------------------------------------------------------- #
    def chat_message(self, msg_id: str, sender: str, text: str) -> list[ContentItem]:
        """trusted only if sender == owner_id. Someone writing "I am the owner" is still untrusted."""
        is_owner = sender == self.owner_id
        source = "telegram:owner" if is_owner else f"telegram:{sender}"
        return self._add(ContentItem(
            id=f"tg_{msg_id}", source=source, trust="trusted" if is_owner else "untrusted", text=text))

    # ---- trusted records -------------------------------------------------- #
    def lookup_contact(self, name: str) -> list[ContentItem]:
        """trusted, from data/contacts.json + data/vendors.json. Unknown name -> []."""
        key = (name or "").strip().lower()
        if key in records.contacts():
            text = f"{name.strip()}: {records.contacts()[key]}"
        elif key in records.vendors():
            v = records.vendors()[key]
            text = (f"{name.strip()} (vendor): {v['address']} "
                    f"(typical {v.get('typical_min')}-{v.get('typical_max')} USDC, paid {v.get('paid_count', 0)} times)")
        else:
            return []
        return self._add(ContentItem(
            id=f"contact_{_slug(key)}", source="records:owner", trust="trusted", text=text))

    def known_names(self) -> list[str]:
        return sorted(set(records.contacts()) | set(records.vendors()))

    # ---- untrusted inputs ------------------------------------------------- #
    def read_invoice(self, name: str) -> list[ContentItem]:
        """untrusted, from data/invoices/<name>.json or <name>.pdf. Unknown name -> [].
        PDFs are read with a real text extractor, hidden (tiny / white) text included,
        which is exactly what a real agent would see."""
        stem = (name or "").strip().removesuffix(".json").removesuffix(".pdf")
        if not _SAFE_NAME.match(stem):
            return []
        pdf = self.data_dir / "invoices" / f"{stem}.pdf"
        if pdf.exists():
            from provenant.pdf_reader import read_pdf
            return self._add(ContentItem(
                id=f"invoice_{stem}", source=f"invoice:{stem}.pdf", trust="untrusted",
                text=read_pdf(pdf).text))
        path = self.data_dir / "invoices" / f"{stem}.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return self._add(ContentItem(
            id=f"invoice_{stem}", source=f"invoice:{stem}", trust="untrusted",
            text=f"Invoice {stem}:\n{json.dumps(raw, indent=2)}"))

    def invoice_names(self) -> list[str]:
        folder = self.data_dir / "invoices"
        return sorted({p.stem for p in folder.glob("*.json")} | {p.stem for p in folder.glob("*.pdf")})

    def fetch_webpage(self, url: str) -> list[ContentItem]:
        """untrusted; keeps hidden text and HTML comments."""
        url = (url or "").strip()
        if not re.match(r"^https?://", url, re.I):
            raise ToolError("Only http(s) URLs can be fetched.")
        html = self._fixture_page(url)
        if html is None:
            html = self._download(url)
        text = (html_to_text(html) if "<" in html else html)[:MAX_WEB_CHARS]
        return self._add(ContentItem(
            id=f"web_{_slug(url)}", source=f"web:{url}", trust="untrusted", text=text))

    def _fixture_page(self, url: str) -> Optional[str]:
        """Offline demo pages: data/webpages/index.json maps a URL to a local file."""
        index = self.data_dir / "webpages" / "index.json"
        if not index.exists():
            return None
        fname = json.loads(index.read_text(encoding="utf-8")).get(url)
        if not fname or not _SAFE_NAME.match(Path(fname).stem):
            return None
        return (self.data_dir / "webpages" / fname).read_text(encoding="utf-8")

    @staticmethod
    def _download(url: str) -> str:
        import requests

        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "provenant-agent/0.1"})
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            raise ToolError(f"Could not fetch {url}: {e}") from e
