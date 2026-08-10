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


# 전망 문단이 "한 해 전체"를 말하고 있는지 알아보는 표현들.
# 연간 매출 전망을 다음 '분기' 전망으로 쓰면 이익이 4배로 튀어
# 증가율이 +300% 같은 가짜 값이 됩니다.
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
    # "분기"라는 말이 함께 있으면 분기 전망으로 봅니다 (연간·분기 둘 다 적는 회사가 많음)
    if re.search(r"(first|second|third|fourth|next)\s+quarter", head, re.I):
        return False
    return bool(_ANNUAL_HINTS.search(head))


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
    forward = forward_from_guidance(guidance)
    guidance_margin_pct = None   # 가이던스에 내재된 마진 (다다음 분기에도 같은 기준 적용)
    if forward is not None:
        output["forward_op_income"] = forward
        output["basis"] = cfg.SRC_GUIDANCE
        if guidance.get("revenue"):
            guidance_margin_pct = forward / guidance["revenue"] * 100.0
        revenue_text = f"${guidance['revenue']/1e6:,.0f}M" if guidance.get("revenue") else "-"
        output["detail"] = f"회사가 제시한 다음 분기 전망(매출 {revenue_text} 기준)으로 계산했습니다"
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
