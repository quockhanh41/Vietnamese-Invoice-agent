# M4-A Corpus & Reference Alignment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the synthetic corpus able to support the measurements M4 and M6 are gated on — a learnable `category` label, department codes that join the seeded reference data, a canonical budget rank that means what it says, and a declared gate batch — without changing what M2 and M3 already proved.

**Architecture:** The generator currently picks a description, then picks a label by `i % 6`. This plan inverts that: pick the category first, draw the description from that category's pool, derive the department from the category with declared noise. A new `keywords_vi` baseline classifier in `src/` is the instrument that measures the result, and is the same one M4-C and M6.7 will use. Nothing about XML *structure* changes; only line description text and manifest labels.

**Tech Stack:** Python 3.13 · Jinja2 (generator templates) · pytest · ruff · mypy · Postgres 17 (seed data only; most tasks need no database).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-31-m4a-corpus-reference-alignment-design.md`. Read §1 before changing the generator; it explains why each defect is what it is.
- **Authoring language is English** — code comments, docstrings, commit messages. **Invoice descriptions stay Vietnamese**: they are text the system ingests (CLAUDE.md product-language exception).
- **The generator must not import** `invoice_agent.extraction.xml_schema`, `invoice_agent.extraction.xml_parser`, or `invoice_agent.domain` — enforced by `tests/test_generator_independence.py`. `scripts.seed_reference` is the one allowed dependency and this plan adds a second symbol to it.
- **Every non-obvious constant carries a comment with date + measurement + report filename** (CLAUDE.md). `TIER_MIX` and `DEPARTMENT_NOISE_RATE` are both such constants.
- **`rng` is always a parameter**, never module-level — `test_same_seed_produces_identical_bytes` depends on it.
- **Corpus is gitignored** (`.gitignore:28`). Never `git add corpus/`. Regeneration is `make generate`.
- **Verify with commands, not intuition.** `make check` = ruff + mypy + pytest. Run it before claiming a task done.
- Python is `.venv/bin/python`. Regenerate with `make generate` (= `--count 300 --seed 42 --out corpus`).
- **A task is not done until `make check` is green.** M4-A's success criterion 10 requires green on the *pushed* commit; three milestones running have failed that.

---

### Task 1: Category description pools

**Files:**
- Create: `scripts/generator/descriptions.py`
- Test: `tests/test_generator_descriptions.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Description` (frozen dataclass: `text: str`, `tier: str`); `CATEGORY_POOLS: dict[str, tuple[Description, ...]]`; `TIER_MIX: dict[str, float]`; `draw(category: str, rng: Random) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_generator_descriptions.py`:

```python
"""The description pools are what make `category` a function of the invoice.

Measured 2026-07-30 before this module existed: the keywords_vi baseline agreed
with the manifest label on 5 of 285 XML invoices (1.8%), below the 16.7% chance
rate, because descriptions were near-constant boilerplate drawn independently of
the label. See spec 2026-07-31-m4a §1.1 D3.
"""

from random import Random

from scripts.generator import descriptions

CATEGORIES = (
    "MARKETING", "IT_SERVICES", "OFFICE_SUPPLIES",
    "LOGISTICS", "UTILITIES", "TOOLS",
)


def test_every_corpus_category_has_a_pool():
    for category in CATEGORIES:
        assert category in descriptions.CATEGORY_POOLS
        assert descriptions.CATEGORY_POOLS[category], f"{category} pool is empty"


def test_every_pool_covers_all_three_tiers():
    """A pool missing the keyword tier makes the baseline unbeatable-downward;
    one missing synonym/jargon makes it unbeatable-upward. Both break the M6
    gate, in opposite directions."""
    for category, pool in descriptions.CATEGORY_POOLS.items():
        tiers = {d.tier for d in pool}
        assert tiers == {"keyword", "synonym", "jargon"}, f"{category}: {tiers}"


def test_tier_mix_is_a_distribution():
    assert set(descriptions.TIER_MIX) == {"keyword", "synonym", "jargon"}
    assert abs(sum(descriptions.TIER_MIX.values()) - 1.0) < 1e-9


def test_draw_is_deterministic_for_a_given_rng():
    a = [descriptions.draw("MARKETING", Random(7)) for _ in range(5)]
    b = [descriptions.draw("MARKETING", Random(7)) for _ in range(5)]
    assert a == b


def test_draw_returns_text_from_the_requested_category():
    rng = Random(42)
    texts = {d.text for d in descriptions.CATEGORY_POOLS["LOGISTICS"]}
    for _ in range(60):
        assert descriptions.draw("LOGISTICS", rng) in texts


def test_descriptions_are_unique_across_categories():
    """Two categories sharing a description string would make that invoice's
    label unrecoverable from its text — the exact defect this module fixes."""
    seen: dict[str, str] = {}
    for category, pool in descriptions.CATEGORY_POOLS.items():
        for entry in pool:
            assert entry.text not in seen, (
                f"{entry.text!r} is in both {seen.get(entry.text)} and {category}"
            )
            seen[entry.text] = category
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generator_descriptions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.generator.descriptions'`

- [ ] **Step 3: Write the implementation**

Create `scripts/generator/descriptions.py`:

```python
"""Per-category Vietnamese line descriptions, in three tiers.

WHY TIERS. The M6 gate requires the LLM classifier to beat the `keywords_vi`
baseline by >=15 points. A corpus whose descriptions always contain a seeded
keyword makes the baseline near-perfect and the LLM redundant; one whose
descriptions contain none makes both score at chance. Measured 2026-07-30, the
corpus was in the second state: baseline agreement 5/285 = 1.8%, below the 16.7%
chance rate (spec 2026-07-31-m4a §1.1 D3).

  keyword  - contains a seeded keywords_vi term verbatim. Baseline hits.
  synonym  - real invoice phrasing carrying no seeded term. Baseline misses.
  jargon   - vendor abbreviations and mixed Vietnamese/English, as real invoices
             actually read. Baseline misses.

Text stays Vietnamese: this is data the system INGESTS, not authoring style
(CLAUDE.md). The `jargon` tier mixes English because real Vietnamese invoices do.

HONESTY NOTE. The baseline's resulting score is a construction parameter, not a
measurement — this file chooses it. Only the LLM's MARGIN over the baseline is
evidence, and that margin is bounded by whoever wrote the `synonym` tier. See
spec §4.1; the 8 real fixtures are the held-out check.

This module imports nothing from the parser or the domain layer, and must not —
`tests/test_generator_independence.py` enforces it.
"""

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class Description:
    text: str
    tier: str  # keyword | synonym | jargon


def _d(text: str, tier: str) -> Description:
    return Description(text=text, tier=tier)


# Keyword-tier entries are written against the keywords_vi lists seeded in
# scripts/seed_reference.py CATEGORIES. Changing a seeded keyword without
# revisiting this table silently moves the baseline score.
CATEGORY_POOLS: dict[str, tuple[Description, ...]] = {
    "MARKETING": (
        _d("Chi phí quảng cáo Facebook Ads tháng 07/2026", "keyword"),
        _d("Dịch vụ truyền thông thương hiệu quý III", "keyword"),
        _d("In ấn banner và standee cho hội chợ", "keyword"),
        _d("Chi phí tổ chức sự kiện ra mắt dòng hàng mới", "keyword"),
        _d("Chi phí tổ chức hội thảo khách hàng thường niên", "synonym"),
        _d("Thiết kế bộ nhận diện thương hiệu cho chi nhánh mới", "synonym"),
        _d("Chi phí booth triển lãm tại hội chợ thương mại", "synonym"),
        _d("Triển khai chiến dịch digital branding Q3/2026", "jargon"),
        _d("Phí quản lý fanpage và content social media", "jargon"),
    ),
    "IT_SERVICES": (
        _d("Gia hạn bản quyền phần mềm kế toán 12 tháng", "keyword"),
        _d("Chi phí thuê máy chủ cloud tháng 07/2026", "keyword"),
        _d("Phí duy trì tên miền và hosting website", "keyword"),
        _d("Mua license phần mềm thiết kế đồ họa", "keyword"),
        _d("Dịch vụ bảo trì hệ thống mạng nội bộ", "synonym"),
        _d("Phí triển khai hệ thống quản trị nguồn lực doanh nghiệp", "synonym"),
        _d("Chi phí sao lưu và phục hồi dữ liệu định kỳ", "synonym"),
        _d("Subscription SaaS quản lý công việc gói Business", "jargon"),
        _d("Phí thuê VPS và CDN tháng 07/2026", "jargon"),
    ),
    "OFFICE_SUPPLIES": (
        _d("Mua văn phòng phẩm quý III năm 2026", "keyword"),
        _d("Giấy A4 định lượng 70gsm và mực in laser", "keyword"),
        _d("Bút bi, sổ tay và kẹp tài liệu cho văn phòng", "keyword"),
        _d("Mua ghim bấm và bút dạ quang", "keyword"),
        _d("Vật tư tiêu hao cho khu vực làm việc chung", "synonym"),
        _d("Đồ dùng phục vụ phòng họp và tiếp khách", "synonym"),
        _d("Chi phí mua bìa còng và phong bì thư", "synonym"),
        _d("Stationery gói tiêu chuẩn cho 40 nhân sự", "jargon"),
        _d("Consumables văn phòng gói tháng 07", "jargon"),
    ),
    "LOGISTICS": (
        _d("Cước vận chuyển nội thành tháng 07/2026", "keyword"),
        _d("Phí dịch vụ chuyển phát nhanh tài liệu", "keyword"),
        _d("Chi phí giao hàng cho khách lẻ khu vực miền Trung", "keyword"),
        _d("Dịch vụ logistics kho bãi quý III", "keyword"),
        _d("Phí bốc xếp và lưu kho tại cảng", "synonym"),
        _d("Chi phí thuê xe tải tuyến Hà Nội - Hải Phòng", "synonym"),
        _d("Phụ phí xăng dầu tuyến đường dài", "synonym"),
        _d("Phí last-mile delivery khu vực nội đô", "jargon"),
        _d("Chi phí forwarding lô hàng nhập khẩu", "jargon"),
    ),
    "UTILITIES": (
        _d("Tiền điện tháng 07/2026 văn phòng trụ sở", "keyword"),
        _d("Tiền nước sinh hoạt tháng 07/2026", "keyword"),
        _d("Cước viễn thông và internet tháng 07", "keyword"),
        _d("Cước điện thoại cố định quý III", "keyword"),
        _d("Phí sử dụng điện năng khu vực kho", "synonym"),
        _d("Chi phí thuê bao đường truyền dữ liệu", "synonym"),
        _d("Phí dịch vụ xử lý rác thải văn phòng", "synonym"),
        _d("Phí data leased line tháng 07/2026", "jargon"),
        _d("Chi phí utilities khu vực showroom", "jargon"),
    ),
    "TOOLS": (
        _d("Mua dụng cụ sửa chữa cho bộ phận kỹ thuật", "keyword"),
        _d("Trang bị công cụ cầm tay cho tổ bảo trì", "keyword"),
        _d("Mua thiết bị đo lường cầm tay", "keyword"),
        _d("Máy móc nhỏ phục vụ xưởng lắp ráp", "keyword"),
        _d("Mua thang nhôm và xe đẩy phục vụ kho", "synonym"),
        _d("Trang bị bảo hộ lao động cho tổ kỹ thuật", "synonym"),
        _d("Mua bộ tô vít và kìm điện cho phòng bảo trì", "synonym"),
        _d("Hand tools kit cho đội maintenance", "jargon"),
        _d("Mua toolbox và phụ kiện thay thế", "jargon"),
    ),
}

# ⚠️ TUNED CONSTANT — set by Task 6 of plan 2026-07-31-m4a, measured against the
# keywords_vi baseline and recorded in docs/notes/2026-07-31-m4a-corpus-labels.md.
# The keyword weight is approximately the baseline's accuracy, because keyword-tier
# descriptions are the ones it can match. Target band 55-70%: high enough that the
# baseline is a real competitor, low enough to leave the LLM >=15 points of headroom.
TIER_MIX: dict[str, float] = {"keyword": 0.60, "synonym": 0.25, "jargon": 0.15}


def draw(category: str, rng: Random) -> str:
    """Pick a description for `category`, weighted by TIER_MIX.

    `rng` is a parameter, never module-level: `test_same_seed_produces_identical_bytes`
    requires the whole generator to be a pure function of its seed.
    """
    pool = CATEGORY_POOLS[category]
    tiers = list(TIER_MIX)
    tier = rng.choices(tiers, weights=[TIER_MIX[t] for t in tiers], k=1)[0]
    candidates = [d for d in pool if d.tier == tier]
    return rng.choice(candidates).text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_generator_descriptions.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Confirm independence still holds**

Run: `.venv/bin/python -m pytest tests/test_generator_independence.py -v`
Expected: PASS — the new module imports only `dataclasses` and `random`.

- [ ] **Step 6: Commit**

```bash
git add scripts/generator/descriptions.py tests/test_generator_descriptions.py
git commit -m "feat(generator): per-category description pools in three tiers

