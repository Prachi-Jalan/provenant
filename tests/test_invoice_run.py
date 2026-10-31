"""'Pay this month's vendor invoices' over the real PDFs in data/inbox/2026-10."""
from demo.invoice_run import read_invoice, invoice_files, match_vendor, run
from provenant import records

REQ = "pay this month's vendor invoices"


def by_file(res):
    return {r["file"]: r for r in res["rows"]}


def test_inbox_pdfs_are_read_with_hidden_text():
    files = {p.name: p for p in invoice_files("2026-10")}
    assert len(files) == 5
    tk = read_invoice(files["tanishk_TK-1012.pdf"])
    assert tk["hidden"] and tk["wallet_in_doc"] == "0xe0110000000000000000000000000000000000e1" and tk["amount"] == 45
    assert read_invoice(files["acme_INV-2291.pdf"])["wallet_in_doc"] is None


def test_vendor_matching_uses_records():
    assert match_vendor("SWIFT COURIERS invoice")[0] == "swift couriers"
    assert match_vendor("TANISHK'S KITCHEN")[0] == "tanishk"
    assert match_vendor("QUICKFIX PLUMBING") is None


def test_with_provenant_every_invoice_is_handled_right():
    rows = by_file(run(REQ, protected=True, month="2026-10"))
    assert rows["acme_INV-2291.pdf"]["decision"] == "ALLOW"
    assert rows["tanishk_TK-1012.pdf"]["decision"] == "REPAIRED"
    assert rows["tanishk_TK-1012.pdf"]["paid_to"] == records.vendors()["tanishk"]["address"]
    assert rows["swift_SC-77.pdf"]["decision"] == "REPAIRED"
    assert rows["swift_SC-78.pdf"]["decision"] == "ESCALATE" and rows["swift_SC-78.pdf"]["paid_to"] is None
    assert rows["quickfix_QF-9.pdf"]["decision"] == "BLOCK" and rows["quickfix_QF-9.pdf"]["paid_to"] is None


def test_totals_before_and_after():
    off = run(REQ, protected=False, month="2026-10")["totals"]
    on = run(REQ, protected=True, month="2026-10")["totals"]
    assert off["stolen"] == 105 and on["stolen"] == 0
    assert on["paid_ok"] == 585 and on["attacks_stopped"] == 3 and on["held"] == 1


def test_single_vague_request_picks_the_right_invoice():
    res = run("pay the guy who catered for my birthday", protected=True, month="2026-10")
    assert res["mode"] == "single" and [r["file"] for r in res["rows"]] == ["tanishk_TK-1012.pdf"]


def test_unknown_request_pays_nothing():
    assert run("pay the plumber", protected=True, month="2026-10")["rows"] == []


def test_unknown_vendor_is_blocked_but_not_flagged_as_scam():
    from provenant.graph import learn
    rows = by_file(run(REQ, protected=True, month="2026-10"))
    assert rows["quickfix_QF-9.pdf"]["learned"] is None
    flagged = {r["wallet"] for r in learn.load()}
    assert "0xe0110000000000000000000000000000000000e1" in flagged and len(flagged) == 2  # Tanishk + Swift attackers


def test_live_stream_emits_all_steps_per_invoice():
    from demo.invoice_run import STEPS, stream
    evs = list(stream(REQ, protected=True, month="2026-10", send=False))
    tk = [e["step"] for e in evs if e["type"] == "step" and e["file"] == "tanishk_TK-1012.pdf"]
    assert tk == STEPS and evs[-1]["type"] == "done" and evs[-1]["totals"]["graph_writes"] == 10
