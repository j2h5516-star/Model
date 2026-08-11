"""
status.py — 종목 상태 판정 핵심 (v1 scoring.py 에서 검증된 부분만 이식)
======================================================================

v2(전략.md)는 점수를 만들지 않습니다. 이 파일은 **사실 판정**만 남긴
이식 장기입니다:

  · TTM(4분기 합) 이력과 증가율          — _ttm_series / _ttm_growth_series
  · 델타 방향 판정 (가속/유지/감속/혼조)  — judge_delta
  · TTM 신고점 여부                       — is_ttm_new_high

v1에서 백테스트로 고른 규칙(TTM+기울기, 132시점에서 방향 적중 55→66%)과
실사고 수리(끊긴 구간 이어붙이기 금지, 스파이크 제외, 계절 YoY 는
회계분기 이름으로 짝짓기)를 그대로 보존합니다.

⚠️ 델타 방향은 **이익의 방향** 설명입니다. 주가 신호가 아닙니다 —
   "가속 확인 후 매수"는 3회 측정 모두 기준선 이하였습니다 (측정결과.md).
"""

from __future__ import annotations

import config as cfg
import data_quality as dq


# ---------------------------------------------------------------------------
# 증가율 시계열
# ---------------------------------------------------------------------------
def _growth_segments(quarters: list[dict]) -> list[list[float]]:
    """증가율을 끊기지 않고 이어지는 구간별로 나눠 돌려줍니다.

    적자 분기나 단위가 깨진 분기를 만나면 거기서 구간을 끊습니다.
    끊긴 자리를 이어붙이면 상관없는 시기의 증가율이 이웃이 되어
    없는 가속이 생깁니다 (v1 실사고).
    """
    usable = [
        q for q in quarters
        if q.get("op_income") is not None and q.get("anomaly") != "spike"
    ]
    segments: list[list[float]] = []
    current: list[float] = []
    for previous, current_q in zip(usable, usable[1:]):
        growth = dq.safe_growth_pct(previous.get("op_income"), current_q.get("op_income"))
        if current_q.get("anomaly") == "step":
            growth = None      # 계단(인수합병)이 만든 그 한 번의 증가율만 뺍니다
        if growth is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(growth)
    if current:
        segments.append(current)
    return segments


def qoq_growth_series(quarters: list[dict]) -> list[float]:
    """전분기 대비 증가율(%) — 마지막 연속 구간만."""
    segments = _growth_segments(quarters)
    return segments[-1] if segments else []


def yoy_growth_series(quarters: list[dict]) -> list[float]:
    """작년 같은 분기 대비 증가율(%) — 회계분기 이름으로 짝을 맞춥니다.

    "인덱스로 4칸 뒤"는 분기가 하나만 빠져도 어긋납니다 (v1 실사고:
    ZETA 에서 +25% 가 +108% 로 둔갑).
    """
    usable = [q for q in quarters if q.get("op_income") is not None]
    by_quarter: dict[tuple[int, int], float] = {}
    for quarter in usable:
        key = dq.fiscal_quarter_of(quarter)
        if key is not None:
            by_quarter[key] = quarter["op_income"]

    out: list[float] = []
    for quarter in usable:
        key = dq.fiscal_quarter_of(quarter)
        if key is None:
            continue
        last_year = by_quarter.get((key[0] - 1, key[1]))
        growth = dq.safe_growth_pct(last_year, quarter["op_income"])
        if growth is not None:
            out.append(growth)
    return out


def ttm_series(quarters: list[dict]) -> list[float]:
    """최근 4분기 합(TTM)의 이력. 계절성이 저절로 상쇄됩니다.

    적자 분기가 섞여도 합계는 계산됩니다 — 분기 증가율은 직전이 적자면
    계산 불능이라 바닥 반등 국면이 통째로 안 보이기 때문입니다.
    """
    values = [
        q["op_income"] for q in quarters
        if q.get("op_income") is not None and q.get("anomaly") != "spike"
    ]
    if len(values) < 4:
        return []
    return [sum(values[i - 3 : i + 1]) for i in range(3, len(values))]


def ttm_growth_series(quarters: list[dict]) -> list[float]:
    """TTM 증가율(%) — 구멍을 만나면 앞을 버리고 마지막 연속 구간만.

    구멍(직전 1년치 적자)을 건너뛰고 이어붙이면 몇 년 떨어진 증가율이
    이웃이 되어, 한 푼도 안 늘었는데 '가속'이 나옵니다 (v1 실사고).
    """
    ttm = ttm_series(quarters)
    segment: list[float] = []
    for before, after in zip(ttm, ttm[1:]):
        growth = dq.safe_growth_pct(before, after)
        if growth is None:
            segment = []
        else:
            segment.append(growth)
    return segment


def is_ttm_new_high(quarters: list[dict]) -> bool | None:
    """이번 TTM 이 수집 기간 안의 최고를 넘었는가. 판단 불능이면 None.

    '역대 최고'가 아니라 **수집 기간(약 5년) 안의 최고**입니다 —
    옛날에 더 잘 벌던 회사(인텔)도 여기 들어올 수 있습니다.
    """
    ttm = ttm_series(quarters)
    if len(ttm) < 2:
        return None
    return ttm[-1] > max(ttm[:-1])


