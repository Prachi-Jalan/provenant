"""VS Code terminal chat for the Provenant assistant.

  python -m provenant.assistant.cli                 interactive chat
  python -m provenant.assistant.cli --scenarios     run the 5 demo scenarios, print a pass/fail table
  python -m provenant.assistant.cli --once "pay Rahul 500"
  python -m provenant.assistant.cli --live          real transfers (asks you to type LIVE first)

Safe by default: DRY_RUN=1, offline agent, no API keys needed.
"""
from __future__ import annotations

import argparse
import os
import sys

from .assistant import HELP, Assistant
from .engine import PaymentEngine, load_agent
from .inbox import InboxError
from .router import Router
from .scenarios import ScenarioOutcome, get_scenarios, run_all, run_scenario

COMMANDS = """Commands:
  /as owner                      type as the owner (trusted)
  /as stranger [name]            type as a stranger (untrusted, goes to the inbox)
  /inbox                         show the inbox
  /inbox add msg <text>          add an untrusted message from the current stranger
  /inbox add invoice <name>      add an invoice from data/invoices (e.g. acme_oct_fake)
  /inbox add page <name|url>     add a seeded web page (e.g. defi_guide)
  /inbox clear                   empty the inbox
  /scenarios                     list the 5 demo scenarios
  /scenario <n|all>              run one (or all) of them
  /live  /dry                    switch real / simulated payments
  /help  /quit"""


def _setup_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
        except Exception:
            pass


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass


def _confirm_live() -> bool:
    print("LIVE MODE signs real transactions on Base Sepolia with the owner wallet.")
    return input("Type LIVE to confirm: ").strip() == "LIVE"


def _print_inbox(assistant: Assistant) -> None:
    if not assistant.inbox.items:
        print("(inbox is empty)")
    for it in assistant.inbox.items:
        snippet = it.text.replace("\n", " ")
        print(f"  {it.id:<4} {it.trust:<9} {it.source:<28} {snippet[:70]}{'...' if len(snippet) > 70 else ''}")


def print_outcomes(outcomes: list[ScenarioOutcome]) -> None:
    print(f"\n{'#':<3}{'scenario':<16}{'expected':<18}{'got':<10}result")
    for o in outcomes:
        sc = o.scenario
        print(f"{sc.number:<3}{sc.key:<16}{'/'.join(sc.accept):<18}{str(o.got):<10}{'PASS' if o.ok else 'FAIL'}")
    passed = sum(o.ok for o in outcomes)
    print(f"\n{passed}/{len(outcomes)} scenarios as expected "
          f"(DRY_RUN={os.environ.get('DRY_RUN', '1')})")


def _run_one(assistant: Assistant, sc) -> ScenarioOutcome:
    print(f"\n=== Scenario {sc.number}: {sc.title} ===")
    out = run_scenario(assistant, sc)
    for n in out.setup_notes:
        print(f"  {n}")
    print(f"  {sc.role}{'/' + sc.sender if sc.sender else ''}> {sc.prompt}")
    print(out.reply.text)
    print(f"-> {out.detail}: {'PASS' if out.ok else 'FAIL'}")
    return out


