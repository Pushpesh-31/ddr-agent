"""Tool-use loop, generator-based for incremental UI rendering.

The Streamlit UI iterates the generator returned by `run_agent(...)` and routes each event
to its placeholder. The CLI smoke test (`scripts/run_agent.py`) does the same in plain text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from agent.llm_client import EndTurn, Event, LLMClient, TextDelta, ToolResult, ToolUse, get_client
from agent.tool_implementations import dispatch_tool

AGENT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = AGENT_DIR / "system_prompt.md"
TOOLS_PATH = AGENT_DIR / "tools.json"

MAX_TURNS = 12  # Safety cap. The triage workflow needs ~6 turns; more than 12 means the agent
                # is looping — better to abort cleanly than burn tokens.


def load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def load_tools() -> list[dict]:
    return json.loads(TOOLS_PATH.read_text(encoding="utf-8"))


def run_agent(
    user_message: str,
    mode: str = "triage",
    client: LLMClient | None = None,
) -> Iterator[Event]:
    """Drive the tool-use loop. Yields TextDelta, ToolUse, ToolResult, EndTurn events.

    `mode` is prepended to the user message so the system prompt's mode-routing kicks in;
    it is not a separate API parameter."""
    client = client or get_client()
    system = load_system_prompt()
    tools = load_tools()

    framed = f"[mode: {mode}]\n\n{user_message}"
    messages: list[dict] = [{"role": "user", "content": framed}]

    for turn in range(MAX_TURNS):
        last_end_turn: EndTurn | None = None
        pending_tool_uses: list[ToolUse] = []

        for event in client.stream_with_tools(system, tools, messages):
            if isinstance(event, ToolUse):
                pending_tool_uses.append(event)
            elif isinstance(event, EndTurn):
                last_end_turn = event
            yield event

        if last_end_turn is None:
            return

        # Append the assistant turn verbatim so the next call sees the full conversation.
        messages.append({"role": "assistant", "content": last_end_turn.raw_content})

        if last_end_turn.stop_reason != "tool_use":
            return

        # Dispatch each tool call and emit results.
        tool_result_blocks: list[dict] = []
        for tu in pending_tool_uses:
            try:
                output = dispatch_tool(tu.name, tu.input)
                is_error = isinstance(output, dict) and "error" in output
            except Exception as exc:
                output = {"error": "tool_exception", "tool": tu.name, "detail": str(exc)}
                is_error = True
            yield ToolResult(tool_use_id=tu.id, name=tu.name, output=output, is_error=is_error)
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(output),
                **({"is_error": True} if is_error else {}),
            })

        messages.append({"role": "user", "content": tool_result_blocks})

    yield EndTurn(stop_reason="max_turns_exceeded")
