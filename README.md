# Provenant

**Your AI agent can get tricked. Your money won't.**

A firewall between an AI agent and its crypto wallet. The agent only *proposes* transactions.
Provenant turns the owner's request into a one-time policy, checks where every detail of the
transaction came from, and signs only what traces back to the owner. If the agent was fooled,
Provenant repairs the payment from trusted records or asks the owner.

Built at Hacker House Goa 2026 (AI x Crypto).

## How it works
```
Owner request ─▶ make_policy ─▶ Policy (verified recipient, cap, one use)
                                   │
Agent reads chats/invoices/web ─▶ ProposedTx (agent holds NO key)
                                   │
                         firewall.check
          policy · provenance · intent (LLM) · reputation (TigerGraph)
                                   │
          ALLOW / REPAIRED ─▶ sign on Base Sepolia ─▶ BaseScan link
          ESCALATE ─▶ ask owner      BLOCK ─▶ nothing reaches the chain
```

## Repo layout
| Path | What | Owner |
|---|---|---|
| `provenant/schemas.py` | Shared data shapes | all (see CONTRACTS.md) |
| `provenant/records.py` | Trusted contacts + vendor records | all |
| `provenant/chain/` | Wallet, send, ERC-20 decode | P1 |
| `provenant/agent/` | Agent tools, NaiveAgent, LLM agent | P1 |
| `provenant/firewall/` | Policy, checks, decision + repair | P2 |
| `provenant/graph/` | TigerGraph reputation + attack vector search | P3 |
| `bot/` | Telegram bot | P3 |
| `dashboard/` | Streamlit dashboard | P3 |
| `eval/` | Test cases + evaluation harness | P3 |
| `demo/` | End-to-end scenario runner | all |
| `data/` | Seed data (contacts, vendors, flagged wallets, invoices) | all |

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
pytest                 # contract tests should pass
```

## Run
```bash
python -m provenant.chain.wallet      # P1 milestone: send 1 test USDC
python -m demo.run_scenarios          # all scenarios end to end
python -m eval.run_eval               # numbers table
python bot/telegram_bot.py            # live bot
streamlit run dashboard/app.py        # dashboard
```
