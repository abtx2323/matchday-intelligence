"""Microsoft Agent Framework provider backed by Microsoft Foundry or Azure OpenAI.

Authentication is keyless: ``DefaultAzureCredential`` (managed identity in Azure Container
Apps, ``az login`` locally). No API keys are read or stored by this project.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .base import ProviderError, VoiceRequest, sanitize_model_text

log = logging.getLogger(__name__)

LANG_NAMES = {"en": "English", "es": "Spanish", "fr": "French"}

_COMMON_RULES = (
    "This is a fictional, synthetic football match: every club and player is invented. "
    "Use ONLY the facts provided in the JSON. Never invent players, scores, minutes or numbers, "
    "never mention real clubs, players, leagues or broadcasters. No emojis, hashtags, markdown, "
    "quotes or URLs. Reply with the final text only."
)

INSTRUCTIONS = {
    "commentary": (
        "You are the Storyteller agent in a live broadcast graphics pipeline. You voice one "
        "on-screen commentary line from structured match facts. " + _COMMON_RULES
    ),
    "insight": (
        "You are the Explainer agent in a live broadcast graphics pipeline. You explain WHY a "
        "moment matters (control vs chaos, pressure shifts, momentum, chance quality), citing "
        "the evidence numbers you are given. " + _COMMON_RULES
    ),
    "recap": (
        "You are the Storyteller agent writing a full-time match recap for a studio or "
        "streaming audience. Write 3-5 sentences. " + _COMMON_RULES
    ),
}

STYLE = {
    "analyst": "Audience: data-hungry analyst. Be precise, tactical and include the key numbers.",
    "casual": "Audience: casual fan. Be warm, short, plain language, at most one number.",
}


def _build_client(kind: str, settings: Any) -> Any:
    try:
        from azure.identity.aio import DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise ProviderError(
            "azure-identity is not installed (pip install -r requirements-azure.txt)"
        ) from exc

    credential = DefaultAzureCredential()
    if kind == "foundry":
        try:
            from agent_framework.foundry import FoundryChatClient
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("agent-framework-foundry is not installed") from exc
        if not settings.foundry_project_endpoint or not settings.foundry_model:
            raise ProviderError("FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL must be set")
        return FoundryChatClient(
            project_endpoint=settings.foundry_project_endpoint,
            model=settings.foundry_model,
            credential=credential,
        )
    if kind == "azure_openai":
        try:
            from agent_framework.openai import OpenAIChatCompletionClient
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("agent-framework-openai is not installed") from exc
        if not settings.azure_openai_endpoint or not settings.azure_openai_deployment:
            raise ProviderError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT must be set")
        return OpenAIChatCompletionClient(
            model=settings.azure_openai_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
            credential=credential,
        )
    raise ProviderError(f"unknown provider kind {kind!r}")


class AgentFrameworkProvider:
    """Voices text with Microsoft Agent Framework ``Agent`` objects, one per agent role."""

    def __init__(
        self, kind: str, settings: Any, client: Any | None = None, agent_factory: Any | None = None
    ) -> None:
        self.kind = kind
        model = settings.foundry_model if kind == "foundry" else settings.azure_openai_deployment
        self.name = f"{kind}:{model or 'unset'}"
        self._settings = settings
        self._client = client
        self._agent_factory = agent_factory
        self._agents: dict[str, Any] = {}

    def _agent(self, task: str) -> Any:
        if task in self._agents:
            return self._agents[task]
        if self._agent_factory is not None:
            agent = self._agent_factory(task, INSTRUCTIONS[task])
        else:
            from agent_framework import Agent

            if self._client is None:
                self._client = _build_client(self.kind, self._settings)
            agent = Agent(
                self._client,
                INSTRUCTIONS[task],
                name=f"mdi-{task}",
                description=f"Matchday Intelligence {task} voice",
            )
        self._agents[task] = agent
        return agent

    async def voice(self, request: VoiceRequest) -> str:
        task = request.task if request.task in INSTRUCTIONS else "commentary"
        prompt = (
            f"Language: {LANG_NAMES.get(request.language, 'English')}.\n"
            f"{STYLE.get(request.mode, STYLE['casual'])}\n"
            f"Maximum {request.max_words} words.\n"
            f"Moment type: {request.key}.\n"
            f"Facts (JSON): {json.dumps(request.facts, ensure_ascii=False, default=str)}\n"
            f"Reference line (rephrase naturally, keep every fact true): {request.fallback_text}"
        )
        response = await self._agent(task).run(prompt)
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            raise ProviderError("agent returned no text")
        return sanitize_model_text(text, request.max_words)
