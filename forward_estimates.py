"""
forward_estimates.py — 다음 분기 전망(포워드) 추정
==================================================

무료 데이터만 쓰기 때문에 진짜 "월가 컨센서스"는 구할 수 없습니다.
그래서 다음 두 가지 방법으로 대신합니다:

  ① 가이던스 기반 (더 신뢰도 높음)
     실적 보도자료에 회사가 직접 밝힌 "다음 분기 전망"이 있으면 그걸 씁니다.
     예: "we expect revenue of $180 million to $190 million"
     → 중간값 $185M × 논갭 GM% − 예상 영업비용 = 포워드 영업이익
     화면 배지: 가이던스

  ② 추정 (가이던스가 없을 때)
     yfinance가 제공하는 다음 분기 "매출 컨센서스"에
     최근 4개 분기 평균 논갭 영업마진을 곱해 추정합니다.
     화면 배지: 추정

또한 애널리스트들이 최근 전망을 올렸는지 내렸는지(리비전 방향)를
yfinance의 EPS 추정치 변화 건수로 계산해 +1 / 0 / −1 로 나타냅니다.
"""

from __future__ import annotations

import math
import re

import config as cfg
import data_quality as dq

# ---------------------------------------------------------------------------
# ① 보도자료에서 다음 분기 가이던스 찾기
# ---------------------------------------------------------------------------

# "다음 분기 전망" 문단이 시작되는 신호가 되는 표현들
_GUIDANCE_TRIGGERS = [
    r"business\s+outlook",
    r"financial\s+outlook",
    r"(?:first|second|third|fourth)\s+quarter\s+(?:fiscal\s+)?20\d{2}\s+(?:outlook|guidance)",
    r"guidance\s+for\s+the\s+(?:first|second|third|fourth)\s+quarter",
    r"(?:we|the\s+company)\s+expects?",
    r"outlook\s+for\s+the\s+(?:next|first|second|third|fourth)",
    # "For the third quarter of 2026, AMD expects revenue to be..." 형식.
    # 회사 이름이 주어로 오면 위의 "we|the company expects" 에 안 걸립니다.
    #
    # ⚠️ "for the third quarter of fiscal 2025" 만으로 잡으면 안 됩니다.
    #    보도자료 첫머리의 **과거 서술**("...for the third quarter of fiscal 2025
    #    ended January 31, 2025")까지 걸려서, 전망 문단 대신 손익계산서를
    #    잘라 오게 됩니다. 실제로 그렇게 회귀가 났습니다.
    #    그래서 **앞을 보는 말이 실제로 붙어 있을 때만** 인정합니다.
    r"expects?\s+(?:revenue|net\s+sales)\s+(?:to\s+be|of|in\s+the\s+range)",
]

# 금액 범위 표현: "$180 million to $190 million" / "$180 - $190 million"
_RANGE_RE = re.compile(
    r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(million|billion|bn|mm)?\s*"
    r"(?:to|through|[-–—])\s*"
    r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(million|billion|bn|mm)?",
    re.I,
)

# 단일 금액 표현: "approximately $185 million"
_SINGLE_RE = re.compile(
    r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(million|billion|bn|mm)\b",
    re.I,
)

# 퍼센트 범위: "63% to 65%" / "64% +/- 1%"
_PCT_RANGE_RE = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:to|[-–—])\s*(\d{1,2}(?:\.\d+)?)\s*%",
)
_PCT_SINGLE_RE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%")

_SCALE = {"million": 1e6, "mm": 1e6, "billion": 1e9, "bn": 1e9}


def _to_dollars(number_text: str, scale_word: str | None, default_scale: float) -> float:
    """"185" + "million" → 185,000,000 으로 바꿉니다."""
    value = float(number_text.replace(",", ""))
    if scale_word:
        return value * _SCALE.get(scale_word.lower(), 1.0)
    return value * default_scale


def find_guidance_section(text: str) -> str:
    """보도자료에서 "다음 분기 전망" 문단만 잘라냅니다."""
    if not text:
        return ""
    lowered = text.lower()
    best_pos = -1
    for trigger in _GUIDANCE_TRIGGERS:
        match = re.search(trigger, lowered)
        if match and (best_pos == -1 or match.start() < best_pos):
            best_pos = match.start()
    if best_pos == -1:
        return ""
    return text[best_pos : best_pos + 2500]   # 전망 문단은 보통 이 안에 들어갑니다


def parse_guidance(text: str) -> dict:
    """전망 문단에서 매출·GM%·영업비용(또는 영업마진)을 뽑아냅니다.

    반환: {"revenue": 달러, "gross_margin_pct": %, "opex": 달러,
           "operating_margin_pct": %} — 못 찾은 항목은 None
    """
    section = find_guidance_section(text)
    result = {
        "revenue": None,
        "gross_margin_pct": None,
        "opex": None,
        "operating_margin_pct": None,
        "adjusted_ebitda": None,   # 논갭 영업이익을 안 밝히는 회사가 많습니다
        # 각 항목을 GAAP 에서 집었는지 논갭에서 집었는지 (진단·신뢰도용)
        "gm_basis": "", "opex_basis": "", "om_basis": "", "gaap_only": False,
    }
    if not section:
        return result

    # --- 조정 EBITDA 전망 ---
    # ZETA 처럼 "매출 $X~Y, 조정 EBITDA $A~B" 로만 가이던스를 주는 회사가 많습니다.
    # 예전에는 이걸 통째로 버리고 야후 컨센서스 × 과거 평균 마진(추정 40%)으로
    # 떨어뜨렸습니다. 회사가 직접 준 숫자를 버리는 것은 아깝습니다.
    ebitda_area = _slice_around(section, r"adjusted\s+EBITDA")
    if ebitda_area:
        match = _RANGE_RE.search(ebitda_area)
        if match:
            tail_scale = _SCALE.get((match.group(4) or "").lower(), 1e6)
            low = _to_dollars(match.group(1), match.group(2), tail_scale)
            high = _to_dollars(match.group(3), match.group(4), tail_scale)
            result["adjusted_ebitda"] = (low + high) / 2.0
        else:
            match = _SINGLE_RE.search(ebitda_area)
            if match:
                result["adjusted_ebitda"] = _to_dollars(match.group(1), match.group(2), 1e6)

    # --- 매출 전망 ---
    revenue_area = _slice_around(section, r"revenue|net\s+sales")
    if revenue_area:
        match = _RANGE_RE.search(revenue_area)
        if match:
            # 범위의 중간값을 사용. 앞쪽 숫자에 단위가 없으면 뒤쪽 단위를 따릅니다
            tail_scale = _SCALE.get((match.group(4) or "").lower(), 1e6)
            low = _to_dollars(match.group(1), match.group(2), tail_scale)
            high = _to_dollars(match.group(3), match.group(4), tail_scale)
            result["revenue"] = (low + high) / 2.0
        else:
            match = _SINGLE_RE.search(revenue_area)
            if match:
                result["revenue"] = _to_dollars(match.group(1), match.group(2), 1e6)

    # --- 매출총이익률 전망 (반드시 **논갭** 쪽을 집어야 합니다) ---
    gm_area, gm_basis = _slice_nongaap(section, r"gross\s+margins?")
    if gm_area:
        value = _second_number(gm_area, _parse_pct) if gm_basis == "나란히" else None
        result["gross_margin_pct"] = value if value is not None else _parse_pct(gm_area)
        result["gm_basis"] = gm_basis

    # --- 영업비용 전망 ---
    def _dollars_from(area):
        match = _RANGE_RE.search(area)
        if match:
            tail_scale = _SCALE.get((match.group(4) or "").lower(), 1e6)
            low = _to_dollars(match.group(1), match.group(2), tail_scale)
            high = _to_dollars(match.group(3), match.group(4), tail_scale)
            return (low + high) / 2.0
        match = _SINGLE_RE.search(area)
        if match:
            return _to_dollars(match.group(1), match.group(2), 1e6)
        return None

    opex_area, opex_basis = _slice_nongaap(section, r"operating\s+expenses?")
    if opex_area:
        value = _second_number(opex_area, _dollars_from) if opex_basis == "나란히" else None
        result["opex"] = value if value is not None else _dollars_from(opex_area)
        result["opex_basis"] = opex_basis

    # --- 영업마진 전망 (영업비용 대신 마진을 제시하는 회사도 있음) ---
    om_area, om_basis = _slice_nongaap(section, r"operating\s+margins?")
    if om_area:
        value = _second_number(om_area, _parse_pct) if om_basis == "나란히" else None
        result["operating_margin_pct"] = value if value is not None else _parse_pct(om_area)
        result["om_basis"] = om_basis

    # 어느 항목이든 GAAP 만 찾았다면 그 사실을 남깁니다.
    # 과거 실적은 논갭인데 전망만 GAAP 이면 한 종목 안에서 정의가 섞입니다.
    result["gaap_only"] = any(
        result.get(key) == "GAAP만" for key in ("gm_basis", "opex_basis", "om_basis")
    )

    return result


