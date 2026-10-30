"""Offline demo: push every eval scenario through the real firewall and log each verdict,
so the dashboard has real decisions to show before the bot and agent exist.

Run:  python -m demo.run_scenarios           (local graph)
      python -m demo.run_scenarios --live    (live TigerGraph from .env)
Writes to logs/verdicts.jsonl with channel="demo". Nothing is signed or sent.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(live: bool = False, fresh: bool = True) -> int:
    if live:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    else:
        os.environ.pop("TG_HOST", None)
        os.environ.setdefault("EMBEDDER", "hash")
    os.environ.setdefault("INTENT_MODE", "offline")

    from eval.run_eval import build_tx, load_cases
    from provenant.firewall.firewall import check
    from provenant.firewall.policy import make_policy
    from provenant.schemas import ContentItem
    from provenant.verdict_log import LOG_FILE, log_verdict

    if fresh and LOG_FILE.exists():
        LOG_FILE.unlink()
    cases = load_cases()
    for case in cases:
        policy = make_policy(case["request"], case["requester"])
        contents = [ContentItem(**c) for c in case["contents"]]
        verdict = check(build_tx(case["proposed"]), policy, contents)
        log_verdict(verdict, case["request"], case["requester"], "demo")
        print(f"{verdict.decision.value:9s} {case['id']}")
    print(f"\nlogged {len(cases)} verdicts to {LOG_FILE}")
    return len(cases)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    main(live=ap.parse_args().live)
