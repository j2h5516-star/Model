"""
scoring.py — 펀더멘털 점수 + 기술 점수 + 최종 판정
==================================================

점수 구조 (자세한 배점은 config.py에서 바꿀 수 있습니다):

  펀더멘털 100점
    · 델타 가속      40점 — 이익 늘어나는 "속도"가 빨라지는가
    · GM% 드라이버   25점 — 마진 개선이 어떤 성격인가(물량/믹스/가격)
    · 매출 성장의 질 20점 — 매출이 얼마나, 그리고 가속하며 크는가
    · 포워드 신호    15점 — 다음 분기 전망이 이번 분기보다 좋은가

  기술 100점
    · 추세 5단계     50점
    · 26주선 기울기  10점
    · 이격도         15점
    · 상대강도(RS)   25점

  최종점수 = 펀더 × 0.6 + 기술 × 0.4
"""

from __future__ import annotations

import config as cfg


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------
def _qoq_growth_series(quarters: list[dict]) -> list[float]:
    """분기별 논갭 영업이익의 QoQ(전분기 대비) 증가율(%) 목록을 만듭니다.

    예) 영업이익이 100 → 120 → 150 이면 [20.0, 25.0]
    직전 분기가 0 이하(적자)면 증가율을 계산할 수 없어 건너뜁니다.
    """
    values = [q.get("op_income") for q in quarters if q.get("op_income") is not None]
    growths: list[float] = []
    for prev, curr in zip(values, values[1:]):
        if prev is None or curr is None or prev <= 0:
            continue
        growths.append((curr / prev - 1.0) * 100.0)
    return growths


def _clamp(value: float, low: float, high: float) -> float:
    """값을 low~high 범위 안으로 잘라 넣습니다."""
    return max(low, min(high, value))


def _linear_slope(values: list[float]) -> float | None:
    """숫자들이 전체적으로 올라가는 추세인지 내려가는 추세인지를 기울기로 냅니다.

    최소제곱법(회귀)으로 직선을 그었을 때의 기울기입니다.
    한 값만 튀어도 판정이 뒤집히는 것을 막기 위해 사용합니다.
    """
    n = len(values)
    if n < 3:
        return None

    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n

    numerator = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(values))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    if denominator == 0:
        return None
    return numerator / denominator


# ---------------------------------------------------------------------------
# 데이터 신뢰도 (점수에는 반영하지 않고 표시만 합니다)
# ---------------------------------------------------------------------------
def compute_confidence(quarters: list[dict], forward: dict) -> dict:
    """이 종목의 숫자를 얼마나 믿을 수 있는지 백분율로 계산합니다.

    실적 신뢰도 : 분기별 출처의 가중평균 (최근 분기일수록 큰 가중치)
    전망 신뢰도 : 가이던스 85% / 추정 40% / 없음 0%
    종합 신뢰도 : 실적 × 0.7 + 전망 × 0.3
    """
    # --- 실적 신뢰도 ---
    breakdown: list[dict] = []
    weighted_sum = 0.0
    weight_total = 0.0

    # 최근 분기부터 거슬러 올라가며 가중치를 줄여 갑니다
    for steps_back, quarter in enumerate(reversed(quarters)):
        source = quarter.get("source") or cfg.SRC_NONE
        pct = cfg.CONFIDENCE_PCT.get(source, 0)
        weight = cfg.CONF_RECENCY_DECAY ** steps_back

        weighted_sum += pct * weight
        weight_total += weight
        breakdown.append(
            {
                "분기": quarter.get("period_label", "-"),
                "출처": source,
                "신뢰도(%)": pct,
                "가중치": round(weight, 3),
            }
        )

    actual_conf = (weighted_sum / weight_total) if weight_total > 0 else 0.0
    breakdown.reverse()   # 화면에는 오래된 분기부터 보여줍니다

    # --- 전망 신뢰도 ---
    forward_basis = forward.get("basis") or cfg.SRC_NONE
    forward_conf = float(cfg.CONFIDENCE_PCT.get(forward_basis, 0))

    # --- 종합 ---
    total = actual_conf * cfg.CONF_WEIGHT_ACTUAL + forward_conf * cfg.CONF_WEIGHT_FORWARD

    # 출처별 분기 개수 (화면 요약용)
    counts: dict[str, int] = {}
    for quarter in quarters:
        source = quarter.get("source") or cfg.SRC_NONE
        counts[source] = counts.get(source, 0) + 1

    return {
        "total": round(total, 1),
        "actual": round(actual_conf, 1),
        "forward": round(forward_conf, 1),
        "forward_basis": forward_basis,
        "breakdown": breakdown,
        "counts": counts,
        "quarter_count": len(quarters),
    }