def _slice_around(text: str, keyword_pattern: str, width: int = 260) -> str:
    """특정 단어 주변만 잘라냅니다 (엉뚱한 숫자를 잡지 않도록)."""
    match = re.search(keyword_pattern, text, re.I)
    if not match:
        return ""
    return text[match.start() : match.start() + width]


# "GAAP and non-GAAP gross margins are expected to be 73.3% and 73.5%, respectively"
# 처럼 두 값을 나란히 주는 형식을 알아보기 위한 표식
_RESPECTIVELY_RE = re.compile(r"\brespectively\b", re.I)
_NON_GAAP_RE = re.compile(r"non[-\s]?GAAP", re.I)


def _slice_nongaap(text: str, keyword_pattern: str, width: int = 260) -> tuple[str, str]:
    """**논갭 수치가 있는 자리**를 잘라냅니다.

    ⚠️ 이 함수가 없어서 큰 오류가 있었습니다.
       미국 회사들은 가이던스에 GAAP 과 논갭을 **둘 다** 적고, 관례적으로
       **GAAP 을 먼저** 씁니다. 그런데 그냥 첫 번째 것을 잡으면 GAAP 을 집어 옵니다.

           "GAAP and non-GAAP gross margins are expected to be 73.3% and 73.5%"
           "GAAP operating margin of 12% to 14%. Non-GAAP operating margin of 20% to 22%."

       이 모델은 과거 실적을 **논갭 영업이익**으로 쌓아 놓고 있습니다. 여기에 GAAP
       기준 전망을 붙이면 한 종목 안에서 정의가 섞여, 사업에 아무 변화가 없어도
       증가율에 가짜 신호가 생깁니다. 실제로 위 두 예시에서 전망 영업이익이
       각각 5%, 38% 낮게 나왔습니다. 그러면 '가속 둔화'가 가짜로 뜹니다.

    반환: (잘라낸 문자열, 근거표시)
      근거표시 = "논갭" / "나란히" / "GAAP만" / ""
    """
    match = re.search(keyword_pattern, text, re.I)
    if not match:
        return "", ""

    # ① "GAAP and non-GAAP ... A and B, respectively" 형식인지 **먼저** 봅니다.
    #    이 형식은 한 문장에 두 값이 나란히 있어서, 논갭 쪽을 직접 가리키는
    #    표현이 있어도 값 자체는 뒤쪽 것을 골라야 합니다.
    # ⚠️ 그냥 "." 으로 자르면 **소수점에서 잘립니다** ("73.3%" → "73").
    #    그러면 뒤에 오는 respectively 를 못 보고 GAAP 값을 집어 옵니다.
    #    마침표·세미콜론 **뒤에 공백이 오는 곳**에서만 문장을 끊습니다.
    sentence = _first_sentence(text[match.start() : match.start() + width])
    head = text[max(0, match.start() - 60) : match.start() + width]
    if _NON_GAAP_RE.search(head) and _RESPECTIVELY_RE.search(sentence):
        return text[match.start() : match.start() + width], "나란히"

    # ② "non-GAAP <키워드>" 형태 — 논갭 값을 따로 적어 준 경우
    direct = re.search(r"non[-\s]?GAAP[^.;]{0,40}?" + keyword_pattern, text, re.I)
    if direct:
        return text[direct.start() : direct.start() + width], "논갭"

    # ③ 논갭이 어디에도 없음 — GAAP 만 있는 가이던스
    return text[match.start() : match.start() + width], "GAAP만"


def _first_sentence(text: str) -> str:
    """마침표·세미콜론 **뒤에 공백이 오는 곳**에서만 끊습니다.

    그냥 "." 으로 자르면 "73.3%" 의 소수점에서 잘립니다.
    """
    return re.split(r"(?<=[.;])\s+", text)[0]


def _second_number(area: str, parser):
    """'A and B, respectively' 형식에서 **뒤쪽(논갭)** 값을 고릅니다.

    ⚠️ 반드시 **첫 문장까지만** 봐야 합니다. 잘라 온 구간에는 다음 줄
       ("...operating expenses ... $5.9 billion and $4.2 billion, respectively")
       이 함께 들어 있어서, 그대로 두면 매출총이익률을 찾다가 영업비용 금액을
       집어 오고 결국 GAAP 값으로 되돌아갑니다.
    """
    area = _first_sentence(area)
    if not _RESPECTIVELY_RE.search(area):
        return None
    cut = area.split(" and ")
    if len(cut) < 2:
        return None
    # ⚠️ 'and' 뒤쪽 **전체**를 넘기면 앞의 GAAP 값이 다시 먼저 잡힙니다.
    #    "GAAP and non-GAAP ... 73.3% and 73.5%, respectively" 에서
    #    뒤쪽 전체는 "non-GAAP ... 73.3% and 73.5%..." 라 73.3 이 먼저입니다.
    #    논갭 값은 **마지막 조각**에 있습니다.
    return parser(cut[-1])


def _parse_pct(text: str) -> float | None:
    """퍼센트 값을 뽑습니다. 범위면 중간값을 씁니다."""
    match = _PCT_RANGE_RE.search(text)
    if match:
        return (float(match.group(1)) + float(match.group(2))) / 2.0
    match = _PCT_SINGLE_RE.search(text)
    if match:
        value = float(match.group(1))
        return value if 0 < value <= 100 else None
    return None


# 전망 문단이 "한 해 전체"를 말하고 있는지 알아보는 표현들.
# 연간 매출 전망을 다음 '분기' 전망으로 쓰면 이익이 4배로 튀어
# 증가율이 +300% 같은 가짜 값이 됩니다.
# "다음 분기 전망"을 가리키는 표현만 좁게 잡습니다 (제목의 "Fourth Quarter" 는 제외)
_QUARTER_OUTLOOK_RE = re.compile(
    r"(?:for|in)\s+the\s+(?:first|second|third|fourth|next|current)\s+quarter"
    r"|(?:first|second|third|fourth|next)\s+quarter\s+(?:of\s+)?(?:fiscal\s+)?(?:20\d{2}\s+)?"
    r"(?:outlook|guidance)"
    r"|\bQ[1-4]\s*(?:FY)?\s*20\d{2}\s+(?:outlook|guidance)",
    re.I,
)

_ANNUAL_HINTS = re.compile(
    r"(full[-\s]?year|fiscal\s+year\s+20\d{2}|for\s+the\s+year|annual\s+(?:revenue|outlook|guidance))",
    re.I,
)


