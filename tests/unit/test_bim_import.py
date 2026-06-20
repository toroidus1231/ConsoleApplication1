"""Tests for Module 16 — BIM/IFC Import.

These tests use NO ifcopenshell (it is not installed) and NO network: the IFC
model is a duck-typed ``FakeModel`` and NetBox is mocked via
``httpx.MockTransport`` (mirroring tests/unit/test_netbox_reader.py).
"""

import importlib

import httpx

from src.bim_import import import_ifc, import_to_netbox, parse_model


class FakeEntity:
    """Minimal stand-in for an ifcopenshell entity.

    Exposes the small, documented attribute surface ``parse_model`` reads:
    ``is_a()``, ``Name``, ``ObjectType``, ``Tag``, ``get_info()`` and a
    ``properties`` dict (flat or nested-by-pset).
    """

    def __init__(
        self,
        ifc_class="IfcDistributionElement",
        name=None,
        object_type=None,
        tag=None,
        properties=None,
        coordinates=None,
    ):
        self._ifc_class = ifc_class
        self.Name = name
        self.ObjectType = object_type
        self.Tag = tag
        self.properties = properties or {}
        if coordinates is not None:
            self.coordinates = coordinates

    def is_a(self):
        return self._ifc_class

    def get_info(self):
        return {"id": id(self), "type": self._ifc_class, "Name": self.Name}


class FakeModel:
    """Duck-typed IFC model exposing ``by_type`` over a fixed entity list."""

    def __init__(self, entities):
        self._entities = entities

    def by_type(self, ifc_class):
        return [e for e in self._entities if e.is_a() == ifc_class]


def _sample_model():
    """~3 devices across 2 racks (A3 in site DC1, B1 in site DC1)."""
    pdu = FakeEntity(
        ifc_class="IfcDistributionElement",
        name="pdu-a3-01",
        object_type="CM2000 PDU",
        tag="T1",
        coordinates={"x": 1.5, "y": 2.0, "z": 0.25},
        properties={
            "Site": "DC1",
            "Rack": "A3",
            "Position": 10,
            "PowerSource": "PANEL-1",
            "Connections": [
                {"target": "switch-a3-01", "type": "power"},
                {"target": "core-sw", "type": "network"},
            ],
        },
    )
    switch = FakeEntity(
        ifc_class="IfcFlowController",
        name="switch-a3-01",
        object_type="EX4300 Switch",
        tag="T2",
        properties={
            "Building": "DC1",
            "Enclosure": "A3",
            "RackUnit": 12,
            "PowerOutputs": ["server-a3-09"],
        },
    )
    server = FakeEntity(
        ifc_class="IfcEnergyConversionDevice",
        name=None,  # forces the "<type>-<tag>" name fallback
        object_type="DGX H100",
        tag="T3",
        properties={
            "Facility": "DC1",
            "Cabinet": "B1",
            "U": 9,
            "Connections": ["pdu-b1-01"],  # bare string -> power
        },
    )
    return FakeModel([pdu, switch, server])


# --------------------------------------------------------------------------- #
# parse_model (pure)                                                          #
# --------------------------------------------------------------------------- #


def test_parse_model_extracts_three_devices_across_two_racks():
    devices = parse_model(_sample_model())

    assert len(devices) == 3
    by_name = {d["name"]: d for d in devices}

    # Name fallback for the entity with no .Name -> "<ObjectType>-<Tag>".
    assert set(by_name) == {"pdu-a3-01", "switch-a3-01", "DGX H100-T3"}

    racks = {d["rack"] for d in devices}
    assert racks == {"A3", "B1"}


def test_parse_model_pdu_payload_shape():
    devices = parse_model(_sample_model())
    pdu = next(d for d in devices if d["name"] == "pdu-a3-01")

    assert pdu == {
        "name": "pdu-a3-01",
        "device_type_slug": "cm2000-pdu",
        "site": "DC1",
        "rack": "A3",
        "position": 10,
        "x": 1.5,
        "y": 2.0,
        "z": 0.25,
        "power_source": "PANEL-1",
        "connections": [
            {"target": "switch-a3-01", "type": "power"},
            {"target": "core-sw", "type": "network"},
        ],
    }


def test_parse_model_reads_alternate_property_keys_and_outputs():
    devices = parse_model(_sample_model())
    switch = next(d for d in devices if d["name"] == "switch-a3-01")

    # Building/Enclosure/RackUnit are alternate keys for site/rack/position.
    assert switch["site"] == "DC1"
    assert switch["rack"] == "A3"
    assert switch["position"] == 12
    # PowerOutputs folds into connections as power links.
    assert switch["connections"] == [{"target": "server-a3-09", "type": "power"}]
    # Coordinates absent -> default 0.0 on every axis.
    assert (switch["x"], switch["y"], switch["z"]) == (0.0, 0.0, 0.0)


def test_parse_model_bare_string_connection_defaults_to_power():
    devices = parse_model(_sample_model())
    server = next(d for d in devices if d["name"] == "DGX H100-T3")

    assert server["device_type_slug"] == "dgx-h100"
    assert server["connections"] == [{"target": "pdu-b1-01", "type": "power"}]


