"""LLM adapter tests with fake SDK clients: the message history must stay valid for each provider.

Anthropic rejects a conversation if a tool_use is not answered by a tool_result in the very next
user turn; OpenAI rejects a tool_call without a matching `tool` message. These tests replay the
agent loop against fakes and check those rules, so a real key is not needed to catch such bugs.
"""
import copy
import json
from types import SimpleNamespace

import pytest

from provenant.agent import llm
from provenant.agent.llm import (
    AnthropicClient, LLMClient, LLMTurn, OpenAIClient, ScriptedLLM, ToolCall, make_client,
)
from provenant.agent.tools import TOOL_SPECS

SYSTEM, USER = "you are a payment assistant", "pay Rahul 500"


# ---- fakes --------------------------------------------------------------------------------------
class FakeAnthropic:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []  # deep copies of every request
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.sent.append(copy.deepcopy(kw))
        return SimpleNamespace(content=self.replies.pop(0))


def a_text(text):
    return SimpleNamespace(type="text", text=text)


def a_tool(call_id, tool, **inp):
    return SimpleNamespace(type="tool_use", id=call_id, name=tool, input=inp)


@pytest.fixture
def anthropic_client(monkeypatch):
    def build(replies):
        fake = FakeAnthropic(replies)
        monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: fake)
        return AnthropicClient(), fake

    return build


def check_anthropic_history(messages):
    """Every tool_use must be answered by a tool_result in the NEXT message; roles alternate."""
    pending = set()
    for i, m in enumerate(messages):
        if i > 0:
            assert m["role"] != messages[i - 1]["role"], f"roles must alternate at {i}"
        content = m["content"]
        if m["role"] == "assistant":
            assert not pending, "previous tool_use was never answered"
            pending = {b["id"] for b in content if b["type"] == "tool_use"}
        elif isinstance(content, list):
            answered = {b["tool_use_id"] for b in content if b["type"] == "tool_result"}
            assert answered == pending, f"tool_results {answered} != tool_uses {pending}"
            pending = set()
    return pending


# ---- Anthropic ----------------------------------------------------------------------------------------
def test_anthropic_start_converts_tool_specs(anthropic_client):
    client, fake = anthropic_client([[a_text("ok")]])
    client.start(SYSTEM, USER, TOOL_SPECS)
    client.next_turn()
    sent = fake.sent[0]
    assert client.active and sent["system"] == SYSTEM
    assert sent["messages"] == [{"role": "user", "content": USER}]
    assert {t["name"] for t in sent["tools"]} == {t["name"] for t in TOOL_SPECS}
    assert all("input_schema" in t and "parameters" not in t for t in sent["tools"])


def test_anthropic_parses_text_and_tool_calls(anthropic_client):
    client, _ = anthropic_client([[a_text("looking up"), a_tool("t1", "lookup_contact", name="Rahul")]])
    client.start(SYSTEM, USER, TOOL_SPECS)
    turn = client.next_turn()
    assert turn.text == "looking up"
    assert turn.tool_calls == [ToolCall("t1", "lookup_contact", {"name": "Rahul"})]
    assert client.messages[-1]["role"] == "assistant"


def test_anthropic_full_loop_keeps_a_valid_history(anthropic_client):
    client, fake = anthropic_client([
        [a_tool("t1", "lookup_contact", name="Rahul"), a_tool("t2", "lookup_contact", name="Acme")],
        [a_tool("t3", "propose_tx", action="transfer", to="0x" + "11" * 20, amount=500)],
        [a_text("Proposed.")],
    ])
    client.start(SYSTEM, USER, TOOL_SPECS)
    t = client.next_turn()
    client.add_tool_results([(c, f"result for {c.id}") for c in t.tool_calls])
    t = client.next_turn()
    client.add_tool_results([(c, "recorded") for c in t.tool_calls])
    client.next_turn()
    assert check_anthropic_history(fake.sent[-1]["messages"]) == set()
    assert len(fake.sent[-1]["messages"]) == 5  # user, assistant, results, assistant, results


def test_anthropic_feedback_is_merged_into_the_tool_result_turn(anthropic_client):
    """Re-plan feedback right after tool results must NOT become a second consecutive user message."""
    client, fake = anthropic_client([[a_tool("t1", "propose_tx", action="transfer", to="0x" + "11" * 20, amount=5)],
                                     [a_text("retrying")]])
    client.start(SYSTEM, USER, TOOL_SPECS)
    t = client.next_turn()
    client.add_tool_results([(c, "recorded") for c in t.tool_calls])
    client.add_user("The firewall rejected your proposal")
    client.next_turn()
    msgs = fake.sent[-1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1]["content"][0]["type"] == "tool_result"
    assert msgs[-1]["content"][-1] == {"type": "text", "text": "The firewall rejected your proposal"}
    check_anthropic_history(msgs)


