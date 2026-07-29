"""No binary that can carry PII may be tracked under tests/.

M3 opened by finding six vendor-issued PDFs committed in `a377e15` — real
names, tax codes and QR lookup codes — swept into an M1.5 *spec* commit by
`git add` and pushed to origin. `.gitignore` already carried a comment
forbidding exactly that. A comment is not a guard; this is.

gitleaks does not help here: it looks for tokens, not for a person's name.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _tracked_under_tests() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "tests/"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_no_pdf_is_tracked_under_tests():
    offenders = [f for f in _tracked_under_tests() if f.lower().endswith(".pdf")]
    assert offenders == [], (
        "A PDF is tracked under tests/. A vendor PDF carries PII in "
        "human-readable form plus a QR lookup code, and cannot be anonymised "
        f"the way an XML tree can. Offenders: {offenders}"
    )


def test_no_archive_is_tracked_under_tests():
    """Vendors deliver invoices as ZIPs containing both .xml and .pdf."""
    offenders = [
        f for f in _tracked_under_tests() if f.lower().endswith((".zip", ".rar", ".7z"))
    ]
    assert offenders == [], f"An archive can hide anything: {offenders}"
