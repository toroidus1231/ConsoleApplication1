"""Module 17: PDF-to-Config-Context Pipeline.

Turns a manufacturer equipment PDF into a platform Config Context (a register
map, spec §2.1) by extracting the PDF's text, asking the Anthropic API to emit
a Config Context JSON matching the schema, validating that JSON against the
schema, and (optionally) loading it into NetBox via Module 1
(``config_loader.load_config_context``).

CAUTION — per spec §2.1: an LLM-extracted register map is a *draft*, never an
authority. Addresses, scales, byte/word order, and units transcribed by the
model MUST be human-validated against the manufacturer's register manual before
the resulting Config Context is trusted for commissioning. ``commit=True`` only
loads a context that passed schema validation with zero errors; warnings (and a
human review) are still expected.

Heavy/optional dependencies — ``pdfplumber`` (PDF text) and ``anthropic`` (LLM
API, also needs a network key) — are NOT both reliably installed and are NEVER
imported at module top-level. Each is imported lazily inside its own default
implementation, so this module imports cleanly with neither installed. Tests
inject fakes for the text extractor and the LLM and therefore never import
either dependency or hit the network.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from .config_loader import load_config_context


# Schema constants (spec §2.1).
ALLOWED_PROTOCOLS = {
    "modbus_tcp",
    "modbus_rtu",
    "bacnet_ip",
    "snmp",
    "nvml",
    "redfish",
}

ALLOWED_DATA_TYPES = {
    "uint16",
    "int16",
    "uint32",
    "int32",
    "float32",
    "float64",
    "boolean",
    "bitmap",
}


# The schema text embedded in the LLM prompt. Kept verbatim from spec §2.1 so the
# model emits exactly what ``validate_config_context`` checks for.
CONFIG_CONTEXT_SCHEMA_PROMPT = """\
You are extracting a register map from a manufacturer equipment datasheet into a
JSON "Config Context" for a data-center commissioning platform.

Return ONE JSON object and nothing else, matching this schema:

{
  "protocol": one of ["modbus_tcp", "modbus_rtu", "bacnet_ip", "snmp", "nvml", "redfish"],
  "connection": { ... protocol-specific connection params (e.g. port, unit_id,
                  byte_order, word_order) ... },
  "registers": [
    {
      "name": str,            # required — short machine name, e.g. "frequency_hz"
      "address": int,         # required — register/object address
      "count": int,           # number of 16-bit registers to read
      "function_code": 3 | 4, # Modbus function code (3=holding, 4=input)
      "data_type": one of ["uint16","int16","uint32","int32","float32","float64","boolean","bitmap"],
      "scale": float,         # multiply raw value by this
      "unit": str             # engineering unit, e.g. "Hz", "V", "A"
    }
  ]
}

