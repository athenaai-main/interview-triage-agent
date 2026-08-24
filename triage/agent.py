"""The triage agent loop.

Connects to the MCP server, discovers its tools, and runs a tool-calling
conversation with Gemini until the model stops calling tools.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from google.genai import types
from mcp import Client

from .mcp_server import server as mcp_server
from .model import ModelClient, build_tool_config

POLICY = (Path(__file__).resolve().parent / "policy.md").read_text()
MAX_TURNS = 12


@dataclass
class Trace:
    case_id: str
    turns: list[dict] = field(default_factory=list)
    final_text: str = ""
    tool_calls: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def context_chars(self) -> int:
        return sum(len(t.get("result", "")) for t in self.turns if t["kind"] == "tool_result")

    def show(self) -> str:
        lines = [f"=== trace for {self.case_id} ==="]
        for t in self.turns:
            if t["kind"] == "tool_call":
                lines.append(f"  \033[1;34m→\033[0m {t['name']}({json.dumps(t['args'])})")
            elif t["kind"] == "tool_result":
                preview = " ".join(t["result"].split())
                if len(preview) > 240:
                    preview = f"{preview[:240]}…  [{len(t['result']):,} chars total]"
                lines.append(f"    {preview}")
            elif t["kind"] == "text":
                lines.append(f"  \033[1;35mmodel:\033[0m {t['text'].strip()[:600]}")
        if self.error:
            lines.append(f"  \033[1;31mERROR:\033[0m {self.error}")
        lines.append(f"  [{len(self.tool_calls)} tool calls, {self.context_chars:,} chars of tool output]")
        return "\n".join(lines)


def _result_text(outcome) -> str:
    parts = []
    for block in outcome.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts) if parts else json.dumps({"result": "(empty)"})


async def run_case(case_id: str, *, verbose: bool = False) -> Trace:
    trace = Trace(case_id=case_id)

    async with Client(mcp_server) as session:
        listed = await session.list_tools()
        tool = build_tool_config(listed.tools)
        model = ModelClient()

        contents: list[types.Content] = [
            types.Content(
                role="user",
                parts=[types.Part(text=f"Triage case {case_id}. Investigate and record a decision.")],
            )
        ]

        for _ in range(MAX_TURNS):
            response = model.generate(contents=contents, system_instruction=POLICY, tool=tool)
            candidate = response.candidates[0]
            parts = candidate.content.parts or []
            contents.append(candidate.content)

            calls = [p.function_call for p in parts if p.function_call]
            for p in parts:
                if p.text:
                    trace.turns.append({"kind": "text", "text": p.text})
                    trace.final_text = p.text

            if not calls:
                break

            result_parts = []
            for call in calls:
                args = dict(call.args or {})
                trace.tool_calls.append(call.name)
                trace.turns.append({"kind": "tool_call", "name": call.name, "args": args})
                if verbose:
                    print(f"  \033[1;34m→\033[0m {call.name}({json.dumps(args)})")

                try:
                    outcome = await session.call_tool(call.name, args)
                    payload = _result_text(outcome)
                except Exception as exc:
                    payload = json.dumps({"error": f"tool call failed: {exc}"})

                trace.turns.append({"kind": "tool_result", "result": payload})
                if verbose:
                    print(f"    {' '.join(payload.split())[:160]}…  [{len(payload):,} chars]")

                result_parts.append(
                    types.Part.from_function_response(name=call.name, response={"result": payload})
                )

            contents.append(types.Content(role="user", parts=result_parts))
        else:
            trace.error = f"stopped after {MAX_TURNS} turns without a final answer"

    return trace


def run(case_id: str, *, verbose: bool = False) -> Trace:
    return asyncio.run(run_case(case_id, verbose=verbose))
