"""Turns verdicts into plain-language text. ASCII tags only, so any VS Code terminal can print it."""
from __future__ import annotations

TAGS = {"ALLOW": "[ALLOW]", "REPAIRED": "[REPAIRED]", "ESCALATE": "[ESCALATE]", "BLOCK": "[BLOCK]"}


def decision_value(v) -> str:
    d = v.decision if hasattr(v, "decision") else v.get("decision")
    return getattr(d, "value", d)


def format_check(c) -> str:
    """Works for CheckResult objects and for rows read back from logs/verdicts.jsonl."""
    get = (lambda k: getattr(c, k)) if hasattr(c, "name") else (lambda k: c.get(k))
    hard, passed, score = get("hard"), get("passed"), get("score")
    mark = "[ok]  " if passed else ("[FAIL]" if hard else "[warn]")
    kind = "hard" if hard else "soft"
    score_txt = f" {score:.2f}" if isinstance(score, (int, float)) else ""
    return f"{mark} {get('name')} ({kind}{score_txt}): {get('reason')}"


def payment_line(verdict, outcome) -> str:
    if decision_value(verdict) in ("ALLOW", "REPAIRED"):
        if outcome is None:
            return "Not sent."
        if outcome.error:
            return f"Payment NOT sent: {outcome.error}"
        if outcome.dry_run:
            return f"Dry run: nothing was sent to the chain (ref {outcome.tx_hash})."
        link = f"  BaseScan: {outcome.link}" if outcome.link else ""
        return f"Paid. Tx {outcome.tx_hash}{link}"
    return "Nothing was signed."


def format_result(result, show_checks: bool = True) -> str:
    if not result.verdicts:
        return f"No payment made. {result.note}".strip()
    lines: list[str] = []
    for i, v in enumerate(result.verdicts):
        if result.replan_from is not None and i == result.replan_from:
            lines += ["", "The agent re-planned once after the block. Checking its new proposal:"]
        lines.append(f"{TAGS.get(decision_value(v), '[?]')} risk {v.risk:.2f}")
        proposed = getattr(v.original, "decoded", "") or ""
        if proposed:
            lines.append(f"Agent proposed: {proposed}")
        lines.append(v.message)
        if show_checks:
            lines += ["  " + format_check(c) for c in v.checks]
        lines.append(payment_line(v, result.outcomes.get(i)))
    return "\n".join(lines)


def format_logged_row(index: int, row: dict, show_checks: bool = True) -> str:
    lines = [
        f"#{index}  {str(row.get('ts', ''))[:19]}  {TAGS.get(row.get('decision'), '[?]')} "
        f"risk {float(row.get('risk', 0)):.2f}  channel={row.get('channel', '?')}",
        f"Request: {row.get('request', '')}",
        row.get("message", ""),
    ]
    if show_checks:
        lines += ["  " + format_check(c) for c in row.get("checks", [])]
    if row.get("tx_hash"):
        lines.append(f"Tx: {row['tx_hash']}")
    return "\n".join(lines)