# ---------------------------------------------------------------------------
# 펀더멘털 ① 델타 가속 (40점)
# ---------------------------------------------------------------------------
def score_delta_acceleration(quarters: list[dict]) -> dict:
    """이익 증가 "속도"가 빨라지는지(가속) 느려지는지(감속) 봅니다.

    최근 3개 분기의 QoQ 증가율을 비교합니다.
      가속(증가율이 커지는 중) → 만점권
      유지                     → 중간
      감속(증가율이 작아짐)    → 감점

    ⚠️ 저기저(低基底) 함정 방지:
      최근 분기 논갭 영업이익이 $50M 미만이면 이 항목은 절반(20점)까지만 인정합니다.
      기저가 작으면 증가율이 쉽게 커 보여 착시가 생기기 때문입니다.
    """
    max_score = cfg.W_DELTA_ACCEL
    growths = _qoq_growth_series(quarters)

    if len(growths) < 2:
        return {
            "score": max_score * 0.4,   # 판단할 데이터가 부족하면 중간보다 약간 아래
            "direction": cfg.D_UNKNOWN,
            "detail": (
                "가속/감속을 판단하려면 QoQ 증가율이 최소 2개 필요한데 "
                f"{len(growths)}개만 계산됐습니다. (분기 데이터 부족 또는 직전 분기 적자)"
            ),
            "capped": False,
            "trace": {"growths": growths, "insufficient": True},
        }

    recent = growths[-3:]            # 최근 최대 3개 분기의 증가율
    delta = recent[-1] - recent[-2]  # 증가율이 얼마나 더 빨라졌는가(%p)

    # --- 신호 ① 단기: 직전 분기와의 비교 (민감함) ---
    if delta > cfg.DELTA_THRESHOLD_PP:
        short_signal = 1
    elif delta < -cfg.DELTA_THRESHOLD_PP:
        short_signal = -1
    else:
        short_signal = 0

    # --- 신호 ② 추세: 최근 여러 분기 증가율의 회귀 기울기 (안정적) ---
    window = growths[-cfg.DELTA_TREND_WINDOW :]
    slope = _linear_slope(window)
    if slope is None:
        trend_signal = None
    elif slope > cfg.DELTA_TREND_THRESHOLD:
        trend_signal = 1
    elif slope < -cfg.DELTA_TREND_THRESHOLD:
        trend_signal = -1
    else:
        trend_signal = 0

    # --- 두 신호를 합쳐 최종 방향 결정 ---
    if trend_signal is None:
        # 추세를 낼 만큼 자료가 없으면 단기 신호만 사용합니다
        direction = {1: cfg.D_ACCEL, -1: cfg.D_DECEL, 0: cfg.D_STEADY}[short_signal]
        detail = (
            f"이익 증가율이 직전 분기 대비 {delta:+.1f}%p 변했습니다"
            f" (분기가 적어 추세는 아직 계산하지 못했습니다)"
        )
    elif short_signal == trend_signal:
        # 두 신호가 같은 방향 → 확신 있는 판정
        direction = {1: cfg.D_ACCEL, -1: cfg.D_DECEL, 0: cfg.D_STEADY}[short_signal]
        detail = (
            f"단기({delta:+.1f}%p)와 추세(기울기 {slope:+.1f})가 같은 방향을 가리킵니다"
        )
    elif short_signal == 0 or trend_signal == 0:
        # 한쪽만 방향이 있음 → 방향이 있는 쪽을 따르되 확신은 낮춤
        active = short_signal if short_signal != 0 else trend_signal
        direction = {1: cfg.D_ACCEL, -1: cfg.D_DECEL}[active]
        detail = (
            f"한 신호만 방향을 보입니다 (단기 {delta:+.1f}%p, 추세 기울기 {slope:+.1f})"
        )
    else:
        # 두 신호가 정반대 → 혼조 (억지로 한쪽으로 밀지 않습니다)
        direction = cfg.D_MIXED
        detail = (
            f"단기({delta:+.1f}%p)와 추세(기울기 {slope:+.1f})가 서로 반대라 "
            "방향을 단정하기 어렵습니다"
        )

    ratio = cfg.DELTA_RATIO[direction]

    # 화면의 "계산 과정 보기"에 쓸 자료
    trace = {
        "growths": growths,                 # 분기별 QoQ 증가율 전체 이력
        "recent": recent,                   # 판정에 실제로 쓴 최근 값들
        "delta_pp": delta,                  # 증가율의 변화(%p) — 단기 신호 근거
        "slope": slope,                     # 회귀 기울기 — 추세 신호 근거
        "window": window,                   # 기울기 계산에 쓴 값들
        "short_signal": short_signal,
        "trend_signal": trend_signal,
        "ratio": ratio,
        "threshold": cfg.DELTA_THRESHOLD_PP,
        "trend_threshold": cfg.DELTA_TREND_THRESHOLD,
    }

    # 최근 증가율 자체가 마이너스(이익 감소)면 추가 감점
    if recent[-1] < 0:
        ratio *= 0.5
        trace["ratio"] = ratio
        trace["shrink_penalty"] = True
        detail += " · 다만 최근 분기 이익은 전분기보다 줄었습니다"

    score = max_score * ratio

    # 저기저 함정 방지 상한
    capped = False
    latest_op = next(
        (q["op_income"] for q in reversed(quarters) if q.get("op_income") is not None), None
    )
    if latest_op is not None and latest_op < cfg.LOW_BASE_THRESHOLD_USD:
        limit = max_score * cfg.LOW_BASE_CAP_RATIO
        if score > limit:
            score = limit
            capped = True
            detail += (
                f" · 이익 규모가 작아(${latest_op/1e6:,.0f}M) 이 항목 점수를 "
                f"{int(cfg.LOW_BASE_CAP_RATIO*100)}%로 제한했습니다"
            )
    trace["latest_op"] = latest_op
    trace["capped"] = capped

    return {
        "score": round(score, 1),
        "direction": direction,
        "detail": detail,
        "capped": capped,
        "trace": trace,
    }


