"""Gemini client wrapper.

Recordings are keyed on a hash of the full conversation, so a cassette only
matches a byte-identical exchange.

Set TRIAGE_MODE=live | replay | record.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

MODEL = os.environ.get("TRIAGE_MODEL", "gemini-2.5-flash")
CASSETTE_DIR = Path(__file__).resolve().parent.parent / "cassettes"


def _mode() -> str:
    return os.environ.get("TRIAGE_MODE", "live").lower()


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _json_schema_to_genai(schema: dict) -> types.Schema:
    type_map = {
        "object": types.Type.OBJECT,
        "string": types.Type.STRING,
        "integer": types.Type.INTEGER,
        "number": types.Type.NUMBER,
        "boolean": types.Type.BOOLEAN,
        "array": types.Type.ARRAY,
    }
    kwargs: dict[str, Any] = {"type": type_map.get(schema.get("type", "string"), types.Type.STRING)}
    if "description" in schema:
        kwargs["description"] = schema["description"]
    if "enum" in schema:
        kwargs["enum"] = schema["enum"]
    if schema.get("type") == "object":
        kwargs["properties"] = {
            k: _json_schema_to_genai(v) for k, v in schema.get("properties", {}).items()
        }
        if schema.get("required"):
            kwargs["required"] = schema["required"]
    if schema.get("type") == "array" and "items" in schema:
        kwargs["items"] = _json_schema_to_genai(schema["items"])
    return types.Schema(**kwargs)


def build_tool_config(mcp_tools) -> types.Tool:
    """Convert MCP tool definitions into a Gemini tool declaration."""
    declarations = [
        types.FunctionDeclaration(
            name=t.name,
            description=t.description or "",
            parameters=_json_schema_to_genai(t.input_schema),
        )
        for t in mcp_tools
    ]
    return types.Tool(function_declarations=declarations)


def _fingerprint(contents, system_instruction: str) -> str:
    payload = json.dumps(
        {
            "system": system_instruction,
            "contents": [c.model_dump(exclude_none=True) for c in contents],
            "model": MODEL,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


class ModelClient:
    def __init__(self) -> None:
        _load_env_file()
        self.mode = _mode()
        self._client = None
        if self.mode != "replay":
            key = os.environ.get("GEMINI_API_KEY", "")
            if not key or key == "your-key-here":
                raise RuntimeError(
                    "GEMINI_API_KEY is not set. Put it in .env, or run with TRIAGE_MODE=replay."
                )
            self._client = genai.Client(api_key=key)

    def generate(self, *, contents, system_instruction: str, tool: types.Tool):
        key = _fingerprint(contents, system_instruction)
        cassette = CASSETTE_DIR / f"{key}.json"

        if self.mode == "replay":
            if not cassette.exists():
                raise RuntimeError(
                    f"No cassette for this conversation state ({key}). "
                    "Re-record with: TRIAGE_MODE=record uv run triage evals"
                )
            return types.GenerateContentResponse.model_validate_json(cassette.read_text())

        response = self._client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=[tool],
                temperature=0,
                max_output_tokens=2048,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        if self.mode == "record":
            CASSETTE_DIR.mkdir(exist_ok=True)
            cassette.write_text(response.model_dump_json(exclude_none=True))
        return response
