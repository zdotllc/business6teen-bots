"""Chat tool-loop regression tests: tool calls must always be paired with
tool messages, even when a tool throws — otherwise OpenAI-style providers
reject the conversation with 'insufficient tool messages following tool_calls'.
"""

import asyncio

from agent_company_ai.core.agent import Agent
from agent_company_ai.core.role import Role
from agent_company_ai.core.message_bus import MessageBus
from agent_company_ai.llm.base import LLMMessage, LLMResponse, ToolCall
from agent_company_ai.tools.registry import Tool, ToolRegistry

# Unique tool names so tests never collide with real registered tools.
TOOL_OK = "test_chat_tool_ok"
TOOL_RAISE = "test_chat_tool_raise"


def _register_tools():
    reg = ToolRegistry.get()
    reg.register(Tool(
        name=TOOL_OK,
        description="fake ok tool",
        parameters={"type": "object", "properties": {"mode": {"type": "string"}}},
        func=lambda **kw: "ok-result",
    ))
    def _boom(**kw):
        raise RuntimeError("boom")
    reg.register(Tool(
        name=TOOL_RAISE,
        description="fake raising tool",
        parameters={"type": "object", "properties": {}},
        func=_boom,
    ))


class FakeProvider:
    """Returns a scripted sequence of responses; records every request."""
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.model = "fake-model"

    async def complete(self, messages, tools=None):
        self.requests.append([m for m in messages])
        return self.script.pop(0)


def _make_agent(provider):
    role = Role(
        name="tester", title="Tester", description="test",
        system_prompt="You are a test agent.",
        default_tools=[TOOL_OK, TOOL_RAISE],
    )
    bus = MessageBus()
    return Agent(
        name="Tester", role=role, provider=provider, message_bus=bus,
        db=None, company_name="Test Co",
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_chat_executes_tools_and_pairs_messages():
    _register_tools()
    provider = FakeProvider([
        LLMResponse(
            content="Let me check.",
            tool_calls=[ToolCall(id="c1", name=TOOL_OK, arguments={"mode": "x"})],
            usage={},
        ),
        LLMResponse(content="Final answer.", tool_calls=None, usage={}),
    ])
    agent = _make_agent(provider)

    reply = _run(agent.chat("do it"))
    assert reply == "Final answer."

    # The tool_calls assistant message must be followed by a tool message.
    last_req = provider.requests[-1]
    roles = [m.role for m in last_req]
    for i, m in enumerate(last_req):
        if m.role == "assistant" and m.tool_calls:
            assert i + 1 < len(last_req) and last_req[i + 1].role == "tool"
            assert last_req[i + 1].tool_call_id == "c1"
    # Conversation ends on a plain assistant text message (valid tail).
    assert agent._conversation[-1].role == "assistant"
    assert not agent._conversation[-1].tool_calls


def test_chat_tool_that_raises_still_pairs_and_recovers():
    _register_tools()
    provider = FakeProvider([
        LLMResponse(
            content="",
            tool_calls=[ToolCall(id="c9", name=TOOL_RAISE, arguments={})],
            usage={},
        ),
        LLMResponse(content="Recovered.", tool_calls=None, usage={}),
        # Second chat: provider would 400 if the history had a dangling tail.
        LLMResponse(content="Still fine.", tool_calls=None, usage={}),
    ])
    agent = _make_agent(provider)

    reply1 = _run(agent.chat("first"))
    assert reply1 == "Recovered."

    # The raising tool still got a tool message (with the error), so pairing held.
    last_req = provider.requests[-1]
    tool_msgs = [m for m in last_req if m.role == "tool"]
    assert tool_msgs, "expected a tool message for the raising tool"
    assert "Tool error" in tool_msgs[0].content

    # A second message keeps working — no corrupted history.
    reply2 = _run(agent.chat("second"))
    assert reply2 == "Still fine."


def test_repair_drops_dangling_tool_call_tail():
    _register_tools()
    agent = _make_agent(FakeProvider([LLMResponse(content="ok", tool_calls=None, usage={})]))
    # Simulate a crash that left a dangling assistant tool_calls tail.
    agent._conversation.append(LLMMessage(role="system", content=agent._system_prompt))
    agent._conversation.append(LLMMessage(
        role="assistant", content="", tool_calls=[{"id": "c9", "name": TOOL_RAISE, "arguments": {}}],
    ))
    agent._repair_conversation()
    assert agent._conversation[-1].role == "system"
    assert not agent._conversation[-1].tool_calls

    reply = _run(agent.chat("hello"))
    assert reply == "ok"