# ---------------------------------------------------------------------------
# 펀더멘털 ② GM% 드라이버 (25점)
# ---------------------------------------------------------------------------
def score_gm_driver(quarters: list[dict]) -> dict:
    """마진 개선이 "어떤 이유"로 생겼는지 성격을 구분합니다.

    최근 분기 GM% 에서 직전 4개 분기 평균 GM% 를 뺀 값(%p)으로 판단합니다.
      ±1%p 이내   → 물량형 (제품 잘 팔려서 규모가 커진 것 · 가장 건강) 25점
      +1 ~ +3%p   → 믹스형 (비싼 제품 비중이 늘어난 것)                15점
      +3%p 초과   → 가격형 (일시적 가격 인상 가능성 · 지속성 낮음)      5점
      −1%p 초과 하락 → 마진 악화                                        8점
    """
    max_score = cfg.W_GM_DRIVER
    margins = [
        q["gross_margin_pct"] for q in quarters if q.get("gross_margin_pct") is not None
    ]

    if len(margins) < 2:
        return {
            "score": max_score * 0.4,
            "type": "판단불가",
            "delta_pp": None,
            "latest_gm": margins[-1] if margins else None,
            "detail": (
                "마진 성격을 판단하려면 GM% 데이터가 최소 2개 분기 필요한데 "
                f"{len(margins)}개만 수집됐습니다"
            ),
            "trace": {"margins": margins, "insufficient": True},
        }

    latest = margins[-1]
    baseline_values = margins[-5:-1] if len(margins) >= 5 else margins[:-1]
    baseline = sum(baseline_values) / len(baseline_values)
    delta = latest - baseline

    if delta < -cfg.GM_VOLUME_BAND:
        gm_type, score = "마진 악화", cfg.GM_SCORE_DECLINE
        detail = f"마진이 과거 평균보다 {delta:.1f}%p 떨어졌습니다"
    elif abs(delta) <= cfg.GM_VOLUME_BAND:
        gm_type, score = "물량형", cfg.GM_SCORE_VOLUME
        detail = f"마진이 거의 그대로({delta:+.1f}%p)이면서 규모가 커지는 가장 건강한 형태입니다"
    elif delta <= cfg.GM_MIX_BAND:
        gm_type, score = "믹스형", cfg.GM_SCORE_MIX
        detail = f"마진이 {delta:+.1f}%p 올랐습니다 — 수익성 좋은 제품 비중이 늘어난 것으로 보입니다"
    else:
        gm_type, score = "가격형", cfg.GM_SCORE_PRICE
        detail = f"마진이 {delta:+.1f}%p 급등했습니다 — 일시적 가격 효과일 수 있어 지속성 확인이 필요합니다"

    return {
        "score": round(float(score), 1),
        "type": gm_type,
        "delta_pp": round(delta, 2),
        "latest_gm": round(latest, 2),
        "detail": detail,
        "trace": {
            "margins": margins,                  # GM% 전체 이력
            "latest": latest,                    # 최근 분기 GM%
            "baseline": baseline,                # 직전 4개 분기 평균
            "baseline_values": baseline_values,  # 평균 계산에 쓴 값들
            "delta_pp": delta,
        },
    }


