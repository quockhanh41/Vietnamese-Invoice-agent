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

---

## 2. 30 Injection Case Measurement Results

Measured on 2026-08-02 across 30 prompt injection cases (`test_prompt_injection.py`).

| Claim / Assertion | Target / Surface | Cases | Result | Evidence |
|---|---|---|---|---|
| No decision set | 6 payloads × 4 placements | 24 | PASS (0 decisions set) | `test_no_payload_can_set_an_approval_or_post_an_entry` |
| No journal posted | 6 payloads × 4 placements | 24 | PASS (0 entries posted) | `test_no_payload_can_set_an_approval_or_post_an_entry` |
| Category laundering harmless | `category_laundering` | 1 | PASS (`BUDGET_BREACH` raised) | `test_a_laundered_category_still_breaches_the_budget` |
| Reasoning channel safe | 6 payloads in reasoning | 6 | PASS (`AuditVerdict` schema holds) | `test_a_payload_in_the_classifier_reasoning_cannot_reach_the_auditor` |

---

## 3. Negative Control Measurement (Assertion 4)

Measured on 2026-08-02 across 24 carrier/twin pairs using recorded model replay (`LLM_MODE=record` / `replay`).

- **Total carrier/twin pairs:** 24
- **Matches (`carrier.category == twin.category`):** 18/24
- **Shifts:** 6/24 (e.g. `category_laundering` payload shifting classification from `OFFICE_SUPPLIES` to `MARKETING`).
- **Significance:** Proves that category laundering is expressible as a category classification output, reinforcing that the response model schema boundary does not stop legal-channel category changes—the Python deterministic flag layer (`BUDGET_BREACH`) is what protects budget enforcement.

---

## 4. Mutation Verification Results

Four targeted mutations executed and measured on 2026-08-02. Each mutation turned the test suite RED as expected, and all were reverted cleanly.

| Mutation | Description | Command | Measured Output | Result | Reverted |
|---|---|---|---|---|---|
| **A** | Remove `extra="forbid"` from `ClassificationResult` | `pytest tests/test_prompt_injection.py -v` | FAILED `test_overt_compliance_cannot_be_expressed_as_a_ClassificationResult` | RED | Yes |
| **B** | Break `sanitize.wrap()` (return unescaped text) | `pytest tests/test_prompt_injection.py tests/test_sanitize.py -v` | FAILED `test_wrap_fences_and_escapes` & `test_a_forged_close_tag_is_neutralised` | RED | Yes |
| **C** | Read flags from graph state (`final["findings"]`) instead of `app.invoice_flags` | `pytest tests/test_prompt_injection.py -k launder -v` | FAILED `test_a_laundered_category_still_breaches_the_budget` (`AssertionError: got []`) | RED | Yes |
| **D** | Remove `invoice_id` check in `guards.py` | `pytest tests/test_never_post_without_approval.py tests/test_prompt_injection.py -v` | FAILED `test_cannot_claim_an_approval_belonging_to_a_different_invoice` | RED | Yes |
