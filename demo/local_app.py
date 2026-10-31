"""Provenant: type a request, watch your assistant pay this month's invoices safely.

    streamlit run demo/local_app.py

Logic: demo/invoice_run.py (tested). Payments go through Person 1's wallet, which honours DRY_RUN
(default here: DRY_RUN=1, signs nothing). Only verified or repaired payments are ever signed.
"""
import html
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
os.environ.setdefault("INTENT_MODE", "offline")
os.environ.setdefault("DRY_RUN", "1")          # never broadcast unless .env explicitly says DRY_RUN=0

from demo.invoice_run import INBOX_ROOT, STEPS, months, short, stream
from provenant.graph import learn

st.set_page_config(page_title="Provenant", layout="wide")
st.markdown("""<style>
.block-container{padding-top:1.5rem;max-width:1200px}
mark.bad{background:#F7C1C1;color:#501313;font-weight:700;padding:0 3px;border-radius:3px}
mark.ok{background:#C0DD97;color:#173404;font-weight:700;padding:0 3px;border-radius:3px}
.hid{background:#FFF3B0;border:1.5px dashed #BA7517;border-radius:4px;padding:0 3px}
.doc{font-family:Menlo,monospace;font-size:.8rem;line-height:1.55;background:#FBFAF6;border:1px dashed #D3D1C7;border-radius:8px;padding:10px 12px}
.step{font-family:Menlo,monospace;font-size:.8rem;line-height:1.75}
.step b{display:inline-block;width:120px;color:#5F5E5A}
.ok{color:#27500A}.fix{color:#0C447C}.warn{color:#854F0B}.bad{color:#A32D2D}.skip{color:#B4B2A9}
.ms{color:#B4B2A9;float:right}
</style>""", unsafe_allow_html=True)

RESULT = {"paid": "Paid", "repaired": "Repaired: paid the real vendor", "held": "Held for your approval",
          "blocked": "Blocked", "stolen": "Sent to the attacker", "risky": "Paid, but risky", "skipped": "Skipped"}
STEP_NAMES = {"READ": "Read PDF", "MATCH": "Find vendor", "PROPOSE": "Agent proposes", "POLICY": "Your rules",
              "GRAPH": "TigerGraph", "VERIFY": "Firewall", "SIGN": "Payment", "LEARN": "Graph update"}

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Settings")
    protected = st.toggle("Provenant firewall", value=True)
    month = st.selectbox("Inbox month", months() or ["(none)"])
    up = st.file_uploader("Add an invoice PDF to this month's inbox", type=["pdf"])
    if up and st.button("Add to inbox"):
        (INBOX_ROOT / month / up.name).write_bytes(up.getvalue())
        st.success(f"Added {up.name}")
    speed = st.slider("Pipeline speed (seconds per step)", 0.0, 0.8, 0.2, 0.05,
                      help="Slow it down so each step can be followed on screen.")
    st.divider()
    dry = os.getenv("DRY_RUN", "1").strip().lower() in ("1", "true", "yes", "on")
    st.caption(f"Graph: {'TigerGraph (local copy if asleep)' if os.getenv('TG_HOST') else 'local copy'}")
    st.caption(f"Payments: {'dry run, nothing is sent' if dry else 'LIVE on Base Sepolia'}")
    st.caption(f"Attacks remembered: {len(learn.load())}")
    if st.button("Reset attack memory"):
        learn.forget_all()
        st.rerun()

st.title("Provenant")
st.caption("Your AI assistant pays your bills. Provenant makes sure it pays the right people.")

c1, c2 = st.columns([5, 1])
request = c1.text_input("What should your assistant do?", "pay this month's vendor invoices",
                        label_visibility="collapsed")
go = c2.button("Run", type="primary", width="stretch")


def step_html(steps: dict) -> str:
    return "".join(
        f'<div class="step"><b>{STEP_NAMES[s]}</b><span class="{steps[s]["state"]}">{html.escape(steps[s]["detail"])}</span>'
        f'<span class="ms">{steps[s]["ms"]} ms</span></div>' for s in STEPS if s in steps)