# ---------------------------------------------------------------------------
# 펀더멘털 ③ 매출 성장의 질 (20점)
# ---------------------------------------------------------------------------
def score_revenue_quality(quarters: list[dict]) -> dict:
    """매출이 얼마나 크게 늘었는지(YoY) + 그 속도가 빨라지는지 봅니다."""
    max_score = cfg.W_REVENUE_QUALITY
    revenues = [q["revenue"] for q in quarters if q.get("revenue") is not None]

    if len(revenues) < 2:
        return {
            "score": max_score * 0.4,
            "yoy": None,
            "detail": "매출 데이터가 부족합니다",
        }

    # YoY(1년 전 같은 분기 대비). 4개 분기 전 데이터가 없으면 가장 오래된 값과 비교
    base_index = -5 if len(revenues) >= 5 else 0
    base = revenues[base_index]
    yoy = (revenues[-1] / base - 1.0) * 100.0 if base > 0 else None

    # 성장률 크기 점수 (0~70% 구간을 0~14점으로 환산, 최대 14점)
    growth_score = 0.0
    if yoy is not None:
        growth_score = _clamp(yoy / 70.0, 0.0, 1.0) * (max_score * 0.7)

    # 가속 여부 점수 (최대 6점)
    accel_score = 0.0
    accel_text = ""
    qoq = []
    for prev, curr in zip(revenues, revenues[1:]):
        if prev > 0:
            qoq.append((curr / prev - 1.0) * 100.0)
    if len(qoq) >= 2:
        if qoq[-1] > qoq[-2]:
            accel_score = max_score * 0.3
            accel_text = " · 매출 증가 속도도 빨라지는 중입니다"
        elif qoq[-1] > 0:
            accel_score = max_score * 0.15
            accel_text = " · 매출은 계속 늘고 있습니다"

    yoy_text = f"{yoy:+.1f}%" if yoy is not None else "-"
    return {
        "score": round(growth_score + accel_score, 1),
        "yoy": round(yoy, 1) if yoy is not None else None,
        "detail": f"1년 전 대비 매출 {yoy_text}{accel_text}",
        "trace": {
            "revenues": revenues,
            "base": base,
            "latest": revenues[-1],
            "yoy": yoy,
            "qoq": qoq,
            "growth_score": growth_score,
            "accel_score": accel_score,
        },
    }


