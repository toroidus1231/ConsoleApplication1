"""Tests for the platform.yml loader.

Covers: happy path against the shipped example, default fill-in, and missing
required-field validation.
"""

from pathlib import Path

import pytest
import yaml

from src.config import load_config_from_path, _from_raw


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPO_ROOT / "config" / "platform.yml"


def _minimal_config() -> dict:
    return {
        "facility": {"name": "DC1", "scan_subnets": ["10.0.0.0/24"]},
        "netbox": {"url": "http://nb", "token": "tok"},
        "influxdb": {"url": "http://flux", "token": "tok", "org": "org"},
        "minio": {"endpoint": "minio:9000", "access_key": "a", "secret_key": "b"},
        "api": {"key": "apikey"},
    }


def test_load_example_config_parses():
    cfg = load_config_from_path(EXAMPLE_CONFIG)
    assert cfg.facility_name == "DC1-Ashburn"
    assert cfg.scan_subnets == ["10.0.0.0/24", "10.0.1.0/24"]
    assert cfg.netbox_url.startswith("http://")
    assert cfg.minio_retention_days == 2555
    assert cfg.max_concurrent_polls == 50
    assert cfg.max_concurrent_tests == 5
    assert cfg.api_port == 8080
    assert "http://localhost:8080" in cfg.cors_origins


def test_defaults_applied_when_optional_keys_missing():
    cfg = _from_raw(_minimal_config())
    assert cfg.influxdb_bucket == "commissioning"
    assert cfg.minio_bucket == "attestation"
    assert cfg.minio_retention_days == 2555
    assert cfg.max_concurrent_polls == 50
    assert cfg.max_concurrent_tests == 5
    assert cfg.api_port == 8080
    assert cfg.ntp_server == "pool.ntp.org"
    assert cfg.log_level == "INFO"
    assert cfg.external_apis == {}


@pytest.mark.parametrize(
    "drop_section,drop_key",
    [
        ("facility", "name"),
        ("netbox", "url"),
        ("netbox", "token"),
        ("influxdb", "url"),
        ("influxdb", "token"),
        ("influxdb", "org"),
        ("minio", "endpoint"),
        ("minio", "access_key"),
        ("minio", "secret_key"),
        ("api", "key"),
    ],
)
def test_missing_required_key_raises(drop_section, drop_key):
    raw = _minimal_config()
    raw[drop_section].pop(drop_key)
    with pytest.raises(ValueError, match=f"{drop_section}.{drop_key}"):
        _from_raw(raw)


def test_example_config_is_valid_yaml():
    # Guards against broken YAML sneaking into the committed example.
    with open(EXAMPLE_CONFIG) as f:
        yaml.safe_load(f)