def looks_annual(text: str) -> bool:
    """전망 문단이 분기가 아니라 한 해 전체를 가리키는지 봅니다.

    "Full-year 2026 outlook: we expect ..." 처럼 기간을 알려주는 말이
    전망 문단 **앞쪽**에 있는 경우가 많아, 앞 200자도 함께 봅니다.
    """
    section = find_guidance_section(text)
    if not section:
        return False
    start = text.find(section)
    lead = text[max(0, start - 200) : start] if start >= 0 else ""
    head = lead + section[:400]

    # ⚠️ 분기 가드는 **전망 문단 안에서**, 그리고 '전망을 가리키는 분기 표현'만 봅니다.
    #    보도자료 제목이 "Fourth Quarter and Full Year 2025 Results" 인 경우가 많아,
    #    단순히 "quarter"라는 낱말만 찾으면 연간 가이던스가 분기 전망으로 통과합니다.
    if _QUARTER_OUTLOOK_RE.search(section[:400]):
        return False
    return bool(_ANNUAL_HINTS.search(head))


# --- 다음 분기 가이던스의 조정 EPS (H3 측정용 — 측정결과.md 결정 기록) ---
# 실물 근거: 샌디스크 보도자료의
#   "Expect first quarter 2027 revenue ..., with expected Non-GAAP diluted
#    net income per share to be in the range of $44.00 to $46.00."
# CRDO 처럼 매출·GM 가이던스만 주고 EPS 가이던스는 없는 회사도 있습니다 —
# 그때는 없음(None)으로 둡니다 (창작 금지).
_MONTHS_RE_TEXT = (
    "January|February|March|April|May|June|July|August|September|"
    "October|November|December"
)
_GUID_EPS_QUARTER_RE = re.compile(
    r"(?:fiscal\s+)?(?:first|second|third|fourth)\s+(?:fiscal\s+)?quarter"
    r"|next\s+quarter|\bQ[1-4]\b"
    rf"|(?:{_MONTHS_RE_TEXT})\s+quarter",       # "March quarter" (MCHP 실물)
    re.I,
)

# --- 28차 확대: 분기 선언 구획 상속 -----------------------------------------
# 실물 근거 (전부 data/measure/raw 의 실제 보도자료):
#   · AMBA: "guidance for the second quarter of fiscal year 2027 ...:" 선언 뒤
#     불릿 "• Revenue is expected to be between $105.0 million and $111.0
#     million." — 불릿 자체엔 분기 낱말이 없어 종전 파서가 전부 놓쳤습니다.
#   · CIEN: 연간 블록("fiscal year 2026 to include:")과 분기 블록("fiscal
#     first quarter 2026 to include:")이 연달아 나옴 — 분기 선언에서 시작해
#     연간 표지가 나오면 잘라야 연간 수치가 섞이지 않습니다.
# 규칙: 분기 가이던스 선언 지점부터 (다음 연간 표지 전까지, 최대 1,400자)를
# "분기 전망 구획"으로 보고, 그 안의 문장·불릿은 분기 문맥을 상속합니다.
# 앞을 보는 말 — 과거 실적 문장("Revenue for the first quarter was $X")이
# 가이던스로 오인되는 회귀를 막습니다 (분기+매출+달러가 다 있어도 과거는 과거).
_FORWARD_RE = re.compile(
    r"expect|anticipat|guidance|outlook|forecast|project(?:s|ed|ion)"
    r"|is\s+projected|to\s+be\s+(?:approximately|between|in\s+the\s+range)",
    re.I,
)

# ⚠️ 구획 닻은 guidance/outlook/to include 가 든 **선언**만 인정합니다.
#    맨몸 "expects ... quarter"는 자사주 매입("expects to complete by the end
#    of the second quarter" — UNH 실물 오탐) 같은 무관한 문장도 열어 버립니다.
#    값과 분기가 한 문장에 같이 있는 경우(AMD·MCHP)는 문장 경로가 처리합니다.
_QUARTER_DECL_RE = re.compile(
    r"(?:guidance|outlook)[^.\n]{0,120}?"
    r"(?:fiscal\s+)?(?:first|second|third|fourth|next|current)\s+(?:fiscal\s+)?quarter"
    r"|(?:fiscal\s+)?(?:first|second|third|fourth)\s+quarter"
    r"[^.\n]{0,90}?(?:guidance|outlook|to\s+include)"
    rf"|(?:{_MONTHS_RE_TEXT})\s+quarter[^.\n]{{0,60}}?(?:guidance|outlook)"
    r"|\bQ[1-4]\s*(?:FY\s*)?20\d{2}[^.\n]{0,50}?(?:guidance|outlook)",
    re.I,
)
# 구획을 자르는 연간 표지 — "fiscal year 20xx" 단독은 분기 선언에도 나오므로
# (예: "second quarter of fiscal year 2027") 여기서는 쓰지 않습니다.
_ANNUAL_CUT_RE = re.compile(
    r"full[-\s]?year|for\s+the\s+(?:full\s+)?year\b"
    r"|annual\s+(?:guidance|outlook)"
    r"|fiscal\s+year\s+20\d{2}\s+(?:guidance|outlook|to\s+include)",
    re.I,
)
_ITEM_ANNUAL_RE = re.compile(
    r"full[-\s]?year|for\s+the\s+(?:full\s+)?year\b|annual\s+(?:guidance|outlook|revenue)",
    re.I,
)
_BLOCK_SPAN = 1400


def quarterly_guidance_blocks(text: str) -> list[str]:
    """분기 가이던스 선언부터 시작하는 구획들 (연간 표지에서 자름)."""
    blocks = []
    tail = text
    for match in _QUARTER_DECL_RE.finditer(tail):
        # 가짜 선언 걸러내기:
        #  · 보도자료 제목("First Quarter 2026 Results ... Revises Full Year
        #    Guidance")은 선언이 아닙니다 (UNH 실물 오탐)
        #  · "quarter ... expects" 꼴 산문(구두점 없는 뭉치 속)도 아님
        if re.search(r"results|full[-\s]?year|quarter[^.\n]{0,60}?expect",
                     match.group(0), re.I):
            continue
        segment = tail[match.start():match.start() + _BLOCK_SPAN]
        cut = _ANNUAL_CUT_RE.search(segment, 60)   # 선언 문구 자신은 건너뜀
        if cut:
            segment = segment[:cut.start()]
        blocks.append(segment)
    return blocks


def _quarter_items(text: str) -> list[str]:
    """분기 문맥을 가진 항목(문장·불릿)들 — 두 경로의 합집합.

    ① 분기 낱말을 직접 품은 문장 (종전 방식 그대로)
    ② 분기 선언 구획 안의 문장·불릿 (선언의 분기 문맥을 상속)
    각 항목은 연간 표지가 들어 있으면 버립니다.
    """
    items: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|[\n\r]+", text):
        # 구두점 없는 머리말·표 덩어리는 수백 자짜리 가짜 "문장"이 되어
        # 분기·전망·매출 낱말을 몽땅 품습니다 (AXP 실물 오탐). 진짜
        # 가이던스 문장은 이보다 짧습니다.
        if len(sentence) > 500:
            continue
        if (_GUID_EPS_QUARTER_RE.search(sentence)
                and _FORWARD_RE.search(sentence)):
            items.append(sentence)
    for block in quarterly_guidance_blocks(text):
        # 구획 안에서도 아무 문장이나 받지 않습니다: 불릿 표시가 있는 줄
        # (선언 바로 아래 항목들) 또는 앞보는 말이 있는 줄만. 실물 근거:
        # UNH 보도자료에서 구획이 과거 실적 산문("Optum ... driving revenues
        # of $63.7 billion")까지 삼켜 오탐이 났습니다.
        for line in block.splitlines():
            line = line.strip()
            if len(line) < 12 or len(line) > 500:
                continue
            bullet = line.startswith(("•", "-", "–", "▪", "●", "*", "◦"))
            if bullet or _FORWARD_RE.search(line):
                items.append(line)
    # 연간 오인 방지의 원칙: **분기를 직접 명명한 항목은 연간이 아니다.**
    # (_ANNUAL_HINTS 에는 "fiscal year 20XX"가 들어 있어, "first quarter of
    # fiscal year 2026" 같은 진짜 분기 문장까지 지웠던 회귀 — 29차.)
    # 분기 낱말이 없는 상속 항목(불릿)에만 좁은 연간 표지를 적용한다.
    # 좁은 연간 표지(full year·for the year·annual …)는 분기 낱말이 함께
    # 있어도 버린다 — "full year … for the fourth quarter update" 같은 혼합
    # 문장은 어느 분기 값인지 확신할 수 없다 ("없음"이 안전).
    # 넓은 표지(_ANNUAL_HINTS 의 "fiscal year 20XX")는 여기서 쓰지 않는다 —
    # "first quarter of fiscal year 2026" 같은 진짜 분기 문장을 죽였던
    # 회귀(29차)의 원인이었다.
    return [i for i in items if not _ITEM_ANNUAL_RE.search(i)]


