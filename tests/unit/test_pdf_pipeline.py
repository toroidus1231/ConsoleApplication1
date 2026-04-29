"""Tests for Module 17 — PDF-to-Config-Context Pipeline.

Injects a fake pdf_reader and LLM completer. No pdfplumber, no Anthropic SDK,
no network.
"""

import json

import pytest

from src.pdf_pipeline import (
    PipelineResult,
    extract_config_context,
    validate,
)


# --- validate() unit tests ---------------------------------------------------


def test_validate_modbus_happy_config_returns_no_errors():
    cfg = {
        "protocol": "modbus_tcp",
        "connection": {"port": 502, "unit_id": 1, "byte_order": "big", "word_order": "big"},
        "poll_interval_seconds": 30,
        "registers": [
            {"name": "freq_hz", "address": 1001, "count": 1, "function_code": 3,
             "data_type": "uint16", "unit": "Hz"},
            {"name": "voltage", "address": 32020, "count": 2, "function_code": 3,
             "data_type": "float32", "unit": "V"},
        ],
    }
    errors, warnings = validate(cfg)
    assert errors == []
    assert warnings == []


def test_validate_unknown_protocol_is_error():
    errors, _ = validate({"protocol": "carrier_pigeon"})
    assert any("unknown protocol" in e for e in errors)


def test_validate_unusual_poll_interval_is_warning_not_error():
    cfg = {
        "protocol": "modbus_tcp", "poll_interval_seconds": 7,
        "registers": [{"name": "x", "address": 1, "count": 1, "data_type": "uint16",
                       "unit": "n", "function_code": 3}],
    }
    errors, warnings = validate(cfg)
    assert errors == []
    assert any("unusual poll_interval_seconds" in w for w in warnings)


def test_validate_modbus_missing_registers_is_error():
    errors, _ = validate({"protocol": "modbus_tcp"})
    assert any("registers list" in e for e in errors)


def test_validate_modbus_duplicate_register_name_is_error():
    cfg = {
        "protocol": "modbus_tcp",
        "registers": [
            {"name": "x", "address": 1, "count": 1, "data_type": "uint16",
             "unit": "n", "function_code": 3},
            {"name": "x", "address": 2, "count": 1, "data_type": "uint16",
             "unit": "n", "function_code": 3},
        ],
    }
    errors, _ = validate(cfg)
    assert any("duplicate name" in e for e in errors)


def test_validate_modbus_unknown_data_type_is_error():
    cfg = {
        "protocol": "modbus_tcp",
        "registers": [
            {"name": "x", "address": 1, "count": 1, "data_type": "bigint64",
             "unit": "n", "function_code": 3},
        ],
    }
    errors, _ = validate(cfg)
    assert any("unknown data_type" in e for e in errors)


def test_validate_modbus_float32_count_must_be_2():
    cfg = {
        "protocol": "modbus_tcp",
        "registers": [
            {"name": "v", "address": 1, "count": 1, "data_type": "float32",
             "unit": "V", "function_code": 3},
        ],
    }
    errors, _ = validate(cfg)
    assert any("float32 needs count >= 2" in e for e in errors)


def test_validate_modbus_float64_count_must_be_4():
    cfg = {
        "protocol": "modbus_tcp",
        "registers": [
            {"name": "v", "address": 1, "count": 2, "data_type": "float64",
             "unit": "V", "function_code": 3},
        ],
    }
    errors, _ = validate(cfg)
    assert any("float64 needs count >= 4" in e for e in errors)


def test_validate_bacnet_must_have_objects():
    errors, _ = validate({"protocol": "bacnet_ip"})
    assert any("at least one object" in e for e in errors)


def test_validate_snmp_must_have_oids():
    errors, _ = validate({"protocol": "snmp"})
    assert any("at least one OID" in e for e in errors)


def test_validate_non_dict_top_level_returns_error():
    errors, _ = validate("not an object")
    assert errors


# --- extract_config_context() integration tests ------------------------------


_GOOD_CONFIG = {
    "protocol": "modbus_tcp",
    "connection": {"port": 502, "unit_id": 1, "byte_order": "big", "word_order": "big"},
    "poll_interval_seconds": 30,
    "registers": [
        {"name": "frequency_hz", "address": 1001, "count": 1, "function_code": 3,
         "data_type": "uint16", "unit": "Hz"},
    ],
}


async def test_pipeline_happy_path():
    def reader(_path):
        return "Schneider CM2000 Power Meter — Modbus TCP register map ..."

    async def llm(system, user):
        return json.dumps(_GOOD_CONFIG)

    result = await extract_config_context("dummy.pdf", pdf_reader=reader, llm=llm)

    assert result.validation_errors == []
    assert result.config_context == _GOOD_CONFIG


async def test_pipeline_strips_markdown_fences():
    def reader(_):
        return "fake pdf text"

    async def llm(system, user):
        return f"```json\n{json.dumps(_GOOD_CONFIG)}\n```"

    result = await extract_config_context("x.pdf", pdf_reader=reader, llm=llm)

    assert result.validation_errors == []
    assert result.config_context == _GOOD_CONFIG


async def test_pipeline_rejects_invalid_json():
    def reader(_):
        return "fake pdf text"

    async def llm(system, user):
        return "this is not json"

    result = await extract_config_context("x.pdf", pdf_reader=reader, llm=llm)

    assert result.config_context is None
    assert any("invalid JSON" in e for e in result.validation_errors)


async def test_pipeline_empty_pdf_short_circuits():
    def reader(_):
        return ""

    async def llm(system, user):
        raise AssertionError("LLM should not be called on empty PDF")

    result = await extract_config_context("x.pdf", pdf_reader=reader, llm=llm)

    assert result.config_context is None
    assert any("no extractable text" in e for e in result.validation_errors)


async def test_pipeline_validation_failure_drops_config():
    bad = {**_GOOD_CONFIG, "protocol": "carrier_pigeon"}

    def reader(_):
        return "fake pdf text"

    async def llm(system, user):
        return json.dumps(bad)

    result = await extract_config_context("x.pdf", pdf_reader=reader, llm=llm)

    assert result.config_context is None
    assert result.validation_errors  # at least one


async def test_pipeline_warnings_dont_block_config():
    cfg = {**_GOOD_CONFIG, "poll_interval_seconds": 7}  # unusual but valid

    def reader(_):
        return "fake"

    async def llm(system, user):
        return json.dumps(cfg)

    result = await extract_config_context("x.pdf", pdf_reader=reader, llm=llm)

    assert result.config_context == cfg  # NOT None
    assert result.validation_errors == []
    assert result.warnings  # at least one
