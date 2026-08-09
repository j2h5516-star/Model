"""
explain.py — "이 숫자가 어떻게 나왔는지" 설명문 만들기
====================================================

화면에서 배지나 항목을 눌렀을 때 보여줄 상세 설명을 만듭니다.
각 설명은 아래 네 부분으로 구성됩니다:

  1) 이게 무슨 뜻인가 (비전문가용 한 줄 설명)
  2) 어떻게 계산했나 (실제 숫자를 넣은 계산식)
  3) 어디서 가져온 자료인가 (출처)
  4) 지금까지 어떻게 변해왔나 / 앞으로 어떻게 될 것 같나

계산 자체는 scoring.py가 하고, 이 파일은 그 결과를 사람이 읽을 수 있게 풀어 씁니다.
"""

from __future__ import annotations

import config as cfg


def _m(value: float | None) -> str:
    """달러 금액을 "$123.4M" 형태로 짧게 바꿉니다."""
    if value is None:
        return "-"
    return f"${value / 1e6:,.1f}M"


def _pct(value: float | None, digits: int = 1) -> str:
    """퍼센트를 "+12.3%" 형태로 바꿉니다."""
    if value is None:
        return "-"
    return f"{value:+.{digits}f}%"


# ---------------------------------------------------------------------------
# ① 데이터 출처 배지 (직접공시 / 역산 / 근사치)
# ---------------------------------------------------------------------------
def explain_data_source(score: dict) -> dict:
    """실적 숫자를 어떤 방법으로 구했는지 설명합니다."""
    source = score.get("data_source")
    quarters = score.get("quarters") or []

    meanings = {
        cfg.SRC_DIRECT: (
            "회사가 보도자료에 **직접 적어 놓은** non-GAAP 영업이익을 그대로 가져왔습니다. "
            "가장 정확한 방법입니다."
        ),
        cfg.SRC_DERIVED: (
            "보도자료에 non-GAAP 영업이익이 따로 없어서, 함께 공시된 매출·마진·비용으로 "
            "**계산해서 만든 값**입니다. 회사가 발표했을 값과 거의 같지만 완전히 동일하지는 않을 수 있습니다."
        ),
        cfg.SRC_APPROX: (
            "보도자료를 읽지 못해 SEC의 회계데이터(XBRL)로 **근사치를 만든 값**입니다. "
            "회사가 발표하는 공식 non-GAAP 수치와 차이가 날 수 있으니 참고용으로만 보세요."
        ),
    }

    formula = {
        cfg.SRC_DIRECT: "보도자료의 `Non-GAAP operating income` 항목을 그대로 사용",
        cfg.SRC_DERIVED: "`매출 × non-GAAP 매출총이익률 − non-GAAP 영업비용`",
        cfg.SRC_APPROX: "`GAAP 영업이익 + 주식보상비 + 무형자산상각`",
    }

    return {
        "title": f"데이터 출처: {source or '없음'}",
        "meaning": meanings.get(source, "실적 데이터를 찾지 못했습니다."),
        "formula": formula.get(source, ""),
        "detail": score.get("source_derivation", ""),
        "source_text": (
            "미국 증권거래위원회(SEC) 전자공시 EDGAR의 8-K(Item 2.02 실적발표) 공시"
            if source in (cfg.SRC_DIRECT, cfg.SRC_DERIVED)
            else "SEC EDGAR의 XBRL 회계데이터(Company Facts)"
        ),
        "url": score.get("filing_url", ""),
        "quarter_count": len(quarters),
    }