def test_anthropic_feedback_after_a_plain_reply_is_a_new_user_message(anthropic_client):
    client, fake = anthropic_client([[a_text("done")], [a_text("again")]])
    client.start(SYSTEM, USER, TOOL_SPECS)
    client.next_turn()
    client.add_user("try again")
    client.next_turn()
    assert fake.sent[-1]["messages"][-1] == {"role": "user", "content": "try again"}
    check_anthropic_history(fake.sent[-1]["messages"])


def test_anthropic_default_model_comes_from_env(monkeypatch, anthropic_client):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test-model")
    client, _ = anthropic_client([])
    assert client.model == "claude-test-model"


# ---- OpenAI ------------------------------------------------------------------------------------------------
class FakeOpenAI:
    def __init__(self, messages_out):
        self.out = list(messages_out)
        self.sent = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.sent.append(copy.deepcopy(kw))
        return SimpleNamespace(choices=[SimpleNamespace(message=self.out.pop(0))])


def o_call(call_id, tool, args):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=tool, arguments=args))


def o_msg(content=None, calls=None):
    return SimpleNamespace(content=content, tool_calls=calls)


@pytest.fixture
def openai_client(monkeypatch):
    def build(replies):
        fake = FakeOpenAI(replies)
        monkeypatch.setattr("openai.OpenAI", lambda *a, **k: fake)
        return OpenAIClient(), fake

    return build


def test_openai_start_builds_function_tools(openai_client):
    client, fake = openai_client([o_msg("ok")])
    client.start(SYSTEM, USER, TOOL_SPECS)
    client.next_turn()
    sent = fake.sent[0]
    assert sent["messages"][0] == {"role": "system", "content": SYSTEM}
    assert sent["messages"][1] == {"role": "user", "content": USER}
    assert all(t["type"] == "function" and "parameters" in t["function"] for t in sent["tools"])


def test_openai_full_loop_answers_every_tool_call(openai_client):
    client, fake = openai_client([
        o_msg(None, [o_call("c1", "lookup_contact", '{"name": "Rahul"}'),
                     o_call("c2", "lookup_contact", '{"name": "Acme"}')]),
        o_msg("done"),
    ])
    client.start(SYSTEM, USER, TOOL_SPECS)
    turn = client.next_turn()
    assert turn.tool_calls == [ToolCall("c1", "lookup_contact", {"name": "Rahul"}),
                               ToolCall("c2", "lookup_contact", {"name": "Acme"})]
    client.add_tool_results([(c, f"out {c.id}") for c in turn.tool_calls])
    client.next_turn()
    msgs = fake.sent[-1]["messages"]
    assistant = next(m for m in msgs if m["role"] == "assistant")
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert {tc["id"] for tc in assistant["tool_calls"]} == {m["tool_call_id"] for m in tool_msgs} == {"c1", "c2"}


def test_openai_bad_json_arguments_become_empty_args(openai_client):
    client, _ = openai_client([o_msg(None, [o_call("c1", "propose_tx", "{not json")])])
    client.start(SYSTEM, USER, TOOL_SPECS)
    assert client.next_turn().tool_calls == [ToolCall("c1", "propose_tx", {})]


def test_openai_feedback_is_a_plain_user_message(openai_client):
    client, fake = openai_client([o_msg("hi"), o_msg("retry")])
    client.start(SYSTEM, USER, TOOL_SPECS)
    client.next_turn()
    client.add_user("rejected")
    client.next_turn()
    assert fake.sent[-1]["messages"][-1] == {"role": "user", "content": "rejected"}


# ---- make_client ----------------------------------------------------------------------------------------------
def test_make_client_without_any_key_explains_what_to_do():
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        make_client()


def test_make_client_picks_the_provider_with_a_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert isinstance(make_client(), OpenAIClient)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert isinstance(make_client(), AnthropicClient)  # Anthropic wins when both are set


def test_make_client_respects_llm_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert isinstance(make_client(), OpenAIClient)
    assert isinstance(make_client("anthropic"), AnthropicClient)


def test_make_client_rejects_unknown_provider():
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        make_client("gemini")


# ---- ScriptedLLM ---------------------------------------------------------------------------------------------------
def test_scripted_llm_replays_turns_then_finishes():
    s = ScriptedLLM([LLMTurn(text="a"), LLMTurn(text="b")])
    assert not s.active
    s.start(SYSTEM, USER, [])
    assert s.active and s.next_turn().text == "a" and s.next_turn().text == "b"
    assert "finished" in s.next_turn().text and not s.next_turn().tool_calls


def test_base_client_methods_are_abstract():
    for fn, args in [("start", ("", "", [])), ("next_turn", ()), ("add_tool_results", ([],)), ("add_user", ("",))]:
        with pytest.raises(NotImplementedError):
            getattr(LLMClient(), fn)(*args)


def test_module_exposes_the_expected_surface():
    assert {"LLMClient", "AnthropicClient", "OpenAIClient", "ScriptedLLM", "make_client"} <= set(dir(llm))
    json.dumps([t for t in TOOL_SPECS])  # specs must be plain JSON
