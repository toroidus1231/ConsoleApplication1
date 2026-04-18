"""Platform configuration loader.

Loads /app/config/platform.yml into a PlatformConfig dataclass. See contracts
spec §1.4 for the full field list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class PlatformConfig:
    facility_name: str
    scan_subnets: List[str]
    netbox_url: str
    netbox_token: str
    influxdb_url: str
    influxdb_token: str
    influxdb_org: str
    influxdb_bucket: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_retention_days: int
    max_concurrent_polls: int
    max_concurrent_tests: int
    api_port: int
    api_key: str
    cors_origins: List[str]
    ntp_server: str
    log_level: str
    external_apis: dict = field(default_factory=dict)


def load_config(path: str = "/app/config/platform.yml") -> PlatformConfig:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return _from_raw(raw)


def _from_raw(raw: dict) -> PlatformConfig:
    facility = raw.get("facility", {})
    netbox = raw.get("netbox", {})
    influx = raw.get("influxdb", {})
    minio = raw.get("minio", {})
    workers = raw.get("workers", {})
    orch = raw.get("orchestrator", {})
    api = raw.get("api", {})
    ntp = raw.get("ntp", {})
    logging_cfg = raw.get("logging", {})

    required = {
        "facility.name": facility.get("name"),
        "netbox.url": netbox.get("url"),
        "netbox.token": netbox.get("token"),
        "influxdb.url": influx.get("url"),
        "influxdb.token": influx.get("token"),
        "influxdb.org": influx.get("org"),
        "minio.endpoint": minio.get("endpoint"),
        "minio.access_key": minio.get("access_key"),
        "minio.secret_key": minio.get("secret_key"),
        "api.key": api.get("key"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(f"Missing required config keys: {missing}")

    return PlatformConfig(
        facility_name=facility["name"],
        scan_subnets=facility.get("scan_subnets", []),
        netbox_url=netbox["url"],
        netbox_token=netbox["token"],
        influxdb_url=influx["url"],
        influxdb_token=influx["token"],
        influxdb_org=influx["org"],
        influxdb_bucket=influx.get("bucket", "commissioning"),
        minio_endpoint=minio["endpoint"],
        minio_access_key=minio["access_key"],
        minio_secret_key=minio["secret_key"],
        minio_bucket=minio.get("bucket", "attestation"),
        minio_retention_days=int(minio.get("retention_days", 2555)),
        max_concurrent_polls=int(workers.get("max_concurrent_polls", 50)),
        max_concurrent_tests=int(orch.get("max_concurrent_tests", 5)),
        api_port=int(api.get("port", 8080)),
        api_key=api["key"],
        cors_origins=api.get("cors_origins", ["http://localhost:8080"]),
        ntp_server=ntp.get("server", "pool.ntp.org"),
        log_level=logging_cfg.get("level", "INFO"),
        external_apis=raw.get("external_apis", {}),
    )


def load_config_from_path(path: Path | str) -> PlatformConfig:
    return load_config(str(path))
