"""Tests for Module 16 — BIM/IFC Import.

Injects a fake parser so ifcopenshell isn't needed. Mocks NetBox via
httpx.MockTransport.
"""

import json

import httpx
import pytest

from src.bim_import import (
    CommitCounts,
    ParsedDesign,
    ParsedEntity,
    commit_to_netbox,
    parse_design,
)


# --- parse_design ------------------------------------------------------------


def _entity(guid, name="d", slug="cm2000", power_source_guid=None, rack="A1", pos=1):
    return ParsedEntity(
        ifc_guid=guid, name=name, device_type_slug=slug,
        site="DC1", rack=rack, position=pos,
        power_source_guid=power_source_guid,
    )


def test_parse_design_resolves_known_power_sources():
    entities = [
        _entity("g1", power_source_guid="g_pdu"),
        _entity("g2", power_source_guid="g_pdu"),
        _entity("g_pdu"),
    ]

    def parser(_path):
        return entities

    design = parse_design("anywhere.ifc", parser=parser)

    assert len(design.devices) == 3
    assert ("g1", "g_pdu") in design.power_connections
    assert ("g2", "g_pdu") in design.power_connections


def test_parse_design_drops_dangling_power_source_references():
    entities = [
        _entity("g1", power_source_guid="ghost-not-in-file"),
    ]

    def parser(_path):
        return entities

    design = parse_design("x.ifc", parser=parser)

    assert design.power_connections == []
    assert len(design.devices) == 1


# --- commit_to_netbox --------------------------------------------------------


def _design():
    return ParsedDesign(
        devices=[
            _entity("g1", name="cm2000-A3", power_source_guid="g_pdu"),
            _entity("g_pdu", name="pdu-A3", slug="apc-pdu"),
        ],
        power_connections=[("g1", "g_pdu")],
    )


async def test_commit_creates_new_devices_and_power_connection():
    posts = []
    patches = []
    cables = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/dcim/devices/":
            return httpx.Response(200, json={"results": []})  # not yet in NetBox
        if request.method == "POST" and request.url.path == "/api/dcim/devices/":
            body = json.loads(request.content)
            posts.append(body)
            # Assign a fake id based on order.
            new_id = 100 + len(posts)
            return httpx.Response(201, json={"id": new_id, **body})
        if request.method == "POST" and request.url.path == "/api/dcim/cables/":
            cables.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 999})
        if request.method == "PATCH":
            patches.append(json.loads(request.content))
            return httpx.Response(200, json={})
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        counts = await commit_to_netbox(_design(), "http://nb", "tok", client=c)

    assert counts.devices_created == 2
    assert counts.devices_updated == 0
    assert counts.connections_created == 1
    assert counts.errors == []
    assert len(posts) == 2
    assert len(cables) == 1
    # Cable links the powered device to the source.
    assert cables[0]["termination_a_id"] in {"101", "102"}
    assert cables[0]["termination_b_id"] in {"101", "102"}


async def test_commit_updates_existing_device_via_patch():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/dcim/devices/":
            guid = request.url.params.get("cf_ifc_guid")
            return httpx.Response(200, json={"results": [{"id": 999, "name": "old", "ifc_guid": guid}]})
        if request.method == "PATCH":
            return httpx.Response(200, json={})
        if request.method == "POST" and request.url.path == "/api/dcim/cables/":
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        counts = await commit_to_netbox(_design(), "http://nb", "tok", client=c)

    assert counts.devices_updated == 2
    assert counts.devices_created == 0
    assert counts.errors == []


async def test_commit_collects_errors_per_device_without_aborting():
    state = {"posts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/dcim/devices/":
            return httpx.Response(200, json={"results": []})
        if request.method == "POST" and request.url.path == "/api/dcim/devices/":
            state["posts"] += 1
            # Fail the first device, succeed the second.
            if state["posts"] == 1:
                return httpx.Response(500, text="boom")
            return httpx.Response(201, json={"id": 200})
        if request.method == "POST" and request.url.path == "/api/dcim/cables/":
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        counts = await commit_to_netbox(_design(), "http://nb", "tok", client=c)

    assert counts.devices_created == 1
    assert len(counts.errors) == 1
    # Connection skipped because one endpoint failed.
    assert counts.connections_created == 0


async def test_commit_skips_connection_when_endpoint_missing():
    design = ParsedDesign(
        devices=[_entity("g1", power_source_guid="g_pdu")],
        power_connections=[("g1", "g_pdu")],  # g_pdu is not in devices
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"results": []})
        if request.method == "POST" and request.url.path == "/api/dcim/devices/":
            return httpx.Response(201, json={"id": 100})
        if request.method == "POST" and request.url.path == "/api/dcim/cables/":
            raise AssertionError("should not create cable when one endpoint missing")
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        counts = await commit_to_netbox(design, "http://nb", "tok", client=c)

    assert counts.connections_created == 0
    assert counts.devices_created == 1
