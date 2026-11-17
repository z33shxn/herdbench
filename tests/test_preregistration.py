"""The pre-registration document and the frozen config must state exactly the same values."""

import dataclasses
import re
from pathlib import Path

import pytest

from herdbench.config import PREREG

DOC = Path(__file__).resolve().parents[1] / "docs" / "preregistration.md"
ROW = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|\s*([^|]+?)\s*\|")


def doc_values() -> dict[str, str]:
    values = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            values[m.group(1)] = m.group(2)
    return values


def same(field_value, text: str) -> bool:
    if isinstance(field_value, tuple):
        return [str(v) for v in field_value] == [p.strip() for p in text.split(",")] or \
            [float(v) for v in field_value] == [float(p) for p in text.split(",")]
    if isinstance(field_value, bool):
        return str(field_value).lower() == text.lower()
    if isinstance(field_value, int) and text.startswith("0x"):
        return field_value == int(text, 16)
    if isinstance(field_value, (int, float)):
        return float(field_value) == float(text.replace(",", ""))
    return str(field_value) == text


@pytest.mark.parametrize("field", [f.name for f in dataclasses.fields(PREREG)])
def test_every_parameter_is_stated_in_the_document_with_the_same_value(field):
    values = doc_values()
    assert field in values, f"{field} is missing from docs/preregistration.md"
    assert same(getattr(PREREG, field), values[field]), f"{field}: code {getattr(PREREG, field)!r} vs doc {values[field]!r}"


def test_the_document_states_no_parameter_the_code_lacks():
    names = {f.name for f in dataclasses.fields(PREREG)}
    assert set(doc_values()) <= names
