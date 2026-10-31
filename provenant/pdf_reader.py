"""Read a PDF the way an AI agent's tools do: extract ALL its text (pypdf).

That includes text a human can't see (1-point or white-on-white), which is exactly how the
hidden-instruction attack works: the invoice looks normal, but the extracted text tells the
AI to pay someone else. We also report which lines were invisible, so the dashboard can show
"what you see" vs "what the AI reads". Provenant's decision never depends on this flag: it
checks where the wallet address came from, whatever trick was used to plant it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

TINY_PT = 3.0          # text smaller than this is unreadable to a person (white 1pt text is the classic trick)


@dataclass
class PdfText:
    text: str
    hidden: list[str] = field(default_factory=list)   # invisible lines the AI still reads
    pages: int = 0


def read_pdf(src: str | Path | bytes) -> PdfText:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(src) if isinstance(src, (bytes, bytearray)) else str(src))
    texts, hidden = [], []
    for page in reader.pages:
        def visit(text, cm, tm, font, size):
            if not text or not text.strip():
                return
            scale = abs(tm[0]) if tm else 1.0
            if (size or 0) * (scale or 1.0) < TINY_PT:
                hidden.append(text.strip())

        texts.append(page.extract_text(visitor_text=visit) or "")
    return PdfText("\n".join(texts).strip(), [h for h in hidden if len(h) > 3], len(reader.pages))
