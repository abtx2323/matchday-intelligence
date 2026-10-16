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


def validate_settings(kind: str, settings: Any) -> None:
    """Check cloud configuration *before* any credential or client is created."""
    if kind == "foundry":
        if not settings.foundry_project_endpoint or not settings.foundry_model:
            raise ProviderError("FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL must be set")
        if not str(settings.foundry_project_endpoint).startswith("https://"):
            raise ProviderError("FOUNDRY_PROJECT_ENDPOINT must be an https URL")
    elif kind == "azure_openai":
        if not settings.azure_openai_endpoint or not settings.azure_openai_deployment:
            raise ProviderError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT must be set")
        if not str(settings.azure_openai_endpoint).startswith("https://"):
            raise ProviderError("AZURE_OPENAI_ENDPOINT must be an https URL")
    else:
        raise ProviderError(f"unknown provider kind {kind!r}")


def _build_client(kind: str, settings: Any) -> tuple[Any, Any]:
    """Returns (client, credential). Configuration is validated first."""
    validate_settings(kind, settings)
    try:
        from azure.identity.aio import DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise ProviderError(
            "azure-identity is not installed (pip install -r requirements-azure.txt)"
        ) from exc
    if kind == "foundry":
        try:
            from agent_framework.foundry import FoundryChatClient
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("agent-framework-foundry is not installed") from exc
        credential = DefaultAzureCredential()
        return FoundryChatClient(
            project_endpoint=settings.foundry_project_endpoint,
            model=settings.foundry_model,
            credential=credential,
        ), credential
    try:
        from agent_framework.openai import OpenAIChatCompletionClient
    except ImportError as exc:  # pragma: no cover
        raise ProviderError("agent-framework-openai is not installed") from exc
    credential = DefaultAzureCredential()
    return OpenAIChatCompletionClient(
        model=settings.azure_openai_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
        credential=credential,
    ), credential


class AgentFrameworkProvider:
    """Voices text with Microsoft Agent Framework ``Agent`` objects, one per agent role.

    Built once per app (in the FastAPI lifespan) and shared by every session.
    """

    def __init__(
        self, kind: str, settings: Any, client: Any | None = None, agent_factory: Any | None = None
    ) -> None:
        self.kind = kind
        self.name = kind  # deliberately excludes the model/deployment name
        self._settings = settings
        self._client = client
        self._credential: Any | None = None
        self._agent_factory = agent_factory
        self._agents: dict[str, Any] = {}

    def validate(self) -> None:
        if self._client is None and self._agent_factory is None:
            validate_settings(self.kind, self._settings)

    async def aclose(self) -> None:
        for obj in (self._client, self._credential):
            close = getattr(obj, "close", None)
            if close is None:
                continue
            try:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # noqa: BLE001 - best-effort shutdown
                log.warning("error while closing %s", type(obj).__name__)
        self._credential = None

    def _agent(self, task: str) -> Any:
        if task in self._agents:
            return self._agents[task]
        if self._agent_factory is not None:
            agent = self._agent_factory(task, INSTRUCTIONS[task])
        else:
            from agent_framework import Agent

            if self._client is None:
                self._client, self._credential = _build_client(self.kind, self._settings)
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
