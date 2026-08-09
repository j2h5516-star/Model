"""
sec_fundamentals.py — SEC 8-K 실적 보도자료에서 논갭(non-GAAP) 실적 수집
======================================================================

이 파일이 하는 일:
  1) edgartools로 각 종목의 8-K(Item 2.02 = 실적발표) 공시를 찾고
  2) 첨부된 실적 보도자료에서 다음 숫자를 뽑아냅니다:
       · 매출(revenue)
       · 논갭 영업이익(non-GAAP operating income)
       · 논갭 매출총이익률(non-GAAP gross margin %)
  3) 숫자를 못 찾으면 단계적으로 대체 방법을 시도합니다:
       ① 직접공시 : 보도자료에 논갭 영업이익이 그대로 적혀 있음
       ② 역산     : 매출 × 논갭 GM% − 논갭 영업비용
       ③ 근사치   : SEC XBRL의 GAAP 영업이익 + 주식보상비 + 무형자산상각

"논갭(non-GAAP)"이란?
  회계 규정(GAAP)대로 계산한 이익에서 일회성 비용이나 주식보상비처럼
  "실제 현금이 나가지 않는 항목"을 빼고 다시 계산한 이익입니다.
  회사의 진짜 영업 체력을 보려고 업계에서 널리 쓰는 숫자입니다.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

import config as cfg

# ---------------------------------------------------------------------------
# 숫자 뽑아내기 도우미
# ---------------------------------------------------------------------------

# 보도자료 표에서 "(단위: 천 달러)" 같은 표기를 찾기 위한 패턴
_UNIT_PATTERNS = [
    (re.compile(r"\(\s*in\s+thousands", re.I), 1_000),
    (re.compile(r"\(\s*in\s+millions", re.I), 1_000_000),
    (re.compile(r"\(\s*in\s+billions", re.I), 1_000_000_000),
    (re.compile(r"amounts?\s+in\s+thousands", re.I), 1_000),
    (re.compile(r"amounts?\s+in\s+millions", re.I), 1_000_000),
]

# 숫자 하나를 나타내는 패턴 — 예: $ 1,234.5  /  (1,234)  /  45.1
_NUMBER_RE = re.compile(
    r"""
    (?P<paren_open>\()?          # 괄호로 시작하면 음수
    \s*\$?\s*
    (?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)
    \s*
    (?P<paren_close>\))?
    \s*
    (?P<scale>million|billion|thousand|bn|mm|m\b|b\b)?
    """,
    re.I | re.X,
)

# 텍스트에 붙은 단위 단어 → 곱할 배수
_WORD_SCALE = {
    "thousand": 1_000,
    "million": 1_000_000,
    "mm": 1_000_000,
    "m": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
}


def _detect_table_unit(text: str, position: int, window: int = 3000) -> int:
    """숫자가 발견된 위치 바로 앞쪽에서 "(in thousands)" 같은 단위 표기를 찾습니다.

    표 형태의 보도자료는 숫자에 단위가 안 붙어 있고 표 제목에만 적혀 있기 때문입니다.
    못 찾으면 1(단위 없음)을 돌려줍니다.
    """
    start = max(0, position - window)
    context = text[start:position]
    best_scale, best_pos = 1, -1
    for pattern, scale in _UNIT_PATTERNS:
        for match in pattern.finditer(context):
            if match.start() > best_pos:      # 가장 가까운(=뒤쪽) 표기를 채택
                best_pos, best_scale = match.start(), scale
    return best_scale


def _parse_number_at(
    text: str, search_from: int, search_len: int = 160
) -> tuple[float, int, int, bool] | None:
    """지정한 위치부터 오른쪽으로 훑으며 첫 번째 숫자를 찾아 값으로 바꿉니다.

    반환값: (숫자값, 시작위치, 끝위치, 단위단어가붙었는지) — 못 찾으면 None
    """
    segment = text[search_from : search_from + search_len]
    match = _NUMBER_RE.search(segment)
    if not match:
        return None

    raw = match.group("num").replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None

    # 회계 표기에서 괄호는 음수를 뜻합니다: (1,234) = -1234
    if match.group("paren_open") and match.group("paren_close"):
        value = -value

    # 숫자 뒤에 million 같은 단어가 붙어 있으면 그 배수를 적용
    scale_word = (match.group("scale") or "").lower().strip()
    had_word_scale = scale_word in _WORD_SCALE
    if had_word_scale:
        value *= _WORD_SCALE[scale_word]

    return (
        value,
        search_from + match.start(),
        search_from + match.end(),
        had_word_scale,
    )


def find_labeled_value(
    text: str,
    label_patterns: list[str],
    *,
    is_percent: bool = False,
    apply_table_unit: bool = True,
) -> float | None:
    """"항목 이름"을 찾고 그 오른쪽에 있는 숫자를 값으로 돌려줍니다.

    예) "Non-GAAP operating income    $ 45,123"  →  45123 × 1000(단위 천) = 45,123,000

    label_patterns: 찾을 항목 이름들(정규식). 앞에 있는 것부터 우선 시도합니다.
    is_percent    : 퍼센트 값이면 True (단위 배수를 적용하지 않음)
    """
    for pattern_str in label_patterns:
        pattern = re.compile(pattern_str, re.I)
        for label_match in pattern.finditer(text):
            search_from = label_match.end()

            # 금액을 찾는 중이라면 퍼센트 숫자는 건너뛰고 계속 오른쪽을 봅니다.
            # 예) "Revenue grew 20% year-over-year to $135.0 million"
            #      → 20을 매출로 오인하지 않고 135.0 million을 찾아냅니다.
            for _ in range(4):   # 같은 문장 안에서 최대 4번까지 다시 시도
                parsed = _parse_number_at(text, search_from)
                if parsed is None:
                    break
                value, number_start, number_end, had_word_scale = parsed

                # 숫자 바로 뒤가 % 인지 확인 (공백은 무시)
                is_percent_value = text[number_end : number_end + 3].lstrip().startswith("%")

                if is_percent:
                    # 퍼센트를 찾는 중: %가 붙어 있고 0~100 범위여야 채택
                    if is_percent_value and 0 < value <= 100:
                        return value
                    search_from = number_end
                    continue

                # 금액을 찾는 중: 퍼센트 숫자는 건너뜁니다
                if is_percent_value:
                    search_from = number_end
                    continue

                # 숫자 뒤에 단위 단어가 없었다면 표 제목의 단위를 적용
                if apply_table_unit and not had_word_scale:
                    value *= _detect_table_unit(text, number_start)
                return value
    return None


# ---------------------------------------------------------------------------
# 보도자료에서 찾을 항목 이름들 (회사마다 표기가 달라 여러 변형을 준비)
# ---------------------------------------------------------------------------

# 논갭 영업이익
LABELS_NONGAAP_OP_INCOME = [
    r"non[-\s]?GAAP\s+(?:income|profit)\s+from\s+operations",
    r"non[-\s]?GAAP\s+operating\s+(?:income|profit)",
    r"adjusted\s+operating\s+(?:income|profit)",
    r"adjusted\s+(?:income|profit)\s+from\s+operations",
    r"operating\s+income\s*\(non[-\s]?GAAP\)",
    r"non[-\s]?GAAP\s+operating\s+results?",
]

# 논갭 매출총이익률(%)
LABELS_NONGAAP_GM_PCT = [
    r"non[-\s]?GAAP\s+gross\s+margin(?:\s+percentage)?",
    r"adjusted\s+gross\s+margin",
    r"gross\s+margin\s*\(non[-\s]?GAAP\)",
    r"non[-\s]?GAAP\s+gross\s+profit\s+margin",
]

# GAAP 매출총이익률(%) — 논갭이 없을 때 대체
LABELS_GAAP_GM_PCT = [
    r"GAAP\s+gross\s+margin",
    r"gross\s+margin(?:\s+percentage)?",
]

# 매출
LABELS_REVENUE = [
    r"total\s+revenues?",
    r"net\s+revenues?",
    r"^\s*revenues?\b",
    r"\brevenues?\b",
    r"net\s+sales",
]

# 논갭 영업비용 (역산에 사용)
LABELS_NONGAAP_OPEX = [
    r"non[-\s]?GAAP\s+(?:total\s+)?operating\s+expenses?",
    r"adjusted\s+(?:total\s+)?operating\s+expenses?",
    r"total\s+operating\s+expenses?\s*\(non[-\s]?GAAP\)",
]


# ---------------------------------------------------------------------------
# 보도자료 텍스트 → 실적 숫자
# ---------------------------------------------------------------------------
def parse_press_release(text: str) -> dict:
    """실적 보도자료 텍스트에서 필요한 숫자를 뽑아냅니다.

    반환 딕셔너리:
      revenue           매출 (달러)
      op_income         논갭 영업이익 (달러)
      gross_margin_pct  논갭 매출총이익률 (%)
      source            "직접공시" / "역산" / None (못 찾음)
      gm_is_gaap        GM%가 GAAP 값이면 True
    """
    result: dict = {
        "revenue": None,
        "op_income": None,
        "gross_margin_pct": None,
        "source": None,
        "gm_is_gaap": False,
        "derivation": "",   # 어떻게 구한 값인지 사람이 읽을 수 있는 설명
    }
    if not text:
        return result

    result["revenue"] = find_labeled_value(text, LABELS_REVENUE)

    # ① 논갭 GM% — 없으면 GAAP GM%로 대체
    gm = find_labeled_value(text, LABELS_NONGAAP_GM_PCT, is_percent=True)
    if gm is None:
        gm = find_labeled_value(text, LABELS_GAAP_GM_PCT, is_percent=True)
        if gm is not None:
            result["gm_is_gaap"] = True
    result["gross_margin_pct"] = gm

    # ② 논갭 영업이익이 직접 적혀 있는가?
    op = find_labeled_value(text, LABELS_NONGAAP_OP_INCOME)
    if op is not None:
        result["op_income"] = op
        result["source"] = cfg.SRC_DIRECT
        result["derivation"] = (
            "보도자료의 GAAP→non-GAAP 조정표에 적힌 "
            f"non-GAAP 영업이익 값을 그대로 사용했습니다 (${op/1e6:,.1f}M)."
        )
        return result

    # ③ 없으면 역산: 매출 × GM% − 논갭 영업비용
    opex = find_labeled_value(text, LABELS_NONGAAP_OPEX)
    if result["revenue"] is not None and gm is not None and opex is not None:
        gross_profit = result["revenue"] * (gm / 100.0)
        result["op_income"] = gross_profit - abs(opex)
        result["source"] = cfg.SRC_DERIVED
        result["derivation"] = (
            "보도자료에 non-GAAP 영업이익이 없어 아래 식으로 역산했습니다.\n\n"
            f"매출 ${result['revenue']/1e6:,.1f}M × GM {gm:.1f}% "
            f"= 매출총이익 ${gross_profit/1e6:,.1f}M\n\n"
            f"매출총이익 ${gross_profit/1e6:,.1f}M − 영업비용 ${abs(opex)/1e6:,.1f}M "
            f"= **${result['op_income']/1e6:,.1f}M**"
        )

    return result


# 회계 분기 이름 뽑기 — 예: "first quarter of fiscal 2025" → "FY2025 Q1"
_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_QUARTER_RE = re.compile(
    r"(first|second|third|fourth)\s+quarter[^.]{0,40}?(?:fiscal\s+(?:year\s+)?)?(20\d{2})",
    re.I,
)
_QUARTER_RE_SHORT = re.compile(r"\bQ([1-4])\s*(?:of\s*)?(?:FY\s*)?(20\d{2}|\d{2})\b", re.I)


def extract_period_label(text: str, filing_date: str) -> str:
    """보도자료에서 "몇 년 몇 분기"인지 알아냅니다. 못 찾으면 제출일로 대신합니다.

    차트 가로축에 들어가므로 최대한 짧게 만듭니다 (예: "25 Q3").
    """
    if text:
        match = _QUARTER_RE.search(text)
        if match:
            return f"{match.group(2)[2:]} Q{_QUARTER_WORDS[match.group(1).lower()]}"
        match = _QUARTER_RE_SHORT.search(text)
        if match:
            year = match.group(2)
            year = year[2:] if len(year) == 4 else year
            return f"{year} Q{match.group(1)}"
    # 분기 표현을 못 찾으면 제출 연월만 짧게 표시 (예: "25/05")
    return f"{filing_date[2:4]}/{filing_date[5:7]}"


def period_end_label(period_end: str) -> str:
    """기간종료일(2026-01-31)을 짧은 표시로 바꿉니다 → "26/01" """
    return f"{period_end[2:4]}/{period_end[5:7]}"


# ---------------------------------------------------------------------------
# 캐시 (한 번 받은 데이터는 저장해 두고 재사용)
# ---------------------------------------------------------------------------
def _cache_path(ticker: str) -> str:
    os.makedirs(cfg.CACHE_DIR, exist_ok=True)
    return os.path.join(cfg.CACHE_DIR, f"{ticker}_fundamentals.json")


def load_cache(ticker: str, ttl_days: int | None = None) -> list[dict] | None:
    """저장해 둔 실적 데이터를 불러옵니다. 너무 오래됐으면 None을 돌려줍니다."""
    if ttl_days is None:
        ttl_days = cfg.CACHE_TTL_DAYS
    path = _cache_path(ticker)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        saved_at = datetime.fromisoformat(payload["saved_at"])
        if datetime.now(timezone.utc) - saved_at > timedelta(days=ttl_days):
            return None
        return payload["quarters"]
    except Exception:
        return None  # 캐시 파일이 깨졌으면 무시하고 새로 받습니다


def save_cache(ticker: str, quarters: list[dict]) -> None:
    """받아온 실적 데이터를 파일로 저장합니다 (다음 실행 때 재사용)."""
    try:
        with open(_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump(
                {"saved_at": datetime.now(timezone.utc).isoformat(), "quarters": quarters},
                f,
                ensure_ascii=False,
                indent=1,
            )
    except OSError:
        pass  # 저장에 실패해도 앱은 계속 동작해야 합니다


# ---------------------------------------------------------------------------
# SEC에서 실제로 받아오기
# ---------------------------------------------------------------------------
_identity_configured = False


def _ensure_identity() -> None:
    """SEC는 요청자 신원(이메일)을 요구합니다. 최초 1회만 설정합니다."""
    global _identity_configured
    if _identity_configured:
        return
    from edgar import set_identity

    set_identity(cfg.SEC_IDENTITY)
    _identity_configured = True


def fetch_earnings_8k(ticker: str, start_date: str | None = None) -> list[dict]:
    """한 종목의 8-K 실적발표를 모두 찾아 숫자를 뽑아냅니다.

    반환: 분기별 실적 목록 (오래된 것부터 순서대로)
    """
    if start_date is None:
        start_date = cfg.EARNINGS_START_DATE

    _ensure_identity()
    from edgar import Company

    quarters: list[dict] = []
    company = Company(ticker)
    filings = company.get_filings(form="8-K", filing_date=f"{start_date}:")

    for filing in filings:
        text = _earnings_text(filing)
        if not text or not _looks_like_earnings(text):
            continue

        parsed = parse_press_release(text)
        if parsed["revenue"] is None and parsed["op_income"] is None:
            continue  # 실적 숫자가 전혀 없으면 실적발표가 아닐 가능성

        filing_date = str(filing.filing_date)
        quarters.append(
            {
                "ticker": ticker,
                "filing_date": filing_date,
                "period_label": extract_period_label(text, filing_date),
                "revenue": parsed["revenue"],
                "op_income": parsed["op_income"],
                "gross_margin_pct": parsed["gross_margin_pct"],
                "source": parsed["source"] or cfg.SRC_DERIVED,
                "gm_is_gaap": parsed["gm_is_gaap"],
                # 화면의 "원문 보기" 링크에 사용 (사용자가 직접 공시를 확인할 수 있도록)
                "filing_url": _safe_filing_url(filing),
                "derivation": parsed.get("derivation", ""),  # 어떻게 계산했는지 설명
                "guidance_text": text[-6000:],  # 가이던스는 보도자료 뒷부분에 나옵니다
            }
        )

    quarters.sort(key=lambda q: q["filing_date"])  # 발표일(8-K 제출일) 순 정렬
    return quarters


def _safe_filing_url(filing) -> str:
    """공시 원문 주소를 가져옵니다 (실패해도 앱이 멈추지 않도록 감쌉니다)."""
    for attr in ("filing_url", "url", "homepage_url"):
        try:
            value = getattr(filing, attr, None)
            if value:
                return str(value)
        except Exception:
            continue
    return ""


# 실적발표 공시인지 알아보는 신호들 (회사마다 표현이 달라 여러 개를 봅니다)
_EARNINGS_HINTS = [
    "item 2.02",
    "results of operations and financial condition",
    "financial results",
    "reports first quarter",
    "reports second quarter",
    "reports third quarter",
    "reports fourth quarter",
    "announces first quarter",
    "announces second quarter",
    "announces third quarter",
    "announces fourth quarter",
    "quarterly results",
    "non-gaap",
]


def _looks_like_earnings(text: str) -> bool:
    """이 공시가 실적발표인지 대략 판별합니다."""
    lowered = text[:20000].lower()
    return any(hint in lowered for hint in _EARNINGS_HINTS)


def _earnings_text(filing) -> str:
    """8-K에서 실적 보도자료 텍스트를 최대한 확보합니다.

    회사마다 첨부 방식이 달라 아래 순서로 시도합니다:
      ① edgartools가 인식한 보도자료(press release) 첨부
      ② EX-99 계열 첨부파일을 직접 뒤져서 읽기
      ③ 공시 본문 전체
    """
    # ① edgartools의 보도자료 인식 기능
    try:
        eightk = filing.obj()
    except Exception:
        eightk = None

    if eightk is not None:
        try:
            releases = eightk.press_releases
            if releases:
                parts = []
                for release in releases:
                    try:
                        parts.append(release.text())
                    except Exception:
                        continue
                if parts:
                    return "\n".join(parts)
        except Exception:
            pass

    # ② EX-99 첨부파일을 직접 찾아 읽기
    try:
        exhibits = filing.attachments.exhibits
        for attachment in exhibits:
            doc_type = str(getattr(attachment, "document_type", "") or "")
            description = str(getattr(attachment, "display_description", "") or "")
            if "99" not in doc_type and "99" not in description:
                continue
            try:
                content = attachment.text()
            except Exception:
                continue
            if content and len(content) > 500:
                return content
    except Exception:
        pass

    # ③ 마지막 수단: 공시 본문 전체
    for source in (eightk, filing):
        if source is None:
            continue
        try:
            content = source.text()
            if content:
                return content
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# 마지막 안전망: SEC XBRL로 근사치 계산
# ---------------------------------------------------------------------------
# XBRL은 회사가 SEC에 제출하는 "기계가 읽는 재무데이터"입니다.
# 여기엔 GAAP 숫자만 있으므로, 논갭에 가깝게 되돌리려고
#   GAAP 영업이익 + 주식보상비 + 무형자산상각
# 을 더해 근사치를 만듭니다.

_XBRL_CONCEPTS = {
    "op_income": ["OperatingIncomeLoss"],
    "sbc": [
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
    ],
    "amortization": [
        "AmortizationOfIntangibleAssets",
        "AmortizationOfAcquiredIntangibleAssets",
        "FiniteLivedIntangibleAssetsAmortizationExpense",
    ],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "gross_profit": ["GrossProfit"],
}


def fetch_xbrl_approximation(ticker: str, start_date: str | None = None) -> list[dict]:
    """XBRL에서 분기 실적 근사치를 만듭니다 (8-K 파싱이 실패했을 때 사용).

    화면에는 "근사치" 배지가 붙습니다.
    """
    if start_date is None:
        start_date = cfg.EARNINGS_START_DATE

    _ensure_identity()
    from edgar import Company

    try:
        facts = Company(ticker).get_facts()
        if facts is None:
            return []
    except Exception:
        return []

    # 개념별로 "분기(3개월)" 데이터만 뽑아 {기간종료일: 값} 형태로 정리
    series: dict[str, dict[str, float]] = {}
    for key, concept_names in _XBRL_CONCEPTS.items():
        merged: dict[str, float] = {}
        for concept in concept_names:
            merged.update(_quarterly_series(facts, concept))
        series[key] = merged

    period_ends = sorted(
        d for d in series.get("op_income", {}) if d >= start_date
    )

    quarters: list[dict] = []
    for period_end in period_ends:
        gaap_op = series["op_income"].get(period_end)
        if gaap_op is None:
            continue

        sbc = series["sbc"].get(period_end) or 0.0
        amort = series["amortization"].get(period_end) or 0.0
        revenue = series["revenue"].get(period_end)
        gross_profit = series["gross_profit"].get(period_end)

        gm_pct = None
        if revenue and gross_profit is not None and revenue > 0:
            gm_pct = gross_profit / revenue * 100.0

        approx_op = gaap_op + sbc + amort
        quarters.append(
            {
                "ticker": ticker,
                "filing_date": period_end,   # 실제 제출일 대신 기간종료일 사용
                "period_label": period_end_label(period_end),
                "revenue": revenue,
                "op_income": approx_op,   # 논갭 근사
                "gross_margin_pct": gm_pct,
                "source": cfg.SRC_APPROX,
                "gm_is_gaap": True,
                "filing_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=8-K",
                "derivation": (
                    "보도자료를 읽지 못해 SEC XBRL 회계데이터로 근사했습니다.\n\n"
                    f"GAAP 영업이익 ${gaap_op/1e6:,.1f}M\n\n"
                    f"+ 주식보상비 ${sbc/1e6:,.1f}M\n\n"
                    f"+ 무형자산상각 ${amort/1e6:,.1f}M\n\n"
                    f"= **${approx_op/1e6:,.1f}M** (근사치)\n\n"
                    "※ GM%는 GAAP 값(매출총이익 ÷ 매출)을 사용했습니다."
                ),
                "guidance_text": "",
            }
        )

    quarters.sort(key=lambda q: q["filing_date"])
    return quarters


def _quarterly_series(facts, concept: str) -> dict[str, float]:
    """XBRL에서 특정 항목의 분기(3개월) 값들을 {기간종료일: 값}으로 뽑습니다."""
    try:
        df = (
            facts.query()
            .by_concept(concept)
            .by_period_length(3)     # 3개월 = 한 분기
            .to_dataframe()
        )
    except Exception:
        return {}

    if df is None or len(df) == 0:
        return {}

    # 열 이름은 버전에 따라 다를 수 있어 후보를 순서대로 확인합니다
    date_col = next((c for c in ("period_end", "end", "period_ending") if c in df.columns), None)
    value_col = next((c for c in ("numeric_value", "value", "val") if c in df.columns), None)
    if date_col is None or value_col is None:
        return {}

    out: dict[str, float] = {}
    for _, row in df.iterrows():
        try:
            key = str(row[date_col])[:10]
            out[key] = float(row[value_col])
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# 바깥에서 호출하는 함수
# ---------------------------------------------------------------------------
def get_fundamentals(
    ticker: str,
    start_date: str | None = None,
    use_cache: bool = True,
) -> list[dict]:
    """한 종목의 분기별 논갭 실적을 가져옵니다 (캐시 → 8-K → XBRL 순).

    어떤 방법으로도 실패하면 빈 목록을 돌려줍니다.
    """
    if use_cache:
        cached = load_cache(ticker)
        if cached is not None:
            return cached

    quarters: list[dict] = []
    try:
        quarters = fetch_earnings_8k(ticker, start_date)
    except Exception:
        quarters = []

    # 8-K에서 영업이익을 하나도 못 건졌으면 XBRL 근사치로 대체
    if not any(q.get("op_income") is not None for q in quarters):
        try:
            approx = fetch_xbrl_approximation(ticker, start_date)
            if approx:
                quarters = approx
        except Exception:
            pass

    if quarters:
        save_cache(ticker, quarters)
    return quarters
