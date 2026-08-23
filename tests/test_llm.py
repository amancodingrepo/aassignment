"""Provider-neutral LLM helpers. No network, no key required."""

from __future__ import annotations

from app.agent.llm import gemini_tool_declarations, json_schema_to_gemini, provider_name


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
