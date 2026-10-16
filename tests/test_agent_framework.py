"""Runs the real Microsoft Agent Framework ``Agent`` code path offline with a fake chat client."""

from __future__ import annotations

import asyncio

import pytest

af = pytest.importorskip("agent_framework")

from agent_framework import Agent, BaseChatClient, ChatResponse, Message  # noqa: E402

from app.config import Settings  # noqa: E402
from app.providers import VoiceRequest, VoiceService  # noqa: E402
from app.providers.agent_framework_provider import AgentFrameworkProvider  # noqa: E402


def req():
    return VoiceRequest(
        task="commentary",
        key="goal",
        facts={"player": "A"},
        language="es",
        mode="casual",
        fallback_text="fallback line",
        significance=0.9,
    )


class FakeChatClient(BaseChatClient):
    """Stands in for Microsoft Foundry so the real Agent Framework path runs offline."""

    def __init__(self, reply="**¡Golazo** de prueba!"):
        super().__init__()
        self.reply = reply
        self.calls = 0

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls += 1

        async def respond():
            return ChatResponse(
                messages=[Message(role="assistant", contents=[self.reply])], response_id=f"fake-{self.calls}"
            )

        return respond()


def test_agent_framework_provider_runs_real_agent_with_fake_client():
    client = FakeChatClient()
    provider = AgentFrameworkProvider(
        "foundry", Settings(provider="foundry", foundry_model="test-model"), client=client
    )
    text = asyncio.run(provider.voice(req()))
    assert text == "¡Golazo de prueba!"
    assert client.calls == 1
    assert isinstance(provider._agents["commentary"], Agent)
    assert provider.name == "foundry:test-model"


def test_agent_framework_provider_inside_voice_service_falls_back_on_garbage():
    provider = AgentFrameworkProvider(
        "foundry", Settings(foundry_model="m"), client=FakeChatClient(reply="   ")
    )
    res = asyncio.run(VoiceService(provider, retries=1).voice(req()))
    assert res.fallback_used and res.text == "fallback line"
