"""Thin LLM adapters so the agent loop is provider-agnostic. Owner: Person 1.

Pick a provider with LLM_PROVIDER=anthropic|openai (default: whichever API key is set).
Models: ANTHROPIC_MODEL / OPENAI_MODEL in .env.

ScriptedLLM replays canned turns: for tests and key-free demos.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient:
    """A stateful conversation. The agent loop calls these in order."""

    active = False  # True once start() has been called

    def start(self, system: str, user: str, tools: list[dict]) -> None:
        raise NotImplementedError

    def next_turn(self) -> LLMTurn:
        raise NotImplementedError

    def add_tool_results(self, results: list[tuple[ToolCall, str]]) -> None:
        raise NotImplementedError

    def add_user(self, text: str) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
class AnthropicClient(LLMClient):
    def __init__(self, model: Optional[str] = None, max_tokens: int = 1024) -> None:
        import anthropic

        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens
        self.messages: list[dict] = []

    def start(self, system, user, tools):
        self.system = system
        self.tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in tools
        ]
        self.messages = [{"role": "user", "content": user}]
        self.active = True

    def next_turn(self) -> LLMTurn:
        resp = self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=self.system,
            tools=self.tools, messages=self.messages,
        )
        blocks, text_parts, calls = [], [], []
        for b in resp.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
                text_parts.append(b.text)
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                calls.append(ToolCall(b.id, b.name, dict(b.input)))
        self.messages.append({"role": "assistant", "content": blocks})
        return LLMTurn("".join(text_parts), calls)

    def add_tool_results(self, results):
        self.messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": c.id, "content": out} for c, out in results],
        })

    def add_user(self, text):
        last = self.messages[-1] if self.messages else None
        if last and last["role"] == "user" and isinstance(last["content"], list):
            last["content"].append({"type": "text", "text": text})  # merge after tool_results
        else:
            self.messages.append({"role": "user", "content": text})


# --------------------------------------------------------------------------- #
class OpenAIClient(LLMClient):
    def __init__(self, model: Optional[str] = None) -> None:
        from openai import OpenAI

        self._client = OpenAI()  # reads OPENAI_API_KEY
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.messages: list[dict] = []

    def start(self, system, user, tools):
        self.tools = [
            {"type": "function",
             "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
            for t in tools
        ]
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        self.active = True

    def next_turn(self) -> LLMTurn:
        resp = self._client.chat.completions.create(model=self.model, messages=self.messages, tools=self.tools)
        msg = resp.choices[0].message
        calls = []
        entry: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            entry["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(tc.id, tc.function.name, args))
        self.messages.append(entry)
        return LLMTurn(msg.content or "", calls)

    def add_tool_results(self, results):
        for call, out in results:
            self.messages.append({"role": "tool", "tool_call_id": call.id, "content": out})

    def add_user(self, text):
        self.messages.append({"role": "user", "content": text})


# --------------------------------------------------------------------------- #
class ScriptedLLM(LLMClient):
    """Replays a fixed list of LLMTurn objects. No network. Records what it was sent."""

    def __init__(self, turns: list[LLMTurn]) -> None:
        self._turns = list(turns)
        self.log: list[tuple[str, object]] = []

    def start(self, system, user, tools):
        self.log.append(("start", user))
        self.active = True

    def next_turn(self) -> LLMTurn:
        if not self._turns:
            return LLMTurn(text="(script finished)")
        return self._turns.pop(0)

    def add_tool_results(self, results):
        self.log.append(("tool_results", [out for _, out in results]))

    def add_user(self, text):
        self.log.append(("user", text))


def make_client(provider: Optional[str] = None) -> LLMClient:
    provider = (provider or os.getenv("LLM_PROVIDER", "")).lower()
    if not provider:
        if os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        else:
            raise RuntimeError(
                "No LLM key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env "
                "(or use NaiveAgent, which needs no key)."
            )
    if provider == "anthropic":
        return AnthropicClient()
    if provider == "openai":
        return OpenAIClient()
    raise RuntimeError(f"Unknown LLM_PROVIDER {provider!r} (use 'anthropic' or 'openai')")
