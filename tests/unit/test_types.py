"""Shape + default-value tests for shared dataclasses."""

from src.types import (
    AttestationRecord,
    AttestedRecord,
    DeviceInfo,
    Event,
    PollResult,
    PunchListItem,
    TestRequest,
    TestResult,
)


def test_device_info_round_trip():
    d = DeviceInfo(
        device_id="dev-1",
        name="CM2000-A3",
        primary_ip="10.0.0.5",
        device_type_slug="cm2000",
        config_context={"protocol": "modbus_tcp"},
        protocol="modbus_tcp",
        site="DC1",
        rack="A3",
        position=10,
    )
    assert d.protocol == d.config_context["protocol"]


def test_poll_result_defaults_empty_errors():
    p = PollResult(
        device_id="dev-1",
        timestamp_ns=1,
        measurements={"voltage": 480.0},
        raw_bytes={"voltage": "01e0"},
        protocol="modbus_tcp",
        source_ip="10.0.0.5",
        success=True,
    )
    assert p.errors == {}


def test_attested_record_inherits_attestation_record_fields():
    a = AttestedRecord(
        timestamp_ns=1,
        device_id="dev-1",
        measurement="voltage",
        value=480.0,
        raw_bytes="01e0",
        protocol="modbus_tcp",
        source_ip="10.0.0.5",
        worker_id="w1",
    )
    assert isinstance(a, AttestationRecord)
    assert a.sequence == 0
    assert a.hash == ""


def test_test_request_generates_unique_ids():
    a = TestRequest()
    b = TestRequest()
    assert a.test_id != b.test_id
    assert len(a.test_id) == 36  # uuid4 string


def test_test_result_default_status_empty():
    r = TestResult()
    assert r.status == ""
    assert r.restore_success is True
    assert r.precondition_results == []


def test_punch_list_item_default_status_open():
    i = PunchListItem()
    assert i.status == "open"
    assert i.id  # auto uuid


def test_event_default_empty_payload():
    e = Event(event_type="poll_result", timestamp="2026-04-17T00:00:00Z")
    assert e.data == {}
