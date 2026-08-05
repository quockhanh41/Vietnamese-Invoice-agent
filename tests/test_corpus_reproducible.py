"""The corpus must be what `make generate` produces — not what it produced once.

Found 2026-08-05 (spec §1.1): `corpus/` had drifted from the generator and
nobody noticed for three days, because the stale files kept every test green.
M6-A's history purge rewrote the generator template in the WORKING TREE, so a
fresh generate emitted `REDACTED-MST` where a tax code belongs while the
on-disk corpus still carried the pre-purge value.

Design §10.1's anti-circularity argument requires the published numbers to be
regenerable from a clean clone. This test is that requirement, executable.
"""

import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

CORPUS = pathlib.Path("corpus")


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory) -> pathlib.Path:
    if not (CORPUS / "manifest.jsonl").exists():
        pytest.skip("corpus not generated — run `make generate`")
    out = tmp_path_factory.mktemp("corpus-repro")
    subprocess.run(
        [sys.executable, "-m", "scripts.generate_invoices",
         "--count", "300", "--seed", "42", "--out", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_the_manifest_regenerates_identically(regenerated):
    a = sorted((CORPUS / "manifest.jsonl").read_text(encoding="utf-8").splitlines())
    b = sorted((regenerated / "manifest.jsonl").read_text(encoding="utf-8").splitlines())
    assert a == b


def test_every_manifest_file_regenerates_byte_identically(regenerated):
    """The check that would have caught the drift. Compares CONTENT, not just
    the manifest — the manifest matched throughout while all 285 XMLs differed."""
    manifest = [
        json.loads(line)
        for line in (CORPUS / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    stale = []
    for record in manifest:
        name = pathlib.Path(record["path"]).name
        current, fresh = CORPUS / name, regenerated / name
        if not current.exists() or not fresh.exists():
            stale.append(f"{name} (missing on one side)")
        elif _digest(current) != _digest(fresh):
            stale.append(name)
    assert stale == [], f"{len(stale)} corpus files differ from a fresh generate: {stale[:10]}"


def test_the_generator_emits_no_redaction_marker():
    """`REDACTED-MST` is correct in tests/fixtures/real_samples/ — those are
    deliberately anonymised real invoices. It is wrong in SYNTHETIC data, which
    has nothing to redact, and it is not a tax code."""
    templates = pathlib.Path("scripts/generator/templates")
    offenders = [
        p.name for p in templates.glob("*.j2") if "REDACTED" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"generator templates carry a redaction marker: {offenders}"
