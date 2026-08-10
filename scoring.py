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
import data_quality as dq


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------
def _qoq_growth_series(quarters: list[dict]) -> list[float]:
    """분기별 논갭 영업이익의 QoQ(전분기 대비) 증가율(%) 목록을 만듭니다.

    예) 영업이익이 100 → 120 → 150 이면 [20.0, 25.0]
    직전 분기가 0 이하(적자)면 증가율을 계산할 수 없어 건너뜁니다.
    """
    return _growth_segments(quarters)[-1] if _growth_segments(quarters) else []


def _growth_segments(quarters: list[dict]) -> list[list[float]]:
    """증가율을 **끊기지 않고 이어지는 구간별로** 나눠 돌려줍니다.

    적자 분기나 단위가 깨진 분기를 만나면 거기서 구간을 끊습니다.
    예전에는 끊긴 자리를 그냥 건너뛰고 이어붙여서, 서로 상관없는 시기의
    증가율이 한 줄에 섞인 채 추세(회귀 기울기)를 계산했습니다.
    """
    # 스파이크(데이터 오류로 보이는 분기)는 아예 빼고 계산합니다
    usable = [
        q for q in quarters
        if q.get("op_income") is not None and q.get("anomaly") != "spike"
    ]
    segments: list[list[float]] = []
    current: list[float] = []
    for previous, current_q in zip(usable, usable[1:]):
        growth = dq.safe_growth_pct(previous.get("op_income"), current_q.get("op_income"))

        # 계단(인수합병 등)이 생긴 그 한 번의 증가율만 뺍니다.
        # 분기 자체는 살립니다 — 사업이 커진 것은 진짜이기 때문입니다.
        if current_q.get("anomaly") == "step":
            growth = None

        if growth is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(growth)
    if current:
        segments.append(current)
    return segments