# ± 형 (실물: AMD "approximately $11.2 billion, plus or minus $300 million" ·
# MCHP "net sales of $1.260 billion plus or minus $20.0 million").
# 회사가 준 두 숫자의 덧셈·뺄셈만 합니다 (창작 아님 — 범위의 양 끝 계산).
_PLUSMINUS_DOLLAR_RE = re.compile(
    r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million|bn|mm)?\s*,?\s*"
    r"(?:plus\s+or\s+minus|\+/-|±)\s*"
    r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million|bn|mm)?",
    re.I,
)
_PLUSMINUS_PCT_RE = re.compile(
    r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million|bn|mm)?\s*,?\s*"
    r"(?:plus\s+or\s+minus|\+/-|±)\s*(\d{1,2}(?:\.\d+)?)\s*%",
    re.I,
)
# "non-GAAP … per share/EPS … $A to $B" — 괄호는 회계 표기의 음수입니다
_GUID_EPS_RANGE_RE = re.compile(
    r"non[-\s]?GAAP[^.]{0,120}?(?:per\s+share|EPS)[^.$\d]{0,60}?"
    r"(\()?\$\s*(\d+\.\d{1,2})\)?\s*(?:to|[-–~])\s*(\()?\$\s*(\d+\.\d{1,2})\)?",
    re.I,
)
_GUID_EPS_SINGLE_RE = re.compile(
    r"non[-\s]?GAAP[^.]{0,120}?(?:per\s+share|EPS)[^.$\d]{0,60}?(\()?\$\s*(\d+\.\d{1,2})\)?",
    re.I,
)
# between 형 EPS (28차 실물: UCTT "non-GAAP diluted net income per share to be
# between $0.44 and $0.60" — and 구분자는 between 뒤에서만 범위로 인정)
_GUID_EPS_BETWEEN_RE = re.compile(
    r"non[-\s]?GAAP[^.]{0,120}?(?:per\s+share|EPS)[^.$\d]{0,60}?"
    r"between\s+\$\s*(\d+\.\d{1,2})\s+and\s+\$\s*(\d+\.\d{1,2})",
    re.I,
)
# ± 형 EPS (28차): "non-GAAP EPS of $0.61, plus or minus $0.05"
_GUID_EPS_PM_RE = re.compile(
    r"non[-\s]?GAAP[^.]{0,120}?(?:per\s+share|EPS)[^.$\d]{0,60}?"
    r"\$\s*(\d+\.\d{1,2})\s*,?\s*(?:plus\s+or\s+minus|\+/-|±)\s*\$\s*(\d+\.\d{1,2})",
    re.I,
)


def parse_guidance_eps(text: str) -> dict:
    """다음 분기 가이던스에서 조정 EPS 를 원문 그대로 읽습니다.

    반환: {"low", "high", "mid"} — 못 찾으면 전부 None.
    연간(full year) 전망 문장은 건너뜁니다. 분기를 가리키는 문장에서만 읽습니다.
    """
    result = {"low": None, "high": None, "mid": None}
    if not text:
        return result
    single_result: dict | None = None      # 밴드(범위·±)가 항상 단일값을 이깁니다
    for sentence in _quarter_items(text):
        match = _GUID_EPS_BETWEEN_RE.search(sentence)
        if match:
            low, high = float(match.group(1)), float(match.group(2))
            if low > high:
                low, high = high, low
            result.update(low=low, high=high, mid=round((low + high) / 2, 4))
            return result
        match = _GUID_EPS_RANGE_RE.search(sentence)
        if match:
            low = float(match.group(2)) * (-1 if match.group(1) else 1)
            high = float(match.group(4)) * (-1 if match.group(3) else 1)
            if low > high:
                low, high = high, low
            result.update(low=low, high=high, mid=round((low + high) / 2, 4))
            return result
        pm = _GUID_EPS_PM_RE.search(sentence)
        if pm:
            centre, radius = float(pm.group(1)), float(pm.group(2))
            result.update(low=round(centre - radius, 4),
                          high=round(centre + radius, 4), mid=centre)
            return result
        if single_result is None:
            match = _GUID_EPS_SINGLE_RE.search(sentence)
            if match:
                value = float(match.group(2)) * (-1 if match.group(1) else 1)
                single_result = {"low": value, "high": value, "mid": value}
    return single_result or result


# --- 다음 분기 가이던스의 매출·조정 EBITDA (대책 2 — 2026-08-13) ---
# EPS 가이던스를 주는 회사는 79개 중 7개뿐이지만, 매출 전망은 거의 모든
# 회사가 줍니다 (실패 원문 137건에서 매출 후보 23건·EBITDA 후보 8건 실측).
# 실물 근거: AMBA 2024-08-27 "guidance for the third quarter of fiscal year
# 2025 ... Revenue is expected to be between $77.0 million and $81.0 million."
#
# 규칙은 EPS 가이던스와 같습니다: 분기를 가리키는 문장에서만 읽고,
# 못 찾으면 없음(None). 회사가 준 숫자끼리의 산수(범위 중간값)만 합니다.
#
# ⚠️ "between $A and $B" 형식은 기존 _RANGE_RE 가 못 읽습니다 (구분자에
#    and 가 없음). and 는 "between" 뒤에서만 범위 구분자로 인정합니다 —
#    "revenue of $256.5 million and EPS of $1.04" 처럼 서로 다른 항목을
#    잇는 and 를 범위로 착각하면 중간값이 엉망이 되기 때문입니다.
_GUID_BETWEEN_RE = re.compile(
    r"between\s+\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(million|billion|bn|mm)?"
    r"\s+and\s+\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(million|billion|bn|mm)?",
    re.I,
)
_GUID_REV_KEY_RE = re.compile(r"revenue|net\s+sales", re.I)
_GUID_EBITDA_KEY_RE = re.compile(r"adjusted\s+EBITDA", re.I)


