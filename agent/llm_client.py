"""LLM provider abstraction.

Today the only implementation is `AnthropicClient`. The seam exists so that a future
`GeminiClient` (free-tier, for students without Anthropic credits) can drop in without
touching the runner, tools, or UI. See README "Swapping LLM providers."

The runner consumes the iterator of normalized `Event` instances; the client implementation
is responsible for translating its vendor SDK's stream into these events.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict


@dataclass
class EndTurn:
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | other
    # The raw assistant content blocks (provider-native shape) so the runner can append
    # them to `messages` for the next turn. Treat as opaque.
    raw_content: list = field(default_factory=list)


@dataclass
class ToolResult:
    """Emitted by the runner after dispatching a tool. Not produced by the LLM client."""
    tool_use_id: str
    name: str
    output: Any  # JSON-serializable dict (or {"error": ...} on failure)
    is_error: bool = False


Event = TextDelta | ToolUse | EndTurn | ToolResult


class LLMClient(ABC):
    """One method, one promise: stream a single assistant turn as a sequence of events."""

    @abstractmethod
    def stream_with_tools(
        self,
        system: str,
        tools: list[dict],
        messages: list[dict],
    ) -> Iterator[Event]:
        ...


def _read_secret(key: str) -> str | None:
    """Prefer Streamlit secrets when available (Streamlit Cloud + local secrets.toml).
    Fall back to env vars so pytest and `scripts/run_agent.py` work outside Streamlit."""
    try:
        import streamlit as st  # type: ignore

        # Accessing st.secrets outside a Streamlit run raises; guard it.
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key)


class AnthropicClient(LLMClient):
    """Anthropic Messages API, non-streaming under the hood (yields one TextDelta per text
    block and one ToolUse per tool_use block at the end of each turn). Good enough for the
    Streamlit UI's "agent thinking" expander to feel live; upgrade to true token streaming
    if you want token-by-token text rendering."""

    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, model: str | None = None, max_tokens: int = 4096):
        from anthropic import Anthropic

        api_key = _read_secret("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not found. Set it in .streamlit/secrets.toml for local dev "
                "or in the Streamlit Cloud Secrets dashboard for the deployed app."
            )
        self.client = Anthropic(api_key=api_key)
        self.model = model or self.DEFAULT_MODEL
        self.max_tokens = max_tokens

    def stream_with_tools(self, system, tools, messages):
        response = self.client.messages.create(
            model=self.model,
            system=system,
            tools=tools,
            messages=messages,
            max_tokens=self.max_tokens,
        )
        # Convert SDK content blocks into provider-native dicts so the runner can append
        # them to `messages` without importing anthropic.
        raw_content: list[dict] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                raw_content.append({"type": "text", "text": block.text})
                yield TextDelta(text=block.text)
            elif btype == "tool_use":
                raw_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                yield ToolUse(id=block.id, name=block.name, input=dict(block.input))
        yield EndTurn(stop_reason=response.stop_reason or "end_turn", raw_content=raw_content)


def get_client() -> LLMClient:
    provider = (_read_secret("LLM_PROVIDER") or "anthropic").lower()
    if provider == "anthropic":
        return AnthropicClient()
    raise RuntimeError(
        f"Unknown LLM_PROVIDER={provider!r}. "
        "To add a provider, implement an LLMClient subclass and register it in get_client()."
    )