Measured 2026-07-30: the keywords_vi baseline agreed with the manifest
category on 5/285 XML invoices (1.8%), below the 16.7% chance rate,
because descriptions were boilerplate drawn independently of the label.

Three tiers make the baseline beatable but not trivial: keyword-tier
descriptions carry a seeded term verbatim, synonym and jargon tiers do
not. TIER_MIX is tuned in a later task and carries its measurement."
```

---

### Task 2: The `keywords_vi` baseline classifier

**Files:**
- Create: `src/invoice_agent/eval/__init__.py`, `src/invoice_agent/eval/baseline.py`
- Test: `tests/test_eval_baseline.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `classify(text: str, keywords: Mapping[str, Sequence[str]]) -> str | None`; `load_keywords(conn: Connection) -> dict[str, list[str]]`.

Lives in `src/`, not `scripts/`, because M4-C's agent comparison and M6.7's `scripts/eval.py` both import it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_baseline.py`:

```python
"""The non-LLM baseline principle 3 requires beside every LLM metric.

This is also the instrument that found the corpus label defect (spec
2026-07-31-m4a §1.1 D3), so its own behaviour is pinned here.
"""

from invoice_agent.eval import baseline

KEYWORDS = {
    "OFFICE_SUPPLIES": ["văn phòng phẩm", "giấy a4", "mực in"],
    "LOGISTICS": ["cước", "vận chuyển", "chuyển phát"],
    "UTILITIES": ["cước viễn thông", "internet", "điện thoại"],
}


def test_matches_a_single_keyword():
    assert baseline.classify("Mua giấy A4 cho văn phòng", KEYWORDS) == "OFFICE_SUPPLIES"


def test_returns_none_rather_than_guessing():
    """A baseline that guessed would have hidden the defect this module found:
    249 of 285 descriptions matched NO keyword, and a default category would
    have reported that as ordinary error rather than as absent signal."""
    assert baseline.classify("Dịch vụ tư vấn quản trị doanh nghiệp", KEYWORDS) is None


def test_longest_keyword_wins():
    """`cước` (LOGISTICS) is a substring-neighbour of `cước viễn thông`
    (UTILITIES). Shortest-match would send every phone bill to LOGISTICS."""
    text = "Cước viễn thông tháng 07"
    assert baseline.classify(text, KEYWORDS) == "UTILITIES"


def test_match_is_case_and_accent_form_insensitive():
    assert baseline.classify("MỰC IN laser", KEYWORDS) == "OFFICE_SUPPLIES"


def test_ties_break_by_declaration_order():
    """Two categories with an equally long match must resolve deterministically,
    or the baseline's score changes between runs on the same corpus."""
    tied = {"A": ["abcd"], "B": ["abcd"]}
    assert baseline.classify("xx abcd yy", tied) == "A"


def test_empty_text_is_none_not_a_crash():
    assert baseline.classify("", KEYWORDS) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_baseline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'invoice_agent.eval'`

- [ ] **Step 3: Write the implementation**

Create `src/invoice_agent/eval/__init__.py`:

```python
"""Evaluation instruments. Imported by M4-C's agent comparison and M6.7's eval."""
```

Create `src/invoice_agent/eval/baseline.py`:

```python
"""The non-LLM classification baseline: `keywords_vi` substring matching.

Principle 3 (CLAUDE.md): every LLM metric is reported beside a non-LLM baseline,
and classification must beat this by >=15 points or that node does not need an
LLM. This module is that baseline, and it is also the instrument that measured
the corpus label defect on 2026-07-30 (spec 2026-07-31-m4a §1.1 D3).

PURE BY DESIGN. `classify` takes the keyword map as a parameter so tests need no
database, and `load_keywords` reads the SAME seeded `expense_categories` rows the
system classifies against — not a second copy that could drift from the seed.
"""

import unicodedata
from collections.abc import Mapping, Sequence

from sqlalchemy import Connection, select

from invoice_agent.db import models


def _fold(text: str) -> str:
    """Normalise for comparison. NFC because the corpus and the seed are both
    written in composed form; comparing NFC to NFD silently matches nothing."""
    return unicodedata.normalize("NFC", text).casefold()


def classify(text: str, keywords: Mapping[str, Sequence[str]]) -> str | None:
    """Longest matching keyword wins; ties break by declaration order.

    Returns None when nothing matches. NEVER guesses a default: a 249/285
    no-match rate is exactly what exposed the corpus defect, and a guessing
    baseline would have reported it as ordinary error instead of absent signal.
    """
    if not text:
        return None
    haystack = _fold(text)
    best: str | None = None
    best_len = 0
    for category, terms in keywords.items():
        for term in terms:
            if len(term) > best_len and _fold(term) in haystack:
                best, best_len = category, len(term)
    return best