def _parse_guidance_dollar(text: str, keyword_re: re.Pattern) -> dict:
    """분기 전망 문장에서 keyword 뒤의 달러 범위(또는 단일값)를 읽습니다."""
    result = {"low": None, "high": None, "mid": None}
    if not text:
        return result
    # 1차: 범위·± 형(밴드)만 찾는다 — 2차: 그래도 없으면 단일값.
    # 같은 보도자료 안에 "지난번 가이던스 재인용"(단일값)이 새 가이던스
    # (범위)보다 먼저 나오는 실물(MCHP)이 있어, 밴드가 항상 이깁니다.
    single_result: dict | None = None
    for sentence in _quarter_items(text):
        keyword = keyword_re.search(sentence)
        if not keyword:
            continue
        area = sentence[keyword.start():]
        match = _GUID_BETWEEN_RE.search(area) or _RANGE_RE.search(area)
        if match:
            tail_scale = _SCALE.get((match.group(4) or "").lower(), 1e6)
            low = _to_dollars(match.group(1), match.group(2), tail_scale)
            high = _to_dollars(match.group(3), match.group(4), tail_scale)
            if low > high:
                low, high = high, low
            # 두 값의 자릿수가 크게 다르면 서로 다른 항목을 짝지은 것 — 없음
            if low <= 0 or high / low > 5.0:
                continue
            result.update(low=low, high=high, mid=round((low + high) / 2, 2))
            return result
        pm = _PLUSMINUS_DOLLAR_RE.search(area)
        if pm:
            centre = _to_dollars(pm.group(1), pm.group(2), 1e6)
            radius = _to_dollars(pm.group(3), pm.group(4), 1e6)
            if 0 < radius < centre:       # 반경이 중심보다 크면 잘못 읽은 것
                result.update(low=centre - radius, high=centre + radius,
                              mid=centre)
                return result
        pm = _PLUSMINUS_PCT_RE.search(area)
        if pm:
            centre = _to_dollars(pm.group(1), pm.group(2), 1e6)
            pct = float(pm.group(3)) / 100.0
            if 0 < pct < 0.5:
                result.update(low=round(centre * (1 - pct), 2),
                              high=round(centre * (1 + pct), 2), mid=centre)
                return result
        if single_result is None:
            match = _SINGLE_RE.search(area)
            if match:
                value = _to_dollars(match.group(1), match.group(2), 1e6)
                single_result = {"low": value, "high": value, "mid": value}
    return single_result or result


def parse_guidance_revenue(text: str) -> dict:
    """다음 분기 매출 가이던스: {"low","high","mid"} (달러). 없으면 None."""
    return _parse_guidance_dollar(text, _GUID_REV_KEY_RE)


def parse_guidance_ebitda(text: str) -> dict:
    """다음 분기 조정 EBITDA 가이던스: {"low","high","mid"} (달러). 없으면 None."""
    return _parse_guidance_dollar(text, _GUID_EBITDA_KEY_RE)


def recent_depreciation(quarters: list[dict]) -> float | None:
    """최근 분기들의 감가상각비 중앙값. 조정 EBITDA 를 논갭 영업이익으로 바꿀 때 씁니다."""
    import statistics

    values = [
        q["da"] for q in quarters[-cfg.EBITDA_DA_LOOKBACK:]
        if q.get("da") is not None and q["da"] > 0
    ]
    return statistics.median(values) if values else None


def forward_from_guidance(guidance: dict, quarters: list[dict] | None = None) -> tuple:
    """가이던스 숫자로 다음 분기 논갭 영업이익을 계산합니다.

    방법 1: 매출 × 영업마진%                → 가이던스 (85%)
    방법 2: 매출 × GM% − 영업비용            → 가이던스 (85%)
    방법 3: 조정 EBITDA − 최근 감가상각비    → 역산 (75%)
            감가상각비는 사업이 커지면 같이 커지므로 최근값을 쓰는 것은 근사입니다.
            그래서 '가이던스'가 아니라 '역산'으로 표시합니다.

    반환: (전망 영업이익, 근거 배지, 설명) — 못 구하면 (None, None, "")
    """
    revenue = guidance.get("revenue")

    if revenue is not None and guidance.get("operating_margin_pct") is not None:
        value = revenue * guidance["operating_margin_pct"] / 100.0
        return value, cfg.SRC_GUIDANCE, (
            f"회사 가이던스 매출 ${revenue/1e6:,.0f}M × "
            f"영업마진 {guidance['operating_margin_pct']:.1f}%"
        )

    gm, opex = guidance.get("gross_margin_pct"), guidance.get("opex")
    if revenue is not None and gm is not None and opex is not None:
        value = revenue * gm / 100.0 - abs(opex)
        return value, cfg.SRC_GUIDANCE, (
            f"회사 가이던스 매출 ${revenue/1e6:,.0f}M × GM {gm:.1f}% "
            f"− 영업비용 ${abs(opex)/1e6:,.0f}M"
        )

    # 조정 EBITDA 만 준 경우 (ZETA 형)
    ebitda = guidance.get("adjusted_ebitda")
    if ebitda is not None and quarters:
        da = recent_depreciation(quarters)
        if da is not None:
            value = ebitda - da
            return value, cfg.SRC_DERIVED, (
                f"회사가 준 조정 EBITDA 가이던스 ${ebitda/1e6:,.0f}M 에서 "
                f"최근 감가상각비 ${da/1e6:,.0f}M 을 빼 역산했습니다"
            )

    return None, None, ""


# ---------------------------------------------------------------------------
# ② 월가 컨센서스 (야후 파이낸스 = LSEG 집계 컨센서스)
# ---------------------------------------------------------------------------
# 야후 파이낸스 애널리스트 탭의 숫자는 LSEG(옛 레피니트)가 집계한
# 실제 월가 컨센서스입니다. yfinance로 무료로 가져올 수 있습니다.
#   · revenue_estimate : 매출 컨센서스 (0q=다음 발표 분기, +1q=그다음 분기)
#   · earnings_estimate: EPS 컨센서스 + 참여 애널리스트 수
#   · eps_trend        : 추정치가 7/30/60/90일 전 대비 어떻게 움직였나 (리비전 속도)
#   · eps_revisions    : 상향/하향 건수 (리비전 속도의 보조)


