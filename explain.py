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
import data_quality as dq


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
def explain_confidence(score: dict) -> dict:
    """이 종목의 데이터 신뢰도가 어떻게 나왔는지 설명합니다."""
    conf = score.get("confidence_detail") or {}

    counts = conf.get("counts", {})
    count_text = " · ".join(f"{src} {n}개" for src, n in counts.items()) or "-"

    return {
        "title": f"데이터 신뢰도: {conf.get('total', 0):.0f}%",
        "meaning": (
            "이 종목의 숫자를 **얼마나 믿을 수 있는지**를 백분율로 나타낸 값입니다. "
            "회사가 직접 발표한 수치일수록 높고, 계산으로 만든 근사치일수록 낮습니다. "
            "**점수에는 반영하지 않고 참고용으로만 표시합니다.**"
        ),
        "formula": (
            f"`실적 신뢰도 × {cfg.CONF_WEIGHT_ACTUAL} + 전망 신뢰도 × {cfg.CONF_WEIGHT_FORWARD}` · "
            f"실적 신뢰도는 분기별 출처의 가중평균(최근 분기일수록 큰 가중치)"
        ),
        "calc_lines": [
            f"- 실적 신뢰도: **{conf.get('actual', 0):.0f}%** (분기 {conf.get('quarter_count', 0)}개: {count_text})",
            f"- 전망 신뢰도: **{conf.get('forward', 0):.0f}%** ({conf.get('forward_basis', '-')})",
            f"- 종합: {conf.get('actual', 0):.0f} × {cfg.CONF_WEIGHT_ACTUAL} + "
            f"{conf.get('forward', 0):.0f} × {cfg.CONF_WEIGHT_FORWARD} = "
            f"**{conf.get('total', 0):.0f}%**",
        ],
        "history": conf.get("breakdown", []),
        "source_text": "각 분기의 수집 방법(직접공시/역산/근사치)과 전망 근거(가이던스/추정)",
    }


