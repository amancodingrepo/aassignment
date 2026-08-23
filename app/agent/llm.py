"""LLM providers for the tool loop.

The gate and calculators do not care which vendor is used. Gemini is the
default when `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) is set; Anthropic remains
available behind `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from app.tools.registry import schemas_for


class MissingApiKey(RuntimeError):
    pass


class QuotaExhausted(RuntimeError):
    """Raised after every Gemini model in the fallback chain returns 429/404."""


@dataclass
class ToolUse:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ModelTurn:
    text: str
    tool_uses: list[ToolUse] = field(default_factory=list)
    raw: Any = None


def chat_ready() -> bool:
    return bool(_api_key())


def _api_key() -> tuple[str, str] | None:
    gemini = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gemini:
        return "gemini", gemini
    anthropic = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic:
        return "anthropic", anthropic
    return None


def provider_name() -> str:
    pair = _api_key()
    return pair[0] if pair else "none"


# Gemini 2.0 Flash IDs are shut down; the API 404s them. 2.5 Flash is still
# a live endpoint — do not remap it onto 3.6, whose free-tier daily cap is 20.
_RETIRED_GEMINI = {
    "gemini-2.0-flash": "gemini-3.6-flash",
    "gemini-2.0-flash-001": "gemini-3.6-flash",
    "models/gemini-2.0-flash": "gemini-3.6-flash",
}

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

# Free-tier generate_content quotas are per model. After 3.6-flash's 20 RPD
# is gone, sibling Flash/Lite IDs still work until their own caps are hit.
GEMINI_FALLBACK_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.7-flash",
)

_gemini_sticky_model: str | None = None


def resolve_gemini_model(name: str) -> str:
    return _RETIRED_GEMINI.get(name, name)


def model_name() -> str:
    override = os.environ.get("PARCELPILOT_MODEL")
    if override:
        return resolve_gemini_model(override)
    if provider_name() == "gemini":
        return DEFAULT_GEMINI_MODEL
    return "claude-sonnet-5"


def gemini_model_candidates(primary: str | None = None) -> list[str]:
    chosen = resolve_gemini_model(primary or model_name())
    extras = [
        resolve_gemini_model(item.strip())
        for item in os.environ.get("PARCELPILOT_MODEL_FALLBACKS", "").split(",")
        if item.strip()
    ]
    ordered = [chosen, *extras, *GEMINI_FALLBACK_MODELS]
    seen: set[str] = set()
    unique: list[str] = []
    for item in ordered:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def reset_gemini_sticky() -> None:
    global _gemini_sticky_model
    _gemini_sticky_model = None


def _is_model_unavailable(error: BaseException) -> bool:
    code = getattr(error, "status_code", None)
    if code is None:
        code = getattr(error, "code", None)
    if code in (429, 404, "RESOURCE_EXHAUSTED", "NOT_FOUND"):
        return True
    text = str(error)
    return any(
        needle in text
        for needle in ("RESOURCE_EXHAUSTED", "NOT_FOUND", "429", "404")
    )


def _quota_message(tried: list[str], last_error: BaseException | None) -> str:
    models = ", ".join(tried) if tried else model_name()
    hint = ""
    if last_error is not None:
        detail = str(last_error).replace("\n", " ").strip()
        if len(detail) > 280:
            detail = detail[:277] + "..."
        hint = f" Last error: {detail}"
    return (
        "Gemini free-tier quota is exhausted. "
        f"Tried: {models}. "
        "gemini-3.6-flash allows 20 generate_content requests/day on the free "
        "plan; other Flash/Lite models have separate caps. Set "
        "PARCELPILOT_MODEL=gemini-3.5-flash-lite, wait for the daily reset, "
        "or enable billing: https://ai.google.dev/gemini-api/docs/rate-limits"
        f"{hint}"
    )


def public_error_message(error: BaseException) -> str:
    if isinstance(error, (MissingApiKey, QuotaExhausted)):
        return str(error)
    if _is_model_unavailable(error):
        return _quota_message([model_name()], error)
    return str(error)


def run_with_model_fallback(candidates: list[str], call_model) -> ModelTurn:
    """Try each model id. Stick to the first that succeeds for this process."""
    global _gemini_sticky_model
    ordered = list(candidates)
    sticky = _gemini_sticky_model
    if sticky and sticky in ordered:
        ordered = [sticky] + [item for item in ordered if item != sticky]
    tried: list[str] = []
    last_error: BaseException | None = None
    for model in ordered:
        tried.append(model)
        try:
            turn = call_model(model)
        except Exception as error:
            if not _is_model_unavailable(error):
                raise
            last_error = error
            if _gemini_sticky_model == model:
                _gemini_sticky_model = None
            continue
        _gemini_sticky_model = model
        return turn
    raise QuotaExhausted(_quota_message(tried, last_error)) from last_error


def json_schema_to_gemini(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Translate a JSON Schema fragment to Gemini's uppercase type names."""
    if not schema:
        return {"type": "OBJECT", "properties": {}}
    type_name = str(schema.get("type") or "object").lower()
    mapping = {
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
        "object": "OBJECT",
    }
    converted: dict[str, Any] = {"type": mapping.get(type_name, "STRING")}
    if schema.get("description"):
        converted["description"] = schema["description"]
    if "enum" in schema:
        converted["enum"] = list(schema["enum"])
    if type_name == "object":
        converted["properties"] = {
            key: json_schema_to_gemini(value)
            for key, value in (schema.get("properties") or {}).items()
        }
        if schema.get("required"):
            converted["required"] = list(schema["required"])
    if type_name == "array" and schema.get("items"):
        converted["items"] = json_schema_to_gemini(schema["items"])
    return converted