def _handle_command(line: str, st: dict, assistant: Assistant) -> bool:
    parts = line.split(maxsplit=3)
    cmd = parts[0].lower()
    if cmd in ("/quit", "/exit", "/q"):
        return False
    if cmd == "/help":
        print(HELP + "\n\n" + COMMANDS)
    elif cmd == "/as":
        who = parts[1].lower() if len(parts) > 1 else ""
        if who == "owner":
            st["role"], st["sender"] = "owner", None
        elif who == "stranger":
            st["role"], st["sender"] = "stranger", (parts[2] if len(parts) > 2 else "stranger_42")
        else:
            print("Usage: /as owner | /as stranger [name]")
            return True
        print(f"Now typing as {st['role']}" + (f" ({st['sender']})" if st["sender"] else ""))
    elif cmd == "/inbox":
        sub = parts[1].lower() if len(parts) > 1 else ""
        try:
            if sub == "":
                _print_inbox(assistant)
            elif sub == "clear":
                assistant.inbox.clear()
                print("Inbox cleared.")
            elif sub == "add" and len(parts) >= 4:
                kind, arg = parts[2].lower(), parts[3]
                if kind in ("msg", "message"):
                    it = assistant.inbox.add_message(st["sender"] or "stranger_42", arg, assistant.owner_id)
                elif kind == "invoice":
                    it = assistant.inbox.add_invoice(arg)
                elif kind == "page":
                    it = assistant.inbox.add_page(arg)
                else:
                    print("Usage: /inbox add msg|invoice|page <value>")
                    return True
                print(f"Added {it.id} ({it.source}) as UNTRUSTED.")
            else:
                print("Usage: /inbox | /inbox clear | /inbox add msg|invoice|page <value>")
        except InboxError as e:
            print(f"Inbox: {e}")
    elif cmd == "/scenarios":
        for sc in get_scenarios():
            print(f"  {sc.number}. {sc.title}  (expect {'/'.join(sc.accept)})")
    elif cmd == "/scenario":
        which = parts[1].lower() if len(parts) > 1 else ""
        scs = get_scenarios()
        if which == "all":
            print_outcomes([_run_one(assistant, sc) for sc in scs])
        elif which.isdigit() and 1 <= int(which) <= len(scs):
            _run_one(assistant, scs[int(which) - 1])
        else:
            print("Usage: /scenario <1-5|all>")
    elif cmd == "/live":
        if _confirm_live():
            os.environ["DRY_RUN"] = "0"
            print("LIVE: ALLOW/REPAIRED payments will now be signed.")
        else:
            print("Staying in dry-run mode.")
    elif cmd == "/dry":
        os.environ["DRY_RUN"] = "1"
        print("Dry-run mode: nothing is sent to the chain.")
    else:
        print("Unknown command. Type /help.")
    return True


def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    _load_env()
    ap = argparse.ArgumentParser(prog="provenant.assistant.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", metavar="TEXT", help="send one message and exit")
    ap.add_argument("--as", dest="role", default="owner", choices=["owner", "stranger"])
    ap.add_argument("--sender", default=None, help="stranger name for --as stranger")
    ap.add_argument("--scenarios", action="store_true", help="run the 5 demo scenarios and exit")
    ap.add_argument("--live", action="store_true", help="real transfers (asks for confirmation)")
    ap.add_argument("--router", choices=["auto", "regex", "llm"], default=None)
    ap.add_argument("--agent", choices=["naive", "p1-naive", "p1-llm"], default=None)
    args = ap.parse_args(argv)

    os.environ.setdefault("DRY_RUN", "1")
    if args.live:
        if not _confirm_live():
            print("Aborted. Staying in dry-run mode.")
            os.environ["DRY_RUN"] = "1"
        else:
            os.environ["DRY_RUN"] = "0"

    try:
        engine = PaymentEngine(agent=load_agent(args.agent))
    except (RuntimeError, ValueError) as e:
        print(f"Agent error: {e}")
        return 2
    assistant = Assistant(engine=engine, router=Router(args.router))

    if args.scenarios:
        outcomes = run_all(assistant)
        for o in outcomes:
            print(f"\n--- {o.scenario.number}. {o.scenario.title}\n{o.reply.text}")
        print_outcomes(outcomes)
        return 0 if all(o.ok for o in outcomes) else 1

    if args.once:
        print(assistant.chat(args.once, role=args.role, sender=args.sender).text)
        return 0

    print("Provenant assistant. The agent only proposes; the firewall decides what gets signed.")
    print(f"Payments: {'SIMULATED (DRY_RUN=1)' if os.environ['DRY_RUN'] != '0' else 'LIVE on Base Sepolia'}. "
          "Type /help for commands, /scenario all to run the demo.")
    st = {"role": "owner", "sender": None}
    while True:
        prompt = f"{st['role']}{'/' + st['sender'] if st['sender'] else ''}> "
        try:
            line = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            if not _handle_command(line, st, assistant):
                break
            continue
        print(assistant.chat(line, role=st["role"], sender=st["sender"]).text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
