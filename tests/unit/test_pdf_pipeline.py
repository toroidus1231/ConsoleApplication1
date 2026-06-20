"""Tests for Module 17 — PDF-to-Config-Context Pipeline.

The pipeline's two heavy/external seams — PDF text extraction (pdfplumber) and
the LLM call (anthropic + network key) — are injected as fakes here, so these
tests run with neither dependency installed and with no API key. We exercise the
pure validator, the JSON extractor, and the orchestrator's happy path /
malformed-output path / commit-default behavior.
"""

import json

import httpx
import pytest

from src.pdf_pipeline import (
    parse_llm_json,
    pdf_to_config_context,
    validate_config_context,
)


# A valid CM2000-style read Config Context (spec §2.1): registers carry
# name/address/data_type/unit; protocol + connection present.
CM2000_CONTEXT = {
    "protocol": "modbus_tcp",
    "connection": {"port": 502, "unit_id": 1, "byte_order": "big", "word_order": "big"},
    "registers": [
        {
            "name": "frequency_hz",
            "address": 1001,
            "count": 1,
            "function_code": 3,
            "data_type": "uint16",
            "scale": 0.1,
            "unit": "Hz",
        },
        {
            "name": "voltage_v",
            "address": 32020,
            "count": 2,
            "function_code": 3,
            "data_type": "float32",
            "scale": 1.0,
            "unit": "V",
        },
    ],
}


def _fake_extractor(text: str = "CM2000 datasheet text"):
    """Build a fake text_extractor that records the path it was given."""
    calls: list[str] = []

    def extractor(pdf_path: str) -> str:
        calls.append(pdf_path)
        return text

    extractor.calls = calls
    return extractor


def _fake_llm(raw: str):
    """Build a fake async llm returning a canned string and recording its args."""
    calls: list[dict] = []

    async def llm(text: str, *, api_key: str = "", model: str = "x") -> str:
        calls.append({"text": text, "api_key": api_key, "model": model})
        return raw

    llm.calls = calls
    return llm


# --------------------------------------------------------------------------- #
# validate_config_context — pure
# --------------------------------------------------------------------------- #


def test_valid_cm2000_context_has_no_errors():
    errors, warnings = validate_config_context(CM2000_CONTEXT)
    assert errors == []
    # Every register supplies unit and scale, connection present -> no warnings.
    assert warnings == []


def test_bad_protocol_is_an_error():
    cc = {**CM2000_CONTEXT, "protocol": "carrier_pigeon"}
    errors, _ = validate_config_context(cc)
    assert any("protocol" in e and "carrier_pigeon" in e for e in errors)


def test_missing_protocol_is_an_error():
    cc = {k: v for k, v in CM2000_CONTEXT.items() if k != "protocol"}
    errors, _ = validate_config_context(cc)
    assert any("protocol is required" in e for e in errors)


def test_registers_not_a_list_is_an_error():
    cc = {**CM2000_CONTEXT, "registers": {"name": "oops"}}
    errors, _ = validate_config_context(cc)
    assert any("registers must be a list" in e for e in errors)


def test_register_missing_data_type_is_an_error():
    cc = {
        "protocol": "modbus_tcp",
        "connection": {},
        "registers": [{"name": "x", "address": 1, "unit": "Hz", "scale": 1.0}],
    }
    errors, _ = validate_config_context(cc)
    assert any("data_type" in e and "missing" in e for e in errors)


def test_register_invalid_data_type_is_an_error():
    cc = {
        "protocol": "modbus_tcp",
        "connection": {},
        "registers": [
            {
                "name": "x",
                "address": 1,
                "data_type": "hexquadruple",
                "unit": "Hz",
                "scale": 1.0,
            }
        ],
    }
    errors, _ = validate_config_context(cc)
    assert any("invalid data_type" in e and "hexquadruple" in e for e in errors)


def test_register_address_not_int_is_an_error():
    cc = {
        "protocol": "modbus_tcp",
        "connection": {},
        "registers": [
            {
                "name": "x",
                "address": "1001",  # string, not int
                "data_type": "uint16",
                "unit": "Hz",
                "scale": 1.0,
            }
        ],
    }
    errors, _ = validate_config_context(cc)
    assert any("address must be an int" in e for e in errors)


def test_register_missing_address_is_an_error():
    cc = {
        "protocol": "modbus_tcp",
        "connection": {},
        "registers": [{"name": "x", "data_type": "uint16", "unit": "Hz", "scale": 1.0}],
    }
    errors, _ = validate_config_context(cc)
    assert any("missing address" in e for e in errors)


def test_missing_unit_and_scale_are_warnings_not_errors():
    cc = {
        "protocol": "modbus_tcp",
        "connection": {},
        "registers": [{"name": "x", "address": 1, "data_type": "uint16"}],
    }
    errors, warnings = validate_config_context(cc)
    assert errors == []
    assert any("missing unit" in w for w in warnings)
    assert any("missing scale" in w for w in warnings)