# ---------------------------------------------------------------------------
# 펀더멘털 ④ 포워드 신호 (15점)
# ---------------------------------------------------------------------------
def score_forward(quarters: list[dict], forward: dict) -> dict:
    """다음 분기 전망이 이번 분기보다 좋은지 + 애널리스트가 전망을 올렸는지 봅니다."""
    max_score = cfg.W_FORWARD
    latest_op = next(
        (q["op_income"] for q in reversed(quarters) if q.get("op_income") is not None), None
    )
    forward_op = forward.get("forward_op_income")
    revision = forward.get("revision", 0)

    # 전망 자체 점수 (최대 10점)
    if forward_op is None or latest_op is None:
        forward_score = max_score * 0.4
        growth_text = "다음 분기 전망치를 구하지 못했습니다"
        growth_pct = None
    elif latest_op <= 0:
        # 적자 기저에서는 증가율(%)이 의미가 없으므로 전환/개선 여부로 평가합니다
        growth_pct = None
        if forward_op > 0:
            forward_score = max_score * 0.67 * 0.9
            growth_text = f"적자에서 흑자 전환 전망({ '$%.0fM' % (forward_op/1e6) })"
        elif forward_op > latest_op:
            forward_score = max_score * 0.67 * 0.5
            growth_text = "적자 축소 전망 (기저가 적자라 증가율 대신 개선 여부로 평가)"
        else:
            forward_score = max_score * 0.67 * 0.1
            growth_text = "적자 확대 전망"
    else:
        growth_pct = (forward_op / latest_op - 1.0) * 100.0
        # −10% ~ +30% 구간을 0~10점으로 환산
        ratio = _clamp((growth_pct + 10.0) / 40.0, 0.0, 1.0)
        forward_score = ratio * (max_score * 0.67)
        growth_text = f"다음 분기 영업이익 전망이 이번 분기 대비 {growth_pct:+.1f}%"

    # 리비전 점수 (최대 5점)
    # 추정치가 30일간 몇 % 움직였는지(속도)가 있으면 그것으로 정밀하게,
    # 없으면 상향/하향 방향만으로 계산합니다.
    import math

    velocity = forward.get("revision_velocity_pct")
    if velocity is not None and math.isfinite(velocity):
        full = cfg.REVISION_VELOCITY_FULL_PCT   # ±5%에서 최저/최고점
        ratio = _clamp((velocity + full) / (2 * full), 0.0, 1.0)
        revision_score = ratio * (max_score * 0.33)
        revision_text = f"애널리스트 추정치 30일간 {velocity:+.1f}% 변화"
    else:
        revision_score = {1: max_score * 0.33, 0: max_score * 0.17, -1: 0.0}[revision]
        revision_text = {1: "애널리스트 전망 상향 ↗", 0: "전망 변화 없음 →", -1: "전망 하향 ↘"}[revision]

    return {
        "score": round(forward_score + revision_score, 1),
        "growth_pct": round(growth_pct, 1) if growth_pct is not None else None,
        "revision": revision,
        "detail": f"{growth_text} · {revision_text}",
        "trace": {
            "latest_op": latest_op,
            "forward_op": forward_op,
            "growth_pct": growth_pct,
            "forward_score": forward_score,
            "revision_score": revision_score,
            "revision_text": revision_text,
            "basis": forward.get("basis"),
            "basis_detail": forward.get("detail", ""),
        },
    }


