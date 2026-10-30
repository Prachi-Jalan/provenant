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
| `bot/` | Telegram bot | P1 (A) |
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

## Person 1 quickstart: wallets, first USDC, demo

```bash
python scripts/setup_wallets.py          # makes 3 testnet wallets, writes .env (never commit it)
```
Fund the **owner** address only: Base Sepolia ETH from the Coinbase faucet
(https://portal.cdp.coinbase.com/onchain-tools/faucet) and test USDC from https://faucet.circle.com.
On both, set the network to **Base Sepolia** (not Ethereum Sepolia). Then:

```bash
python -m provenant.chain.wallet --balances    # owner should show ETH > 0 and USDC 20
python -m provenant.chain.wallet               # sends 1 USDC to VENDOR_ADDRESS, prints a BaseScan link
python -m demo.run_scenarios --unprotected     # the agent getting fooled (sends nothing)
python -m demo.run_scenarios                   # all 5 scenarios through the real firewall (dry run)
```
Windows PowerShell: activate with `.venv\Scripts\Activate.ps1`. Dry run: `$env:DRY_RUN=1`.
Mac/Linux: `source .venv/bin/activate`, `DRY_RUN=1 python ...`.

`provenant/pipeline.py` has `handle(request, requester, session, agent)`: the whole flow
(policy, agent, firewall, one re-plan, wallet, verdict log) for the bot and the demo to call.

## Run
```bash
python -m provenant.chain.wallet      # P1 milestone: send 1 test USDC
python -m demo.run_scenarios          # all scenarios end to end
python -m eval.run_eval               # numbers table
python -m bot.telegram_bot --dry-run  # Telegram bot, nothing is sent (drop --dry-run to follow .env)
streamlit run dashboard/app.py        # dashboard
```

## Telegram bot (`bot/telegram_bot.py`)
One-time setup:
1. In Telegram, talk to **@BotFather** -> `/newbot` -> copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
2. For group chats, also in BotFather: `/setprivacy` -> **Disable**, so the bot sees group messages
   (that is how a stranger's "Rahul's new wallet is 0x..." message becomes context for scenario 2).
3. Start the bot, send it `/whoami`, put the number it prints into `.env` as `OWNER_TELEGRAM_ID`, restart.
   Until that is set nobody is the owner, so every payment is blocked (safe default).

```bash
python -m bot.telegram_bot --dry-run   # safe: nothing is signed or sent
python -m bot.telegram_bot             # follows DRY_RUN in .env (DRY_RUN=0 = real Base Sepolia sends)
python -m bot.telegram_bot --agent naive   # scripted gullible agent, no LLM key needed
pytest tests/test_bot.py               # bot tests (no network, no Telegram, no money)
```
How it behaves:
- The owner's Telegram id is trusted (`owner`). Everyone else is untrusted (`tg_<username>`), even if they claim to be the owner.
- DM: every message goes through `pipeline.handle`. Group: all messages are logged as context, but the agent only runs for an @mention, a reply to the bot, or an owner message that asks for a payment.
- Replies show the decision, risk, checks, and a BaseScan link when a payment was really sent. Only the owner sees repaired (trusted-record) addresses.
- Strangers are rate limited (`BOT_RATE_LIMIT_MAX` per `BOT_RATE_LIMIT_WINDOW` seconds). Every decision is logged to `logs/verdicts.jsonl` with `channel="telegram"`.
- Commands: `/whoami`, `/balance` (owner), `/recent` (owner), `/help`.
