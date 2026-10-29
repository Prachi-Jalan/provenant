"""Agent tools. Owner: Person 1.
Every tool returns ContentItems tagged with source + trust AND appends them to
Session.log. The firewall receives Session.log for provenance.
Source naming: see CONTRACTS.md.
"""
from ..schemas import ContentItem


class Session:
    def __init__(self, owner_id: str):
        self.owner_id = owner_id
        self.log: list[ContentItem] = []

    def chat_message(self, msg_id: str, sender: str, text: str) -> list[ContentItem]:
        """trusted only if sender == owner_id."""
        raise NotImplementedError("Person 1")

    def lookup_contact(self, name: str) -> list[ContentItem]:
        """trusted, from data/contacts.json + data/vendors.json."""
        raise NotImplementedError("Person 1")

    def read_invoice(self, name: str) -> list[ContentItem]:
        """untrusted, from data/invoices/<name>.json."""
        raise NotImplementedError("Person 1")

    def fetch_webpage(self, url: str) -> list[ContentItem]:
        """untrusted; keep hidden text."""
        raise NotImplementedError("Person 1")
