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
