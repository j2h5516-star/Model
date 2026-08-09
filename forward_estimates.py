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

import re

import config as cfg

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
    }
    if not section:
        return result

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

    # --- 매출총이익률 전망 ---
    gm_area = _slice_around(section, r"gross\s+margin")
    if gm_area:
        result["gross_margin_pct"] = _parse_pct(gm_area)

    # --- 영업비용 전망 ---
    opex_area = _slice_around(section, r"operating\s+expenses?")
    if opex_area:
        match = _RANGE_RE.search(opex_area)
        if match:
            tail_scale = _SCALE.get((match.group(4) or "").lower(), 1e6)
            low = _to_dollars(match.group(1), match.group(2), tail_scale)
            high = _to_dollars(match.group(3), match.group(4), tail_scale)
            result["opex"] = (low + high) / 2.0
        else:
            match = _SINGLE_RE.search(opex_area)
            if match:
                result["opex"] = _to_dollars(match.group(1), match.group(2), 1e6)

    # --- 영업마진 전망 (영업비용 대신 마진을 제시하는 회사도 있음) ---
    om_area = _slice_around(section, r"operating\s+margin")
    if om_area:
        result["operating_margin_pct"] = _parse_pct(om_area)

    return result


def _slice_around(text: str, keyword_pattern: str, width: int = 260) -> str:
    """특정 단어 주변만 잘라냅니다 (엉뚱한 숫자를 잡지 않도록)."""
    match = re.search(keyword_pattern, text, re.I)
    if not match:
        return ""
    return text[match.start() : match.start() + width]


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


def forward_from_guidance(guidance: dict) -> float | None:
    """가이던스 숫자로 다음 분기 논갭 영업이익을 계산합니다.

    방법 1: 매출 × 영업마진%
    방법 2: 매출 × GM% − 영업비용
    """
    revenue = guidance.get("revenue")
    if revenue is None:
        return None

    if guidance.get("operating_margin_pct") is not None:
        return revenue * guidance["operating_margin_pct"] / 100.0

    gm = guidance.get("gross_margin_pct")
    opex = guidance.get("opex")
    if gm is not None and opex is not None:
        return revenue * gm / 100.0 - abs(opex)

    return None


# ---------------------------------------------------------------------------
# ② yfinance 매출 컨센서스로 추정
# ---------------------------------------------------------------------------
def fetch_yf_forward(ticker: str) -> dict:
    """yfinance에서 다음 분기 매출 컨센서스와 EPS 리비전 정보를 가져옵니다.

    반환: {"next_q_revenue": 달러 or None, "revision": +1/0/-1}
    """
    result = {"next_q_revenue": None, "revision": 0}
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
    except Exception:
        return result

    # --- 다음 분기 매출 컨센서스 ---
    try:
        revenue_est = stock.revenue_estimate
        if revenue_est is not None and len(revenue_est) > 0 and "avg" in revenue_est.columns:
            # 인덱스 "0q" = 이번(다음 발표) 분기, "+1q" = 그다음 분기
            for key in ("0q", "+1q"):
                if key in revenue_est.index:
                    value = revenue_est.loc[key, "avg"]
                    if value is not None and float(value) > 0:
                        result["next_q_revenue"] = float(value)
                        break
    except Exception:
        pass

    # --- EPS 추정치 리비전 방향 (최근 30일 상향/하향 건수) ---
    try:
        revisions = stock.eps_revisions
        if revisions is not None and len(revisions) > 0:
            row_key = "0q" if "0q" in revisions.index else revisions.index[0]
            up = _safe_number(revisions, row_key, ["upLast30days", "upLast30Days"])
            down = _safe_number(revisions, row_key, ["downLast30days", "downLast30Days"])
            if up is not None and down is not None:
                if up > down:
                    result["revision"] = 1
                elif down > up:
                    result["revision"] = -1
    except Exception:
        pass

    return result


def _safe_number(df, row_key, column_candidates: list[str]) -> float | None:
    """표에서 값을 꺼냅니다 (열 이름이 버전마다 달라 여러 후보를 시도)."""
    for column in column_candidates:
        if column in df.columns:
            try:
                value = df.loc[row_key, column]
                if value is not None:
                    return float(value)
            except (KeyError, TypeError, ValueError):
                continue
    return None


def average_operating_margin(quarters: list[dict], n: int = 4) -> float | None:
    """최근 n개 분기의 평균 논갭 영업마진(%)을 계산합니다."""
    margins = []
    for q in quarters[-n:]:
        revenue, op_income = q.get("revenue"), q.get("op_income")
        if revenue and op_income is not None and revenue > 0:
            margins.append(op_income / revenue * 100.0)
    if not margins:
        return None
    return sum(margins) / len(margins)


# ---------------------------------------------------------------------------
# 바깥에서 호출하는 함수
# ---------------------------------------------------------------------------
def estimate_forward(ticker: str, quarters: list[dict]) -> dict:
    """다음 분기 논갭 영업이익 전망을 만듭니다.

    반환:
      forward_op_income  포워드 논갭 영업이익 (달러) — 못 구하면 None
      basis              "가이던스" / "추정" / None
      revision           +1(상향) / 0(중립) / −1(하향)
      detail             화면에 보여줄 설명 문장
    """
    output = {
        "forward_op_income": None,
        "basis": None,
        "revision": 0,
        "detail": "다음 분기 전망 데이터를 찾지 못했습니다",
    }
    if not quarters:
        return output

    # 리비전 방향과 매출 컨센서스는 두 방법 모두에서 쓰이므로 먼저 가져옵니다
    yf_data = fetch_yf_forward(ticker)
    output["revision"] = yf_data["revision"]

    # --- ① 가이던스 기반 ---
    latest = quarters[-1]
    guidance = parse_guidance(latest.get("guidance_text", "") or "")
    forward = forward_from_guidance(guidance)
    if forward is not None:
        output["forward_op_income"] = forward
        output["basis"] = cfg.SRC_GUIDANCE
        revenue_text = f"${guidance['revenue']/1e6:,.0f}M" if guidance.get("revenue") else "-"
        output["detail"] = f"회사가 제시한 다음 분기 전망(매출 {revenue_text} 기준)으로 계산했습니다"
        return output

    # --- ② yfinance 매출 컨센서스 × 최근 4분기 평균 마진 ---
    avg_margin = average_operating_margin(quarters, n=4)
    if yf_data["next_q_revenue"] and avg_margin is not None:
        output["forward_op_income"] = yf_data["next_q_revenue"] * avg_margin / 100.0
        output["basis"] = cfg.SRC_ESTIMATE
        output["detail"] = (
            f"애널리스트 매출 전망(${yf_data['next_q_revenue']/1e6:,.0f}M)에 "
            f"최근 4개 분기 평균 영업마진({avg_margin:.1f}%)을 곱한 추정치입니다"
        )
    return output
