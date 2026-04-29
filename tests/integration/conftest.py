"""Integration-test fixtures: in-memory MinIO + Influx fakes that look like
the real clients well enough to drive the full pipeline.
"""

import asyncio
import io
from dataclasses import dataclass
from typing import Any

import pytest


class IntegrationS3Error(Exception):
    pass


class FakeMinioObject:
    def __init__(self, name):
        self.object_name = name


class FakeMinioStream:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def close(self):
        pass


class IntegrationMinio:
    """MinIO stand-in with Object Lock semantics:
       - put refuses to overwrite an existing key
       - delete is rejected outright
    """

    def __init__(self):
        self.store: dict[str, bytes] = {}

    def put_object(self, bucket_name, object_name, data, length, content_type="application/octet-stream"):
        if object_name in self.store:
            raise IntegrationS3Error(f"Object Lock: {object_name} already exists")
        body = data.read() if hasattr(data, "read") else bytes(data)
        assert len(body) == length
        self.store[object_name] = body

    def get_object(self, bucket_name, object_name):
        return FakeMinioStream(self.store[object_name])

    def list_objects(self, bucket_name, prefix="", recursive=True):
        return [FakeMinioObject(k) for k in self.store if k.startswith(prefix)]

    def remove_object(self, bucket_name, object_name):
        raise IntegrationS3Error("Object Lock: cannot delete attested record")


class IntegrationInflux:
    """Captures every Influx write so tests can assert what data flowed."""

    def __init__(self):
        self.writes: list[tuple] = []  # (poll_result, test_id)

    async def write_poll(self, result, test_id=None):
        self.writes.append((result, test_id))

    async def close(self):
        pass


@dataclass
class IntegrationConfig:
    facility_name: str = "DC1-Integration"
    netbox_url: str = "http://localhost:8000"
    netbox_token: str = "token"
    influxdb_url: str = "http://localhost:8086"
    influxdb_token: str = "token"
    influxdb_org: str = "commissioning"
    influxdb_bucket: str = "commissioning"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "x"
    minio_secret_key: str = "x"
    minio_bucket: str = "attestation"
    minio_retention_days: int = 2555
    max_concurrent_polls: int = 50
    max_concurrent_tests: int = 5
    api_port: int = 8080
    api_key: str = "test-api-key"
    cors_origins: list = None
    ntp_server: str = "pool.ntp.org"
    log_level: str = "INFO"
    external_apis: dict = None

    def __post_init__(self):
        if self.cors_origins is None:
            self.cors_origins = ["http://localhost:8080"]
        if self.external_apis is None:
            self.external_apis = {}


@pytest.fixture
def integration_config():
    return IntegrationConfig()


@pytest.fixture
def integration_minio():
    return IntegrationMinio()


@pytest.fixture
def integration_influx():
    return IntegrationInflux()