def gemini_tool_declarations(role: str) -> list[dict[str, Any]]:
    declarations = []
    for spec in schemas_for(role):
        parameters = json_schema_to_gemini(spec.get("input_schema") or {"type": "object"})
        declarations.append(
            {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": parameters,
            }
        )
    return declarations


def complete(
    *,
    system: str,
    role: str,
    conversation: list[dict[str, Any]],
) -> ModelTurn:
    pair = _api_key()
    if pair is None:
        raise MissingApiKey(
            "No chat API key is set. Put GEMINI_API_KEY (or ANTHROPIC_API_KEY) "
            "in .env. Ingest, calculators, the gate, signals and confirm still "
            "run without a model."
        )
    name, key = pair
    if name == "gemini":
        return _complete_gemini(key, system, role, conversation)
    return _complete_anthropic(key, system, role, conversation)


def _complete_gemini(
    api_key: str,
    system: str,
    role: str,
    conversation: list[dict[str, Any]],
) -> ModelTurn:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    tools = [types.Tool(function_declarations=gemini_tool_declarations(role))]
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=tools,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0.1,
        max_output_tokens=2048,
    )
    contents = _gemini_contents(conversation)

    def call_model(model: str) -> ModelTurn:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        return _parse_gemini_response(response)

    return run_with_model_fallback(gemini_model_candidates(), call_model)


def _parse_gemini_response(response: Any) -> ModelTurn:
    if not getattr(response, "candidates", None):
        text = getattr(response, "text", None) or ""
        return ModelTurn(text=text)

    content = response.candidates[0].content
    text_parts: list[str] = []
    tool_uses: list[ToolUse] = []
    for index, part in enumerate(content.parts or []):
        if getattr(part, "text", None):
            text_parts.append(part.text)
        call = getattr(part, "function_call", None)
        if call and getattr(call, "name", None):
            args = dict(call.args) if call.args else {}
            tool_uses.append(
                ToolUse(id=f"{call.name}-{index}", name=call.name, args=args)
            )
    return ModelTurn(text="\n".join(text_parts), tool_uses=tool_uses, raw=content)


def _gemini_contents(conversation: list[dict[str, Any]]):
    from google.genai import types

    contents = []
    for message in conversation:
        role = message.get("role")
        if role == "user" and isinstance(message.get("content"), str):
            contents.append(
                types.Content(role="user", parts=[types.Part(text=message["content"])])
            )
        elif role == "assistant":
            if message.get("raw") is not None:
                contents.append(message["raw"])
                continue
            parts = []
            if message.get("content"):
                parts.append(types.Part(text=str(message["content"])))
            for call in message.get("tool_uses") or []:
                parts.append(
                    types.Part.from_function_call(name=call["name"], args=call["args"])
                )
            if parts:
                contents.append(types.Content(role="model", parts=parts))
        elif role == "tool":
            parts = [
                types.Part.from_function_response(
                    name=item["name"],
                    response=_as_dict(item["result"]),
                )
                for item in message.get("results") or []
            ]
            if parts:
                contents.append(types.Content(role="user", parts=parts))
    return contents


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"result": value}


def _complete_anthropic(
    api_key: str,
    system: str,
    role: str,
    conversation: list[dict[str, Any]],
) -> ModelTurn:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    messages = _anthropic_messages(conversation)
    response = client.messages.create(
        model=model_name(),
        max_tokens=2048,
        system=system,
        tools=schemas_for(role),
        messages=messages,
    )
    text_parts = [block.text for block in response.content if block.type == "text"]
    tool_uses = [
        ToolUse(id=block.id, name=block.name, args=dict(block.input or {}))
        for block in response.content
        if block.type == "tool_use"
    ]
    return ModelTurn(
        text="\n".join(text_parts),
        tool_uses=tool_uses,
        raw=response.content,
    )


def _anthropic_messages(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for message in conversation:
        role = message.get("role")
        if role == "user" and isinstance(message.get("content"), str):
            messages.append({"role": "user", "content": message["content"]})
        elif role == "assistant":
            if message.get("raw") is not None:
                messages.append({"role": "assistant", "content": message["raw"]})
                continue
            blocks: list[dict[str, Any]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": str(message["content"])})
            for call in message.get("tool_uses") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call["args"],
                    }
                )
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": item["id"],
                            "content": json.dumps(item["result"], default=str),
                        }
                        for item in message.get("results") or []
                    ],
                }
            )
    return messages