def fetch_consensus(ticker: str) -> dict:
    """월가 컨센서스를 최대한 가져옵니다. 실패한 항목은 errors에 사유를 남깁니다."""
    out = {
        "revenue_0q": None,      # 다음 발표 분기 매출 컨센서스 (달러)
        "revenue_1q": None,      # 그다음 분기 매출 컨센서스
        "eps_0q": None,          # 다음 발표 분기 EPS 컨센서스
        "eps_1q": None,
        # 컨센서스와 **같은 기준**의 직전 실제 EPS.
        # 증가율을 만들려면 분자·분모가 같은 자여야 하므로 반드시 여기서 받아옵니다.
        "eps_actual_last": None,
        "shares_shrink_pct": None,   # 최근 1년 희석주식수 변화(%) — 음수면 감소
        "analysts_0q": None,     # 참여 애널리스트 수 (컨센서스의 무게)
        "revision": 0,           # 방향: +1 상향 / 0 중립 / -1 하향
        "revision_velocity_pct": None,   # 30일간 추정치가 몇 % 움직였나
        "errors": [],            # 진단 패널에 보여줄 실패 사유
    }

    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
    except Exception as exc:
        out["errors"].append(f"yfinance 초기화: {type(exc).__name__}")
        return out

    # --- 매출 컨센서스 (두 분기) ---
    try:
        revenue_est = stock.revenue_estimate
        if revenue_est is None or len(revenue_est) == 0:
            out["errors"].append("매출 컨센서스 없음")
        else:
            value = _safe_number(revenue_est, "0q", ["avg"])
            if value and value > 0:
                out["revenue_0q"] = value
            value = _safe_number(revenue_est, "+1q", ["avg"])
            if value and value > 0:
                out["revenue_1q"] = value
            analysts = _safe_number(revenue_est, "0q", ["numberOfAnalysts"])
            if analysts and analysts > 0:
                out["analysts_0q"] = int(analysts)
    except Exception as exc:
        out["errors"].append(f"매출 컨센서스: {type(exc).__name__}")

    # --- EPS 컨센서스 (교차검증용) ---
    try:
        eps_est = stock.earnings_estimate
        if eps_est is not None and len(eps_est) > 0:
            out["eps_0q"] = _safe_number(eps_est, "0q", ["avg"])
            out["eps_1q"] = _safe_number(eps_est, "+1q", ["avg"])
            if out["analysts_0q"] is None:
                analysts = _safe_number(eps_est, "0q", ["numberOfAnalysts"])
                if analysts and analysts > 0:
                    out["analysts_0q"] = int(analysts)
    except Exception as exc:
        out["errors"].append(f"EPS 컨센서스: {type(exc).__name__}")

    # --- 컨센서스와 **같은 기준**의 직전 실제 EPS ---
    # 야후(LSEG/IBES)의 earnings_history 는 추정치와 같은 잣대로 실제치를 실어 줍니다.
    # 그래서 이 둘로 만든 증가율은 정의 불일치가 원천적으로 없습니다.
    # 회사가 논갭 정의를 바꿔도 분자·분모가 같이 바뀌어 상쇄됩니다.
    try:
        history = stock.earnings_history
        if history is not None and len(history) > 0:
            for column in ("epsActual", "eps_actual", "actual"):
                if column in history.columns:
                    values = [
                        v for v in history[column].tolist()
                        if v is not None and isinstance(v, (int, float))
                        and math.isfinite(v)
                    ]
                    if values:
                        out["eps_actual_last"] = float(values[-1])
                    break
    except AttributeError:
        pass      # 이 yfinance 판에는 없는 기능 — 오류가 아니라 '없음'입니다
    except Exception as exc:
        out["errors"].append(f"실제 EPS 이력: {type(exc).__name__}")

    # --- 희석주식수 변화 (자사주 매입이 EPS 증가율을 부풀리는지) ---
    try:
        shares = stock.get_shares_full(start=None)
        if shares is not None and len(shares) > 8:
            recent, year_ago = float(shares.iloc[-1]), float(shares.iloc[-min(len(shares), 250)])
            if year_ago > 0:
                out["shares_shrink_pct"] = (recent / year_ago - 1) * 100.0
    except AttributeError:
        pass      # 위와 같음 (선택 항목)
    except Exception as exc:
        out["errors"].append(f"주식수: {type(exc).__name__}")

    # --- 리비전 속도: 추정치가 30일 전 대비 몇 % 움직였나 ---
    # 분모가 5센트 미만이면 계산하지 않습니다: 손익분기 근처 EPS에서는
    # 2센트 이동(반올림 노이즈)이 +200% 같은 극단값을 만들기 때문입니다.
    try:
        trend = stock.eps_trend
        if trend is not None and len(trend) > 0 and "0q" in trend.index:
            current = _safe_number(trend, "0q", ["current"])
            past_30d = _safe_number(trend, "0q", ["30daysAgo", "30DaysAgo"])
            if current is not None and past_30d is not None and abs(past_30d) >= 0.05:
                out["revision_velocity_pct"] = (current - past_30d) / abs(past_30d) * 100.0
    except Exception as exc:
        out["errors"].append(f"추정 추이: {type(exc).__name__}")

    # --- 리비전 방향 ---
    # 속도가 있으면 속도의 부호로, 없으면 상향/하향 건수로 정합니다
    velocity = out["revision_velocity_pct"]
    if velocity is not None:
        out["revision"] = 1 if velocity > 0.5 else (-1 if velocity < -0.5 else 0)
    else:
        try:
            revisions = stock.eps_revisions
            if revisions is not None and len(revisions) > 0:
                row_key = "0q" if "0q" in revisions.index else revisions.index[0]
                up = _safe_number(revisions, row_key, ["upLast30days", "upLast30Days"])
                down = _safe_number(revisions, row_key, ["downLast30days", "downLast30Days"])
                if up is not None and down is not None:
                    out["revision"] = 1 if up > down else (-1 if down > up else 0)
        except Exception as exc:
            out["errors"].append(f"리비전: {type(exc).__name__}")

    return out


# 예전 이름 호환 (테스트·외부 코드가 깨지지 않도록)
def fetch_yf_forward(ticker: str) -> dict:
    consensus = fetch_consensus(ticker)
    return {"next_q_revenue": consensus["revenue_0q"], "revision": consensus["revision"]}


def _safe_number(df, row_key, column_candidates: list[str]) -> float | None:
    """표에서 값을 꺼냅니다 (열 이름이 버전마다 달라 여러 후보를 시도).

    yfinance 표에는 빈 값이 NaN으로 들어오는데, NaN이 그대로 흘러가면
    비교·클램프가 엉뚱하게 동작합니다(예: 리비전 점수 만점). 여기서 차단합니다.
    """
    import math

    for column in column_candidates:
        if column in df.columns:
            try:
                value = df.loc[row_key, column]
                if value is None:
                    continue
                number = float(value)
                if not math.isfinite(number):
                    continue
                return number
            except (KeyError, TypeError, ValueError):
                continue
    return None