# ---------------------------------------------------------------------------
# 기술 점수 (100점)
# ---------------------------------------------------------------------------
def predict_delta(quarters: list[dict], forward: dict, delta_result: dict) -> dict:
    """전망치로 "앞으로 가속이 이어질지"를 미리 봅니다.

    두 단계로 봅니다:
      · 델타예측 (다음 분기): 다음 분기 예상 QoQ vs 최근 실제 QoQ
      · 델타가속예측 (2분기 경로): 가이던스(다음 분기) + 월가 컨센서스(다다음 분기)로
        증가율이 두 분기에 걸쳐 어떤 길을 갈지 판정
    """
    growths = delta_result.get("trace", {}).get("growths", [])
    latest_op = next(
        (q["op_income"] for q in reversed(quarters) if q.get("op_income") is not None), None
    )
    forward_op = forward.get("forward_op_income")
    forward_op_2 = forward.get("forward_op_income_2")

    empty = {
        "label": cfg.F_NONE,
        "accel_label": cfg.F2_NONE,
        "next_qoq": None,
        "next2_qoq": None,
        "change_pp": None,
        "basis": forward.get("basis"),
        "basis_2": forward.get("basis_2"),
        "confidence": cfg.CONFIDENCE_PCT.get(forward.get("basis"), 0),
        "detail": "다음 분기 전망치가 없어 예측할 수 없습니다",
        "accel_detail": "",
    }
    if forward_op is None or latest_op is None or latest_op <= 0 or not growths:
        return empty

    threshold = cfg.DELTA_THRESHOLD_PP
    last_qoq = growths[-1]

    # --- 델타예측 (다음 분기 하나) ---
    next_qoq = (forward_op / latest_op - 1.0) * 100.0
    change = next_qoq - last_qoq
    was_rising = last_qoq >= 0

    if change > threshold:
        label = cfg.F_ACCEL_KEEP if was_rising else cfg.F_REBOUND
    elif change < -threshold:
        label = cfg.F_ACCEL_SLOW if was_rising else cfg.F_DECEL_KEEP
    else:
        label = cfg.F_FLAT

    # --- 델타가속예측 (2분기 경로) ---
    next2_qoq = None
    if forward_op_2 is not None and forward_op > 0:
        next2_qoq = (forward_op_2 / forward_op - 1.0) * 100.0

    if next2_qoq is None:
        # 다다음 분기 자료가 없으면 다음 분기 예측을 그대로 씁니다
        accel_label = label if label != cfg.F_NONE else cfg.F2_NONE
        accel_detail = "다다음 분기 컨센서스가 없어 다음 분기까지만 예측했습니다"
    else:
        # 1단계 변화(실제→다음)와 2단계 변화(다음→다다음)의 방향을 조합합니다.
        # 단분기 판정과 마찬가지로 "지금 증가 중인가(was_rising)"를 함께 봅니다:
        # 감익 중인 종목이 회복되는 경로는 '가속'이 아니라 '반등'이기 때문입니다.
        step1 = 1 if change > threshold else (-1 if change < -threshold else 0)
        change2 = next2_qoq - next_qoq
        step2 = 1 if change2 > threshold else (-1 if change2 < -threshold else 0)

        if not was_rising and step1 > 0:
            # 감익(-QoQ)에서 좋아지는 경로 = 반등 (정점 신호인 '가속 후 둔화'와 구분)
            accel_label = cfg.F2_REBOUND
        elif step1 > 0 and step2 >= 0:
            accel_label = cfg.F2_ACCEL_KEEP
        elif step1 > 0 and step2 < 0:
            accel_label = cfg.F2_ACCEL_SLOW
        elif step1 < 0 and step2 <= 0:
            accel_label = cfg.F2_DECEL_KEEP
        elif step1 < 0 and step2 > 0:
            accel_label = cfg.F2_REBOUND
        elif step1 == 0 and step2 > 0:
            accel_label = cfg.F2_ACCEL_KEEP      # 유지하다가 후반 가속
        elif step1 == 0 and step2 < 0:
            accel_label = cfg.F2_DECEL_KEEP      # 유지하다가 후반 둔화
        else:
            accel_label = cfg.F2_FLAT
        accel_detail = (
            f"증가율 경로: 실제 {last_qoq:+.1f}% → 다음 {next_qoq:+.1f}% → "
            f"다다음 {next2_qoq:+.1f}%"
        )

    return {
        "label": label,
        "accel_label": accel_label,
        "next_qoq": round(next_qoq, 1),
        "next2_qoq": round(next2_qoq, 1) if next2_qoq is not None else None,
        "last_qoq": round(last_qoq, 1),
        "change_pp": round(change, 1),
        "basis": forward.get("basis"),
        "basis_2": forward.get("basis_2"),
        "confidence": cfg.CONFIDENCE_PCT.get(forward.get("basis"), 0),
        "detail": (
            f"최근 실제 증가율 {last_qoq:+.1f}% → 다음 분기 예상 {next_qoq:+.1f}% "
            f"({change:+.1f}%p)"
        ),
        "accel_detail": accel_detail,
    }


