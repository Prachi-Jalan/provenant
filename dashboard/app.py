"""Streamlit dashboard. Owner: Person 3.
Run: streamlit run dashboard/app.py
Reads logs/verdicts.jsonl via provenant.verdict_log.read_verdicts().
Shows: list of payments (ALLOW green / REPAIRED blue / ESCALATE amber / BLOCK red),
click -> 4 checks + reasons + BaseScan link, plus the eval numbers table.
"""
import streamlit as st

st.title("Provenant")
st.info("Dashboard coming soon (Person 3).")
