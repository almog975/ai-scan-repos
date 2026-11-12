"""Validate report/finding/checkpoint JSON Schemas against fixtures.

Fixture layout: fixtures/{valid,invalid}/<schema>__<case>.json where <schema> is
report | finding | checkpoint.
"""
import json

import pytest
from jsonschema import Draft202012Validator

from conftest import FIXTURES_DIR, SCHEMA_FILES, load_schema


def _cases(kind):
    return sorted((FIXTURES_DIR / kind).glob("*.json"))


def _schema_for(path):
    return f"{path.name.split('__')[0]}.schema.json"


@pytest.mark.parametrize("name", SCHEMA_FILES)
def test_schema_is_valid_draft_2020_12(name):
    Draft202012Validator.check_schema(load_schema(name))


@pytest.mark.parametrize("name", SCHEMA_FILES)
def test_every_schema_has_fixtures(name):
    prefix = name.split(".")[0] + "__"
    for kind in ("valid", "invalid"):
        assert any(p.name.startswith(prefix) for p in _cases(kind)), f"no {kind} fixture for {name}"


@pytest.mark.parametrize("path", _cases("valid"), ids=lambda p: p.stem)
def test_valid_fixture_accepted(path, validator_for):
    errors = list(validator_for(_schema_for(path)).iter_errors(json.loads(path.read_text())))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize("path", _cases("invalid"), ids=lambda p: p.stem)
def test_invalid_fixture_rejected(path, validator_for):
    assert not validator_for(_schema_for(path)).is_valid(json.loads(path.read_text()))
