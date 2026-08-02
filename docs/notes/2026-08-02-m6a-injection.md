# M6-A Injection Security Implementation & Measurement Record

Date: 2026-08-02

## 1. PII Git History Purge Record

Purged on 2026-08-02 per Spec §3.8 / Plan Task 1 using `git-filter-repo`.

### Exposure Details
- **Files carrying PII:**
  - `tests/fixtures/real_samples_pdf/evn.pdf`
  - `tests/fixtures/real_samples_pdf/ktx-b.pdf`
  - `tests/fixtures/real_samples_pdf/luyen-noi.pdf`
  - `tests/fixtures/real_samples_pdf/phongvu.pdf`
  - `tests/fixtures/real_samples_pdf/viettelpost.pdf`
  - `tests/fixtures/real_samples_pdf/vina-link.pdf`
  - Tax code string `0100109106` in `docs/Plan_Project_F_2026-07-25.md`
- **Commits introducing PII:** `a377e15` (2026-07-27) for PDF fixtures; `bc81169` (2026-07-28) for tax code in docs.
- **Exposure Window:** 2026-07-27 to 2026-08-02 (6 days).
- **Repository Status:** Private repository with 1 contributor throughout.

### Purge Action & Verification
1. Bare mirror backup created at `/tmp/vietnamese-invoice-agent-backup-2026-08-02.git`.
2. Executed `git filter-repo --invert-paths --path-glob 'tests/fixtures/real_samples_pdf/*.pdf' --path-glob 'tests/fixtures/real_samples/*.pdf' --force`.
3. Executed `git filter-repo --replace-text /tmp/redactions.txt --force` replacing `0100109106` with `REDACTED-MST`.
4. Verified `git log --all --oneline -- '*.pdf'` and `git log --all -S'0100109106' --oneline` both returned empty.
5. Re-scanned rewritten history using `gitleaks detect --source . --log-opts="--all" --no-banner`: scanned 221 commits (~5.46 MB), 0 leaks found.
6. Force-pushed `origin/main` and tags (`git push origin --force --all && git push origin --force --tags`).