def load_keywords(conn: Connection) -> dict[str, list[str]]:
    """Read `keywords_vi` from the seeded reference data."""
    stmt = select(
        models.expense_categories.c.code, models.expense_categories.c.keywords_vi
    ).order_by(models.expense_categories.c.code)
    return {row.code: list(row.keywords_vi) for row in conn.execute(stmt)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_eval_baseline.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run lint and typecheck**

Run: `make lint && make typecheck`
Expected: `All checks passed` and `no issues found`.

- [ ] **Step 6: Commit**

```bash
git add src/invoice_agent/eval tests/test_eval_baseline.py
git commit -m "feat(eval): keywords_vi baseline classifier

The non-LLM baseline principle 3 requires beside every LLM metric, and
the instrument that measured the corpus label defect on 2026-07-30.

Pure: the keyword map is a parameter, so tests need no database.
load_keywords reads the same seeded expense_categories rows the system
classifies against. Returns None rather than guessing — a guessing
baseline would have reported 249/285 absent signal as ordinary error."
```

---

### Task 3: Departments derived from the seed

**Files:**
- Modify: `scripts/generate_invoices.py:43` (the `DEPARTMENTS` literal), `scripts/generate_invoices.py:25` (import)
- Test: `tests/test_generator_reference_join.py`

**Interfaces:**
- Consumes: `scripts.seed_reference.DEPARTMENTS`, `scripts.seed_reference.BUDGETS`.
- Produces: `generate_invoices.DEPARTMENTS: tuple[str, ...]` derived from the seed.

This is the guard whose absence hid D1 for two milestones. Do it before the category rewiring so the two stay separately reviewable.

- [ ] **Step 1: Write the failing test**

Create `tests/test_generator_reference_join.py`:

```python
"""The corpus must join the reference data it will be inserted against.

Measured 2026-07-30: the generator used ("MKT","IT","HR","LOG","ADM","SAL") and
the seed created ("MKT","ADMIN","OPS","IT","SALES"). Only MKT and IT overlapped,
so 194 of 300 corpus invoices could not satisfy invoices.department -> FK
departments.code, and four corpus departments had no budgets row to reconcile
against. M2-A built the schema, M2-C built the generator, and nothing ever
joined them — M4.5 would have been the first code to try.
See spec 2026-07-31-m4a §1.1 D1.
"""

from scripts import generate_invoices
from scripts.generator import manifest
from scripts.seed_reference import BUDGETS, DEPARTMENTS as SEEDED

BUDGET_PERIOD = "2026-07"


def test_generator_departments_come_from_the_seed():
    assert set(generate_invoices.DEPARTMENTS) <= {code for code, _name in SEEDED}


def test_every_manifest_department_is_seeded_and_budgeted(tmp_path):
    """The load-bearing guard. Restore the literal DEPARTMENTS tuple and this
    goes red — that is the mutation this test exists to catch."""
    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    records = manifest.read_manifest(out / "manifest.jsonl")

    seeded = {code for code, _name in SEEDED}
    budgeted = {dept for dept, period, _cat, _amt in BUDGETS if period == BUDGET_PERIOD}

    used = {r["department"] for r in records}
    assert used <= seeded, f"departments with no departments row: {sorted(used - seeded)}"
    assert used <= budgeted, (
        f"departments with no {BUDGET_PERIOD} budgets row, so nothing to "
        f"reconcile against: {sorted(used - budgeted)}"
    )


def test_every_manifest_category_is_seeded(tmp_path):
    """Same failure mode one column over: invoices.category is also a FK."""
    from scripts.seed_reference import CATEGORIES

    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    records = manifest.read_manifest(out / "manifest.jsonl")

    seeded = {row[0] for row in CATEGORIES}
    used = {r["category"] for r in records}
    assert used <= seeded, f"categories with no expense_categories row: {sorted(used - seeded)}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generator_reference_join.py -v`
Expected: FAIL — `test_generator_departments_come_from_the_seed` and `test_every_manifest_department_is_seeded_and_budgeted` both fail, reporting `['ADM', 'HR', 'LOG', 'SAL']` as unseeded.

- [ ] **Step 3: Write the implementation**

In `scripts/generate_invoices.py`, change the import at line 25:

```python
from scripts.seed_reference import BUDGETS, DEPARTMENTS as _SEEDED_DEPARTMENTS
```

Replace line 43:

```python
# Derived from the seed, not written twice. Measured 2026-07-30: the two lists
# had drifted to ("MKT","IT","HR","LOG","ADM","SAL") here against
# ("MKT","ADMIN","OPS","IT","SALES") in the seed, so 194 of 300 invoices could
# not satisfy the invoices.department foreign key and four departments had no
# budget to reconcile against. Deriving makes that class of drift
# unrepresentable rather than merely fixed. seed_reference is the ONE allowed
# dependency (M2-A §7) and already supplies BUDGETS.
DEPARTMENTS = tuple(code for code, _name in _SEEDED_DEPARTMENTS)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_generator_reference_join.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Mutation-verify the guard**

Temporarily replace the `DEPARTMENTS` assignment with the old literal
`("MKT", "IT", "HR", "LOG", "ADM", "SAL")`, then run:

`.venv/bin/python -m pytest tests/test_generator_reference_join.py -v`

Expected: RED on both department tests. Restore the derivation and confirm green. Record the mutation result in the task note; a guard that cannot be shown red is the M1/M2/M3 pattern this project keeps finding.

- [ ] **Step 6: Confirm nothing else broke**

Run: `.venv/bin/python -m pytest tests/test_generator_cli.py -v`
Expected: PASS, 8 tests. Department is manifest-only and never reaches XML bytes, so these are unaffected.

- [ ] **Step 7: Commit**

```bash
git add scripts/generate_invoices.py tests/test_generator_reference_join.py
git commit -m "fix(generator): derive departments from the seed, not a second list

Measured 2026-07-30: generator ("MKT","IT","HR","LOG","ADM","SAL") vs
seed ("MKT","ADMIN","OPS","IT","SALES"). Only MKT and IT overlapped, so
194 of 300 corpus invoices could not satisfy invoices.department -> FK
departments.code, and four departments had no budgets row for 2026-07.

M2-A built the schema and M2-C built the generator; nothing ever joined
them, and M4.5 would have been the first code to try. The new test is
mutation-verified: restore the literal tuple and it goes red."
```

---

### Task 4: Category-first generation

**Files:**
- Modify: `scripts/generate_invoices.py` — `_make_base_spec` (line 74), the budget case loop (line 164), the injection loop (line 167), the fill loop (line 178), the `budget_overrun` override (line 216), the `ambiguous_category` loop (line 310), the manifest assembly (line 390)
- Test: `tests/test_generator_labels.py`

**Interfaces:**
- Consumes: `descriptions.draw`, `descriptions.CATEGORY_POOLS` (Task 1); `DEPARTMENTS` (Task 3).
- Produces: `CATEGORY_TO_DEPARTMENT: dict[str, str]`, `SECONDARY_DEPARTMENT: dict[str, str]`, `DEPARTMENT_NOISE_RATE: float`, `_pick_department(category: str, rng: Random) -> str`. Every spec now carries `meta["category"]`.

The inversion: category is chosen first and stored in `meta`, the description is drawn from it, and the manifest reads `meta["category"]` instead of `i % 6`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_generator_labels.py`:

```python
"""`category` must be a function of the invoice text, and `department` of the category.

Measured 2026-07-30, before this change: INV-0001, INV-0002 and INV-0003 carried
the IDENTICAL description "Chi phí quảng cáo trực tuyến tháng 07/2026" and were
labelled MARKETING, IT_SERVICES and OFFICE_SUPPLIES. The label was provably not a
function of the invoice. See spec 2026-07-31-m4a §1.1 D3.
"""

import collections

from scripts import generate_invoices
from scripts.generator import descriptions, manifest

from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY


def build(tmp_path, count=300, seed=42):
    out = tmp_path / "corpus"
    generate_invoices.generate(count=count, seed=seed, out=out)
    return out, manifest.read_manifest(out / "manifest.jsonl")


def _text_to_category() -> dict[str, str]:
    return {
        entry.text: category
        for category, pool in descriptions.CATEGORY_POOLS.items()
        for entry in pool
    }


def test_identical_descriptions_never_carry_different_categories(tmp_path):
    """The sharpest form of the defect: same text, three different labels."""
    out, records = build(tmp_path)
    by_text: dict[str, set[str]] = collections.defaultdict(set)
    for record in records:
        if record["source_type"] != "xml":
            continue
        invoice = parse_invoice_xml((out.parent / record["path"]).read_bytes())
        for line in invoice.lines:
            if line.description:
                by_text[line.description].add(record["category"])
    ambiguous = {t: c for t, c in by_text.items() if len(c) > 1}
    assert not ambiguous, f"same text, different labels: {list(ambiguous.items())[:3]}"


def test_pool_descriptions_carry_their_own_category(tmp_path):
    """Every invoice whose description came from a pool must be labelled with
    that pool's category. Draw from a different pool and this goes red."""
    out, records = build(tmp_path)
    lookup = _text_to_category()
    wrong = []
    for record in records:
        if record["source_type"] != "xml":
            continue
        invoice = parse_invoice_xml((out.parent / record["path"]).read_bytes())
        for line in invoice.lines:
            expected = lookup.get(line.description or "")
            if expected and expected != record["category"]:
                wrong.append(f"{record['invoice_id']}: {expected} != {record['category']}")
    assert not wrong, wrong[:5]


def test_injection_twins_share_a_category(tmp_path):
    """§9.3 negative control: the payload replaces the description, so the pair
    must take its label from the STRIPPED twin. If the label moved with the
    payload, a classification difference between twin and carrier would measure
    the label change rather than the injection."""
    _out, records = build(tmp_path)
    by_id = {r["invoice_id"]: r for r in records}
    pairs = [(by_id[r["twin_of"]], r) for r in records if r.get("twin_of") and r["twin_of"] in by_id]
    assert pairs, "no injection twins found — the control is not being exercised"
    for carrier, twin in pairs:
        assert carrier["category"] == twin["category"], (
            f"{carrier['invoice_id']} vs {twin['invoice_id']}"
        )


def test_department_is_derived_from_category(tmp_path):
    """Every department is either the category's primary or its declared
    secondary — nothing else. Budget-overrun invoices are pinned to MKT."""
    _out, records = build(tmp_path)
    for record in records:
        if "budget_overrun" in record["defects"]:
            assert record["department"] == "MKT"
            continue
        category = record["category"]
        allowed = {
            generate_invoices.CATEGORY_TO_DEPARTMENT[category],
            generate_invoices.SECONDARY_DEPARTMENT[category],
        }
        assert record["department"] in allowed, f"{record['invoice_id']}: {record['department']}"


def test_department_noise_is_present_and_bounded(tmp_path):
    """The noise rate IS M4-C's department accuracy ceiling. Zero noise makes
    department a free lunch; too much makes it unpredictable."""
    _out, records = build(tmp_path)
    eligible = [r for r in records if "budget_overrun" not in r["defects"]]
    secondary = [
        r for r in eligible
        if r["department"] == generate_invoices.SECONDARY_DEPARTMENT[r["category"]]
        and r["department"] != generate_invoices.CATEGORY_TO_DEPARTMENT[r["category"]]
    ]
    rate = len(secondary) / len(eligible)
    target = generate_invoices.DEPARTMENT_NOISE_RATE
    assert abs(rate - target) < 0.05, f"noise rate {rate:.3f}, declared {target}"


def test_ambiguous_invoices_take_the_primary_of_their_pair(tmp_path):
    """defects.AMBIGUOUS_DESCRIPTIONS already pairs each description with
    (primary, plausible_alternative). generate_invoices.py:313 discarded it."""
    from scripts.generator import defects

    out, records = build(tmp_path)
    primaries = {desc: pair[0] for desc, pair in defects.AMBIGUOUS_DESCRIPTIONS}
    checked = 0
    for record in records:
        if "ambiguous_category" not in record["defects"] or record["source_type"] != "xml":
            continue
        invoice = parse_invoice_xml((out.parent / record["path"]).read_bytes())
        expected = primaries.get(invoice.lines[0].description or "")
        if expected:
            checked += 1
            assert record["category"] == expected, record["invoice_id"]
    assert checked, "no ambiguous invoices checked — the group is not being exercised"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generator_labels.py -v`
Expected: FAIL — `AttributeError: module 'scripts.generate_invoices' has no attribute 'CATEGORY_TO_DEPARTMENT'`, plus the label tests failing on the round-robin assignment.

- [ ] **Step 3: Add the department maps**

In `scripts/generate_invoices.py`, below the `DEPARTMENTS` derivation from Task 3:

```python
# Which department owns which kind of spend. Many-to-one on purpose: two
# categories map to ADMIN and two to OPS, because a real company's chart of
# accounts is wider than its org chart.
CATEGORY_TO_DEPARTMENT: dict[str, str] = {
    "MARKETING": "MKT",
    "IT_SERVICES": "IT",
    "OFFICE_SUPPLIES": "ADMIN",
    "UTILITIES": "ADMIN",
    "LOGISTICS": "OPS",
    "TOOLS": "OPS",
}

# Where the noise share goes. Realistic — any department buys office supplies —
# and it is the only path to SALES, which no category owns primarily.
SECONDARY_DEPARTMENT: dict[str, str] = {
    "MARKETING": "SALES",
    "IT_SERVICES": "ADMIN",
    "OFFICE_SUPPLIES": "SALES",
    "UTILITIES": "OPS",
    "LOGISTICS": "SALES",
    "TOOLS": "IT",
}

# ⚠️ DECLARED CONSTANT — this rate IS the ceiling on department classification
# accuracy, because no model can recover an allocation the invoice does not
# encode. Set by plan 2026-07-31-m4a Task 4, reported in
# docs/notes/2026-07-31-m4a-corpus-labels.md so M4-C does not read a score at
# the ceiling as a failure. Zero noise would make department a lossless function
# of category and measure nothing.
DEPARTMENT_NOISE_RATE = 0.15


def _pick_department(category: str, rng: Random) -> str:
    if rng.random() < DEPARTMENT_NOISE_RATE:
        return SECONDARY_DEPARTMENT[category]
    return CATEGORY_TO_DEPARTMENT[category]
```

Add the import at the top of the file, beside the other generator imports:

```python
from scripts.generator import defects, descriptions, manifest, pdf, templates, vendors
```

- [ ] **Step 4: Thread the category through spec construction**

Change `_make_base_spec` (line 74) to take and use a category:

```python
def _make_base_spec(
    index: int, vendor: vendors.Vendor, rng: Random, category: str
) -> InvoiceSpec:
```

and replace the `description=` argument in its `LineSpec` (line 90) with:

```python
        description=descriptions.draw(category, rng),
```

Add a category chooser above `generate`:

```python
def _pick_category(index: int) -> str:
    """Round-robin over categories keeps the class balance flat (50 each at
    count=300), which the baseline measurement in Task 6 assumes. It is the
    DESCRIPTION that must follow the category, not the other way round — that
    inversion is the whole fix."""
    return CATEGORIES[index % len(CATEGORIES)]
```

- [ ] **Step 5: Update every call site to carry a category**

Budget case (line 143-165) — these three override the description, so pin the
category to match the text they carry:

```python
    # 1. First, build the budget overrun case (3 items)
    base_template = _make_base_spec(0, established_vendors[0], rng, "MARKETING")
    budget_case_specs = defects.build_budget_overrun_case(base_template, rng)
```

and in the loop that appends them, carry the category in `meta`:

```python
    for rank, bspec in enumerate(budget_case_specs, start=1):
        specs.append((bspec, ["budget_overrun"], {"rank": rank, "category": "MARKETING"}))
```

Injection carriers and twins (line 167-176) — the twin's category is the pair's
category, and the carrier takes it from the twin:

```python
    for i, (_name, payload) in enumerate(defects.INJECTION_PAYLOADS):
        v = established_vendors[i % len(established_vendors)]
        category = _pick_category(len(specs))
        carrier_base = _make_base_spec(len(specs), v, rng, category)
        carrier_spec = defects.seed_injection(carrier_base, payload)
        # The payload REPLACES the description, so the carrier cannot derive a
        # category from its own text. It inherits the twin's — otherwise the
        # §9.3 negative control would measure a label change rather than the
        # injection.
        specs.append((carrier_spec, ["prompt_injection"], {"category": category}))

        twin_spec = defects.strip_injection(carrier_spec)
        specs.append(
            (twin_spec, [], {"twin_of": carrier_spec.doc_id, "category": category})
        )
```

Fill loop (line 178-184):

```python
    curr_idx = len(specs)
    while len(specs) < count:
        v = established_vendors[curr_idx % len(established_vendors)]
        category = _pick_category(curr_idx)
        base = _make_base_spec(curr_idx, v, rng, category)
        specs.append((base, [], {"category": category}))
        curr_idx += 1
```

Budget-overrun override (line 216-230) — it replaces the description with a
marketing one, so the category must move with it:

```python
    for idx in _allocate_indices(max(0, counts["budget_overrun"] - 3)):
        s, d, meta = specs[idx]
        overrun_line = LineSpec(
            line_no=1, item_kind=1, description="Chi phí sự kiện truyền thông MKT",
            unit="Gói", quantity=Decimal("1"), unit_price=Decimal("60000000"),
            amount=Decimal("60000000"), vat_rate=None, vat_amount=None,
            ext_amount=None, self_closing=False,
        )
        bspec = dataclasses.replace(
            s, lines=[overrun_line], subtotal=None, vat_total=None,
            total_amount=Decimal("60000000"), total_in_words=None, template_no="2",
        )
        # The description changed to marketing text, so the label must follow it.
        specs[idx] = (bspec, d + ["budget_overrun"], {**meta, "category": "MARKETING"})
```

Ambiguous group (line 310-316) — stop discarding the pair:

```python
    for i, idx in enumerate(_allocate_indices(counts["ambiguous_category"])):
        s, d, meta = specs[idx]
        desc, (primary, alternative) = defects.AMBIGUOUS_DESCRIPTIONS[
            i % len(defects.AMBIGUOUS_DESCRIPTIONS)
        ]
        line = dataclasses.replace(s.lines[0], description=desc)
        ambig_spec = dataclasses.replace(s, lines=[line])
        # The primary is the label; the alternative is recorded so M6 can report
        # "chose the plausible alternative" separately from "was wrong". These
        # nine are the honest hard cases and must not be scored as ordinary errors.
        specs[idx] = (
            ambig_spec,
            d + ["ambiguous_category"],
            {**meta, "category": primary, "alternative_category": alternative},
        )
```

Duplicates (lines 232-262) copy the source spec, so they must copy its label —
a duplicate carries its original's description verbatim, so any other label would
recreate the same-text-different-label defect this task exists to remove.

`exact_duplicate` loop:

```python
    for idx in _allocate_indices(counts["exact_duplicate"]):
        target_idx = idx - 10
        orig_spec = specs[target_idx][0]
        dup_spec = defects.duplicate_of(orig_spec)
        specs[idx] = (
            dup_spec,
            specs[idx][1] + ["exact_duplicate"],
            {
                "duplicate_of": orig_spec.doc_id,
                # A duplicate copies the description, so it copies the label.
                "category": specs[target_idx][2]["category"],
                "inherited_defects": _inherited_body_defects(
                    specs[target_idx][1], specs[target_idx][2], specs[idx][1]
                ),
            },
        )
```

`near_duplicate` loop:

```python
    for idx in _allocate_indices(counts["near_duplicate"]):
        target_idx = idx - 15
        orig_spec = specs[target_idx][0]
        near_spec = defects.near_duplicate_of(orig_spec, rng)
        specs[idx] = (
            near_spec,
            specs[idx][1] + ["near_duplicate"],
            {
                "category": specs[target_idx][2]["category"],
                "inherited_defects": _inherited_body_defects(
                    specs[target_idx][1], specs[target_idx][2], specs[idx][1]
                ),
            },
        )
```

- [ ] **Step 6: Read the label from meta at manifest time**

Replace lines 390-392:

```python
        # Department logic. `category` was decided when the spec was built and
        # the description was drawn FROM it — reading it back here is what makes
        # the label a function of the invoice (spec 2026-07-31-m4a §1.1 D3).
        cat = meta["category"]
        dept = "MKT" if "budget_overrun" in defect_list else _pick_department(cat, rng)
```

- [ ] **Step 7: Add `alternative_category` to the manifest record**

In `scripts/generator/manifest.py`, add to `ManifestRecord` after `category`:

```python
    # Set only for the `ambiguous_category` group: the OTHER defensible label.
    # M6 reports "chose the plausible alternative" apart from "was wrong".
    alternative_category: str | None = None
```

and in `generate_invoices.py`'s `ManifestRecord(...)` call, pass:

```python
            alternative_category=meta.get("alternative_category"),
```

- [ ] **Step 8: Run the label tests**

Run: `.venv/bin/python -m pytest tests/test_generator_labels.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 9: Run the full regression contract**

Run: `.venv/bin/python -m pytest tests/test_generator_cli.py tests/test_generator_independence.py tests/test_generator_manifest.py tests/test_generator_reference_join.py -v`

Expected: all PASS. If `test_clean_invoices_produce_no_findings` or
`test_no_invoice_carries_an_UNDECLARED_finding` goes red, **do not change the
generator reflexively** — spec §6 and `test_generator_cli.py:92` both record that
twice already the validator was what was wrong. The 8 real fixtures arbitrate.

- [ ] **Step 10: Regenerate the corpus and spot-check the fix**

```bash
make generate
.venv/bin/python -c "
import json
rows=[json.loads(l) for l in open('corpus/manifest.jsonl')]
for r in rows[:3]:
    print(r['invoice_id'], r['category'], r['department'])
"
```

Expected: `INV-0001`, `INV-0002`, `INV-0003` all `MARKETING` / `MKT` — the three
budget-case invoices that previously carried three different labels for one
description.

- [ ] **Step 11: Commit**

```bash
git add scripts/generate_invoices.py scripts/generator/manifest.py tests/test_generator_labels.py
git commit -m "fix(generator): choose category first, draw the description from it

Measured 2026-07-30: INV-0001/0002/0003 carried the identical description
'Chi phí quảng cáo trực tuyến tháng 07/2026' and were labelled MARKETING,
IT_SERVICES and OFFICE_SUPPLIES. Both category and department were
assigned i % len(...), independent of the text, so the label was not a
function of the invoice and no classifier could beat chance.

Category is now decided when the spec is built, the description is drawn
from that category's pool, and department is derived from the category
with a declared 15% noise share. That rate is the ceiling on department
accuracy and is recorded as such.

Also stops discarding what defects.AMBIGUOUS_DESCRIPTIONS already knew:
each entry pairs a description with (primary, alternative), and line 313
read it as `desc, _`. The alternative now reaches the manifest so M6 can
separate 'chose the plausible alternative' from 'was wrong'."
```

---

### Task 5: `canonical_budget_rank` means canonical position

**Files:**
- Modify: `scripts/generate_invoices.py` (the budget-case loop from Task 4 step 5)
- Test: `tests/test_generator_budget_case.py` (existing file — add to it)

**Interfaces:**
- Consumes: `defects.build_budget_overrun_case`.
- Produces: manifest `canonical_budget_rank` = 1-based position in `(issue_date, invoice_series, invoice_number)` order.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_generator_budget_case.py`:

```python
def test_canonical_budget_rank_is_canonical_position(tmp_path):
    """The field name says canonical; the value was file order.

    Measured 2026-07-30: rank was assigned by `enumerate` over construction
    order (generate_invoices.py:164), which for this case is invoice-number
    order — and defects.py:163 DELIBERATELY makes invoice-number order the
    reverse of canonical order, so shuffling can tell a sorted implementation
    from an unsorted one. So rank=1 was INV-0001, which is THIRD in canonical
    order and the one that breaches. M4-D reading this field as canonical
    position would have got exactly the wrong answer, and passed.
    See spec 2026-07-31-m4a §1.1 D2.
    """
    import json

    from scripts import generate_invoices

    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    rows = [json.loads(line) for line in (out / "manifest.jsonl").open(encoding="utf-8")]

    ranked = [r for r in rows if r["canonical_budget_rank"] is not None]
    assert len(ranked) == 3, f"expected the 3-invoice budget case, got {len(ranked)}"

    canonical = sorted(
        ranked, key=lambda r: (r["issue_date"], r["invoice_series"], r["invoice_number"])
    )
    for position, record in enumerate(canonical, start=1):
        assert record["canonical_budget_rank"] == position, (
            f"{record['invoice_id']} issued {record['issue_date']} is #{position} in "
            f"canonical order but carries rank {record['canonical_budget_rank']}"
        )

    # The one that breaches is LAST in canonical order, not first in file order.
    assert canonical[-1]["canonical_budget_rank"] == 3
    assert canonical[-1]["issue_date"].endswith("-20")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generator_budget_case.py::test_canonical_budget_rank_is_canonical_position -v`
Expected: FAIL — `INV-0003 issued 2026-07-05 is #1 in canonical order but carries rank 3`.

- [ ] **Step 3: Write the implementation**

Replace the budget-case append loop in `scripts/generate_invoices.py`:

```python
    # Rank by CANONICAL order (issue_date, series, number), not by construction
    # order. defects.py:163 deliberately makes invoice-number order the reverse
    # of canonical order so that shuffling arrival order can distinguish a
    # sorted implementation from an unsorted one; ranking by construction order
    # therefore stored precisely the reverse of what the field name promises.
    canonical_case = sorted(
        budget_case_specs,
        key=lambda s: (s.issue_date, s.invoice_series, s.invoice_number),
    )
    ranks = {id(spec): rank for rank, spec in enumerate(canonical_case, start=1)}
    for bspec in budget_case_specs:
        specs.append(
            (bspec, ["budget_overrun"], {"rank": ranks[id(bspec)], "category": "MARKETING"})
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_generator_budget_case.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 5: Mutation-verify**

Reverse the sort (`reverse=True` on `sorted`), run the test, confirm RED. Restore and confirm green.

- [ ] **Step 6: Commit**

```bash
git add scripts/generate_invoices.py tests/test_generator_budget_case.py
git commit -m "fix(generator): canonical_budget_rank holds canonical position

It held construction order, which for the budget case is invoice-number
order — and defects.py:163 deliberately makes that the reverse of
canonical order. So rank=1 was INV-0001, which is third in canonical
order and the one that breaches.

Nothing pinned the field: the only test mentioning it passed None. M4-D's
test_budget_serialization would have read it as canonical position, got
the reverse, and passed while proving nothing. Mutation-verified."
```

---

### Task 6: Tune the tier mix and record the baseline

**Files:**
- Modify: `scripts/generator/descriptions.py` (`TIER_MIX` only)
- Create: `tests/test_corpus_baseline_band.py`
- Create: `docs/notes/2026-07-31-m4a-corpus-labels.md`

**Interfaces:**
- Consumes: `invoice_agent.eval.baseline.classify` (Task 2), `descriptions.TIER_MIX` (Task 1), the regenerated corpus (Task 4).
- Produces: a recorded baseline accuracy, and a test that pins it to the declared band.

- [ ] **Step 1: Write the measurement test**

Create `tests/test_corpus_baseline_band.py`:

```python
"""The corpus must leave the LLM room to win, and the baseline room to compete.

Measured 2026-07-30 before Task 4: baseline agreement 5/285 = 1.8%, below the
16.7% chance rate. A baseline near 100% would be the opposite failure — it would
mean the classify node does not need an LLM at all (principle 3), which is a
legitimate finding but not the one this corpus is built to support.
"""

import pathlib

from scripts import generate_invoices
from scripts.generator import manifest
from scripts.seed_reference import CATEGORIES

from invoice_agent.eval import baseline  # TEST-ONLY
from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY

KEYWORDS = {code: list(keywords) for code, _n, _d, _v, _c, keywords in CATEGORIES}

# The band declared in spec 2026-07-31-m4a §3.2. Measured value and the TIER_MIX
# that produced it are recorded in docs/notes/2026-07-31-m4a-corpus-labels.md.
BAND_LOW, BAND_HIGH = 0.55, 0.70


def measure(out: pathlib.Path, records: list[dict]) -> tuple[int, int, int]:
    """Returns (correct, no_match, total) over XML invoices."""
    correct = no_match = total = 0
    for record in records:
        if record["source_type"] != "xml":
            continue
        invoice = parse_invoice_xml((out.parent / record["path"]).read_bytes())
        text = " ".join(line.description or "" for line in invoice.lines)
        predicted = baseline.classify(text, KEYWORDS)
        total += 1
        if predicted is None:
            no_match += 1
        elif predicted == record["category"]:
            correct += 1
    return correct, no_match, total


def test_baseline_accuracy_is_in_the_tuned_band(tmp_path):
    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    records = manifest.read_manifest(out / "manifest.jsonl")
    correct, no_match, total = measure(out, records)
    accuracy = correct / total
    assert BAND_LOW <= accuracy <= BAND_HIGH, (
        f"baseline accuracy {accuracy:.1%} ({correct}/{total}, {no_match} no-match) "
        f"is outside the declared band {BAND_LOW:.0%}-{BAND_HIGH:.0%}. "
        f"Retune descriptions.TIER_MIX, or record the real number and drop the "
        f"band per principle 2 — do not adjust the number to fit."
    )


def test_baseline_beats_chance_by_a_wide_margin(tmp_path):
    """The floor the previous corpus failed: 1.8% against a 16.7% chance rate."""
    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    records = manifest.read_manifest(out / "manifest.jsonl")
    correct, _no_match, total = measure(out, records)
    assert correct / total > 2 * (1 / 6)
```

- [ ] **Step 2: Run it and read the number**

Run: `.venv/bin/python -m pytest tests/test_corpus_baseline_band.py -v`

Record whatever it prints. If it is inside the band, go to step 4. If not, step 3.

- [ ] **Step 3: Retune if needed**

The keyword weight in `TIER_MIX` is approximately the accuracy, because
keyword-tier descriptions are the ones the baseline can match. Adjust
`TIER_MIX["keyword"]` toward the middle of the band (keeping the three weights
summing to 1.0), rerun step 2, and repeat at most three times.

If three attempts cannot land inside the band without contriving text,
**stop adjusting**. Record the real number, widen or drop the band in both the
test and the spec, and write the reason in the note — principle 2 says a
technique that misses its number gets its reason recorded, not its number edited.

- [ ] **Step 4: Write the note**

Create `docs/notes/2026-07-31-m4a-corpus-labels.md` recording, with real numbers
from the runs above:

- the before measurement (5/285 = 1.8%, 249/285 no-match, chance 16.7%);
- the after measurement (correct/total, no-match count, accuracy);
- the final `TIER_MIX`;
- `DEPARTMENT_NOISE_RATE` and the sentence that it is M4-C's accuracy ceiling;
- the §4.1 honesty note: the baseline score is a construction parameter, and only
  the LLM's margin over it is evidence.

- [ ] **Step 5: Update the constant comment**

Make sure `TIER_MIX`'s comment in `descriptions.py` names the final measured
accuracy and `docs/notes/2026-07-31-m4a-corpus-labels.md`, per CLAUDE.md's
date + measurement + report-filename rule.

- [ ] **Step 6: Commit**

```bash
git add scripts/generator/descriptions.py tests/test_corpus_baseline_band.py docs/notes/2026-07-31-m4a-corpus-labels.md
git commit -m "test(corpus): pin the keywords_vi baseline to its tuned band

Before (2026-07-30): 5/285 = 1.8% agreement, 249/285 matching no keyword,
against a 16.7% chance rate. After: see the note.

The band is a guard in both directions. Too low and no classifier can
beat chance; too high and principle 3 says the classify node does not
need an LLM. The baseline score is a construction parameter, not a
measurement — only the LLM's margin over it is evidence."
```

---

### Task 7: The declared gate batch

**Files:**
- Create: `scripts/generator/gate_batch.py`
- Modify: `scripts/generate_invoices.py` (emit the JSON beside the manifest)
- Test: `tests/test_gate_batch.py`

**Interfaces:**
- Consumes: the regenerated manifest; `invoice_agent.domain.budget.consume` (test only).
- Produces: `GATE_BATCH_IDS: tuple[str, ...]`, `EXPECTED_BREACH_ID: str`, `write_gate_batch(path, records) -> None`; the emitted `corpus/gate_batch.json`.

`corpus/` is gitignored, so the module is the committed source of truth and the
JSON is a generated artifact — the same relationship `manifest.jsonl` already has.

- [ ] **Step 1: Compute the id list mechanically**

The selection is rule-driven, not a judgement call, so compute it rather than
choose it. Run this and keep the paste-ready block it prints:

```bash
make generate
.venv/bin/python - <<'PY'
import json

rows = [json.loads(line) for line in open("corpus/manifest.jsonl", encoding="utf-8")]
by_id = {r["invoice_id"]: r for r in rows}

def first(pred):
    return next(r["invoice_id"] for r in rows if pred(r))

# The three budget-case invoices: the batch's ONLY MKT members, or other MKT
# spend changes which one breaches and the expected chain stops holding.
chosen = ["INV-0001", "INV-0002", "INV-0003"]

# One exact duplicate and its original. `duplicate_of` names the original's
# natural key, so find the original by matching invoice_number.
dup = next(r for r in rows if "exact_duplicate" in r["defects"])
original = next(
    r["invoice_id"] for r in rows
    if r["invoice_number"] == dup["invoice_number"] and r["invoice_id"] != dup["invoice_id"]
)
chosen += [original, dup["invoice_id"]]

chosen.append(first(lambda r: "near_duplicate" in r["defects"]))
chosen.append(first(lambda r: "new_vendor" in r["defects"]))
chosen.append(first(lambda r: "price_outlier" in r["defects"]))

# Fill to 30 with clean, non-MKT, XML-backed invoices.
for r in rows:
    if len(chosen) == 30:
        break
    if (
        r["invoice_id"] not in chosen
        and not r["defects"]
        and r["department"] != "MKT"
        and r["source_type"] == "xml"
    ):
        chosen.append(r["invoice_id"])

assert len(chosen) == 30, len(chosen)
mkt = sorted(i for i in chosen if by_id[i]["department"] == "MKT")
assert mkt == ["INV-0001", "INV-0002", "INV-0003"], mkt

print("GATE_BATCH_IDS: tuple[str, ...] = (")
for i in chosen:
    r = by_id[i]
    note = ", ".join(r["defects"]) or "clean"
    print(f'    "{i}",  # {r["department"]} / {r["category"]} — {note}')
print(")")
PY
```

The two asserts are the selection rules enforced at discovery time; the tests in
step 2 enforce them again against the frozen list.

- [ ] **Step 2: Write the failing test**

Create `tests/test_gate_batch.py`:

```python
"""The gate batch M4-D measures against.

BUDGET_BREACH is a property of the BATCH, not of the invoice: all 12
budget_overrun invoices are MKT, MKT's seeded limit is 50,000,000, and the corpus
holds 59 MKT invoices totalling 993,010,000 (measured 2026-07-30). So a
per-invoice expectation is only true relative to a declared batch, and this is
that declaration. See spec 2026-07-31-m4a §1.1 D5 and §5.
"""

import datetime
import json
from decimal import Decimal

from scripts import generate_invoices
from scripts.generator import gate_batch, manifest

from invoice_agent.domain import budget  # TEST-ONLY

MKT_LIMIT = Decimal("50000000")
WARN_PCT = Decimal("0.8")


def build(tmp_path):
    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    return out, manifest.read_manifest(out / "manifest.jsonl")


def test_gate_batch_has_thirty_distinct_invoices():
    assert len(gate_batch.GATE_BATCH_IDS) == 30
    assert len(set(gate_batch.GATE_BATCH_IDS)) == 30


def test_gate_batch_ids_all_exist(tmp_path):
    _out, records = build(tmp_path)
    known = {r["invoice_id"] for r in records}
    missing = [i for i in gate_batch.GATE_BATCH_IDS if i not in known]
    assert not missing, f"declared ids not in the corpus: {missing}"


def test_gate_batch_covers_the_cases_it_claims(tmp_path):
    """Coverage, asserted rather than intended. If regeneration moves a defect
    off one of these ids, this goes red instead of the batch quietly losing
    the case M4-D exists to exercise."""
    _out, records = build(tmp_path)
    chosen = [r for r in records if r["invoice_id"] in set(gate_batch.GATE_BATCH_IDS)]
    defect_union = {d for r in chosen for d in r["defects"]}
    for required in ("budget_overrun", "exact_duplicate", "near_duplicate",
                     "new_vendor", "price_outlier"):
        assert required in defect_union, f"gate batch has no {required} case"


def test_the_budget_case_invoices_are_the_only_MKT_members(tmp_path):
    """Any other MKT spend consumes the same 50,000,000 and changes which
    invoice breaches — the expected chain below would silently stop holding."""
    _out, records = build(tmp_path)
    chosen = [r for r in records if r["invoice_id"] in set(gate_batch.GATE_BATCH_IDS)]
    mkt = sorted(r["invoice_id"] for r in chosen if r["department"] == "MKT")
    assert mkt == ["INV-0001", "INV-0002", "INV-0003"], mkt


def test_declared_breach_matches_domain_budget(tmp_path):
    """The declaration is checked against the arithmetic, not asserted beside it."""
    _out, records = build(tmp_path)
    by_id = {r["invoice_id"]: r for r in records}
    mkt_inputs = [
        budget.BudgetInput(
            invoice_id=r["invoice_id"],
            issue_date=datetime.date.fromisoformat(r["issue_date"]),
            invoice_series=r["invoice_series"],
            invoice_number=r["invoice_number"],
            amount=Decimal(r["total_amount"]),
        )
        for r in (by_id[i] for i in gate_batch.GATE_BATCH_IDS)
        if r["department"] == "MKT"
    ]
    consumptions = budget.consume(MKT_LIMIT, WARN_PCT, mkt_inputs)
    breached = [c.invoice_id for c in consumptions
                if any(f.code == "BUDGET_BREACH" for f in c.findings)]
    assert breached == [gate_batch.EXPECTED_BREACH_ID], breached


def test_generate_emits_gate_batch_json(tmp_path):
    out, _records = build(tmp_path)
    payload = json.loads((out / "gate_batch.json").read_text(encoding="utf-8"))
    assert payload["invoice_ids"] == list(gate_batch.GATE_BATCH_IDS)
    assert payload["expected_breach_id"] == gate_batch.EXPECTED_BREACH_ID
    assert len(payload["balance_after_chain"]) == 3
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gate_batch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.generator.gate_batch'`

- [ ] **Step 4: Write the module**

Create `scripts/generator/gate_batch.py`, substituting the ids discovered in
step 1:

```python
"""The 30-invoice batch M4's gate is measured against.

WHY DECLARED RATHER THAN SAMPLED. BUDGET_BREACH is a property of the batch, not
of the invoice: measured 2026-07-30, the corpus holds 59 MKT invoices totalling
993,010,000 against MKT's seeded limit of 50,000,000, so any batch containing MKT
spend beyond the three-invoice case breaches far more than the manifest declares.
A per-invoice expectation is only meaningful relative to a named batch.

The roadmap's original phrase — "batch 30 invoices -> ~26 auto, ~4 flagged" —
matches neither this nor the corpus's 28% finding rate (84/300 carry at least one
expected finding, so a random 30 flags ~8). The gate criterion is therefore EXACT
AGREEMENT between what the batch flags and what this file declares; the auto/flag
split is reported as whatever it is.

`corpus/` is gitignored, so this module is the committed source of truth and
`corpus/gate_batch.json` is a generated artifact — the same relationship
`manifest.jsonl` already has.

COMPOSITION, chosen for coverage and asserted by tests/test_gate_batch.py:
  * INV-0001/0002/0003 - the budget case, whose canonical order is the reverse
    of their file order, and the batch's ONLY MKT members;
  * one exact duplicate and its original;
  * one near-duplicate;
  * one NEW_VENDOR, one PRICE_OUTLIER;
  * the remainder clean and non-MKT.
"""

import json
import pathlib
from decimal import Decimal
from typing import Any

# ⚠️ FROZEN LIST — ids discovered from the corpus regenerated at
# count=300, seed=42 by plan 2026-07-31-m4a Task 7 step 1. If regeneration moves
# a defect off one of these, tests/test_gate_batch.py goes red rather than the
# batch quietly losing the case it was chosen to exercise.
GATE_BATCH_IDS: tuple[str, ...] = (
    # Paste the block printed by plan 2026-07-31-m4a Task 7 step 1 here, keeping
    # its per-id comments — they record why each invoice is in the batch, which
    # is what makes a later drift readable rather than mysterious.
)

# Third in canonical order (issue_date 2026-07-20), first in file order — the
# distinction canonical_budget_rank exists to record. See spec §1.1 D2.
EXPECTED_BREACH_ID = "INV-0001"

MKT_LIMIT = Decimal("50000000")


def write_gate_batch(path: pathlib.Path, records: list[dict[str, Any]]) -> None:
    """Emit the resolved batch beside the manifest.

    The balance chain is computed from the manifest's own amounts in canonical
    order, so the JSON cannot drift from the corpus it describes.
    """
    chosen = {r["invoice_id"]: r for r in records if r["invoice_id"] in set(GATE_BATCH_IDS)}
    mkt = sorted(
        (r for r in chosen.values() if r["department"] == "MKT"),
        key=lambda r: (r["issue_date"], r["invoice_series"], r["invoice_number"]),
    )
    chain: list[dict[str, str]] = []
    balance = MKT_LIMIT
    for record in mkt:
        before = balance
        balance = before - Decimal(record["total_amount"])
        chain.append(
            {
                "invoice_id": record["invoice_id"],
                "balance_before": str(before),
                "balance_after": str(balance),
            }
        )

    payload = {
        "invoice_ids": list(GATE_BATCH_IDS),
        "expected_breach_id": EXPECTED_BREACH_ID,
        "mkt_limit": str(MKT_LIMIT),
        "balance_after_chain": chain,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
```

- [ ] **Step 5: Emit it from the generator**

In `scripts/generate_invoices.py`, add `gate_batch` to the generator imports:

```python
from scripts.generator import (
    defects, descriptions, gate_batch, manifest, pdf, templates, vendors,
)
```

Then replace the final `manifest.write_manifest(...)` line of `generate` with:

```python
    manifest.write_manifest(out / "manifest.jsonl", records)
    # Read back rather than passing `records` directly: the manifest encodes
    # Decimal as str, and the gate batch must be computed from exactly the bytes
    # on disk so the two artifacts cannot disagree about an amount.
    gate_batch.write_gate_batch(
        out / "gate_batch.json", manifest.read_manifest(out / "manifest.jsonl")
    )
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_gate_batch.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 7: Mutation-verify the arithmetic check**

Change `EXPECTED_BREACH_ID` to `"INV-0003"`, run
`test_declared_breach_matches_domain_budget`, confirm RED. Restore and confirm green.

- [ ] **Step 8: Commit**

```bash
git add scripts/generator/gate_batch.py scripts/generate_invoices.py tests/test_gate_batch.py
git commit -m "feat(corpus): declare the 30-invoice gate batch

BUDGET_BREACH is a property of the batch, not the invoice: the corpus
holds 59 MKT invoices totalling 993,010,000 against a 50,000,000 limit,
so any batch with MKT spend beyond the three-invoice case breaches far
more than the manifest declares.

The gate criterion becomes exact agreement with this declaration rather
than the roadmap's '~26 auto, ~4 flagged', which matched neither the
corpus's 28% finding rate nor the batch-dependence of budget breach.

The declared breach is checked against domain.budget.consume rather than
asserted beside it, and the batch's only MKT members are the three case
invoices — any other MKT spend would silently change which one breaches."
```

---

### Task 8: Hand-labelled real fixtures as the held-out check

**Files:**
- Create: `tests/fixtures/real_samples/categories.json`
- Test: `tests/test_real_fixture_baseline.py`

**Interfaces:**
- Consumes: `invoice_agent.eval.baseline.classify` (Task 2).
- Produces: a recorded real-data baseline number.

Spec §4.1: the synthetic baseline score is a construction parameter. These 8 real
invoices are the only non-synthetic check available.

- [ ] **Step 1: Write the labels**

Create `tests/fixtures/real_samples/categories.json`. Labels assigned 2026-07-31
by reading each fixture's line descriptions against the seeded chart of accounts.
Three fixtures are **not labelable**: two are university dormitory fees and one is
a bare subscription code, none of which any seeded category covers. Recording
that is the honest option; forcing a label would fabricate the held-out check.

```json
{
  "_provenance": "Hand-labelled 2026-07-31 (plan 2026-07-31-m4a Task 8) by reading line descriptions against the categories seeded in scripts/seed_reference.py. UNLABELABLE means no seeded category covers the spend; those are excluded from the score rather than guessed.",
  "dienluc_gtgt_cccd_2.1.0.xml": "UTILITIES",
  "hokinhdoanh_banhang_qrcode_2.1.0.xml": "UNLABELABLE",
  "ktx_banhang_cccd_2.1.0.xml": "UNLABELABLE",
  "lienket_gtgt_7dong_2.1.0.xml": "GOODS",
  "newimage_gtgt_pban2.0.1.xml": "GOODS",
  "phongvu_gtgt_tdiep_2.0.0.xml": "TOOLS",
  "viettelpost_gtgt_2.1.0.xml": "LOGISTICS",
  "vnpt_hoadon_banhang_2.1.0.xml": "UNLABELABLE"
}
```

- [ ] **Step 2: Write the test**

Create `tests/test_real_fixture_baseline.py`:

```python
"""The only non-synthetic check on the classification label.

Spec 2026-07-31-m4a §4.1: tier-`keyword` descriptions contain seeded keywords
verbatim, so the synthetic baseline score is a CONSTRUCTION PARAMETER, not a
measurement. Only the LLM's margin over it is evidence, and even that margin is
bounded by whoever wrote the synonym tier. These 8 real invoices are the held-out
check — weak at n=5 labelable, and the only real one that exists.

This test RECORDS rather than gates. It fails only if the fixture set or the
label file drifts out of agreement, never on the score itself.
"""

import json
import pathlib

from scripts.seed_reference import CATEGORIES

from invoice_agent.eval import baseline  # TEST-ONLY
from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY

FIXTURES = pathlib.Path("tests/fixtures/real_samples")
KEYWORDS = {code: list(keywords) for code, _n, _d, _v, _c, keywords in CATEGORIES}


def _labels() -> dict[str, str]:
    raw = json.loads((FIXTURES / "categories.json").read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def test_every_real_fixture_carries_a_label():
    """Add a fixture without labelling it and this goes red, rather than the
    held-out check silently shrinking."""
    on_disk = {p.name for p in FIXTURES.glob("*.xml")}
    labelled = set(_labels())
    assert on_disk == labelled, f"unlabelled: {on_disk - labelled}, stale: {labelled - on_disk}"


def test_labels_name_seeded_categories_or_UNLABELABLE():
    seeded = {row[0] for row in CATEGORIES} | {"UNLABELABLE"}
    for name, label in _labels().items():
        assert label in seeded, f"{name}: {label} is not a seeded category"


def test_baseline_on_real_invoices_is_recorded():
    """Prints the held-out number. Run with `-s` to read it."""
    labels = _labels()
    scored = correct = 0
    lines = []
    for name, truth in sorted(labels.items()):
        if truth == "UNLABELABLE":
            continue
        invoice = parse_invoice_xml((FIXTURES / name).read_bytes())
        text = " ".join(line.description or "" for line in invoice.lines)
        predicted = baseline.classify(text, KEYWORDS)
        scored += 1
        correct += predicted == truth
        lines.append(f"  {name}: truth={truth} predicted={predicted}")
    print(f"\nreal-fixture baseline: {correct}/{scored}")
    print("\n".join(lines))
    assert scored >= 5, "fewer than 5 labelable real fixtures — the check is too weak to report"
```

- [ ] **Step 3: Run it and read the number**

Run: `.venv/bin/python -m pytest tests/test_real_fixture_baseline.py -v -s`
Expected: PASS, 3 tests, with the held-out number printed.

- [ ] **Step 4: Record the number in the note**

Append the real-fixture result to `docs/notes/2026-07-31-m4a-corpus-labels.md`,
beside the synthetic number, with one sentence on the gap between them. Note
also that the real fixtures skew toward `GOODS` — the one seeded category the
synthetic corpus never uses (spec §10 open question 1).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/real_samples/categories.json tests/test_real_fixture_baseline.py docs/notes/2026-07-31-m4a-corpus-labels.md
git commit -m "test(eval): hand-label the 8 real fixtures as a held-out check

The synthetic baseline score is a construction parameter — tier-keyword
descriptions contain seeded keywords verbatim, so this spec chooses the
number. Only the LLM's margin over it is evidence.

These 8 real invoices are the only non-synthetic check available. Three
are not labelable against the seeded chart of accounts (two dormitory
fee invoices, one bare subscription code) and are excluded rather than
guessed; forcing a label would fabricate the check."
```

---

### Task 9: Record the first vision fixture

**Files:**
- Create: one fixture under `tests/fixtures/llm_cache/`
- Create: `tests/test_vision_accuracy.py`
- Modify: `docs/notes/2026-07-31-m4a-corpus-labels.md`

**Interfaces:**
- Consumes: `scripts/run_one.py --invoice`, `LLM_MODE=record`, `settings.llm_cache_dir` (`tests/fixtures/llm_cache`).
- Produces: the project's first vision accuracy number.

**Precondition:** a vision-capable API key must be configured (`vision_model_chain`
in `config.py`; Gemini free tier per plan §2.1). **If no key is available, stop
after step 1**, do not fake a fixture, and go to Task 10 — the debt is then
re-declared explicitly in the note and the roadmap. It has been deferred twice;
a third *silent* deferral is the failure, not the deferral.

- [ ] **Step 1: Confirm a key is configured**

```bash
.venv/bin/python -c "
from invoice_agent.config import Settings
s = Settings()
print('vision chain:', s.vision_model_chain)
print('mode:', s.llm_mode)
"
```

Expected: a non-empty chain and at least one usable key. If not, stop here and
record the block in the note.

- [ ] **Step 2: Pick a PDF twin from the corpus**

```bash
.venv/bin/python -c "
import json
rows=[json.loads(l) for l in open('corpus/manifest.jsonl')]
twins=[r for r in rows if r.get('pdf_path')]
print([(r['invoice_id'], r['pdf_path']) for r in twins])
"
```

Expected: three XML↔PDF twins. Take the first — its XML gives the ground truth
the vision extraction is scored against.

- [ ] **Step 3: Record the call**

```bash
LLM_MODE=record .venv/bin/python -m scripts.run_one --invoice <INV-ID> --corpus corpus
```

Expected: a new fixture file under `tests/fixtures/llm_cache/`.

- [ ] **Step 4: Write the accuracy test**

Create `tests/test_vision_accuracy.py`:

```python
"""The first vision accuracy measurement in this project.

The vision branch shipped at M3-B and M3-C with NO accuracy measurement of any
kind, synthetic or real, because no vision-capable key was configured (M3-B §8.1,
M3-C §8.1). The plan calls this the debt that will hurt most: M6 cannot report a
vision number until it is paid, and paying it later means rebuilding M3's context
first.

Scored against the XML twin, which is ground truth by construction: the same
InvoiceSpec rendered two ways.
"""

import json
import pathlib

import pytest

from invoice_agent.extraction.xml_parser import parse_invoice_xml

CORPUS = pathlib.Path("corpus")

# The invoice recorded in plan 2026-07-31-m4a Task 9 step 3. Substitute the id
# printed by step 2 — it must be one of the three XML<->PDF twins, because the
# XML is what supplies ground truth.
TWIN_ID = "INV-XXXX"

FIELDS = ("invoice_number", "invoice_series", "seller_tax_code", "total_amount")


@pytest.mark.skipif(not CORPUS.exists(), reason="corpus not generated")
def test_vision_extraction_matches_its_xml_twin(monkeypatch):
    """Records per-field agreement. Replay-backed, so it costs no tokens.

    REPLAY IS PINNED HERE, NOT INHERITED. `pyproject.toml` sets only
    `addopts = "-q"` and `config.py` defaults `llm_mode` to "live", so a test
    that built its gateway from the ambient environment would make a real API
    call on every `make check` once a vision key is configured — turning the
    one recording run this task is meant to cost into an unbounded bill.
    """
    monkeypatch.setenv("LLM_MODE", "replay")

    from invoice_agent.config import Settings  # TEST-ONLY
    from invoice_agent.extraction import vision  # TEST-ONLY
    from invoice_agent.llm.gateway import LLMGateway  # TEST-ONLY

    gateway = LLMGateway(Settings())
    rows = [json.loads(line) for line in (CORPUS / "manifest.jsonl").open(encoding="utf-8")]
    record = next(r for r in rows if r["invoice_id"] == TWIN_ID)
    truth = parse_invoice_xml((CORPUS.parent / record["path"]).read_bytes())

    extracted = vision.extract(
        (CORPUS.parent / record["pdf_path"]).read_bytes(),
        gateway=gateway,
        current_errors=None,
        calls=[],
    )

    agree = [f for f in FIELDS if getattr(extracted, f) == getattr(truth, f)]
    print(f"\nvision field agreement: {len(agree)}/{len(FIELDS)} — {agree}")
    assert extracted is not None, "vision returned nothing; there is no number to report"
```

- [ ] **Step 5: Verify it replays with no key**

```bash
LLM_MODE=replay .venv/bin/python -m pytest tests/test_vision_accuracy.py -v -s
```

Expected: PASS with the agreement number printed, and no network call.

- [ ] **Step 6: Verify the whole suite stays hermetic**

```bash
env -u GROQ_API_KEY -u GEMINI_API_KEY LLM_MODE=replay .venv/bin/python -m pytest -q
```

Expected: all green with no API keys — the condition CI and the live demo depend on.

- [ ] **Step 7: Record the number and commit**

Add the vision agreement number to `docs/notes/2026-07-31-m4a-corpus-labels.md`.

```bash
git add tests/fixtures/llm_cache tests/test_vision_accuracy.py docs/notes/2026-07-31-m4a-corpus-labels.md
git commit -m "test(vision): first vision accuracy measurement

The vision branch shipped at M3-B and M3-C with no accuracy measurement
of any kind, synthetic or real, because no vision-capable key was
configured. Scored against the XML twin, which is ground truth by
construction — the same InvoiceSpec rendered two ways.

Fixture committed, so the suite stays green under LLM_MODE=replay with
no API keys."
```

---

### Task 10: Roadmap update and gate verification

**Files:**
- Modify: `docs/Plan_Project_F_2026-07-25.md` (§1 "Next", §3 M4 table and gate, §7 status row)
- Modify: `docs/notes/2026-07-31-m4a-corpus-labels.md`

- [ ] **Step 1: Update the M4 section of the roadmap**

In `docs/Plan_Project_F_2026-07-25.md` §3, add a row above item 4.1 for M4-A, and
replace the M4 gate paragraph. The new gate text:

```markdown
**Gate M4**: exact agreement between the declared gate batch
(`scripts/generator/gate_batch.py`, 30 invoices) and what the batch actually
flags — the auto/flag split is reported, not targeted, because the corpus's
finding rate is 28% and `BUDGET_BREACH` is a property of the batch rather than
of the invoice; **`test_budget_serialization.py` green** — shuffle input order
10 times, `balance_after` chain byte-identical and the same invoice breaches;
one corrupt file lands in `failed` without killing the batch; Auditor override
precision measured and reported however it scores (below 50% is net harmful and
gets said).
```

- [ ] **Step 2: Update §7's status table**

Replace the empty M4 row with this, filling the two bracketed numbers from the
note written in Task 6 and Task 9:

```markdown
| M4 — Multi-agent + memory + serialization | 🚧 | | Split into four specs: **M4-A** corpus & reference alignment (new — not in the original six items) · **M4-B** memory + `signals` · **M4-C** agents · **M4-D** batch graph + serial reconciliation. **M4-A done 2026-07-31.** Planning found **five defects in M2/M3 outputs**, all before any M4 code was written, and three of them made M4's own gate unmeasurable: generator departments `(MKT,IT,HR,LOG,ADM,SAL)` did not join the seeded `(MKT,ADMIN,OPS,IT,SALES)`, so **194 of 300 invoices could not satisfy `invoices.department`** and four departments had no budget to reconcile against; `canonical_budget_rank` held construction order, which `defects.py:163` deliberately makes the **reverse** of canonical order; the corpus **did not encode `category` in its text** — baseline agreement **5/285 = 1.8%**, below the 16.7% chance rate, with 249/285 matching no keyword at all, and `INV-0001/0002/0003` carrying one identical description under three different labels; `AMBIGUOUS_DESCRIPTIONS` already paired each description with `(primary, alternative)` and line 313 read it as `desc, _`; and `BUDGET_BREACH` was treated as a per-invoice property when 59 MKT invoices total 993,010,000 against a 50,000,000 limit. Baseline after the fix: **[X]%** with `TIER_MIX` recorded. Held-out real-fixture check: **[Y]/5**. Evidence `docs/notes/2026-07-31-m4a-corpus-labels.md`. **These were found by planning, not by implementation** — the first milestone in this project where that is true |
```

- [ ] **Step 3: Update §1's "Next" paragraph**

The paragraph currently lists the vision debt, the PII purge, the two design
debts and the ADRs. Update: vision measurement now paid (or explicitly
re-declared if Task 9 was blocked), and add that M4-A found five defects in
M2/M3 outputs.

- [ ] **Step 4: Run the full check**

```bash
make check
```

Expected: ruff clean, mypy clean, all tests green. Fix anything red before
continuing — a milestone is not closed on a red `make check`.

- [ ] **Step 5: Verify replay hermeticity one more time**

```bash
env -u GROQ_API_KEY -u GEMINI_API_KEY LLM_MODE=replay .venv/bin/python -m pytest -q
```

Expected: green.

- [ ] **Step 6: Commit and push**

```bash
git add docs/Plan_Project_F_2026-07-25.md docs/notes/2026-07-31-m4a-corpus-labels.md
git commit -m "docs(roadmap): close M4-A — corpus and reference alignment

Five defects, all found by planning rather than by implementation: the
department codes did not join the seed (194 of 300 invoices could not be
inserted), canonical_budget_rank held the reverse of canonical order, the
corpus did not encode category in its text (baseline 1.8%, below the
16.7% chance rate), the ambiguous-category pairs were discarded one line
before use, and BUDGET_BREACH was treated as a per-invoice property.

Gate restated: exact agreement with a declared 30-invoice batch, rather
than a target auto/flag split that matched neither the corpus nor the
batch-dependence of budget breach."
git push
```

- [ ] **Step 7: Confirm CI is green on the pushed commit**

```bash
gh run list --limit 3
gh run watch
```

Expected: green on the commit just pushed. **This is success criterion 10 and
the one three milestones running have failed** — once on a commit that was
pushed, ran red in CI, and was not looked at. Do not report M4-A complete until
this shows green.

---

## Notes for the implementer

**When a corpus test goes red, the validator may be what is wrong.** Twice
already it was: M1's naive arithmetic rule flagged 6 of 8 real invoices, and
M2-B's unconditioned guards flagged 3 of 8. `tests/test_generator_cli.py:92`
records this. **The 8 real fixtures arbitrate**, not the generator and not the
domain layer.

**Do not adjust a measurement to fit a target.** If the baseline lands outside
55–70%, record the real number and drop the band (principle 2). The project's
own history is that recorded failures are portfolio content.

**Every guard added here must be shown red under mutation before it is trusted.**
Tasks 3, 5 and 7 have explicit mutation steps. M1 produced four tests that passed
while guarding nothing, M2 produced two more, M3 produced three; the pattern is
the finding, and the only defence that has worked is breaking the source and
watching the test fail.
