"""Streamlit chat page for the Provenant assistant.

Run:  streamlit run dashboard/chat.py

Left sidebar  = the simulated outside world (untrusted inbox) + the 5 demo scenarios.
Main area     = chat. 'Owner' messages are trusted; 'Stranger' messages go to the inbox as untrusted.
Every payment goes agent -> firewall.check -> wallet. Default is DRY_RUN (nothing is sent on-chain).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # `streamlit run dashboard/chat.py` only puts dashboard/ on the path
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DRY_RUN", "1")

import streamlit as st  # noqa: E402

from provenant.assistant import Assistant, InboxError, PaymentEngine, Router  # noqa: E402
from provenant.assistant.engine import load_agent  # noqa: E402
from provenant.assistant.replies import decision_value, format_check, payment_line  # noqa: E402
from provenant.assistant.scenarios import get_scenarios, run_scenario  # noqa: E402

st.set_page_config(page_title="Provenant Assistant", page_icon=None, layout="wide")

BANNER = {"ALLOW": st.success, "REPAIRED": st.info, "ESCALATE": st.warning, "BLOCK": st.error}


def _state() -> None:
    if "assistant" not in st.session_state:
        try:
            engine = PaymentEngine(agent=load_agent())
        except (RuntimeError, ValueError) as e:
            st.sidebar.error(f"Agent error: {e}. Falling back to the offline agent.")
            engine = PaymentEngine(agent=load_agent("naive"))
        st.session_state.assistant = Assistant(engine=engine, router=Router())
        st.session_state.messages = []


def _render_checks(verdict) -> None:
    rows = []
    for c in verdict.checks:
        status = "ok" if c.passed else ("FAIL" if c.hard else "warn")
        rows.append({
            "check": c.name, "result": status, "type": "hard" if c.hard else "soft",
            "score": "" if c.score is None else f"{c.score:.2f}", "reason": c.reason,
        })
    st.table(rows)


def render_reply(reply) -> None:
    if reply.result and reply.result.verdicts:
        if reply.extra:
            st.text(reply.extra)
        result = reply.result
        for i, v in enumerate(result.verdicts):
            if result.replan_from is not None and i == result.replan_from:
                st.caption("The agent re-planned once after the block. New proposal:")
            d = decision_value(v)
            BANNER.get(d, st.info)(f"{d}  |  risk {v.risk:.2f}")
            if v.original is not None and getattr(v.original, "decoded", ""):
                st.caption(f"Agent proposed: {v.original.decoded}")
            st.write(v.message)
            with st.expander("The 5 checks"):
                _render_checks(v)
            outcome = result.outcomes.get(i)
            st.caption(payment_line(v, outcome))
            if outcome and outcome.link:
                st.markdown(f"[View on BaseScan]({outcome.link})")
        tail = reply.text.splitlines()[-1] if reply.needs_confirmation else ""
        if tail:
            st.warning(tail)
    else:
        st.text(reply.text)


def _sidebar(assistant: Assistant) -> tuple[str, str]:
    sb = st.sidebar
    sb.header("Who is typing?")
    role_label = sb.radio("Channel", ["Owner (trusted)", "Stranger (untrusted)"], key="role_radio")
    role = "owner" if role_label.startswith("Owner") else "stranger"
    sender = sb.text_input("Stranger name", "stranger_42", key="sender") if role == "stranger" else ""

    dry = sb.checkbox("Dry run (simulate payments)", value=True, key="dry")
    os.environ["DRY_RUN"] = "1" if dry else "0"
    if not dry:
        sb.warning("LIVE: ALLOW / REPAIRED payments are signed on Base Sepolia.")

    sb.header("Inbox (untrusted)")
    with sb.form("add_msg", clear_on_submit=True):
        text = st.text_area("Add a message from a stranger", height=80)
        if st.form_submit_button("Add to inbox") and text.strip():
            assistant.inbox.add_message(sender or "stranger_42", text.strip(), assistant.owner_id)
    c1, c2 = sb.columns(2)
    invoices, pages = assistant.inbox.available_invoices(), assistant.inbox.available_pages()
    inv = sb.selectbox("Invoice", ["-"] + invoices, key="inv_pick")
    if sb.button("Add invoice", key="add_inv") and inv != "-":
        _safe_add(lambda: assistant.inbox.add_invoice(inv))
    page = sb.selectbox("Web page", ["-"] + pages, key="page_pick")
    if sb.button("Add page", key="add_page") and page != "-":
        _safe_add(lambda: assistant.inbox.add_page(page))
    if sb.button("Clear inbox", key="clear_inbox"):
        assistant.inbox.clear()
        assistant.pending = None
    if assistant.inbox.items:
        for it in assistant.inbox.items:
            sb.caption(f"{it.id} | {it.trust} | {it.source}")
            sb.code(it.text[:160] + ("..." if len(it.text) > 160 else ""), language=None)
    else:
        sb.caption("(empty)")

    sb.header("Demo scenarios")
    for sc in get_scenarios():
        if sb.button(f"{sc.number}. {sc.title}", key=f"sc{sc.number}"):
            out = run_scenario(assistant, sc)
            who = f"{sc.role}" + (f" ({sc.sender})" if sc.sender else "")
            st.session_state.messages.append({"who": "note", "text": f"Scenario {sc.number}: " + "; ".join(out.setup_notes) if out.setup_notes else f"Scenario {sc.number}"})
            st.session_state.messages.append({"who": who, "text": sc.prompt})
            st.session_state.messages.append({"who": "assistant", "reply": out.reply,
                                              "expect": f"{out.detail} -> {'PASS' if out.ok else 'FAIL'}"})
    return role, sender


def _safe_add(fn) -> None:
    try:
        fn()
    except InboxError as e:
        st.sidebar.error(str(e))


def main() -> None:
    _state()
    assistant: Assistant = st.session_state.assistant

    st.title("Provenant assistant")
    st.caption(
        "Your AI agent can get tricked. Your money won't. The agent only proposes a payment; "
        "the firewall checks where every detail came from and signs only what traces back to the owner."
    )

    role, sender = _sidebar(assistant)

    prompt = st.chat_input("Ask the assistant, e.g. 'pay Rahul 500'" if role == "owner"
                           else "Type as a stranger (goes to the untrusted inbox)")
    if prompt:
        reply = assistant.chat(prompt, role=role, sender=sender or None)
        st.session_state.messages.append({"who": "owner" if role == "owner" else (sender or "stranger"), "text": prompt})
        st.session_state.messages.append({"who": "assistant", "reply": reply})

    if not st.session_state.messages:
        st.info("Try the demo scenarios in the sidebar, or type 'help'.")
    for m in st.session_state.messages:
        if m["who"] == "assistant":
            with st.chat_message("assistant"):
                render_reply(m["reply"])
                if m.get("expect"):
                    st.caption(m["expect"])
        elif m["who"] == "note":
            st.caption(m["text"])
        else:
            with st.chat_message("user"):
                st.caption(m["who"])
                st.write(m["text"])


main()