def score_technical(price_info: dict) -> dict:
    """주가 추세·기울기·이격도·상대강도를 합쳐 기술 점수를 냅니다."""
    state = price_info.get("state", cfg.S_UNKNOWN)
    trend_score = cfg.TREND_SCORES.get(state, 0.0)
    slope_score = cfg.SLOPE_SCORES.get(price_info.get("slope", "-"), 0.0)

    # 이격도 점수
    disparity = price_info.get("disparity")
    if disparity is None:
        disparity_score = 0.0
        disparity_text = "이격도 계산 불가"
    elif disparity < 0:
        disparity_score = cfg.DISPARITY_SCORE_NEGATIVE
        disparity_text = f"주가가 26주선 아래({disparity:+.1f}%)"
    elif disparity <= cfg.DISPARITY_OVERHEAT_PCT:
        disparity_score = cfg.DISPARITY_SCORE_NORMAL
        disparity_text = f"26주선 위 정상 범위({disparity:+.1f}%)"
    else:
        disparity_score = cfg.DISPARITY_SCORE_OVERHEAT
        disparity_text = f"26주선보다 {disparity:+.1f}% 높아 단기 과열 구간"

    # 상대강도(RS) 점수
    rs = price_info.get("rs")
    if rs is None:
        rs_score = 0.0
        rs_text = "상대강도 계산 불가"
    else:
        rs_score = next(score for threshold, score in cfg.RS_SCORE_BANDS if rs > threshold)
        if rs > 0:
            rs_text = f"최근 13주간 시장(SPY)보다 {rs:+.1f}%p 더 올랐습니다"
        else:
            rs_text = f"최근 13주간 시장(SPY)보다 {rs:+.1f}%p 뒤처졌습니다"

    total = trend_score + slope_score + disparity_score + rs_score
    return {
        "total": round(total, 1),
        "trend_score": trend_score,
        "slope_score": slope_score,
        "disparity_score": disparity_score,
        "rs_score": rs_score,
        "disparity_text": disparity_text,
        "rs_text": rs_text,
    }


# ---------------------------------------------------------------------------
# 최종 종합
# ---------------------------------------------------------------------------
def score_fundamental(quarters: list[dict], forward: dict) -> dict:
    """펀더멘털 4개 항목을 계산해 100점 만점으로 합칩니다."""
    delta = score_delta_acceleration(quarters)
    gm = score_gm_driver(quarters)
    revenue = score_revenue_quality(quarters)
    fwd = score_forward(quarters, forward)

    total = delta["score"] + gm["score"] + revenue["score"] + fwd["score"]
    return {
        "total": round(total, 1),
        "delta": delta,
        "gm": gm,
        "revenue": revenue,
        "forward": fwd,
    }


def decide_verdict(fund_total: float, trend_stage: int) -> str:
    """펀더멘털 점수와 추세 단계를 조합해 최종 판정을 내립니다.

    추세 단계: 1=완전 정배열, 2=준정배열, 3=중립, 4=추세 훼손, 5=완전 역배열
    """
    return explain_verdict(fund_total, trend_stage)["verdict"]


