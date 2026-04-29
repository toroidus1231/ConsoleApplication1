"""Module 17: PDF-to-Config-Context Pipeline.

Takes a manufacturer PDF, runs the text through Anthropic's Claude with the
Config Context schema as system prompt, parses + validates the JSON the
model returns, and returns a (config_context, validation_errors) pair.

⚠ Per spec: LLM extraction is not 100% reliable. The pipeline is a starting
point, not a final product. Validation is intentionally strict — anything
the model produces that doesn't match the schema goes into validation_errors
so a human can review before the config is loaded into NetBox.

The PDF reader (pdfplumber), the LLM client (Anthropic SDK), and the
NetBox client (httpx) are all dependency-injected so unit tests work
without any of those installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


PDFTextReader = Callable[[str | Path], str]
LLMCompleter = Callable[[str, str], Awaitable[str]]  # (system_prompt, user_text) → JSON string


def extract_text_pdfplumber(pdf_path: str | Path) -> str:
    """Production-grade PDF text extractor using pdfplumber.

    Handles multi-page PDFs and joins page text with double newlines so
    section boundaries are preserved for the LLM.
    """
    import pdfplumber  # noqa: PLC0415 — optional dep at this point

    parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
    return "\n\n".join(parts)


async def llm_anthropic(system_prompt: str, user_text: str, *, model: str = "claude-sonnet-4-5") -> str:
    """Production LLM caller using the Anthropic SDK.

    Reads ANTHROPIC_API_KEY from the environment. Raises RuntimeError if
    the key is unset (so the caller can decide whether to fall back to
    a fixture in test environments).
    """
    import os  # noqa: PLC0415

    import anthropic  # noqa: PLC0415

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.AsyncAnthropic()
    msg = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
    )
    return "".join(block.text for block in msg.content if hasattr(block, "text"))


SYSTEM_PROMPT = """You are extracting a Modbus/BACnet/SNMP/Redfish device Config Context
from a manufacturer PDF. Output exactly one JSON object matching this schema:

{
  "protocol": "modbus_tcp" | "bacnet_ip" | "snmp" | "redfish" | "rest_api" | "iec61850" | "nvml",
  "connection": {
    "port": int,
    "unit_id": int (optional, Modbus only),
    "byte_order": "big" | "little" (optional),
    "word_order": "big" | "little" (optional),
    "timeout_seconds": int (optional, default 5)
  },
  "poll_interval_seconds": int (5, 30, 60, or 300),
  "registers": [               // Modbus
    {"name": str, "address": int, "count": int, "function_code": 3 | 4,
     "data_type": "uint16" | "int16" | "uint32" | "int32" | "float32" |
                  "float64" | "boolean" | "bitmap",
     "scale": float (optional, default 1.0),
     "unit": str}
  ],
  "objects": [...]              // BACnet, when applicable
  "oids": [...]                 // SNMP, when applicable
  "active_tests": [...]         // optional
}

Output ONLY the JSON. No prose, no Markdown fences. If a field is unknown
omit it rather than guessing.
"""


@dataclass
class PipelineResult:
    config_context: dict | None
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_llm_response: str = ""


VALID_PROTOCOLS = {
    "modbus_tcp", "bacnet_ip", "snmp", "redfish", "rest_api", "iec61850", "nvml",
}
VALID_DATA_TYPES = {
    "uint16", "int16", "uint32", "int32", "float32", "float64", "boolean", "bitmap",
}
VALID_FUNCTION_CODES = {3, 4}
VALID_POLL_INTERVALS = {5, 30, 60, 300}


async def extract_config_context(
    pdf_path: str | Path,
    *,
    pdf_reader: PDFTextReader,
    llm: LLMCompleter,
) -> PipelineResult:
    """Run the pipeline end to end. Returns the parsed config + validation."""
    text = pdf_reader(pdf_path)
    if not text.strip():
        return PipelineResult(
            config_context=None,
            validation_errors=["PDF contained no extractable text"],
        )

    raw = await llm(SYSTEM_PROMPT, text)
    try:
        parsed = _parse_llm_json(raw)
    except json.JSONDecodeError as e:
        return PipelineResult(
            config_context=None,
            validation_errors=[f"LLM returned invalid JSON: {e}"],
            raw_llm_response=raw,
        )

    errors, warnings = validate(parsed)
    return PipelineResult(
        config_context=parsed if not errors else None,
        validation_errors=errors,
        warnings=warnings,
        raw_llm_response=raw,
    )


def validate(cfg: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors mean don't deploy; warnings are
    informational."""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(cfg, dict):
        return [f"top-level value is not an object: {type(cfg).__name__}"], []

    protocol = cfg.get("protocol")
    if protocol not in VALID_PROTOCOLS:
        errors.append(f"unknown protocol: {protocol!r}")

    poll_interval = cfg.get("poll_interval_seconds")
    if poll_interval is not None and poll_interval not in VALID_POLL_INTERVALS:
        warnings.append(
            f"unusual poll_interval_seconds={poll_interval} "
            f"(expected one of {sorted(VALID_POLL_INTERVALS)})"
        )

    if protocol == "modbus_tcp":
        errors.extend(_validate_modbus(cfg))

    elif protocol == "bacnet_ip":
        if not cfg.get("objects"):
            errors.append("bacnet_ip config must have at least one object")

    elif protocol == "snmp":
        if not cfg.get("oids"):
            errors.append("snmp config must have at least one OID")

    return errors, warnings


def _validate_modbus(cfg: dict) -> list[str]:
    errors: list[str] = []
    registers = cfg.get("registers")
    if not registers or not isinstance(registers, list):
        errors.append("modbus_tcp config must have a non-empty registers list")
        return errors

    seen_names: set[str] = set()
    for i, reg in enumerate(registers):
        if not isinstance(reg, dict):
            errors.append(f"registers[{i}]: not an object")
            continue
        name = reg.get("name")
        if not name:
            errors.append(f"registers[{i}]: missing name")
        elif name in seen_names:
            errors.append(f"registers[{i}]: duplicate name {name!r}")
        else:
            seen_names.add(name)

        if not isinstance(reg.get("address"), int):
            errors.append(f"registers[{i}] {name!r}: address must be int")

        dt = reg.get("data_type")
        if dt not in VALID_DATA_TYPES:
            errors.append(f"registers[{i}] {name!r}: unknown data_type {dt!r}")

        fc = reg.get("function_code", 3)
        if fc not in VALID_FUNCTION_CODES:
            errors.append(f"registers[{i}] {name!r}: function_code must be 3 or 4 (got {fc})")

        # Multi-register types need count >= 2.
        count = reg.get("count", 1)
        if dt in {"uint32", "int32", "float32"} and count < 2:
            errors.append(
                f"registers[{i}] {name!r}: {dt} needs count >= 2 (got {count})"
            )
        if dt == "float64" and count < 4:
            errors.append(
                f"registers[{i}] {name!r}: float64 needs count >= 4 (got {count})"
            )

    return errors


def _parse_llm_json(raw: str) -> Any:
    """Parse the LLM's response into JSON. Tolerates Markdown fences if present."""
    s = raw.strip()
    if s.startswith("```"):
        # Strip triple-backtick fence.
        s = s.split("\n", 1)[-1]
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
    return json.loads(s)
