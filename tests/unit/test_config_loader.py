"""Tests for Module 1 — Config Context Loader.

We mock NetBox via httpx.MockTransport so tests never hit a real API.
"""

import json
from pathlib import Path

import httpx
import pytest

from src.config_loader import load_config_context


SAMPLE_CONFIG = {
    "protocol": "modbus_tcp",
    "connection": {"port": 502, "byte_order": "big", "word_order": "big"},
    "poll_interval_seconds": 30,
    "registers": [
        {
            "name": "frequency_hz",
            "address": 1001,
            "count": 1,
            "function_code": 3,
            "data_type": "uint16",
            "unit": "Hz",
        }
    ],
}


def _write_json(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data))
    return p


def _mock_transport(handler):
    return httpx.MockTransport(handler)


async def test_happy_path_posts_config_context(tmp_path):
    cfg_file = _write_json(tmp_path, "cm2000.json", SAMPLE_CONFIG)

    captured_posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/device-types/":
            assert request.url.params.get("slug") == "cm2000"
            assert request.headers["Authorization"] == "Token tok"
            return httpx.Response(200, json={"results": [{"id": 42, "slug": "cm2000"}]})
        if request.url.path == "/api/extras/config-contexts/":
            body = json.loads(request.content)
            captured_posts.append(body)
            return httpx.Response(201, json={"id": 7, **body})
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=_mock_transport(handler), base_url="http://netbox:8080"
    ) as client:
        result = await load_config_context(
            "http://netbox:8080", "tok", cfg_file, "cm2000", client=client
        )

    assert result["id"] == 7
    assert len(captured_posts) == 1
    post = captured_posts[0]
    assert post["device_types"] == [42]
    assert post["data"] == SAMPLE_CONFIG
    assert post["is_active"] is True
    assert post["name"] == "cm2000-config"


async def test_device_type_not_found_raises(tmp_path):
    cfg_file = _write_json(tmp_path, "cm2000.json", SAMPLE_CONFIG)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/device-types/":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=_mock_transport(handler), base_url="http://netbox:8080"
    ) as client:
        with pytest.raises(ValueError, match="cm2000"):
            await load_config_context(
                "http://netbox:8080", "tok", cfg_file, "cm2000", client=client
            )


async def test_missing_json_file_raises(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError):
        await load_config_context("http://nb", "tok", missing, "cm2000")


async def test_invalid_json_file_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    with pytest.raises(json.JSONDecodeError):
        await load_config_context("http://nb", "tok", bad, "cm2000")


async def test_non_2xx_from_netbox_propagates(tmp_path):
    cfg_file = _write_json(tmp_path, "cm2000.json", SAMPLE_CONFIG)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/device-types/":
            return httpx.Response(200, json={"results": [{"id": 1}]})
        return httpx.Response(500, text="boom")

    async with httpx.AsyncClient(
        transport=_mock_transport(handler), base_url="http://netbox:8080"
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await load_config_context(
                "http://netbox:8080", "tok", cfg_file, "cm2000", client=client
            )