Required per register: name, address (an integer), and data_type (from the
allowed set). protocol is required. Only output JSON parseable by a strict JSON
parser — no prose, no markdown fences, no comments.
"""


def extract_text(pdf_path: str) -> str:
    """Extract and concatenate all page text from a PDF.

    ``pdfplumber`` is a heavy optional dependency, so it is imported lazily here
    — only when no text extractor is injected. Tests always inject a fake text
    extractor and therefore never import pdfplumber. (Not exercised by tests.)
    """
    import pdfplumber  # noqa: PLC0415 — lazy: pdfplumber is a heavy optional dep

    parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


async def call_llm(
    text: str,
    *,
    api_key: str,
    model: str = "claude-3-5-sonnet-20241022",
) -> str:
    """Ask the Anthropic API to convert datasheet ``text`` into a Config Context.

    Returns the raw LLM response string (expected to be a JSON object). The
    ``anthropic`` SDK requires a network key and is not reliably installed, so it
    is imported lazily here — only when no ``llm`` is injected. Tests always
    inject a fake async ``llm`` and therefore never import anthropic or make a
    network call. (Not exercised by tests.)
    """
    import anthropic  # noqa: PLC0415 — lazy: anthropic needs a network key + SDK

    client = anthropic.AsyncAnthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    message = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=CONFIG_CONTEXT_SCHEMA_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "Extract the register map from this equipment datasheet text "
                    "and return the Config Context JSON described in the system "
                    "prompt.\n\n"
                    f"{text}"
                ),
            }
        ],
    )
    return "".join(block.text for block in message.content if block.type == "text")


def parse_llm_json(raw: str) -> dict:
    """Robustly extract a single JSON object from an LLM response.

    Handles a bare JSON object, a ```json ...``` (or plain ``` ... ```) fenced
    block, and a JSON object embedded in surrounding prose. Raises a clear
    ``ValueError`` if no JSON object can be found or parsed.
    """
    if raw is None or not raw.strip():
        raise ValueError("No JSON object found in LLM response: response was empty")

    # 1) Fenced code block (```json ... ``` or ``` ... ```).
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass  # fall through to brace scanning

    # 2) Whole string is JSON.
    stripped = raw.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 3) JSON object embedded in prose — scan for the first balanced {...} that
    #    parses, tracking string literals so braces inside strings don't confuse
    #    the depth count.
    for start in (m.start() for m in re.finditer(r"\{", raw)):
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except json.JSONDecodeError:
                        break  # try the next opening brace
    raise ValueError("No JSON object found in LLM response")


def validate_config_context(cc: dict) -> tuple[list[str], list[str]]:
    """Validate a Config Context against the read schema (spec §2.1). PURE.

    Returns ``(errors, warnings)``.

    Errors (a structurally invalid context):
      - ``protocol`` missing or not in the allowed set.
      - ``registers`` is not a list.
      - any register: missing ``name``; missing ``data_type``; ``data_type`` not
        in the allowed set; missing ``address``; ``address`` not an int.

    Warnings (loadable, but worth a human's attention):
      - ``connection`` missing.
      - any register missing ``unit``.
      - any register missing ``scale``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(cc, dict):
        return ["Config Context must be a JSON object"], warnings

    protocol = cc.get("protocol")
    if protocol is None:
        errors.append("protocol is required")
    elif protocol not in ALLOWED_PROTOCOLS:
        errors.append(
            f"protocol '{protocol}' is not allowed "
            f"(must be one of {sorted(ALLOWED_PROTOCOLS)})"
        )

    if "connection" not in cc:
        warnings.append("connection is missing")

    registers = cc.get("registers")
    if not isinstance(registers, list):
        errors.append("registers must be a list")
        return errors, warnings

    for idx, reg in enumerate(registers):
        # A non-dict register can't carry any of the required fields.
        if not isinstance(reg, dict):
            errors.append(f"register[{idx}] must be an object")
            continue

        label = reg.get("name", f"index {idx}")

        if "name" not in reg:
            errors.append(f"register[{idx}] is missing name")

        if "data_type" not in reg:
            errors.append(f"register '{label}' is missing data_type")
        elif reg["data_type"] not in ALLOWED_DATA_TYPES:
            errors.append(
                f"register '{label}' has invalid data_type '{reg['data_type']}' "
                f"(must be one of {sorted(ALLOWED_DATA_TYPES)})"
            )

        if "address" not in reg:
            errors.append(f"register '{label}' is missing address")
        elif not isinstance(reg["address"], int) or isinstance(reg["address"], bool):
            errors.append(
                f"register '{label}' address must be an int, got "
                f"{type(reg['address']).__name__}"
            )

        if "unit" not in reg:
            warnings.append(f"register '{label}' is missing unit")
        if "scale" not in reg:
            warnings.append(f"register '{label}' is missing scale")

    return errors, warnings


async def pdf_to_config_context(
    pdf_path: str,
    device_type_slug: str,
    *,
    text_extractor: Callable[[str], str] = extract_text,
    llm: Callable[..., Awaitable[str]] = call_llm,
    api_key: str = "",
    netbox_url: str | None = None,
    netbox_token: str | None = None,
    commit: bool = False,
    netbox_client: object | None = None,
) -> dict:
    """Run the full PDF -> Config Context pipeline.

    Pipeline:
      1. ``text = text_extractor(pdf_path)`` — when the default (sync) extractor
         is used it is wrapped in ``asyncio.to_thread`` so PDF parsing doesn't
         block the event loop; an injected extractor is called directly.
      2. ``raw = await llm(text, api_key=...)`` — the LLM emits Config Context
         JSON (as a string).
      3. ``cc = parse_llm_json(raw)`` — extract the JSON object.
      4. ``errors, warnings = validate_config_context(cc)``.
      5. If ``commit`` and there are no errors and NetBox creds are supplied,
         write ``cc`` to a temp ``.json`` and load it into NetBox via Module 1.

    Returns ``{"config_context", "validation_errors", "warnings", "committed"}``.

    Raises whatever ``parse_llm_json`` raises (``ValueError``) on malformed LLM
    output — the caller decides how to surface that.
    """
    if text_extractor is extract_text:
        text = await asyncio.to_thread(extract_text, pdf_path)
    else:
        text = text_extractor(pdf_path)

    raw = await llm(text, api_key=api_key)
    cc = parse_llm_json(raw)
    errors, warnings = validate_config_context(cc)

    committed = False
    if (
        commit
        and not errors
        and netbox_url is not None
        and netbox_token is not None
    ):
        # load_config_context reads from disk (spec §2), so spill the validated
        # context to a temp .json named after the device type — the file stem
        # becomes the loaded context's name hint.
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / f"{device_type_slug}.json"
            json_path.write_text(json.dumps(cc, indent=2))
            await load_config_context(
                netbox_url,
                netbox_token,
                json_path,
                device_type_slug,
                client=netbox_client,
            )
        committed = True

    return {
        "config_context": cc,
        "validation_errors": errors,
        "warnings": warnings,
        "committed": committed,
    }