# ---------------------------------------------------------------------------
# ② 포워드(전망) 배지 — 가이던스 / 추정
# ---------------------------------------------------------------------------
def explain_forward(score: dict) -> dict:
    """다음 분기 전망을 어떻게 만들었는지 설명합니다."""
    basis = score.get("forward_basis")
    trace = score["fundamental"]["forward"].get("trace", {})

    if basis == cfg.SRC_GUIDANCE:
        meaning = (
            "**회사가 직접 발표한** 다음 분기 전망(가이던스)을 사용했습니다. "
            "회사가 내부 수주·재고를 보고 내놓은 숫자라 신뢰도가 상대적으로 높습니다."
        )
        formula = "`가이던스 매출(범위면 중간값) × 영업마진%` 또는 `매출 × GM% − 영업비용`"
        source_text = "8-K 실적 보도자료의 'Business Outlook / Guidance' 문단"
    elif basis == cfg.SRC_ESTIMATE:
        meaning = (
            "회사가 전망을 밝히지 않아 **통계적으로 추정한 값**입니다. "
            "무료 데이터로는 진짜 월가 컨센서스를 구할 수 없어서 쓰는 대체 방법입니다. "
            "실제 결과와 다를 수 있습니다."
        )
        formula = "`애널리스트 다음 분기 매출 전망 × 최근 4개 분기 평균 non-GAAP 영업마진`"
        source_text = "Yahoo Finance의 애널리스트 매출 추정치(revenue estimate)"
    else:
        meaning = "다음 분기 전망을 만들 자료를 찾지 못했습니다. 이 경우 포워드 항목은 기본 점수만 받습니다."
        formula = ""
        source_text = "-"

    latest_op = trace.get("latest_op")
    forward_op = trace.get("forward_op")
    growth = trace.get("growth_pct")

    calc_lines = []
    if forward_op is not None:
        calc_lines.append(f"- 다음 분기 예상 영업이익: **{_m(forward_op)}**")
    if latest_op is not None:
        calc_lines.append(f"- 가장 최근 분기 실제 영업이익: {_m(latest_op)}")
    if growth is not None:
        calc_lines.append(f"- 변화율: {_pct(growth)} ({'증가' if growth > 0 else '감소'} 전망)")

    return {
        "title": f"다음 분기 전망 근거: {basis or '없음'}",
        "meaning": meaning,
        "formula": formula,
        "detail": score.get("forward_detail", ""),
        "calc_lines": calc_lines,
        "source_text": source_text,
        "revision_text": trace.get("revision_text", ""),
    }


# ---------------------------------------------------------------------------
# ③ GM% 드라이버 (물량형 / 믹스형 / 가격형 / 마진 악화)
# ---------------------------------------------------------------------------
def explain_gm_driver(score: dict) -> dict:
    """마진 변화의 성격이 왜 그렇게 판정됐는지 설명합니다."""
    gm = score["fundamental"]["gm"]
    trace = gm.get("trace", {})
    quarters = score.get("quarters") or []

    meanings = {
        "물량형": (
            "마진은 거의 그대로인데 **매출 규모가 커지면서** 이익이 늘어나는 형태입니다. "
            "가장 건강한 성장으로 봅니다. 제품이 잘 팔린다는 뜻이기 때문입니다."
        ),
        "믹스형": (
            "**수익성 좋은 제품의 비중이 늘어나서** 마진이 올라간 형태입니다. "
            "좋은 신호지만, 제품 구성이 다시 바뀌면 원래대로 돌아갈 수 있습니다."
        ),
        "가격형": (
            "마진이 급등했습니다. **가격 인상이나 일시적 요인**일 가능성이 큽니다. "
            "이런 마진 상승은 오래 유지되기 어려워 점수를 낮게 줍니다."
        ),
        "마진 악화": (
            "마진이 과거 평균보다 눈에 띄게 떨어졌습니다. "
            "원가 상승이나 가격 경쟁이 심해졌을 수 있습니다."
        ),
        "판단불가": "마진 데이터가 부족해 성격을 판단할 수 없습니다.",
    }

    # 분기별 GM% 이력 표
    history = []
    margins = trace.get("margins", [])
    labeled = [q for q in quarters if q.get("gross_margin_pct") is not None]
    for quarter, margin in zip(labeled, margins):
        history.append({"분기": quarter.get("period_label", "-"), "GM%": round(margin, 2)})

    return {
        "title": f"GM% 드라이버: {gm.get('type', '-')}",
        "meaning": meanings.get(gm.get("type"), ""),
        "formula": "`최근 분기 GM% − 직전 4개 분기 평균 GM%` 의 차이(%p)로 성격을 나눕니다",
        "bands": [
            ("±1%p 이내", "물량형", cfg.GM_SCORE_VOLUME),
            ("+1 ~ +3%p", "믹스형", cfg.GM_SCORE_MIX),
            ("+3%p 초과", "가격형", cfg.GM_SCORE_PRICE),
            ("−1%p 초과 하락", "마진 악화", cfg.GM_SCORE_DECLINE),
        ],
        "calc_lines": [
            f"- 최근 분기 GM%: **{trace.get('latest', 0):.2f}%**"
            if trace.get("latest") is not None else "",
            f"- 직전 4개 분기 평균: {trace.get('baseline', 0):.2f}%"
            if trace.get("baseline") is not None else "",
            f"- 차이: **{trace.get('delta_pp', 0):+.2f}%p** → {gm.get('type')}"
            if trace.get("delta_pp") is not None else "",
            f"- 점수: **{gm.get('score', 0):.0f} / {cfg.W_GM_DRIVER}점**",
        ],
        "history": history,
        "score": gm.get("score"),
        "source_text": "각 분기 8-K 보도자료의 non-GAAP 매출총이익률(없으면 GAAP 값)",
    }