def confidence_tier_table() -> list[dict]:
    """신뢰도 등급표 전체를 표로 만듭니다 (화면의 '등급표' 섹션용)."""
    return [
        {
            "순위": rank,
            "종류": name,
            "신뢰도": f"{pct}%",
            "무엇인가": description,
            "계산 방법": formula,
        }
        for rank, name, pct, description, formula in cfg.CONFIDENCE_TIERS
    ]


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
            "회사가 전망을 밝히지 않아 **월가 컨센서스 기반 추정치**를 사용했습니다. "
            "야후 파이낸스의 애널리스트 매출 컨센서스(LSEG 집계)에 과거 평균 마진을 곱한 "
            "값이라 실제 결과와 다를 수 있습니다."
        )
        formula = "`월가 다음 분기 매출 컨센서스 × 최근 4개 분기 평균 non-GAAP 영업마진`"
        source_text = "Yahoo Finance 애널리스트 컨센서스 (LSEG 집계)"
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

    # 다다음 분기(월가 컨센서스)와 컨센서스의 무게(애널리스트 수)도 보여줍니다
    forward_op_2 = score.get("forward_op_income_2")
    if forward_op_2 is not None:
        calc_lines.append(f"- 다다음 분기 예상(컨센서스): {_m(forward_op_2)}")
    forecast = score.get("forecast_detail") or {}
    consensus = (score.get("forward_consensus") or {})
    analysts = consensus.get("analysts_0q")
    if analysts:
        calc_lines.append(f"- 컨센서스 참여 애널리스트: **{analysts}명**")
    velocity = consensus.get("revision_velocity_pct")
    if velocity is not None:
        calc_lines.append(f"- 추정치 30일 변화(리비전 속도): **{velocity:+.1f}%**")

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
# ③ 국면 — 이익 사이클에서 지금 어디에 서 있는가
# ---------------------------------------------------------------------------
def explain_phase(score: dict) -> dict:
    """왜 이 국면으로 판정됐는지, 그리고 이 신호를 어디까지 믿어야 하는지."""
    phase_result = score.get("fundamental", {}).get("phase", {})
    trace = phase_result.get("trace", {})
    phase = phase_result.get("phase", "-")

    meanings = {
        cfg.PH_TURNAROUND: (
            "1년치 이익이 **적자에서 흑자로 돌아섰습니다.** 사이클 바닥을 막 지난 자리입니다.\n\n"
            "델타(증가 속도)만으로는 이 순간을 볼 수 없습니다. 직전이 적자면 "
            "증가율을 계산할 자체가 안 되기 때문입니다. 2025년 반도체 슈퍼사이클에서 "
            "마이크론의 이익 폭증이 시작된 분기를 델타는 **4분기 뒤에야** 알아챘고, "
            "이 신호는 바로 그 분기에 켜졌습니다."
        ),
        cfg.PH_NEW_HIGH: (
            "1년치 이익이 **수집 기간 안의 최고 기록을 넘어섰습니다.** "
            "사이클 상단을 새로 뚫고 올라가는 자리입니다.\n\n"
            "검증에서 이 신호가 켜진 67개 시점 중 95.5%는 2분기 뒤 이익이 더 늘었고, "
            "크게 오른 구간(1년치 이익 +30% 이상)의 83%를 잡아냈습니다.\n\n"
            "⚠️ **'역대 최고'가 아닙니다.** 이 대시보드는 3년치만 모으므로, "
            "예전에 훨씬 잘 벌다가 무너진 회사가 조금 회복해도 여기에 들어옵니다 "
            "(예: 인텔). 그래서 이름을 '신고점'이 아니라 **'3년내 최고'** 로 뒀습니다. "
            "회사가 원래 어느 수준에서 벌던 곳인지는 따로 확인하셔야 합니다."
        ),
        cfg.PH_LOSS: (
            "1년치 이익이 **아직 적자**입니다. 흑자로 돌아서기 전까지는 "
            "사이클의 어디쯤인지 말할 수가 없습니다.\n\n"
            "적자가 줄어드는 중일 수도 있지만, 그것과 '이익 사이클의 좋은 자리'는 "
            "다릅니다. 좋아 보이게 포장하지 않고 그대로 표시합니다."
        ),
        cfg.PH_ROLLOVER: (
            f"1년치 이익이 **과거 최고의 {cfg.PHASE_ROLLOVER_RATIO*100:.0f}% 아래로** "
            "내려왔습니다. 고점에서 이탈한 자리입니다.\n\n"
            "검증에서 이 상태의 29개 시점 중 2분기 뒤 이익이 늘어난 것은 45%뿐이었습니다 "
            "(전체 평균 73%)."
        ),
        cfg.PH_NONE: (
            "과거 최고를 넘지도, 크게 벗어나지도 않은 **중간 자리**입니다.\n\n"
            "검증에서 이 상태의 34개 시점 중 1년치 이익이 30% 이상 오른 것은 "
            "**한 번도 없었습니다.** 나쁜 자리는 아니지만 크게 오를 자리도 아닙니다."
        ),
        cfg.PH_UNKNOWN: "1년치 이익을 두 번 비교할 만큼 분기가 모이지 않았습니다.",
    }

    calc_lines = []
    if trace.get("now") is not None:
        calc_lines.append(f"- 지금 1년치 이익: **{_m(trace.get('now'))}**")
        calc_lines.append(f"- 직전 1년치 이익: {_m(trace.get('previous'))}")
        calc_lines.append(f"- 과거 최고 1년치 이익: {_m(trace.get('peak'))}")
        if trace.get("vs_peak_pct") is not None:
            calc_lines.append(f"- 최고 대비: **{trace['vs_peak_pct']:.0f}%**")
        calc_lines.append(
            f"- 배점 비율 {trace.get('ratio', 0):.2f} → "
            f"점수 **{phase_result.get('score', 0):.0f} / {cfg.W_PHASE}점**"
        )

    return {
        "title": f"국면: {phase}",
        "meaning": meanings.get(phase, ""),
        "formula": (
            "**최근 4분기를 더한 1년치 이익**이 사이클의 어디에 있는지로 판정합니다.\n\n"
            "· 직전 1년치가 적자였는데 지금 흑자 → **턴어라운드 진입**\n\n"
            "· 지금이 과거 최고보다 높음 → **신고점 돌파**\n\n"
            f"· 지금이 과거 최고의 {cfg.PHASE_ROLLOVER_RATIO*100:.0f}% 미만 → **고점 이탈**\n\n"
            "· 그 사이 → **중간 자리**\n\n"
            "⚠️ **이 신호가 하지 못하는 일**: 이것은 *종목끼리 줄 세우는* 신호이지 "
            "*한 종목을 언제 사야 하는지* 알려주는 신호가 아닙니다. 검증에 쓴 표본에서 "
            "잘 크는 종목은 거의 항상 신호가 있었고 부진한 종목은 거의 항상 없었기 때문에, "
            "한 종목 안에서의 타이밍 효과는 재지 못했습니다. 그대로 밝혀 둡니다."
        ),
        "calc_lines": calc_lines,
        "detail": phase_result.get("detail", ""),
    }


