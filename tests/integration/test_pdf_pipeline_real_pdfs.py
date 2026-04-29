"""Integration test for Module 17 against the actual spec PDFs we shipped
in docs/. Validates the production pdfplumber extractor and the full
pipeline plumbing using a deterministic fake LLM.

The Anthropic-backed end-to-end test is skipped unless ANTHROPIC_API_KEY
is set — that test is documented in
test_pdf_pipeline_with_anthropic_when_key_set().
"""

import json
import os
from pathlib import Path

import pytest

from src.pdf_pipeline import (
    extract_config_context,
    extract_text_pdfplumber,
)


REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"


@pytest.fixture
def real_pdf_path() -> Path:
    p = DOCS / "commissioning-spec-v5.pdf"
    if not p.exists():
        pytest.skip(f"missing {p}")
    return p


def test_extract_text_pdfplumber_pulls_substantive_text(real_pdf_path):
    """Real pdfplumber pulls the actual spec content out of the PDF."""
    text = extract_text_pdfplumber(real_pdf_path)
    assert len(text) > 5_000, f"only {len(text)} chars extracted"
    # Spot-check known phrases from the spec.
    assert "Modbus" in text or "modbus" in text.lower()
    assert "Config Context" in text or "config_context" in text
    assert "register" in text.lower()


def test_extract_text_handles_multi_page(real_pdf_path):
    """Page-break markers (double newline between pages) are preserved."""
    text = extract_text_pdfplumber(real_pdf_path)
    assert "\n\n" in text


async def test_pipeline_uses_real_extractor_with_fake_llm(real_pdf_path):
    """End-to-end: real PDF text extraction → fake LLM → validation. The
    LLM is fake but the rest of the pipeline (extractor, JSON parsing,
    validation) is the real production code."""
    captured = {}

    async def fake_llm(system, user):
        captured["system"] = system
        captured["user"] = user
        return json.dumps({
            "protocol": "modbus_tcp",
            "connection": {"port": 502, "byte_order": "big", "word_order": "big"},
            "poll_interval_seconds": 30,
            "registers": [
                {"name": "frequency_hz", "address": 1001, "count": 1,
                 "function_code": 3, "data_type": "uint16",
                 "scale": 0.01, "unit": "Hz"},
            ],
        })

    result = await extract_config_context(
        real_pdf_path,
        pdf_reader=extract_text_pdfplumber,
        llm=fake_llm,
    )

    assert result.validation_errors == []
    assert result.config_context["protocol"] == "modbus_tcp"
    # The real PDF text was passed to the LLM.
    assert "Modbus" in captured["user"] or "modbus" in captured["user"].lower()
    assert "Config Context" in captured["system"]


def test_extract_text_on_missing_file_raises():
    with pytest.raises(Exception):
        extract_text_pdfplumber(REPO / "no-such-file.pdf")


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; this test hits the real Anthropic API",
)
async def test_pipeline_with_anthropic_when_key_set(real_pdf_path):
    """Real Anthropic API call using a real PDF.

    Cost-aware: only runs when ANTHROPIC_API_KEY is set in the env. CI
    should set it as a secret if you want this in the regular run.

    Even with a real LLM, the result is validated against our schema —
    this protects against the model returning slightly off shapes.
    """
    from src.pdf_pipeline import llm_anthropic

    result = await extract_config_context(
        real_pdf_path,
        pdf_reader=extract_text_pdfplumber,
        llm=llm_anthropic,
    )
    # The shipped spec PDF isn't a manufacturer datasheet so the model
    # may legitimately decline to produce a config. Either way validation
    # errors should surface clearly.
    if result.config_context is None:
        assert result.validation_errors  # explained why
    else:
        assert "protocol" in result.config_context