def test_parse_model_deduplicates_entity_in_multiple_types():
    # One entity reported by by_type for two queried classes -> emitted once.
    multi = FakeEntity(ifc_class="IfcFlowController", name="dual", object_type="X")

    class DupModel:
        def by_type(self, ifc_class):
            if ifc_class in ("IfcDistributionElement", "IfcFlowController"):
                return [multi]
            return []

    devices = parse_model(DupModel())
    assert [d["name"] for d in devices] == ["dual"]


def test_parse_model_empty_model_returns_empty_list():
    assert parse_model(FakeModel([])) == []


# --------------------------------------------------------------------------- #
# import_to_netbox (httpx.MockTransport)                                      #
# --------------------------------------------------------------------------- #


def _device(name, slug="cm2000", rack="A3", connections=None):
    return {
        "name": name,
        "device_type_slug": slug,
        "site": "DC1",
        "rack": rack,
        "position": 10,
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "power_source": "PANEL-1",
        "connections": connections or [],
    }


async def test_import_to_netbox_posts_devices_and_connections():
    devices = [
        _device("pdu-a3-01", connections=[{"target": "sw", "type": "power"}]),
        _device("sw-a3-01", connections=[{"target": "core", "type": "network"}]),
    ]

    device_posts: list[dict] = []
    port_posts: list[dict] = []
    feed_posts: list[dict] = []
    next_id = iter(range(100, 200))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token tok"
        body = __import__("json").loads(request.content)
        path = request.url.path
        if path == "/api/dcim/devices/":
            device_posts.append(body)
            return httpx.Response(201, json={"id": next(next_id), **body})
        if path == "/api/dcim/power-ports/":
            port_posts.append(body)
            return httpx.Response(201, json={"id": next(next_id), **body})
        if path == "/api/dcim/power-feeds/":
            feed_posts.append(body)
            return httpx.Response(201, json={"id": next(next_id), **body})
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        result = await import_to_netbox(
            devices, netbox_url="http://nb", netbox_token="tok", client=c
        )

    assert result["devices_committed"] == 2
    assert result["connections_created"] == 2
    assert result["errors"] == []

    # Each device hit the right endpoint with the right body.
    assert [d["name"] for d in device_posts] == ["pdu-a3-01", "sw-a3-01"]
    assert device_posts[0]["device_type"] == {"slug": "cm2000"}
    assert device_posts[0]["rack"] == {"name": "A3"}

    # A power connection creates a port AND a feed; a network one only a port.
    assert len(port_posts) == 2
    assert len(feed_posts) == 1
    assert feed_posts[0]["upstream"] == "sw"


async def test_import_to_netbox_isolates_failing_device():
    devices = [
        _device("good-1", connections=[{"target": "x", "type": "power"}]),
        _device("bad-1"),
        _device("good-2"),
    ]

    next_id = iter(range(100, 200))

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        if request.url.path == "/api/dcim/devices/":
            if body["name"] == "bad-1":
                return httpx.Response(400, text="bad device")
            return httpx.Response(201, json={"id": next(next_id), **body})
        return httpx.Response(201, json={"id": next(next_id), **body})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        result = await import_to_netbox(
            devices, netbox_url="http://nb", netbox_token="tok", client=c
        )

    # The two good devices commit; the bad one is captured, not raised.
    assert result["devices_committed"] == 2
    assert result["connections_created"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["device"] == "bad-1"
    assert "HTTPStatusError" in result["errors"][0]["error"]


async def test_import_to_netbox_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        result = await import_to_netbox(
            [], netbox_url="http://nb", netbox_token="tok", client=c
        )

    assert result == {"devices_committed": 0, "connections_created": 0, "errors": []}


# --------------------------------------------------------------------------- #
# import_ifc orchestration + clean import                                     #
# --------------------------------------------------------------------------- #


async def test_import_ifc_uses_injected_model_and_client():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.path)
        body = __import__("json").loads(request.content)
        return httpx.Response(201, json={"id": 1, **body})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://nb"
    ) as c:
        result = await import_ifc(
            "ignored.ifc",
            netbox_url="http://nb",
            netbox_token="tok",
            client=c,
            model=_sample_model(),
        )

    assert result["devices_committed"] == 3
    # 3 devices were POSTed; load_ifc / ifcopenshell never touched.
    assert captured.count("/api/dcim/devices/") == 3


def test_module_imports_without_ifcopenshell():
    # ifcopenshell is NOT installed in the test env; importing the module must
    # still succeed because the import is lazy (inside load_ifc only).
    import src.bim_import as mod

    importlib.reload(mod)
    assert hasattr(mod, "parse_model")
    assert hasattr(mod, "import_to_netbox")
    assert hasattr(mod, "load_ifc")

    # Sanity: ifcopenshell truly absent, proving laziness is required.
    import pytest

    with pytest.raises(ModuleNotFoundError):
        __import__("ifcopenshell")