# ---------------------------------------------------------------------------
# ④ GM% 드라이버 (물량형 / 믹스형 / 가격형 / 마진 악화)
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
    # scoring 과 **같은 필터**를 써야 목록 길이가 맞습니다.
    # (예전에는 범위를 벗어난 GM%를 scoring 만 걸러내서 이력표가 한 칸씩 밀렸습니다)
    labeled = [
        q for q in quarters
        if q.get("gross_margin_pct") is not None
        and cfg.MARGIN_MIN_PCT <= q["gross_margin_pct"] <= cfg.MARGIN_MAX_PCT
    ]
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
        cfg.D_ACCEL: (
            "이익이 늘어나는 **속도 자체가 빨라지는 중**입니다. "
            "단순히 '많이 벌었다'가 아니라 '점점 더 빠르게 벌고 있다'는 뜻으로, "
            "주가가 가장 잘 반응하는 구간으로 알려져 있습니다."
        ),
        cfg.D_STEADY: "이익 증가 속도가 비슷한 수준을 유지하고 있습니다. 나쁘지 않지만 탄력은 붙지 않은 상태입니다.",
        cfg.D_DECEL: (
            "이익은 늘고 있을 수 있지만 **늘어나는 속도가 느려지는 중**입니다. "
            "성장 사이클의 후반부일 가능성을 살펴야 합니다."
        ),
        cfg.D_MIXED: (
            "**두 신호가 서로 반대**입니다. 최근 한 분기는 좋아졌는데 여러 분기 추세는 "
            "나빠지고 있거나, 그 반대입니다. 억지로 가속/감속 중 하나로 정하지 않고 "
            "'판단이 갈린다'고 솔직히 표시합니다. 다음 실적발표를 보고 판단하는 게 좋습니다."
        ),
        cfg.D_UNKNOWN: "분기 데이터가 부족해 속도 변화를 계산할 수 없습니다.",
    }

    # 분기별 증가율 이력.
    # ⚠️ 압축된 growths 목록과 그냥 zip 하면 안 됩니다. 적자·단위오류로 건너뛴
    #    분기가 있으면 증가율이 **한 칸씩 밀려 엉뚱한 분기에 붙고** 최근 분기가
    #    통째로 빠집니다. 여기서 분기 쌍마다 다시 계산해 빈칸을 그대로 둡니다.
    history = []
    valid_quarters = [q for q in quarters if q.get("op_income") is not None]

    if trace.get("use_ttm"):
        # 판정에 실제로 쓴 것은 '최근 4분기 합'이므로 그것을 그대로 보여 줍니다.
        # 화면에 보이는 숫자와 모델이 본 숫자가 다르면 근거를 확인할 수가 없습니다.
        for index in range(3, len(valid_quarters)):
            quarter = valid_quarters[index]
            window = [q["op_income"] for q in valid_quarters[index - 3 : index + 1]]
            ttm_now = sum(window)
            growth = None
            if index >= 4:
                previous = sum(
                    q["op_income"] for q in valid_quarters[index - 4 : index]
                )
                growth = dq.safe_growth_pct(previous, ttm_now)
            history.append(
                {
                    "분기": quarter.get("period_label", "-"),
                    "영업이익($M)": round(quarter["op_income"] / 1e6, 1),
                    "1년치 합($M)": round(ttm_now / 1e6, 1),
                    "1년치 증가율(%)": None if growth is None else round(growth, 1),
                }
            )
    else:
        growth_label = "YoY 증가율(%)" if trace.get("seasonal") else "QoQ 증가율(%)"
        step = 4 if trace.get("seasonal") else 1
        for index in range(step, len(valid_quarters)):
            quarter = valid_quarters[index]
            growth = dq.safe_growth_pct(
                valid_quarters[index - step].get("op_income"), quarter.get("op_income")
            )
            history.append(
                {
                    "분기": quarter.get("period_label", "-"),
                    "영업이익($M)": round(quarter["op_income"] / 1e6, 1),
                    growth_label: None if growth is None else round(growth, 1),
                }
            )

    # --- 앞으로의 예측 (scoring.predict_delta 결과를 그대로 풀어 씁니다) ---
    forecast = score.get("forecast_detail") or {}
    forecast_lines = []
    if forecast.get("next_qoq") is not None:
        forecast_lines.append(f"- 다음 분기 예측: **{forecast.get('label')}**")

        # 이 예측이 과거에 실제로 얼마나 맞았는지 함께 보여 줍니다.
        # 특히 '가속 둔화'는 오래 "정점 근처 신호"로 표시해 왔지만 검증에서
        # 사실이 아니었습니다. 그 사실을 숨기지 않고 같은 자리에 적습니다.
        measured = cfg.FORECAST_MEASURED_PEAK.get(forecast.get("label"))
        if measured is not None:
            base = cfg.FORECAST_PEAK_BASELINE
            verdict = (
                "정점을 알려주는 신호가 **아닙니다**" if measured <= base
                else "정점 신호로 쓸 만합니다"
            )
            forecast_lines.append(
                f"- 📏 이 예측이 뜬 뒤 실제로 정점이었던 비율 **{measured*100:.1f}%** "
                f"(아무 때나 찍었을 때 {base*100:.1f}%) — {verdict}"
            )
        multiplier = cfg.FORECAST_SCORE_MULT.get(forecast.get("label"), 1.0)
        if multiplier != 1.0:
            forecast_lines.append(
                f"- 이 예측 때문에 델타 점수를 **{multiplier:.2f}배** 했습니다"
            )
        forecast_lines.append(
            f"- 최근 실제 증가율 {forecast.get('last_qoq'):+.1f}% → "
            f"다음 분기 예상 **{forecast.get('next_qoq'):+.1f}%** "
            f"({forecast.get('change_pp'):+.1f}%p)"
        )
        # 델타가속예측 (2분기 경로)
        if forecast.get("next2_qoq") is not None:
            forecast_lines.append(
                f"- **델타가속예측: {forecast.get('accel_label')}** — "
                f"{forecast.get('accel_detail', '')}"
            )
            forecast_lines.append(
                f"- 다다음 분기는 월가 컨센서스 매출 × 평균 마진으로 계산한 값입니다 "
                f"(근거 **{forecast.get('basis_2') or '추정'}**)"
            )
        elif forecast.get("accel_detail"):
            forecast_lines.append(f"- {forecast.get('accel_detail')}")
        # 근거는 분기마다 다릅니다 (다음 분기=가이던스, 다다음 분기=컨센서스 추정).
        # 하나로 뭉뚱그리면 2분기 예측의 신뢰도를 실제보다 높게 보이게 합니다.
        basis_text = f"다음 분기 **{forecast.get('basis') or '추정'}**"
        if forecast.get("next2_qoq") is not None:
            basis_text += f" + 다다음 분기 **{forecast.get('basis_2') or '추정'}**"
        forecast_lines.append(
            f"- ⚠️ 이 예측은 {basis_text} 기반"
            f"(다음 분기 신뢰도 {forecast.get('confidence', 0)}%)이며 실제 결과와 다를 수 있습니다"
        )
    else:
        forecast_lines.append("- 다음 분기 전망치가 없어 예측을 계산할 수 없습니다")

    calc_lines = []
    recent = trace.get("recent", [])
    unit = "1년치 이익 증가율" if trace.get("use_ttm") else "분기 증가율"
    if len(recent) >= 2:
        calc_lines.append(f"- 기준: **{trace.get('basis', '-')}**")
        calc_lines.append(f"- 직전 {unit}: {recent[-2]:+.1f}%")
        calc_lines.append(f"- 최근 {unit}: **{recent[-1]:+.1f}%**")
        calc_lines.append(
            f"- **신호① 단기**: {trace.get('delta_pp', 0):+.1f}%p "
            f"(기준 ±{trace.get('threshold', 3.0):.0f}%p)"
        )
    if trace.get("slope") is not None:
        window = trace.get("window", [])
        calc_lines.append(
            f"- **신호② 추세**: 최근 {len(window)}개 증가율의 기울기 "
            f"{trace.get('slope'):+.1f} (기준 ±{trace.get('trend_threshold', 1.5):.1f})"
        )
        calc_lines.append(f"- 두 신호를 합친 결과 → **{delta.get('direction')}**")
    if trace.get("capped"):
        calc_lines.append(
            f"- ⚠️ 최근 분기 이익이 {_m(trace.get('latest_op'))}로 "
            f"{_m(cfg.LOW_BASE_THRESHOLD_USD)} 미만이라 점수를 절반으로 제한했습니다 (저기저 함정 방지)"
        )
    measured = trace.get("measured_hit")
    if measured is not None:
        calc_lines.append(
            f"- 📏 이 방향('{delta.get('direction')}')을 과거 20종목 132개 시점에서 "
            f"검증했을 때 실제로 맞은 비율은 **{measured*100:.0f}%** 였습니다"
            + ("" if measured >= 0.6 else " — **믿을 것이 못 됩니다.** 참고만 하세요")
        )
    calc_lines.append(f"- 점수: **{delta.get('score', 0):.0f} / {cfg.W_DELTA_ACCEL}점**")

    if trace.get("use_ttm"):
        formula = (
            "**최근 4분기를 더한 1년치 이익**의 증가율이 어떻게 변하는지를 봅니다.\n\n"
            "① **단기** = 최근 1년치 증가율 − 직전 1년치 증가율\n\n"
            f"② **추세** = 최근 {cfg.DELTA_TREND_WINDOW}개 증가율의 회귀 기울기\n\n"
            "두 신호가 같은 방향이면 가속/감속, **서로 반대면 '혼조'** 로 표시합니다.\n\n"
            "왜 분기 단위가 아니라 1년치인가 — 20종목 132개 시점으로 두 방식을 "
            "맞대결시킨 결과, 분기 단위는 방향을 55% 맞혔고 1년치는 66% 맞혔습니다. "
            "특히 '가속'이라 말했을 때 실제로 맞은 비율이 82% → 91% 로 올랐습니다. "
            "1년치는 계절 장사의 오르내림도 저절로 상쇄됩니다."
        )
    else:
        formula = (
            "분기가 6개가 안 돼 1년치(TTM)로 볼 수 없어, **분기 단위**로 봅니다.\n\n"
            "① **단기** = 최근 증가율 − 직전 증가율 (민감함)\n\n"
            f"② **추세** = 최근 {cfg.DELTA_TREND_WINDOW}개 증가율의 회귀 기울기 (안정적)\n\n"
            "⚠️ 이 방식은 1년치로 보는 것보다 덜 정확합니다(검증 55% vs 66%). "
            "분기가 더 쌓이면 자동으로 1년치 기준으로 바뀝니다."
        )

    return {
        "title": f"델타 방향: {delta.get('direction', '-')}",
        "meaning": meanings.get(delta.get("direction"), ""),
        "formula": formula,
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