def _yoy_growth_series(quarters: list[dict]) -> list[float]:
    """작년 같은 분기 대비 증가율(%) 목록. 계절 장사를 판정할 때 씁니다.

    ⚠️ 짝은 **회계분기 이름으로** 맞춥니다. "인덱스로 4칸 뒤"는 분기가 하나만
       빠져도 완전히 어긋납니다. 실제 ZETA 예시로 확인한 결과:
         올바른 짝  : +20.0%  +20.0%  +25.0%  +25.0%
         인덱스 4칸 : +20.0%  +30.9%  +108.3%  -40.0%   ← 완전히 엉터리
       Q4 역산 실패로 분기가 비는 일은 흔해서, 실전에서 거의 확실히 터집니다.
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


def _ttm_series(quarters: list[dict]) -> list[float]:
    """최근 4분기 이익을 더한 값(TTM)의 이력을 만듭니다.

    4분기를 더하면 계절성이 저절로 상쇄됩니다 — 어느 시점에서 잘라도
    1년치 장사가 통째로 들어가기 때문입니다. 그래서 계절 장사 종목이라고
    따로 다른 잣대를 쓸 필요가 없습니다.

    적자 분기가 섞여 있어도 합계는 계산됩니다. 이것이 중요한 이유:
    분기 단위 증가율은 직전 분기가 적자면 아예 계산할 수 없어서,
    바닥에서 돌아서는 국면이 통째로 보이지 않게 됩니다.
    """
    values = [
        q["op_income"] for q in quarters
        if q.get("op_income") is not None and q.get("anomaly") != "spike"
    ]
    if len(values) < 4:
        return []
    return [sum(values[i - 3 : i + 1]) for i in range(3, len(values))]


def _ttm_growth_series(quarters: list[dict]) -> list[float]:
    """TTM 이익의 분기별 증가율(%) 목록."""
    ttm = _ttm_series(quarters)
    return [
        growth for growth in (
            dq.safe_growth_pct(before, after) for before, after in zip(ttm, ttm[1:])
        )
        if growth is not None
    ]


def _all_approximated(quarters: list[dict], window: int = 4) -> bool:
    """최근 1년치를 이루는 분기가 전부 '근사치'인지 봅니다.

    근사치는 회사 공식 발표가 아니라 XBRL 회계데이터로 만든 값이라,
    이것만으로 "적자다" 같은 단정을 하면 안 됩니다. 한 분기라도 회사가
    직접 밝힌 값(직접공시·역산)이 섞여 있으면 근거가 있다고 봅니다.
    """
    usable = [q for q in quarters if q.get("op_income") is not None]
    if not usable:
        return False
    return all(q.get("source") == cfg.SRC_APPROX for q in usable[-window:])


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

    # ★ 어떤 기준으로 볼지 **먼저 판단**합니다.
    #
    #   1순위: TTM(최근 4분기 합) 증가율의 변화
    #     20종목 132개 시점 백테스트에서 전분기 대비보다 확실히 나았습니다
    #     (방향 적중 55.0% → 65.5%, "가속"의 정확도 81.6% → 92.9%).
    #     4분기를 더하면 계절성이 저절로 상쇄되므로 계절 장사도 같은 잣대로 봅니다.
    #
    #   2순위(분기가 6개 미만일 때): 예전 방식 — 계절 장사면 작년 같은 분기 대비,
    #     아니면 전분기 대비. 덜 믿을 만하다는 것을 화면에도 밝힙니다.
    season = quarters[0].get("seasonal") if quarters else None
    if season is None:
        season = dq.detect_seasonality(quarters)["seasonal"]

    ttm_growths = _ttm_growth_series(quarters)
    use_ttm = len(ttm_growths) >= 2

    if use_ttm:
        growths = ttm_growths
        basis_text = "최근 4분기 합(TTM) 증가율의 변화"
    elif season:
        growths = _yoy_growth_series(quarters)
        basis_text = "작년 같은 분기 대비(분기가 모자라 TTM 대신)"
    else:
        growths = _qoq_growth_series(quarters)
        basis_text = "전분기 대비(분기가 모자라 TTM 대신)"

    if len(growths) < 2:
        return {
            "score": max_score * 0.4,   # 판단할 데이터가 부족하면 중간보다 약간 아래
            "direction": cfg.D_UNKNOWN,
            "detail": (
                "가속/감속을 판단하려면 증가율이 최소 2개 필요한데 "
                f"{len(growths)}개만 계산됐습니다. (분기 데이터 부족 또는 직전 기간 적자)"
            ),
            "capped": False,
            "trace": {"growths": growths, "insufficient": True,
                      "basis": basis_text, "seasonal": bool(season)},
        }

    # 최근 구간에 '한 분기만 튄 곳'(일회성 이익·인수합병)이 있으면 방향을 말하지 않습니다.
    # 그런 분기를 그대로 두면 "가속!" 다음 분기에 "감속!"으로 뒤집힙니다.
    # '아직 스파이크인지 계단인지 알 수 없는' 최근 분기가 있으면 방향을 말하지 않습니다.
    # (계단은 그 한 번의 증가율만 빼고 정상 판정합니다 — 판정을 통째로 미루는 것은 과잉입니다)
    recent_anomaly = any(q.get("anomaly") == "pending" for q in quarters[-2:])
    if recent_anomaly:
        return {
            "score": max_score * 0.4,
            "direction": cfg.D_UNKNOWN,
            "detail": (
                "가장 최근 분기의 이익이 크게 뛰었는데, 다음 분기가 아직 없어 "
                "일시적인 것인지 새 수준으로 올라선 것인지 알 수 없습니다. "
                "지금 방향을 말하면 다음 실적발표에서 뒤집히므로 판정을 미룹니다."
            ),
            "capped": False,
            "trace": {"growths": growths, "anomaly": True,
                      "basis": basis_text, "seasonal": bool(season)},
        }

    recent = growths[-3:]            # 최근 최대 3개 분기의 증가율
    delta = recent[-1] - recent[-2]  # 증가율이 얼마나 더 빨라졌는가(%p)

    # --- 신호 ① 단기: 직전 기간과의 비교 ---
    if delta > cfg.DELTA_THRESHOLD_PP:
        short_signal = 1
    elif delta < -cfg.DELTA_THRESHOLD_PP:
        short_signal = -1
    else:
        short_signal = 0

    # --- 신호 ② 추세: 최근 여러 분기 증가율의 회귀 기울기 ---
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
        what = "1년치 이익의 증가율" if use_ttm else "이익 증가율"
        detail = (
            f"{what}의 최근 변화({delta:+.1f}%p)와 추세(기울기 {slope:+.1f})가 "
            "같은 방향을 가리킵니다"
        )
    elif short_signal == 0 or trend_signal == 0:
        # 한쪽만 방향이 있음 → **단정하지 않고** 약한 판정으로 내립니다.
        # 근거가 절반이면 주장도 절반이어야 합니다.
        active = short_signal if short_signal != 0 else trend_signal
        direction = {1: cfg.D_WEAK_ACCEL, -1: cfg.D_WEAK_DECEL}[active]
        detail = (
            f"두 신호 중 하나만 방향을 보입니다 (단기 {delta:+.1f}%p, 추세 기울기 {slope:+.1f}) — "
            "방향을 단정하지 않고 약한 신호로만 봅니다"
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
        "basis": basis_text,                # 무엇과 비교했나 (TTM / 전분기 / 작년 같은 분기)
        "use_ttm": use_ttm,
        "seasonal": bool(season),
        "growths": growths,                 # 기간별 증가율 전체 이력
        "recent": recent,                   # 판정에 실제로 쓴 최근 값들
        "delta_pp": delta,                  # 증가율의 변화(%p) — 판정의 직접 근거
        "slope": slope,                     # 회귀 기울기 — 추세 신호 근거
        "window": window,                   # 기울기 계산에 쓴 값들
        "short_signal": short_signal,
        "trend_signal": trend_signal,
        "ratio": ratio,
        "threshold": cfg.DELTA_THRESHOLD_PP,
        "trend_threshold": cfg.DELTA_TREND_THRESHOLD,
        # 이 방향을 백테스트에서 실제로 얼마나 맞혔는지 (없으면 잰 적 없음)
        "measured_hit": cfg.DELTA_MEASURED_HIT.get(direction),
    }

    # 최근 증가율 자체가 마이너스(이익 감소)면 추가 감점
    if recent[-1] < 0:
        ratio *= 0.5
        trace["ratio"] = ratio
        trace["shrink_penalty"] = True
        detail += (
            " · 다만 1년치 이익 자체가 줄고 있습니다" if use_ttm
            else " · 다만 최근 분기 이익은 전분기보다 줄었습니다"
        )

    score = max_score * ratio

    # 저기저 함정 방지 상한
    #
    # ⚠️ 문턱은 **기준자마다 다릅니다.** 논갭 영업이익은 달러($50M), 조정 EPS 는
    #    주당 달러($0.05)입니다. 하나의 문턱을 그대로 쓰면 EPS 8.53 달러가
    #    "$50M 미만"으로 판정되어 모든 종목의 델타 점수가 조용히 깎입니다.
    #    실제로 그렇게 깎이는 것을 확인하고 기준자별로 나눴습니다.
    capped = False
    basis = cfg.quarters_basis(quarters)
    threshold = cfg.LOW_BASE_THRESHOLD.get(basis, cfg.LOW_BASE_THRESHOLD_USD)
    latest_op = next(
        (q["op_income"] for q in reversed(quarters) if q.get("op_income") is not None), None
    )
    if latest_op is not None and latest_op < threshold:
        limit = max_score * cfg.LOW_BASE_CAP_RATIO
        if score > limit:
            score = limit
            capped = True
            detail += (
                f" · 이익 규모가 작아({cfg.fmt_amount(latest_op, basis, 0)}) 이 항목 점수를 "
                f"{int(cfg.LOW_BASE_CAP_RATIO*100)}%로 제한했습니다"
            )
    trace["latest_op"] = latest_op
    trace["capped"] = capped
    # ⚠️ 이름 주의: trace["basis"] 는 이미 "TTM 이냐 분기 단위냐"를 뜻합니다.
    #    여기서 말하는 기준자(어느 숫자로 재는가)는 다른 개념이라 이름을 나눕니다.
    trace["metric_basis"] = basis
    trace["low_base_threshold"] = threshold

    return {
        "score": round(score, 1),
        "direction": direction,
        "detail": detail,
        "capped": capped,
        "trace": trace,
    }


# ---------------------------------------------------------------------------
# 펀더멘털 ② 국면 — 이익 사이클에서 지금 어디에 서 있는가 (10점)
# ---------------------------------------------------------------------------
def score_phase(quarters: list[dict]) -> dict:
    """1년치 이익(TTM)이 사이클의 어디쯤에 있는지 봅니다.

    델타는 "얼마나 빨라지고 있나"를 봅니다. 그런데 델타만으로는
    **바닥에서 돌아서는 순간**을 구조적으로 놓칩니다 — 직전 기간이 적자면
    증가율 자체를 계산할 수 없기 때문입니다.

    실제로 놓쳤습니다. 2025년 반도체 슈퍼사이클에서
      · 마이크론: 이익이 폭증하기 시작한 것은 FY25 Q1 인데
        델타가 처음 "가속"이라고 말한 것은 **4분기 뒤인 FY25 Q4** 였습니다.
      · 크레도: 국면 신호는 FY24 Q4, 델타의 첫 가속은 FY25 Q2.
    국면 신호는 두 경우 모두 폭증이 시작된 그 분기에 켜졌습니다.

    ⚠️ 이 신호가 하는 일과 하지 못하는 일:
      할 수 있음 — "지금 어느 종목이 좋은 자리에 있나" 줄 세우기.
        백테스트에서 국면 신호가 있는 시점의 97%는 2분기 뒤 이익이 늘었고,
        신호가 없는 시점에서 크게 오른 경우는 34번 중 0번이었습니다.
      할 수 없음 — "한 종목 안에서 언제 사야 하나" 맞히기.
        표본에서 성장 종목은 거의 항상 신호가 있었고 부진 종목은 거의 항상
        없었기 때문에, 한 종목 안에서의 타이밍 효과는 재지 못했습니다.
    """
    max_score = cfg.W_PHASE
    ttm = _ttm_series(quarters)

    if len(ttm) < 2:
        return {
            "score": round(max_score * cfg.PHASE_RATIO[cfg.PH_UNKNOWN], 1),
            "phase": cfg.PH_UNKNOWN,
            "detail": (
                f"1년치 이익을 두 번 이상 비교하려면 분기가 최소 "
                f"{cfg.PHASE_MIN_QUARTERS}개 필요한데 모자랍니다."
            ),
            "trace": {"ttm": ttm, "insufficient": True},
        }

    now, previous = ttm[-1], ttm[-2]
    history = ttm[:-1]
    peak = max(history)

    if previous <= 0 < now:
        phase = cfg.PH_TURNAROUND
        detail = (
            f"1년치 이익이 적자(${previous/1e6:,.0f}M)에서 흑자(${now/1e6:,.0f}M)로 "
            "돌아섰습니다 — 사이클 바닥을 지난 자리입니다"
        )
    elif now <= 0 and _all_approximated(quarters):
        # ⚠️ 근사치만으로는 적자를 선언하지 않습니다.
        #
        # 근사치는 'XBRL 의 GAAP 영업이익 + 주식보상비 + 무형자산상각' 입니다.
        # 그런데 회사가 무형자산상각을 따로 신고하지 않으면 그 되돌리기가 0이 되어,
        # GAAP 적자가 그대로 논갭 적자로 남습니다.
        #
        # 실제 사례 — 코히런트(COHR) FY2025:
        #   GAAP 주당순이익 −$0.52 (적자)  /  조정 주당순이익 +$3.53 (흑자)
        # 인수로 생긴 상각비가 원인이라 GAAP 만 적자입니다. 그런데 근사치가
        # 이것을 '적자 지속'(국면 최하 등급)으로 만들어 순위 바닥으로 밀었습니다.
        #
        # 그래서 근사치만 있는 종목의 마이너스는 '판단불가'로 표시합니다.
        # 틀린 확신보다 모른다고 말하는 편이 낫습니다.
        phase = cfg.PH_UNKNOWN
        detail = (
            f"1년치 이익이 마이너스(${now/1e6:,.0f}M)로 나왔지만, 이 값은 회사 공식 "
            "발표가 아니라 **XBRL 회계데이터로 만든 근사치**입니다. 무형자산상각을 "
            "되돌리지 못하면 GAAP 적자가 그대로 남기 때문에, 실제로 적자인지 "
            "판단할 수 없습니다"
        )
    elif now <= 0:
        # 1년치가 아직 적자라면 사이클 위치를 논할 단계가 아닙니다.
        # 이것을 '중간 자리'로 뭉뚱그리면, 계속 적자인 회사가 고점과 바닥 사이에
        # 서 있는 멀쩡한 회사와 같은 표시를 받게 됩니다.
        phase = cfg.PH_LOSS
        detail = (
            f"1년치 이익이 아직 적자입니다(${now/1e6:,.0f}M). "
            "흑자로 돌아서기 전까지는 사이클 위치를 말할 수 없습니다"
        )
    elif peak > 0 and now > peak:
        phase = cfg.PH_NEW_HIGH
        detail = (
            f"1년치 이익 ${now/1e6:,.0f}M 이 수집 기간(3년) 안의 최고 "
            f"${peak/1e6:,.0f}M 을 넘어섰습니다. "
            "⚠️ 3년치만 모으므로 '역대 최고'가 아니라 **3년 안에서의 최고**입니다 — "
            "예전에 훨씬 잘 벌다 무너진 회사도 조금 회복하면 여기에 들어옵니다"
        )
    elif peak > 0 and now < peak * cfg.PHASE_ROLLOVER_RATIO:
        phase = cfg.PH_ROLLOVER
        detail = (
            f"1년치 이익 ${now/1e6:,.0f}M 이 3년 내 최고 ${peak/1e6:,.0f}M 의 "
            f"{now/peak*100:.0f}% 까지 내려왔습니다 — 고점에서 이탈한 자리입니다"
        )
    else:
        phase = cfg.PH_NONE
        detail = (
            f"1년치 이익 ${now/1e6:,.0f}M — 3년 내 최고(${peak/1e6:,.0f}M)를 넘지도, "
            "크게 벗어나지도 않은 중간 자리입니다"
        )

    ratio = cfg.PHASE_RATIO[phase]
    return {
        "score": round(max_score * ratio, 1),
        "phase": phase,
        "detail": detail,
        "trace": {
            "ttm": ttm,
            "now": now,
            "previous": previous,
            "peak": peak,
            "vs_peak_pct": (now / peak * 100) if peak > 0 else None,
            "rollover_ratio": cfg.PHASE_ROLLOVER_RATIO,
            "ratio": ratio,
        },
    }


# ---------------------------------------------------------------------------
# 펀더멘털 ③ GM% 드라이버 (25점)
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
    # 매출총이익률은 정의상 100%를 넘을 수 없습니다.
    # 범위를 벗어난 값(계산이 깨진 분기)은 판단에서 뺍니다 —
    # 예전에는 82,804,463.3% 가 그대로 통과해 화면에 출력됐습니다.
    margins = [
        q["gross_margin_pct"]
        for q in quarters
        if q.get("gross_margin_pct") is not None
        and cfg.MARGIN_MIN_PCT <= q["gross_margin_pct"] <= cfg.MARGIN_MAX_PCT
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
# 이익의 질 — GAAP EPS 와 조정 EPS 의 격차가 벌어지는가 (점수 없음 · 표시 전용)
# ---------------------------------------------------------------------------
def check_earnings_quality(quarters: list[dict]) -> dict:
    """'조정' 주당순이익이 무엇을 얼마나 가리고 있는지 추세를 봅니다.

    조정 EPS 는 감독기관이 아니라 **회사가 스스로 정의**합니다. 그래서 숫자
    자체보다 "GAAP 과 얼마나 벌어져 있고, 그 격차가 커지는가"가 중요합니다.

        격차 = 조정 EPS − GAAP EPS

    · 격차가 좁아지는 중  → 구조조정·상각 같은 일회성이 실제로 끝나가는 중 (건강)
    · 격차가 그대로       → 인수 상각처럼 예정된 항목 (정상)
    · 격차가 벌어지는 중  → ⚠️ '조정'이 점점 더 많은 것을 가리는 중

    ⚠️ 이 검사는 **점수에 반영하지 않습니다.** 아직 과거 데이터로 검증한 적이
       없기 때문입니다. 화면에 보여 주기만 합니다. 검증 없이 점수를 주지
       않는다는 이 저장소의 원칙을 따릅니다.
    """
    pairs = [
        (q["adj_eps"], q["gaap_eps"])
        for q in quarters
        if q.get("adj_eps") is not None and q.get("gaap_eps") is not None
    ]
    if len(pairs) < cfg.EPS_GAP_MIN_QUARTERS:
        return {
            "verdict": cfg.QUALITY_GAP_UNKNOWN,
            "detail": (
                f"GAAP·조정 주당순이익이 함께 잡힌 분기가 {len(pairs)}개뿐입니다 "
                f"(최소 {cfg.EPS_GAP_MIN_QUARTERS}개 필요). 격차 추세를 말할 수 없습니다."
            ),
            "trace": {"pairs": pairs},
        }

    gaps = [adj - gaap for adj, gaap in pairs]
    half = len(gaps) // 2
    older, recent = gaps[:half], gaps[half:]
    older_avg = sum(older) / len(older)
    recent_avg = sum(recent) / len(recent)

    # 앞 절반의 평균 격차가 0 근처면 배수를 낼 수 없습니다(0으로 나누기).
    # 그럴 때는 '조정할 것이 거의 없던 회사'이므로 격차가 생긴 것 자체를 봅니다.
    if abs(older_avg) < 0.01:
        verdict = (
            cfg.QUALITY_GAP_WIDENING if abs(recent_avg) >= 0.10
            else cfg.QUALITY_GAP_STABLE
        )
        ratio = None
    else:
        ratio = recent_avg / older_avg
        if ratio >= cfg.EPS_GAP_WIDEN_RATIO:
            verdict = cfg.QUALITY_GAP_WIDENING
        elif ratio <= cfg.EPS_GAP_NARROW_RATIO:
            verdict = cfg.QUALITY_GAP_NARROWING
        else:
            verdict = cfg.QUALITY_GAP_STABLE

    notes = {
        cfg.QUALITY_GAP_WIDENING: (
            "⚠️ 조정 주당순이익이 GAAP 과 점점 더 벌어지고 있습니다. "
            "'조정'으로 빼는 몫이 커지는 중이라, 조정 EPS 의 증가율을 그대로 "
            "믿으면 안 됩니다"
        ),
        cfg.QUALITY_GAP_STABLE: (
            "GAAP 과의 격차가 일정합니다. 인수 상각처럼 예정된 항목일 가능성이 큽니다"
        ),
        cfg.QUALITY_GAP_NARROWING: (
            "GAAP 과의 격차가 줄고 있습니다. 일회성 비용이 실제로 끝나가는 중입니다"
        ),
    }
    return {
        "verdict": verdict,
        "detail": (
            f"주당 격차(조정−GAAP) 평균이 ${older_avg:.2f} → ${recent_avg:.2f} 로 "
            f"움직였습니다. {notes[verdict]}"
        ),
        "trace": {
            "pairs": pairs,
            "gaps": [round(g, 4) for g in gaps],
            "older_avg": round(older_avg, 4),
            "recent_avg": round(recent_avg, 4),
            "ratio": round(ratio, 3) if ratio is not None else None,
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
        # ⚠️ 매출이 줄고 있는데 "증가 속도가 빨라진다"고 쓰면 안 됩니다.
        #    −20% → −5% 는 감소 폭이 줄어든 것이지 매출이 느는 게 아닙니다.
        #    예전에는 이 경우에도 가점을 주고 "빨라지는 중"이라고 적었습니다.
        if qoq[-1] > qoq[-2] and qoq[-1] > 0 and qoq[-2] > 0:
            accel_score = max_score * 0.3
            accel_text = " · 매출 증가 속도도 빨라지는 중입니다"
        elif qoq[-1] > 0:
            accel_score = max_score * 0.15
            accel_text = " · 매출은 계속 늘고 있습니다"
        elif qoq[-1] > qoq[-2]:
            accel_score = max_score * 0.05
            accel_text = " · 매출은 줄고 있지만 감소 폭이 작아지는 중입니다"
        else:
            accel_text = " · 매출 감소 폭이 커지는 중입니다"

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
        # 안전 계산: 비현실적인 증가율(단위가 깨진 경우)은 점수도 만점을 주면 안 됩니다.
        # 예전에는 +461,711,116% 가 그대로 만점권 점수를 받았습니다.
        growth_pct = dq.safe_growth_pct(latest_op, forward_op)
        if growth_pct is None:
            forward_score = max_score * 0.4
            growth_text = (
                "전망 증가율이 비현실적이어서(단위가 깨진 것으로 보임) 판단하지 않았습니다"
            )
        else:
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

    # ⚠️ 앞과 뒤를 **같은 잣대로** 재야 합니다.
    #   델타 판정 기준을 1년치(TTM)로 바꾸면서, 여기만 그대로 두는 바람에
    #   '최근 실제'는 1년치 증가율(예: +23%)인데 '다음 전망'은 분기 증가율
    #   (예: +17%)이 되어 서로 다른 자를 맞대고 있었습니다.
    #   꾸준히 크는 회사는 1년치 증가율이 분기 증가율보다 항상 크므로,
    #   이 상태로는 **거의 모든 성장 종목이 '가속 둔화'로 찍힙니다.**
    #   실제로 전체 판정의 28%가 그렇게 나왔습니다 (NVDA 는 세 분기 연속).
    #   그래서 1년치로 판정했으면 전망도 1년치로 환산해 견줍니다.
    use_ttm = delta_result.get("trace", {}).get("use_ttm", False)
    values = [
        q["op_income"] for q in quarters
        if q.get("op_income") is not None and q.get("anomaly") != "spike"
    ]

    if use_ttm and len(values) >= 4:
        ttm_now = sum(values[-4:])
        ttm_next = sum(values[-3:]) + forward_op
        next_qoq = dq.safe_growth_pct(ttm_now, ttm_next)
        growth_unit = "1년치"
    else:
        # 전망 증가율도 안전 계산을 씁니다 (단위가 깨지면 +461,711,116% 같은 값이 나옵니다)
        next_qoq = dq.safe_growth_pct(latest_op, forward_op)
        growth_unit = "분기"

    if next_qoq is None:
        return {**empty, "accel_detail": "전망 증가율이 비현실적이어서 예측을 만들지 않았습니다"}
    change = next_qoq - last_qoq
    was_rising = last_qoq >= 0

    if change > threshold:
        label = cfg.F_ACCEL_KEEP if was_rising else cfg.F_REBOUND
    elif change < -threshold:
        label = cfg.F_ACCEL_SLOW if was_rising else cfg.F_DECEL_KEEP
    else:
        label = cfg.F_FLAT

    # --- 델타가속예측 (2분기 경로) ---
    # 여기도 같은 잣대를 씁니다 — 1년치로 봤으면 다다음 분기도 1년치로.
    next2_qoq = None
    if forward_op_2 is not None:
        if use_ttm and len(values) >= 4:
            ttm_next = sum(values[-3:]) + forward_op
            ttm_next2 = sum(values[-2:]) + forward_op + forward_op_2
            next2_qoq = dq.safe_growth_pct(ttm_next, ttm_next2)
        elif forward_op > 0:
            next2_qoq = dq.safe_growth_pct(forward_op, forward_op_2)

    if next2_qoq is None:
        # 다다음 분기 자료가 없으면 '2분기 경로'를 판정하지 않습니다.
        # 예전에는 다음 분기 예측을 그대로 복사했는데, F_*와 F2_* 문자열이 같아
        # 화면에서 2분기까지 계산한 것처럼 보였습니다 (없는 근거를 있는 것처럼 표시).
        accel_label = cfg.F2_NONE
        accel_detail = "다다음 분기 컨센서스가 없어 2분기 경로는 판정하지 않았습니다"
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
            f"{growth_unit} 증가율 경로: 실제 {last_qoq:+.1f}% → 다음 {next_qoq:+.1f}% → "
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
        "growth_unit": growth_unit,
        "detail": (
            f"최근 실제 {growth_unit} 증가율 {last_qoq:+.1f}% → 다음 예상 "
            f"{next_qoq:+.1f}% ({change:+.1f}%p)"
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
    """펀더멘털 5개 항목을 계산해 100점 만점으로 합칩니다."""
    delta = score_delta_acceleration(quarters)
    phase = score_phase(quarters)
    gm = score_gm_driver(quarters)
    revenue = score_revenue_quality(quarters)
    fwd = score_forward(quarters, forward)

    # 다음 분기 전망이 가리키는 방향을 델타 점수에 반영합니다.
    #
    # ⚠️ **검증된 두 가지만** 반영합니다.
    #   '가속 둔화 ↗↘' 는 오랫동안 "정점 근처 신호"라고 표시해 왔지만, 20종목
    #   142개 시점으로 재 보니 정점 적중이 16.7% 로 기준선(21.1%)보다 오히려
    #   낮았습니다. 문턱을 3~50%p 로 바꿔 가며 전부 시험해도 마찬가지였습니다.
    #   근거가 없으므로 이 신호는 점수를 **건드리지 않습니다**(배수 1.0).
    #   진짜였던 것은 '감속 지속 ↘↘'(정점 66.7%)과 '가속 지속 ↗↗'(정점 0%)입니다.
    forecast = predict_delta(quarters, forward, delta)
    multiplier = cfg.FORECAST_SCORE_MULT.get(forecast.get("label"), 1.0)
    delta_score = min(delta["score"] * multiplier, float(cfg.W_DELTA_ACCEL))

    if multiplier != 1.0:
        delta = {
            **delta,
            "score": round(delta_score, 1),
            "forecast_multiplier": multiplier,
            "detail": (
                f"{delta['detail']} · 다음 분기 전망이 '{forecast['label']}' 이라 "
                f"이 항목을 {multiplier:.2f}배 했습니다"
            ),
        }

    # --- 자사주 방어장치 (구조대 경로에서만) ---
    #
    # 논갭 영업이익을 못 구해 조정 EPS 로 판정하는 중이라면, 자사주 매입이
    # '가속'을 만들어 낼 수 있습니다.  EPS = 순이익 ÷ 주식수  이므로
    # 영업이익이 제자리여도 분모가 줄면 EPS 는 오릅니다.
    #
    # 자사주가 큰 4종목 39개 시점으로 재 본 결과:
    #   · 주식수 감소와 EPS 부풀림의 상관계수 -0.703
    #   · 주식수가 1년에 -12% 줄어든 구간에서 편향이 +1.5 ~ +3.8%p
    #     → 판정 문턱(3.0%p)과 겹칩니다
    #   · EBAY 2021-12-31(주식수 -11.7%)에서 논갭 영업이익은 '유지'인데
    #     조정 EPS 는 '가속' 이라고 했습니다. 그 분기는 **주가 정점 직후**였습니다.
    #
    # 그래서 이 구간에서는 델타 점수를 절반까지만 인정합니다.
    shrink = (forward.get("consensus") or {}).get("shares_shrink_pct")
    if (
        cfg.quarters_basis(quarters) == cfg.BASIS_ADJ_EPS
        and shrink is not None
        and shrink <= cfg.EPS_BASIS_BUYBACK_LIMIT_PCT
    ):
        capped_to = min(delta["score"], cfg.W_DELTA_ACCEL * cfg.EPS_BASIS_BUYBACK_CAP_RATIO)
        if capped_to < delta["score"]:
            delta = {
                **delta,
                "score": round(capped_to, 1),
                "buyback_capped": True,
                "detail": (
                    f"{delta['detail']} · ⚠️ 조정 EPS 로 판정 중인데 주식수가 1년에 "
                    f"{shrink:.1f}% 줄었습니다. 자사주 매입이 '가속'을 만들었을 수 "
                    f"있어 이 항목을 {int(cfg.EPS_BASIS_BUYBACK_CAP_RATIO*100)}%로 "
                    "제한했습니다"
                ),
            }

    total = (
        delta["score"] + phase["score"] + gm["score"]
        + revenue["score"] + fwd["score"]
    )
    return {
        "total": round(total, 1),
        "delta": delta,
        "phase": phase,
        "gm": gm,
        "revenue": revenue,
        "forward": fwd,
        "forecast": forecast,
    }


def decide_verdict(fund_total: float, trend_stage: int, has_fundamentals: bool = True) -> str:
    """펀더멘털 점수와 추세 단계를 조합해 최종 판정을 내립니다.

    추세 단계: 1=완전 정배열, 2=준정배열, 3=중립, 4=추세 훼손, 5=완전 역배열
    """
    return explain_verdict(fund_total, trend_stage, has_fundamentals)["verdict"]


def explain_verdict(fund_total: float, trend_stage: int, has_fundamentals: bool = True) -> dict:
    """판정 결과와 함께 "왜 그 판정이 나왔는지"를 돌려줍니다.

    화면에서 판정을 눌렀을 때 보여줄 근거로 사용합니다.

    has_fundamentals=False (실적을 한 분기도 못 구한 종목)이면 판정을 매기지 않습니다.
    이때의 펀더멘털 점수는 "자료가 없어 판단 불가"인 항목들의 기본 배점이 더해진 값이라
    실적이 좋아서 받은 점수가 아닙니다. 그대로 두면 실적이 있는 종목보다 위에 랭크되어
    "실적이 좋은 종목"으로 오해하게 됩니다.
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

    if not has_fundamentals:
        return {
            "verdict": cfg.V_NO_DATA,
            "rule": "실적 자료를 한 분기도 구하지 못함",
            "meaning": (
                "SEC에서 이 종목의 실적을 찾지 못했습니다. 화면의 펀더멘털 점수는 "
                "실적이 좋아서 받은 점수가 아니라 **'판단 불가' 항목들의 기본 배점**이므로, "
                "다른 종목과 같은 기준으로 비교할 수 없습니다. "
                "아래 🔧 데이터 수집 진단에서 어느 단계에서 막혔는지 확인해 주세요."
            ),
            "fund_text": f"펀더멘털 {fund_total:.0f}점 (실적 자료 없음 — 비교 불가)",
            "stage_text": stage_text,
            "fund_total": fund_total,
            "trend_stage": trend_stage,
        }

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
    # score_fundamental 이 이미 계산해 뒀습니다 (델타 점수에 반영하느라).
    # 여기서 다시 부르면 배수가 곱해진 델타를 근거로 삼게 되어 값이 어긋납니다.
    forecast = fundamental["forecast"]
    # 이익의 질 — 점수에는 넣지 않고 화면에만 띄웁니다(아직 검증 전).
    quality = check_earnings_quality(quarters)

    final = fundamental["total"] * cfg.WEIGHT_FUNDAMENTAL + technical["total"] * cfg.WEIGHT_TECHNICAL
    stage = price_info.get("stage", cfg.TREND_STAGE[cfg.S_UNKNOWN])
    verdict_info = explain_verdict(fundamental["total"], stage, has_fundamentals=bool(quarters))
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
        "phase": fundamental["phase"]["phase"],
        "revenue_yoy": fundamental["revenue"]["yoy"],
        "forward_basis": forward.get("basis"),
        "forward_op_income": forward.get("forward_op_income"),
        "revision": forward.get("revision", 0),
        "data_source": data_source,
        "confidence": confidence["total"],
        "confidence_detail": confidence,
        "earnings_quality": quality["verdict"],
        "earnings_quality_detail": quality,
        # 최근 분기의 주당순이익 — 점수에는 쓰지 않고 화면 확인용입니다(1단계).
        "adj_eps": (latest_quarter or {}).get("adj_eps"),
        "gaap_eps": (latest_quarter or {}).get("gaap_eps"),
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