def _linear_slope(values: list[float]) -> float | None:
    """최소제곱 회귀 기울기 — 한 값이 튀어도 판정이 덜 뒤집히게."""
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
# 델타 방향 판정 (v1 score_delta_acceleration 의 방향 부분 — 점수는 제거)
# ---------------------------------------------------------------------------
def judge_delta(quarters: list[dict]) -> dict:
    """이익 증가 '속도'가 빨라지는지 판정합니다. 점수는 만들지 않습니다.

    반환: {"direction", "detail", "trace"}
      · 1순위 자: TTM 증가율의 변화 (분기 6개 미만이면 계절=YoY, 아니면 QoQ)
      · 신호 둘(단기 변화 + 회귀 기울기)이 일치해야 강한 판정,
        하나만 방향이면 약한 판정, 반대면 혼조 — 근거가 절반이면 주장도 절반.
    """
    season = quarters[0].get("seasonal") if quarters else None
    if season is None:
        season = dq.detect_seasonality(quarters)["seasonal"]

    ttm_growths = ttm_growth_series(quarters)
    use_ttm = len(ttm_growths) >= 2

    if use_ttm:
        growths = ttm_growths
        growth_mode = "TTM"
        basis_text = "최근 4분기 합(TTM) 증가율의 변화"
    elif season:
        growths = yoy_growth_series(quarters)
        growth_mode = "YoY"
        basis_text = "작년 같은 분기 대비(분기가 모자라 TTM 대신)"
    else:
        growths = qoq_growth_series(quarters)
        growth_mode = "QoQ"
        basis_text = "전분기 대비(분기가 모자라 TTM 대신)"

    base_trace = {
        "growths": growths, "use_ttm": use_ttm,
        "growth_mode": growth_mode, "basis": basis_text, "seasonal": bool(season),
    }

    if len(growths) < 2:
        return {
            "direction": cfg.D_UNKNOWN,
            "detail": (
                "가속/감속을 판단하려면 증가율이 최소 2개 필요한데 "
                f"{len(growths)}개만 계산됐습니다 (분기 부족 또는 직전 기간 적자)."
            ),
            "trace": {**base_trace, "insufficient": True},
        }

    # 최근 분기가 '아직 스파이크인지 계단인지 모르는' 급등이면 판정을 미룹니다
    if any(q.get("anomaly") == "pending" for q in quarters[-2:]):
        return {
            "direction": cfg.D_UNKNOWN,
            "detail": (
                "가장 최근 분기 이익이 크게 뛰었는데 다음 분기가 아직 없어 "
                "일시적인지 새 수준인지 알 수 없습니다 — 판정을 미룹니다."
            ),
            "trace": {**base_trace, "anomaly": True},
        }

    recent = growths[-3:]
    delta = recent[-1] - recent[-2]

    if delta > cfg.DELTA_THRESHOLD_PP:
        short_signal = 1
    elif delta < -cfg.DELTA_THRESHOLD_PP:
        short_signal = -1
    else:
        short_signal = 0

    window = growths[-cfg.DELTA_TREND_WINDOW:]
    slope = _linear_slope(window)
    if slope is None:
        trend_signal = None
    elif slope > cfg.DELTA_TREND_THRESHOLD:
        trend_signal = 1
    elif slope < -cfg.DELTA_TREND_THRESHOLD:
        trend_signal = -1
    else:
        trend_signal = 0

    if trend_signal is None:
        direction = {1: cfg.D_ACCEL, -1: cfg.D_DECEL, 0: cfg.D_STEADY}[short_signal]
        detail = (
            f"이익 증가율이 직전 대비 {delta:+.1f}%p 변했습니다 "
            "(분기가 적어 추세는 아직 계산하지 못했습니다)"
        )
    elif short_signal == trend_signal:
        direction = {1: cfg.D_ACCEL, -1: cfg.D_DECEL, 0: cfg.D_STEADY}[short_signal]
        detail = (
            f"최근 변화({delta:+.1f}%p)와 추세(기울기 {slope:+.1f})가 같은 방향입니다"
        )
    elif short_signal == 0 or trend_signal == 0:
        active = short_signal if short_signal != 0 else trend_signal
        direction = {1: cfg.D_WEAK_ACCEL, -1: cfg.D_WEAK_DECEL}[active]
        detail = (
            f"두 신호 중 하나만 방향을 보입니다 (단기 {delta:+.1f}%p, "
            f"기울기 {slope:+.1f}) — 약한 신호로만 봅니다"
        )
    else:
        direction = cfg.D_MIXED
        detail = (
            f"단기({delta:+.1f}%p)와 추세(기울기 {slope:+.1f})가 반대라 "
            "방향을 단정하기 어렵습니다"
        )

    return {
        "direction": direction,
        "detail": detail,
        "trace": {
            **base_trace,
            "recent": recent, "delta_pp": delta, "slope": slope, "window": window,
            "short_signal": short_signal, "trend_signal": trend_signal,
            "threshold": cfg.DELTA_THRESHOLD_PP,
            "trend_threshold": cfg.DELTA_TREND_THRESHOLD,
            "measured_hit": cfg.DELTA_MEASURED_HIT.get(direction),
        },
    }