# ---------------------------------------------------------------------------
# ④ 델타 방향 (가속 / 유지 / 감속) — 이력과 예측 포함
# ---------------------------------------------------------------------------
def explain_delta(score: dict) -> dict:
    """이익 증가 속도가 왜 가속/감속으로 판정됐는지, 앞으로는 어떨지 설명합니다."""
    delta = score["fundamental"]["delta"]
    trace = delta.get("trace", {})
    quarters = score.get("quarters") or []
    growths = trace.get("growths", [])

    meanings = {
        "가속": (
            "이익이 늘어나는 **속도 자체가 빨라지는 중**입니다. "
            "단순히 '많이 벌었다'가 아니라 '점점 더 빠르게 벌고 있다'는 뜻으로, "
            "주가가 가장 잘 반응하는 구간으로 알려져 있습니다."
        ),
        "유지": "이익 증가 속도가 비슷한 수준을 유지하고 있습니다. 나쁘지 않지만 탄력은 붙지 않은 상태입니다.",
        "감속": (
            "이익은 늘고 있을 수 있지만 **늘어나는 속도가 느려지는 중**입니다. "
            "성장 사이클의 후반부일 가능성을 살펴야 합니다."
        ),
        "판단불가": "분기 데이터가 부족해 속도 변화를 계산할 수 없습니다.",
    }

    # 분기별 QoQ 증가율 이력 (증가율은 두 번째 분기부터 계산됨)
    history = []
    valid_quarters = [q for q in quarters if q.get("op_income") is not None]
    for quarter, growth in zip(valid_quarters[1:], growths):
        history.append(
            {
                "분기": quarter.get("period_label", "-"),
                "영업이익($M)": round(quarter["op_income"] / 1e6, 1),
                "QoQ 증가율(%)": round(growth, 1),
            }
        )

    # --- 앞으로의 예측 ---
    # 다음 분기 전망치로 QoQ 증가율을 미리 계산해, 가속이 이어질지 꺾일지 봅니다.
    forward_trace = score["fundamental"]["forward"].get("trace", {})
    forward_op = forward_trace.get("forward_op")
    latest_op = forward_trace.get("latest_op")

    forecast_lines = []
    if forward_op is not None and latest_op and latest_op > 0:
        next_growth = (forward_op / latest_op - 1.0) * 100.0
        forecast_lines.append(
            f"- 다음 분기 예상 QoQ 증가율: **{next_growth:+.1f}%** "
            f"({_m(latest_op)} → {_m(forward_op)})"
        )
        if growths:
            change = next_growth - growths[-1]
            if change > 3.0:
                verdict = "**가속이 이어질 것으로 보입니다** ↗"
            elif change < -3.0:
                verdict = "**감속으로 돌아설 가능성이 있습니다** ↘"
            else:
                verdict = "**비슷한 속도를 유지할 것으로 보입니다** →"
            forecast_lines.append(
                f"- 최근 실제 증가율 {growths[-1]:+.1f}% 대비 {change:+.1f}%p → {verdict}"
            )
        forecast_lines.append(
            f"- ⚠️ 이 예측은 **{score.get('forward_basis') or '추정'}** 기반이며 실제 결과와 다를 수 있습니다"
        )
    else:
        forecast_lines.append("- 다음 분기 전망치가 없어 예측을 계산할 수 없습니다")

    calc_lines = []
    recent = trace.get("recent", [])
    if len(recent) >= 2:
        calc_lines.append(f"- 직전 분기 증가율: {recent[-2]:+.1f}%")
        calc_lines.append(f"- 최근 분기 증가율: **{recent[-1]:+.1f}%**")
        calc_lines.append(
            f"- 변화: **{trace.get('delta_pp', 0):+.1f}%p** "
            f"(기준 ±{trace.get('threshold', 3.0):.0f}%p) → {delta.get('direction')}"
        )
    if trace.get("capped"):
        calc_lines.append(
            f"- ⚠️ 최근 분기 이익이 {_m(trace.get('latest_op'))}로 "
            f"{_m(cfg.LOW_BASE_THRESHOLD_USD)} 미만이라 점수를 절반으로 제한했습니다 (저기저 함정 방지)"
        )
    calc_lines.append(f"- 점수: **{delta.get('score', 0):.0f} / {cfg.W_DELTA_ACCEL}점**")

    return {
        "title": f"델타 방향: {delta.get('direction', '-')}",
        "meaning": meanings.get(delta.get("direction"), ""),
        "formula": "`이번 분기 QoQ 증가율 − 직전 분기 QoQ 증가율` 이 +3%p 초과면 가속, −3%p 미만이면 감속",
        "calc_lines": calc_lines,
        "history": history,
        "forecast_lines": forecast_lines,
        "source_text": "각 분기 8-K 보도자료의 non-GAAP 영업이익 (2025년 초부터 발표일 순)",
    }


