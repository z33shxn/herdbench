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
        return [str(v) for v in field_value] == [p.strip() for p in text.split(",")] or [
            float(v) for v in field_value
        ] == [float(p) for p in text.split(",")]
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
    assert same(getattr(PREREG, field), values[field]), (
        f"{field}: code {getattr(PREREG, field)!r} vs doc {values[field]!r}"
    )


def test_the_document_states_no_parameter_the_code_lacks():
    names = {f.name for f in dataclasses.fields(PREREG)}
    assert set(doc_values()) <= names


def _records() -> list[tuple[str, str, str]]:
    """(commit, path, sha256) for each timestamped record in docs/timestamps."""
    out = []
    for record in sorted((DOC.parent / "timestamps").glob("*-v1.txt")):
        text = record.read_text(encoding="utf-8")
        commit = re.search(r"^commit ([0-9a-f]{40})$", text, re.M)
        doc = re.search(r"^sha256\((docs/[^)]+)\) ([0-9a-f]{64})$", text, re.M)
        assert commit, record.name
        assert doc, record.name
        out.append((commit.group(1), doc.group(1), doc.group(2)))
    return out


def test_registered_documents_changed_only_in_typography_since_they_were_timestamped():
    """The registered texts were later edited for typography only: long dashes and minus signs became ASCII hyphens.
    The timestamped versions stay in git at the recorded commits; both facts are checked here."""
    import hashlib
    import subprocess

    root = DOC.parents[1]
    records = _records()
    assert {path for _, path, _ in records} == {"docs/preregistration.md", "docs/preregistration_addendum.md"}
    for commit, path, digest in records:
        shown = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=root, capture_output=True, check=False)
        if shown.returncode != 0:
            pytest.skip("git history is not available in this checkout")
        assert hashlib.sha256(shown.stdout).hexdigest() == digest, path
        registered = shown.stdout.decode("utf-8")
        typeset = registered.replace(chr(0x2013), "-").replace(chr(0x2212), "-")
        assert (root / path).read_text(encoding="utf-8") == typeset, path
