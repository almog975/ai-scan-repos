import json
import os
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SCHEMA_FILES = ["report.schema.json", "finding.schema.json", "checkpoint.schema.json", "progress.schema.json"]
SPEC_FILES = ["review-api.openapi.yaml", "gateway.openapi.yaml"]
BASE_URL_ENV = {"review-api.openapi.yaml": "REVIEW_API_BASE_URL", "gateway.openapi.yaml": "GATEWAY_BASE_URL"}


def load_schema(name):
    return json.loads((CONTRACTS_DIR / name).read_text())


@pytest.fixture(scope="session")
def registry():
    """Registry resolving both $id URLs and bare relative refs (e.g. 'finding.schema.json')."""
    reg = Registry()
    for name in SCHEMA_FILES:
        res = Resource.from_contents(load_schema(name))
        reg = reg.with_resource(res.contents["$id"], res).with_resource(name, res)
    return reg


@pytest.fixture(scope="session")
def validator_for(registry):
    def _make(name):
        return Draft202012Validator(
            load_schema(name), registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER
        )
    return _make


def load_spec(name):
    return yaml.safe_load((CONTRACTS_DIR / name).read_text())
