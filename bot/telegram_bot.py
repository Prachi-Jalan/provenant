"""Telegram bot. Owner: Person 3.

Flow per message:
  sender == OWNER_TELEGRAM_ID -> trusted request -> make_policy -> agent.act
  anyone else                 -> untrusted content (and a deny-all policy if it asks to pay)
  -> firewall.check -> wallet.send(final_tx) if ALLOW/REPAIRED -> log_verdict -> reply
Reply: verdict.message + BaseScan link when paid.
"""


def main() -> None:
    raise NotImplementedError("Person 3 (python-telegram-bot)")


if __name__ == "__main__":
    main()
