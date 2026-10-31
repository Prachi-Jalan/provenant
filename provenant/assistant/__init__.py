"""Prompt-driven assistant: the front door that replaces the Telegram bot.

Everything that can move money goes through PaymentEngine -> firewall.check -> wallet.send.
"""
from .assistant import Assistant, Reply  # noqa: F401
from .engine import PaymentEngine, RunResult  # noqa: F401
from .inbox import Inbox, InboxError  # noqa: F401
from .router import Intent, Router  # noqa: F401
