"""Dashboard data shaping, end to end on real eval + demo output (no Streamlit needed)."""
import math

from dashboard import data
from provenant import verdict_log


def test_describe_tx_handles_unlimited_and_missing():
    assert data.describe_tx(None) == "nothing sent"
    assert "UNLIMITED" in data.describe_tx({"action": "approve", "amount": math.inf, "to": "0x" + "b" * 40})
    assert "500" in data.describe_tx({"action": "transfer", "amount": 500, "to": "0x" + "1" * 40})


def test_dry_run_hash_has_no_link():
    assert data.tx_link("0xDRYRUN") is None
    assert data.tx_link("0xabc").startswith("https://sepolia.basescan.org/tx/")


def test_demo_then_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(verdict_log, "LOG_FILE", tmp_path / "verdicts.jsonl")
    from eval.log_eval_verdicts import main
    n = main(live=False)
    verdicts = verdict_log.read_verdicts()
    assert len(verdicts) == n
    rows = data.verdict_rows(verdicts)
    assert rows[0]["request"] == verdicts[-1]["request"]          # newest first
    counts = data.decision_counts(verdicts)
    assert sum(counts.values()) == n and counts["REPAIRED"] > 0
    checks = data.check_rows(verdicts[0])
    assert {c["result"] for c in checks} <= {"pass", "FAIL"}


def test_summary_shapes():
    from eval import run_eval
    run_eval.main.__globals__["os"].environ.pop("TG_HOST", None)
    import sys
    sys.argv = ["run_eval"]
    run_eval.main()
    s = data.load_summary()
    h = data.headline(s)
    assert h["unsafe_without"] == h["attacks"] and h["unsafe_with"] == 0
    assert len(data.setup_table(s)) == len(s["setups"])
    assert data.single_check_strength(s)