def test_missing_connection_is_a_warning():
    cc = {
        "protocol": "modbus_tcp",
        "registers": [
            {"name": "x", "address": 1, "data_type": "uint16", "unit": "Hz", "scale": 1.0}
        ],
    }
    errors, warnings = validate_config_context(cc)
    assert errors == []
    assert any("connection is missing" in w for w in warnings)


# --------------------------------------------------------------------------- #
# parse_llm_json
# --------------------------------------------------------------------------- #


def test_parse_plain_json():
    raw = json.dumps(CM2000_CONTEXT)
    assert parse_llm_json(raw) == CM2000_CONTEXT


def test_parse_json_fenced():
    raw = "Here is the config context:\n```json\n" + json.dumps(CM2000_CONTEXT) + "\n```\n"
    assert parse_llm_json(raw) == CM2000_CONTEXT


def test_parse_json_plain_fence_no_language():
    raw = "```\n" + json.dumps(CM2000_CONTEXT) + "\n```"
    assert parse_llm_json(raw) == CM2000_CONTEXT


def test_parse_json_embedded_in_prose():
    raw = (
        "Sure! Based on the datasheet, the register map is "
        + json.dumps(CM2000_CONTEXT)
        + " — let me know if you need adjustments."
    )
    assert parse_llm_json(raw) == CM2000_CONTEXT


def test_parse_no_json_raises_valueerror():
    with pytest.raises(ValueError):
        parse_llm_json("I'm sorry, I could not read that PDF at all.")


# --------------------------------------------------------------------------- #
# pdf_to_config_context — orchestration (fakes injected)
# --------------------------------------------------------------------------- #


async def test_happy_path_returns_context_with_no_errors():
    extractor = _fake_extractor("CM2000 datasheet text")
    llm = _fake_llm(json.dumps(CM2000_CONTEXT))

    result = await pdf_to_config_context(
        "cm2000.pdf",
        "cm2000",
        text_extractor=extractor,
        llm=llm,
        api_key="sk-test",
    )

    assert result["config_context"] == CM2000_CONTEXT
    assert result["validation_errors"] == []
    assert result["warnings"] == []
    assert result["committed"] is False
    # Pipeline wired text -> llm correctly.
    assert extractor.calls == ["cm2000.pdf"]
    assert llm.calls[0]["text"] == "CM2000 datasheet text"
    assert llm.calls[0]["api_key"] == "sk-test"


async def test_fenced_llm_output_is_parsed_and_validated():
    extractor = _fake_extractor()
    fenced = "```json\n" + json.dumps(CM2000_CONTEXT) + "\n```"
    llm = _fake_llm(fenced)

    result = await pdf_to_config_context(
        "cm2000.pdf", "cm2000", text_extractor=extractor, llm=llm
    )

    assert result["config_context"] == CM2000_CONTEXT
    assert result["validation_errors"] == []


async def test_malformed_llm_output_raises_valueerror():
    extractor = _fake_extractor()
    llm = _fake_llm("I could not find a register table in this document.")

    with pytest.raises(ValueError):
        await pdf_to_config_context(
            "cm2000.pdf", "cm2000", text_extractor=extractor, llm=llm
        )


async def test_commit_default_leaves_committed_false_and_does_not_touch_netbox():
    extractor = _fake_extractor()
    llm = _fake_llm(json.dumps(CM2000_CONTEXT))

    # No netbox creds, commit defaults to False -> NetBox is never called.
    result = await pdf_to_config_context(
        "cm2000.pdf", "cm2000", text_extractor=extractor, llm=llm
    )

    assert result["committed"] is False


async def test_invalid_context_is_returned_with_errors_not_committed():
    extractor = _fake_extractor()
    bad = {
        "protocol": "modbus_tcp",
        "connection": {},
        "registers": [{"name": "x", "address": "nope", "data_type": "uint16"}],
    }
    llm = _fake_llm(json.dumps(bad))

    # Even with commit=True + creds, errors block the NetBox write.
    result = await pdf_to_config_context(
        "bad.pdf",
        "cm2000",
        text_extractor=extractor,
        llm=llm,
        netbox_url="http://netbox:8080",
        netbox_token="tok",
        commit=True,
    )

    assert result["validation_errors"]  # non-empty
    assert result["committed"] is False


async def test_commit_true_loads_into_netbox_via_module_1():
    extractor = _fake_extractor()
    llm = _fake_llm(json.dumps(CM2000_CONTEXT))

    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/device-types/":
            return httpx.Response(200, json={"results": [{"id": 42, "slug": "cm2000"}]})
        if request.url.path == "/api/extras/config-contexts/":
            body = json.loads(request.content)
            captured.append(body)
            return httpx.Response(201, json={"id": 7, **body})
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://netbox:8080"
    ) as nb_client:
        result = await pdf_to_config_context(
            "cm2000.pdf",
            "cm2000",
            text_extractor=extractor,
            llm=llm,
            netbox_url="http://netbox:8080",
            netbox_token="tok",
            commit=True,
            netbox_client=nb_client,
        )

    assert result["committed"] is True
    assert result["validation_errors"] == []
    assert len(captured) == 1
    # Module 1 attached the validated context to the resolved device type.
    assert captured[0]["device_types"] == [42]
    assert captured[0]["data"] == CM2000_CONTEXT