def average_operating_margin(quarters: list[dict], n: int = 4) -> float | None:
    """최근 n개 분기의 평균 논갭 영업마진(%)을 계산합니다.

    ⚠️ 적자 분기는 평균에서 뺍니다. 턴어라운드(적자→흑자 전환) 종목에서
    적자 분기가 평균을 음수로 끌어내리면 "미래 전망 = 매출 × 음수 마진"이 되어
    개선 중인 회사에 적자 전망을 붙이는 오류가 생기기 때문입니다.
    흑자 분기가 하나도 없으면 None (전망을 만들지 않는 편이 정직합니다).
    """
    margins = []
    for q in quarters[-n:]:
        revenue, op_income = q.get("revenue"), q.get("op_income")
        if op_income is None or op_income <= 0:
            continue
        # 말이 되는 마진일 때만 씁니다 (영업이익이 매출보다 클 수는 없습니다).
        # 예전에는 검사 없이 나눠서 82,804,463% 같은 값이 그대로 흘러갔습니다.
        margin = dq.safe_margin_pct(revenue, op_income)
        if margin is not None:
            margins.append(margin)
    if not margins:
        return None
    if len(margins) >= 3:
        # 한 분기만 크게 튀면(껍데기 잔차 등) 평균이 통째로 흔들립니다.
        # 가운데 값에서 크게 벗어난 분기는 평균에서 뺍니다.
        ordered = sorted(margins)
        median = ordered[len(ordered) // 2]
        kept = [m for m in margins if abs(m - median) <= max(15.0, abs(median) * 0.8)]
        if len(kept) >= 2:
            margins = kept
    return sum(margins) / len(margins)


def forward_from_eps_growth(consensus: dict, latest_op: float | None) -> tuple:
    """컨센서스 EPS 의 **증가율만** 빌려 와 영업이익 전망을 만듭니다.

    ⚠️ 이것은 **마지막 대안**입니다. 우선순위는 언제나
        ① 가이던스(논갭 영업이익)  ② 컨센서스 매출 × 논갭 마진  ③ 이 경로
    논갭 영업이익으로 만들 수 있으면 절대 여기까지 오지 않습니다.

    왜 레벨이 아니라 증가율인가:
      컨센서스 EPS 와 우리가 쌓아 둔 논갭 영업이익은 **레벨의 기준이 다릅니다**.
      레벨을 환산하려면 주식수·세율·이자를 다 알아야 하는데 셋 다 잡음입니다.
      반면 증가율은 **같은 출처 안에서 나누면**(IBES 실제 ÷ IBES 추정) 기준이
      무엇이든 상쇄됩니다. 회사가 논갭 정의를 바꿔도 분자·분모가 같이 바뀝니다.

    실데이터로 확인한 근거: 같은 종목·같은 분기를 논갭 영업이익으로 볼 때와
    조정 EPS 로 볼 때 델타 **방향이 96% 일치**했습니다(50개 시점 중 48개).
    AMD 가 인수 대금을 주식으로 치러 희석주식수가 +36.5% 늘어난 구간에서도
    방향은 뒤집히지 않았습니다.

    반환: (전망 영업이익, 근거 배지, 설명) — 못 만들면 (None, None, "")
    """
    estimate = consensus.get("eps_0q")
    actual = consensus.get("eps_actual_last")
    if estimate is None or actual is None or latest_op is None or latest_op <= 0:
        return None, None, ""

    # 손익분기 근처에서는 1~2센트 반올림이 +200% 를 만듭니다
    if abs(actual) < cfg.EPS_MIN_ABS or abs(estimate) < cfg.EPS_MIN_ABS:
        return None, None, ""
    # 부호가 다르면(적자 ↔ 흑자) 증가율에 뜻이 없습니다
    if (actual > 0) != (estimate > 0):
        return None, None, ""

    analysts = consensus.get("analysts_0q")
    if analysts is not None and analysts < cfg.EPS_MIN_ANALYSTS:
        return None, None, ""

    growth = dq.safe_growth_pct(actual, estimate)
    if growth is None or abs(growth) > cfg.EPS_MAX_GROWTH_PCT:
        return None, None, ""

    value = latest_op * (1 + growth / 100.0)
    note = (
        f"논갭 영업이익으로는 전망을 만들지 못해, 월가 **EPS 컨센서스의 증가율만** "
        f"빌려 왔습니다 (직전 실제 {actual:+.2f} → 컨센서스 {estimate:+.2f}, "
        f"{growth:+.1f}%). 그 증가율을 최근 영업이익 ${latest_op/1e6:,.0f}M 에 "
        f"적용했습니다"
    )
    if analysts:
        note += f" · 애널리스트 {analysts}명"

    shrink = consensus.get("shares_shrink_pct")
    if shrink is not None and shrink <= -cfg.EPS_SHARE_SHRINK_WARN:
        note += (
            f" · ⚠️ 최근 1년 주식수가 {shrink:.0f}% 줄었습니다(자사주 매입). "
            "EPS 증가율이 사업 성장보다 부풀려져 있을 수 있습니다"
        )
    return value, cfg.SRC_EPS_GROWTH, note


# ---------------------------------------------------------------------------
# 바깥에서 호출하는 함수
# ---------------------------------------------------------------------------
def find_latest_guidance_text(quarters: list[dict]) -> str:
    """"아직 발표되지 않은 다음 분기"를 겨냥한 가이던스만 찾습니다.

    가이던스는 그 발표 분기의 **바로 다음 분기** 전망입니다. 따라서:
      · 마지막 분기 행에 가이던스가 있으면 → 다음(미발표) 분기 전망 = 사용 ✅
      · 마지막 행이 XBRL 뼈대(가이던스 없음)인데 그 앞 행에서 가져오면
        → 그 가이던스는 "이미 발표된 마지막 분기"의 전망 = 낡음 = 사용 ❌
          (낡은 가이던스를 쓰면 가속 중인 종목을 '둔화'로 오판합니다)

    낡은 경우에는 빈 문자열을 돌려 컨센서스 경로로 넘깁니다.
    ※ 최신 8-K가 XBRL보다 먼저 나온 경우는 merge_quarters가 그 8-K를
      새 분기 행으로 승격시키므로, 신선한 가이던스는 마지막 행에 오게 됩니다.
    """
    if not quarters:
        return ""
    return (quarters[-1].get("guidance_text") or "").strip()


def _forward_on_eps_basis(output: dict, consensus: dict, quarters: list[dict]) -> dict:
    """조정 EPS 로 판정 중인 종목의 전망 — **같은 단위(주당 달러)** 로 만듭니다.

    과거도 주당 달러, 전망도 주당 달러이므로 환산이 필요 없습니다.
    이것이 EPS 기준의 유일한 장점입니다: 월가 컨센서스가 바로 이 단위로 나옵니다.

    가이던스와 '매출 × 마진' 경로는 **쓰지 않습니다.** 둘 다 달러라서,
    주당 달러로 쌓은 과거와 맞대면 자가 어긋납니다.
    """
    eps_next = consensus.get("eps_0q")
    eps_after = consensus.get("eps_1q")
    analysts = consensus.get("analysts_0q")

    latest = next(
        (q["op_income"] for q in reversed(quarters)
         if q.get("op_income") is not None), None
    )

    if eps_next is not None:
        output["forward_op_income"] = eps_next
        output["basis"] = cfg.SRC_ESTIMATE
        analysts_text = f", 애널리스트 {analysts}명" if analysts else ""
        output["detail"] = (
            f"이 종목은 논갭 영업이익을 구하지 못해 **조정 EPS**로 판정하고 있어서, "
            f"전망도 같은 단위인 월가 EPS 컨센서스(${eps_next:,.2f}/주{analysts_text})를 "
            "그대로 씁니다. 과거와 전망의 자가 같아 환산 오차가 없습니다"
        )
    else:
        consensus.setdefault("errors", []).append(
            "조정 EPS 기준인데 EPS 컨센서스를 구하지 못해 전망 없음"
        )

    if eps_after is not None:
        output["forward_op_income_2"] = eps_after
        output["basis_2"] = cfg.SRC_ESTIMATE
        output["detail_2"] = (
            f"다다음 분기도 EPS 컨센서스(${eps_after:,.2f}/주)를 그대로 썼습니다"
        )

    # 크기 검사 — 두 분기 모두 봅니다.
    # (예전에는 다음 분기만 검사해서, 다다음 분기의 깨진 값이 그대로 차트에 그려졌습니다)
    positive_latest = next(
        (q["op_income"] for q in reversed(quarters)
         if q.get("op_income") is not None and q["op_income"] > 0), None
    )
    for key, label in (("forward_op_income", "다음 분기"),
                       ("forward_op_income_2", "다다음 분기")):
        ok, reason = dq.check_forward(output[key], positive_latest or latest)
        if not ok:
            consensus.setdefault("errors", []).append(f"{label} 전망 제외: {reason}")
            output[key] = None
    return output


def estimate_forward(ticker: str, quarters: list[dict]) -> dict:
    """다음 분기(그리고 가능하면 다다음 분기까지) 논갭 영업이익 전망을 만듭니다.

    반환:
      forward_op_income    다음 분기 전망 (달러) — 못 구하면 None
      basis                "가이던스" / "추정" / None
      forward_op_income_2  다다음 분기 전망 (컨센서스 기반) — 못 구하면 None
      basis_2              "추정" / None
      revision             +1(상향) / 0(중립) / −1(하향)
      revision_velocity_pct  추정치의 30일 변화율(%)
      consensus            수집한 컨센서스 원본 (애널리스트 수 등)
      detail / detail_2    화면에 보여줄 설명 문장
    """
    output = {
        "forward_op_income": None,
        "basis": None,
        "forward_op_income_2": None,
        "basis_2": None,
        "revision": 0,
        "revision_velocity_pct": None,
        "consensus": {},
        "detail": "다음 분기 전망 데이터를 찾지 못했습니다",
        "detail_2": "",
    }

    # 컨센서스는 분기 실적이 없어도 가져와 둡니다 (진단·리비전에 사용)
    consensus = fetch_consensus(ticker)
    output["consensus"] = consensus
    output["revision"] = consensus["revision"]
    output["revision_velocity_pct"] = consensus["revision_velocity_pct"]

    if not quarters:
        return output

    # --- 컨센서스 시점 보정 (롤오버 확인) ---
    # 실적발표 직후 며칠간 야후의 '0q'가 방금 발표된 분기를 그대로 가리키는
    # 경우가 있습니다. '0q' 매출이 마지막 실제 분기 매출과 ±1% 이내로 거의
    # 같으면 아직 안 넘어간 것으로 보고 한 분기씩 당깁니다.
    # (±1%로 좁게 잡는 이유: 분기 성장이 거의 없는 회사의 정상적인 다음 분기
    #  전망을 낡은 것으로 오판해 버리면 안 되기 때문입니다. 서프라이즈가 커서
    #  추정과 실적이 1% 넘게 어긋난 미롤오버는 잡지 못하지만, 그 상태는
    #  며칠이면 지나가므로 오탐 없는 쪽을 택합니다.)
    latest_revenue = next(
        (q["revenue"] for q in reversed(quarters) if q.get("revenue")), None
    )
    if (
        latest_revenue
        and consensus["revenue_0q"]
        and abs(consensus["revenue_0q"] / latest_revenue - 1.0) <= 0.01
    ):
        consensus["errors"].append("컨센서스가 발표 분기를 가리켜 한 분기 당김")
        consensus["revenue_0q"] = consensus["revenue_1q"]
        consensus["revenue_1q"] = None
        consensus["eps_0q"] = consensus["eps_1q"]
        consensus["eps_1q"] = None

    # --- 구조대 경로: 과거를 조정 EPS 로 판정 중이면 전망도 EPS 여야 합니다 ---
    #
    # ⚠️ 이것을 빼먹으면 자가 어긋납니다. 과거는 주당 달러($0.87)인데 전망은
    #    달러($60,000,000)로 나와, 증가율이 수십억 %가 되거나(그래서 통째로
    #    버려지거나) 평균 마진이 0.0000002% 로 계산돼 전망이 0 이 됩니다.
    #    실제로 LITE(루멘텀)에서 그렇게 나왔습니다 — 논갭 영업이익을 한 건도
    #    못 읽어 EPS 로 판정하는데 전망만 달러였습니다.
    #
    # 다행히 EPS 는 월가 컨센서스를 **같은 단위(주당 달러)로** 바로 구할 수
    # 있습니다. 그래서 이 경로에서는 증가율을 이식할 필요 없이 레벨을 그대로 씁니다.
    if cfg.quarters_basis(quarters) == cfg.BASIS_ADJ_EPS:
        return _forward_on_eps_basis(output, consensus, quarters)

    avg_margin = average_operating_margin(quarters, n=4)

    # --- 다음 분기: ① 가이던스 우선 ---
    guidance_text = find_latest_guidance_text(quarters)
    if looks_annual(guidance_text):
        # 연간 전망을 분기 전망으로 쓰면 이익이 4배로 튀어 가짜 급가속이 됩니다.
        # 4로 나눠 분기화하는 것도 계절성 때문에 왜곡되므로, 그냥 쓰지 않고
        # 월가 컨센서스 경로로 넘깁니다.
        consensus["errors"].append("연간 가이던스로 판단되어 분기 전망에서 제외")
        guidance_text = ""
    guidance = parse_guidance(guidance_text)
    forward, forward_basis, forward_note = forward_from_guidance(guidance, quarters)
    guidance_margin_pct = None   # 가이던스에 내재된 마진 (다다음 분기에도 같은 기준 적용)
    if forward is not None:
        output["forward_op_income"] = forward
        output["basis"] = forward_basis
        if guidance.get("revenue"):
            guidance_margin_pct = forward / guidance["revenue"] * 100.0
        output["detail"] = forward_note
        # 가이던스에서 논갭을 못 찾고 GAAP 만 집었다면 반드시 알려야 합니다.
        # 과거 실적은 논갭인데 전망만 GAAP 이면 한 종목 안에서 정의가 섞여,
        # 사업에 아무 변화가 없어도 증가율에 가짜 신호가 생깁니다.
        if guidance.get("gaap_only"):
            output["detail"] += (
                " · ⚠️ 이 가이던스에서 논갭 수치를 찾지 못해 GAAP 기준으로 계산했습니다. "
                "과거 실적(논갭)과 기준이 달라 증가율이 실제보다 낮게 나올 수 있습니다"
            )
            consensus["errors"].append("가이던스에 논갭이 없어 GAAP 으로 계산함")
    # --- 다음 분기: ② 컨센서스 매출 × 평균 마진 ---
    elif consensus["revenue_0q"] and avg_margin is not None:
        output["forward_op_income"] = consensus["revenue_0q"] * avg_margin / 100.0
        output["basis"] = cfg.SRC_ESTIMATE
        analysts_text = (
            f", 애널리스트 {consensus['analysts_0q']}명"
            if consensus.get("analysts_0q") else ""
        )
        output["detail"] = (
            f"월가 매출 컨센서스(${consensus['revenue_0q']/1e6:,.0f}M{analysts_text})에 "
            f"최근 4개 분기 평균 영업마진({avg_margin:.1f}%)을 곱한 추정치입니다"
        )

    # --- 다음 분기: ③ 마지막 대안 — EPS 컨센서스의 '증가율만' 빌려 오기 ---
    # 여기까지 왔다는 것은 **논갭 영업이익으로는 전망을 만들 수 없었다**는 뜻입니다.
    #   · 회사가 가이던스를 안 줬거나 파싱하지 못했고
    #   · 매출 컨센서스가 없거나 과거 흑자 분기가 없어 평균 마진을 못 냈습니다
    # 그럴 때 전망을 통째로 포기하는 대신, 커버리지가 가장 넓은 EPS 컨센서스에서
    # **증가율만** 가져옵니다. 레벨은 기준이 달라 쓰지 않습니다.
    else:
        latest_for_eps = next(
            (q["op_income"] for q in reversed(quarters)
             if q.get("op_income") is not None), None
        )
        eps_forward, eps_basis, eps_note = forward_from_eps_growth(
            consensus, latest_for_eps
        )
        if eps_forward is not None:
            output["forward_op_income"] = eps_forward
            output["basis"] = eps_basis
            output["detail"] = eps_note

    # --- 다다음 분기: 컨센서스 매출 × 마진 ---
    # ⚠️ 다음 분기가 가이던스 기반이면 다다음 분기도 "가이던스에 내재된 마진"을
    #    씁니다. 마진 기준이 분기마다 다르면(가이던스 26% vs 과거 평균 31%),
    #    매출이 그대로여도 이익이 튀는 것처럼 보여 가짜 반등/꺾임 신호가 생깁니다.
    margin_for_q2 = guidance_margin_pct if guidance_margin_pct is not None else avg_margin
    if consensus["revenue_1q"] and margin_for_q2 is not None:
        output["forward_op_income_2"] = consensus["revenue_1q"] * margin_for_q2 / 100.0
        output["basis_2"] = cfg.SRC_ESTIMATE
        margin_label = "가이던스 내재 마진" if guidance_margin_pct is not None else "평균 영업마진"
        output["detail_2"] = (
            f"다다음 분기는 월가 매출 컨센서스(${consensus['revenue_1q']/1e6:,.0f}M) × "
            f"{margin_label}({margin_for_q2:.1f}%)으로 추정했습니다"
        )

    # --- 마지막 관문: 전망이 말이 되는 크기인지 확인 ---
    # 여기까지 오는 동안 어딘가에서 단위가 어긋났다면, 한 분기 만에 이익이
    # 수백만 배가 되는 값이 나옵니다. 그런 값은 화면에 내보내지 않고
    # "전망 없음"으로 두는 편이 정직합니다.
    latest_op = next(
        (q["op_income"] for q in reversed(quarters)
         if q.get("op_income") is not None and q["op_income"] > 0),
        None,
    )
    ok, reason = dq.check_forward(output["forward_op_income"], latest_op)
    if not ok:
        consensus["errors"].append(f"다음 분기 전망 제외: {reason}")
        output["forward_op_income"] = None
        output["basis"] = None
        output["detail"] = f"계산된 전망이 이상해서 쓰지 않았습니다 ({reason})"
        # 다음 분기가 무너지면 그것을 기준으로 만든 다다음 분기도 믿을 수 없습니다
        output["forward_op_income_2"] = None
        output["basis_2"] = None
        output["detail_2"] = ""
    else:
        ok2, reason2 = dq.check_forward(output["forward_op_income_2"], latest_op)
        if not ok2:
            consensus["errors"].append(f"다다음 분기 전망 제외: {reason2}")
            output["forward_op_income_2"] = None
            output["basis_2"] = None
            output["detail_2"] = f"계산된 전망이 이상해서 쓰지 않았습니다 ({reason2})"

    return output