# ---------------------------------------------------------------------------
# ⑤ 최종 판정 (매수 후보 / 경고 / 관찰 / 펀더 없는 모멘텀 / 제외)
# ---------------------------------------------------------------------------
def explain_verdict_full(score: dict) -> dict:
    """최종 판정이 어떤 규칙으로 나왔는지 설명합니다."""
    info = score.get("verdict_info", {})

    return {
        "title": f"판정: {info.get('verdict', '-')}",
        "meaning": info.get("meaning", ""),
        "formula": "`펀더멘털 점수`와 `추세 단계` 두 가지를 조합한 표(매트릭스)로 판정합니다",
        "calc_lines": [
            f"- {info.get('fund_text', '')}",
            f"- {info.get('stage_text', '')} — {score.get('trend_state', '-')}",
            f"- 적용된 규칙: {info.get('rule', '')}",
            f"- 최종점수: **{score.get('final_score', 0):.0f}점** "
            f"(펀더 {score.get('fund_score', 0):.0f} × {cfg.WEIGHT_FUNDAMENTAL} + "
            f"기술 {score.get('tech_score', 0):.0f} × {cfg.WEIGHT_TECHNICAL})",
        ],
        "rules": [
            (cfg.V_BUY, f"펀더 {cfg.FUND_STRONG:.0f}+ & 추세 1~2단계", "실적·주가 모두 양호"),
            (cfg.V_WARN, f"펀더 {cfg.FUND_STRONG:.0f}+ & 추세 4~5단계", "실적 좋은데 주가 꺾임 — 정점 가능성"),
            (cfg.V_WATCH, f"펀더 {cfg.FUND_WEAK:.0f}~{cfg.FUND_STRONG:.0f} & 추세 1~2단계", "추세는 좋으나 실적 확신 부족"),
            (cfg.V_MOMENTUM, f"펀더 {cfg.FUND_WEAK:.0f} 미만 & 추세 1~2단계", "실적 근거 없이 주가만 상승"),
            (cfg.V_EXCLUDE, "그 외", "뚜렷한 특징 없음"),
        ],
        "current": info.get("verdict"),
        "source_text": "펀더멘털 점수(SEC 실적 기반) + 추세 단계(야후 주가 기반)",
    }


# ---------------------------------------------------------------------------
# ⑥ 기술 점수 세부 (추세/기울기/이격도/RS)
# ---------------------------------------------------------------------------
def explain_technical(score: dict) -> dict:
    """기술 점수 100점이 어떻게 쪼개졌는지 설명합니다."""
    tech = score["technical"]

    return {
        "title": f"기술 점수: {score.get('tech_score', 0):.0f}점",
        "meaning": (
            "주가 흐름만 따로 떼어 100점으로 만든 점수입니다. "
            "주봉(1주 단위) 이동평균선의 배열과 시장 대비 성과를 봅니다."
        ),
        "formula": "`추세 50 + 26주선 기울기 10 + 이격도 15 + 상대강도 25`",
        "calc_lines": [
            f"- 추세: **{tech['trend_score']:.0f} / {cfg.W_TREND}점** — {score.get('trend_state')}",
            f"- 26주선 기울기: **{tech['slope_score']:.0f} / {cfg.W_SLOPE}점** — {score.get('slope')}",
            f"- 이격도: **{tech['disparity_score']:.0f} / {cfg.W_DISPARITY}점** — {tech['disparity_text']}",
            f"- 상대강도: **{tech['rs_score']:.0f} / {cfg.W_RS}점** — {tech['rs_text']}",
        ],
        "source_text": "Yahoo Finance 주가(주봉 종가 기준) · 비교 지수 SPY",
    }