def explain_verdict(fund_total: float, trend_stage: int) -> dict:
    """판정 결과와 함께 "왜 그 판정이 나왔는지"를 돌려줍니다.

    화면에서 판정을 눌렀을 때 보여줄 근거로 사용합니다.
    """
    strong_trend = trend_stage in (1, 2)
    weak_trend = trend_stage in (4, 5)

    fund_text = (
        f"펀더멘털 {fund_total:.0f}점"
        + (
            f" (기준 {cfg.FUND_STRONG:.0f}점 이상 → 강함)"
            if fund_total >= cfg.FUND_STRONG
            else f" (기준 {cfg.FUND_WEAK:.0f}점 이상 → 보통)"
            if fund_total >= cfg.FUND_WEAK
            else f" (기준 {cfg.FUND_WEAK:.0f}점 미만 → 약함)"
        )
    )
    stage_text = (
        f"추세 {trend_stage}단계"
        + (" (1~2단계 → 양호)" if strong_trend else " (4~5단계 → 꺾임)" if weak_trend else " (3단계 → 중립)")
    )

    if fund_total >= cfg.FUND_STRONG and strong_trend:
        verdict = cfg.V_BUY
        rule = f"펀더 {cfg.FUND_STRONG:.0f}점 이상 **그리고** 추세 1~2단계"
        meaning = "실적도 좋아지고 주가 흐름도 좋습니다. 매수 후보끼리는 상대강도(RS)가 높은 순으로 정렬됩니다."
    elif fund_total >= cfg.FUND_STRONG and weak_trend:
        verdict = cfg.V_WARN
        rule = f"펀더 {cfg.FUND_STRONG:.0f}점 이상 **그러나** 추세 4~5단계"
        meaning = (
            "실적은 좋은데 주가가 먼저 꺾인 상태입니다. "
            "시장이 이미 정점을 반영했을 가능성이 있어 주의가 필요합니다."
        )
    elif cfg.FUND_WEAK <= fund_total < cfg.FUND_STRONG and strong_trend:
        verdict = cfg.V_WATCH
        rule = f"펀더 {cfg.FUND_WEAK:.0f}~{cfg.FUND_STRONG:.0f}점 **그리고** 추세 1~2단계"
        meaning = "주가 흐름은 좋지만 실적 근거가 아직 확실하지 않습니다. 다음 실적발표를 지켜볼 구간입니다."
    elif fund_total < cfg.FUND_WEAK and strong_trend:
        verdict = cfg.V_MOMENTUM
        rule = f"펀더 {cfg.FUND_WEAK:.0f}점 미만 **그러나** 추세 1~2단계"
        meaning = (
            "실적이 뒷받침되지 않는데 주가만 오르고 있습니다. "
            "기대감이나 수급으로 움직이는 구간일 수 있어 변동성이 클 수 있습니다."
        )
    else:
        verdict = cfg.V_EXCLUDE
        rule = "위 네 가지 조건 어디에도 해당하지 않음"
        meaning = "실적과 주가 조합이 뚜렷한 특징을 보이지 않아 관심 목록에서 제외됩니다."

    return {
        "verdict": verdict,
        "rule": rule,
        "meaning": meaning,
        "fund_text": fund_text,
        "stage_text": stage_text,
        "fund_total": fund_total,
        "trend_stage": trend_stage,
    }


def build_score(ticker: str, quarters: list[dict], forward: dict, price_info: dict) -> dict:
    """한 종목의 모든 점수를 계산해 화면에서 쓸 형태로 정리합니다."""
    fundamental = score_fundamental(quarters, forward)
    technical = score_technical(price_info)
    confidence = compute_confidence(quarters, forward)
    forecast = predict_delta(quarters, forward, fundamental["delta"])

    final = fundamental["total"] * cfg.WEIGHT_FUNDAMENTAL + technical["total"] * cfg.WEIGHT_TECHNICAL
    stage = price_info.get("stage", cfg.TREND_STAGE[cfg.S_UNKNOWN])
    verdict_info = explain_verdict(fundamental["total"], stage)
    verdict = verdict_info["verdict"]

    # 데이터 출처 배지 (가장 최근 분기 기준)
    latest_quarter = quarters[-1] if quarters else None
    data_source = latest_quarter["source"] if latest_quarter else None

    return {
        "ticker": ticker,
        "final_score": round(final, 1),
        "fund_score": fundamental["total"],
        "tech_score": technical["total"],
        "verdict": verdict,
        "trend_state": price_info.get("state", cfg.S_UNKNOWN),
        "trend_stage": stage,
        "slope": price_info.get("slope", "-"),
        "disparity": price_info.get("disparity"),
        "rs": price_info.get("rs"),
        "close": price_info.get("close"),
        "week_change": price_info.get("week_change"),
        "gm_type": fundamental["gm"]["type"],
        "gm_delta_pp": fundamental["gm"]["delta_pp"],
        "delta_direction": fundamental["delta"]["direction"],
        "revenue_yoy": fundamental["revenue"]["yoy"],
        "forward_basis": forward.get("basis"),
        "forward_op_income": forward.get("forward_op_income"),
        "revision": forward.get("revision", 0),
        "data_source": data_source,
        "confidence": confidence["total"],
        "confidence_detail": confidence,
        "delta_forecast": forecast["label"],
        "accel_forecast": forecast["accel_label"],
        "forward_op_income_2": forward.get("forward_op_income_2"),
        "forward_consensus": forward.get("consensus", {}),
        "forecast_detail": forecast,
        "source_derivation": (latest_quarter or {}).get("derivation", ""),
        "filing_url": (latest_quarter or {}).get("filing_url", ""),
        "quarters": quarters,
        "fundamental": fundamental,
        "technical": technical,
        "forward_detail": forward.get("detail", ""),
        "verdict_info": verdict_info,
    }
