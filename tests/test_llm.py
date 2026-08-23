"""Provider-neutral LLM helpers. No network, no key required."""

from __future__ import annotations

import pytest

from app.agent.llm import (
    ModelTurn,
    QuotaExhausted,
    gemini_model_candidates,
    gemini_tool_declarations,
    json_schema_to_gemini,
    model_name,
    provider_name,
    public_error_message,
    reset_gemini_sticky,
    run_with_model_fallback,
)


def test_json_schema_types_are_uppercased_for_gemini():
    converted = json_schema_to_gemini(
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["cancellation_fee", "service_credit"]},
                "limit": {"type": "integer"},
                "topics": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["kind"],
        }
    )
    assert converted["type"] == "OBJECT"
    assert converted["properties"]["kind"]["type"] == "STRING"
    assert converted["properties"]["kind"]["enum"] == ["cancellation_fee", "service_credit"]
    assert converted["properties"]["limit"]["type"] == "INTEGER"
    assert converted["properties"]["topics"]["type"] == "ARRAY"
    assert converted["properties"]["topics"]["items"]["type"] == "STRING"
    assert converted["required"] == ["kind"]


def test_customer_gemini_tools_omit_account_id():
    declarations = gemini_tool_declarations("customer")
    names = {item["name"] for item in declarations}
    assert {"search_documents", "lookup_orders", "compute", "propose_action"} <= names
    assert "list_signals" not in names
    for item in declarations:
        properties = (item["parameters"].get("properties") or {})
        assert "account_id" not in properties


def test_internal_gemini_tools_include_signals_and_account_id():
    declarations = gemini_tool_declarations("internal")
    by_name = {item["name"]: item for item in declarations}
    assert "list_signals" in by_name
    assert "account_id" in by_name["lookup_orders"]["parameters"]["properties"]


def test_provider_name_without_keys_is_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider_name() == "none"


def test_retired_gemini_ids_map_to_current_flash(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PARCELPILOT_MODEL", raising=False)
    assert model_name() == "gemini-3.6-flash"
    monkeypatch.setenv("PARCELPILOT_MODEL", "gemini-2.0-flash")
    assert model_name() == "gemini-3.6-flash"
    # 2.5 Flash is still a live model with its own free-tier quota.
    monkeypatch.setenv("PARCELPILOT_MODEL", "gemini-2.5-flash")
    assert model_name() == "gemini-2.5-flash"


def test_gemini_candidates_lead_with_primary_then_lite(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PARCELPILOT_MODEL", raising=False)
    monkeypatch.delenv("PARCELPILOT_MODEL_FALLBACKS", raising=False)
    candidates = gemini_model_candidates()
    assert candidates[0] == "gemini-3.6-flash"
    assert "gemini-3.5-flash-lite" in candidates
    assert candidates.index("gemini-3.5-flash-lite") == 1


def test_quota_falls_back_then_sticks(monkeypatch):
    reset_gemini_sticky()
    calls: list[str] = []

    def call_model(model: str) -> ModelTurn:
        calls.append(model)
        if model == "gemini-3.6-flash":
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
                "generate_content_free_tier_requests, limit: 20, "
                "model: gemini-3.6-flash"
            )
        return ModelTurn(text=f"ok:{model}")

    first = run_with_model_fallback(
        ["gemini-3.6-flash", "gemini-3.5-flash-lite"], call_model
    )
    assert first.text == "ok:gemini-3.5-flash-lite"
    second = run_with_model_fallback(
        ["gemini-3.6-flash", "gemini-3.5-flash-lite"], call_model
    )
    assert second.text == "ok:gemini-3.5-flash-lite"
    assert calls == [
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite",
    ]
    reset_gemini_sticky()


def test_all_models_exhausted_raises_readable_error():
    reset_gemini_sticky()

    def call_model(model: str) -> ModelTurn:
        raise RuntimeError(f"429 RESOURCE_EXHAUSTED model: {model}")

    with pytest.raises(QuotaExhausted, match="20 generate_content requests/day"):
        run_with_model_fallback(
            ["gemini-3.6-flash", "gemini-3.5-flash-lite"], call_model
        )
    reset_gemini_sticky()


def test_public_error_message_strips_clienterror_repr():
    error = RuntimeError(
        "429 RESOURCE_EXHAUSTED. You exceeded your current quota, "
        "model: gemini-3.6-flash"
    )
    message = public_error_message(error)
    assert "ClientError" not in message
    assert "gemini-3.6-flash" in message