# ------------------------------------------------------------------ live run
if go:
    st.session_state.pop("done", None)
    msg = st.empty()
    st.subheader("Payment pipeline")
    bar = st.progress(0.0, text="Starting…")
    live, boxes, steps, total, n = {}, {}, {}, 1, 0
    for ev in stream(request, protected, month):
        if ev["type"] == "plan":
            msg.write(ev["message"])
            total = max(ev["count"], 1) * len(STEPS)
        elif ev["type"] == "step":
            f = ev["file"]
            if f not in live:
                live[f] = st.status(f"{f}: working…", expanded=True)
                boxes[f] = live[f].empty()
                steps[f] = {}
            steps[f][ev["step"]] = ev
            boxes[f].markdown(step_html(steps[f]), unsafe_allow_html=True)
            n += 1
            bar.progress(min(n / total, 1.0), text=f"{f} · {STEP_NAMES[ev['step']]}")
            time.sleep(speed)
        elif ev["type"] == "row":
            r = ev["row"]
            bad = r["outcome"] in ("stolen", "risky", "blocked")
            live[r["file"]].update(label=f"{r['file']} · {r['vendor']} · {RESULT[r['outcome']]}",
                                   state="error" if bad else "complete", expanded=False)
        elif ev["type"] == "done":
            bar.progress(1.0, text="Done")
            st.session_state["done"] = {"rows": ev["rows"], "totals": ev["totals"], "protected": protected,
                                        "steps": steps}
    st.rerun()

res = st.session_state.get("done")
if not res:
    st.info('Type a request and press Run. Try "pay this month\'s vendor invoices" or '
            '"pay the guy who catered for my birthday". Turn the firewall off in the sidebar to see what '
            'an unprotected assistant does.')
    st.stop()

rows, t, was_on = res["rows"], res["totals"], res["protected"]
if not rows:
    st.write("Nothing to pay for that request.")
    st.stop()

# ------------------------------------------------------------------ summary
m = st.columns(4)
m[0].metric("Invoices read", t["invoices"])
m[1].metric("Paid to the right wallets", f"{t['paid_ok']:g} USDC")
if was_on:
    m[2].metric("Problems stopped", t["attacks_stopped"])
    m[3].metric("Held for your approval", t["held"])
else:
    m[2].metric("Sent to attackers", f"{t['stolen']:g} USDC")
    m[3].metric("Risky payments", f"{t['risky']:g} USDC")
st.caption(f"Firewall was {'ON' if was_on else 'OFF'}.")


def chain(r):
    h = r.get("tx_hash")
    if r["decision"] == "SIGNED":
        return "simulation only"
    if not r.get("paid_to"):
        return "nothing sent"
    if not h or h == "0xDRYRUN":
        return "signed (dry run)"
    return f"https://sepolia.basescan.org/tx/{h}"


st.dataframe(pd.DataFrame([{
    "Invoice": r["file"], "From": r["vendor"], "In your records": "Yes" if r["known_vendor"] else "No",
    "Amount": f"{r['amount']:g} USDC" if r["amount"] else "",
    "Wallet on invoice": short(r["wallet_in_doc"]) if r["wallet_in_doc"] else "none (uses wallet on file)",
    "Result": RESULT[r["outcome"]], "Paid to": short(r["paid_to"]) or "",
    "Base Sepolia": chain(r), "Why": r["why"],
} for r in rows]), hide_index=True, width="stretch")

# ------------------------------------------------------------------ pipeline + TigerGraph (after the run)
left, right = st.columns([1.4, 1], gap="large")
with left:
    st.subheader("Payment pipeline")
    for r in rows:
        with st.expander(f"{r['file']} · {r['vendor']} · {RESULT[r['outcome']]}"):
            st.markdown(step_html(res["steps"].get(r["file"], {})), unsafe_allow_html=True)
with right:
    st.subheader("TigerGraph activity")
    tg = [e for f in res["steps"] for e in res["steps"][f].values()
          if e["step"] in ("GRAPH", "LEARN") and e["state"] != "skip"]
    if not tg:
        st.caption("No graph activity: the firewall was off.")
    for e in tg:
        kind = "Lookup" if e["step"] == "GRAPH" else "Update"
        st.markdown(f'<div class="step"><b>{kind}</b><span class="{e["state"]}">{html.escape(e["file"])}: '
                    f'{html.escape(e["detail"])}</span></div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ inside each invoice
st.subheader("Look inside each invoice")
for r in rows:
    with st.expander(f"{r['file']} · {r['vendor']} · {RESULT[r['outcome']]}"):
        if r["hidden"]:
            st.warning(f"This PDF contains {len(r['hidden'])} line(s) of invisible text. "
                       "You can't see it, but the AI reads it (highlighted below).")
        a, b = st.columns([1.3, 1])
        a.markdown("**What the AI read**")
        a.markdown(f'<div class="doc">{r["text_html"]}</div>', unsafe_allow_html=True)
        b.markdown("**Provenant's checks**")
        if not r["checks"]:
            b.caption("Firewall was off, so the agent's proposal was paid as-is.")
        for c in r["checks"]:
            word = "Pass" if c["passed"] else ("Fail" if c["hard"] else "Risk")
            b.markdown(f"**{word}** · {c['name']}: {c['reason']}")
        if r.get("learned") and r["learned"].get("new"):
            b.info(f"TigerGraph learned this attack: wallet {short(r['learned']['wallet'])} is now flagged.")
