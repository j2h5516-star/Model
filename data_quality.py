"""
data_quality.py — 숫자를 쓰기 전에 먼저 검사하는 단계
=====================================================

**이 모듈이 존재하는 이유**

배포된 화면에 이런 숫자가 그대로 나왔습니다.

    최근 4개 분기 평균 영업마진 82,804,463.3%
    다음 분기 예상 영업이익 $389,938,207.9M   (최근 실제 분기는 $84.5M)
    변화율 +461,711,116.6%

영업마진은 정의상 100%를 넘을 수 없습니다. 어느 분기의 매출이 영업이익보다
100만 배쯤 작게 들어온 것이 원인인데, **아무도 검사하지 않아서** 그 값이
평균 마진 → 다음 분기 전망 → 증가율 → 차트 축까지 그대로 흘러갔습니다.
차트의 실제 실적 막대는 축이 4억 배로 늘어나는 바람에 화면에서 사라졌습니다.

그래서 수집과 계산 사이에 **검사 단계**를 하나 넣습니다.

    수집(SEC) →  ★ 검사·보정(이 파일) ★  →  점수·전망 계산  →  화면

원칙 세 가지:

  ① **버리기 전에 먼저 판단한다.** 이상해 보인다고 통째로 버리지 않습니다.
     어느 값이 왜 이상한지 판정하고, 고칠 수 있으면 고쳐서 씁니다.
  ② **고칠 수 있을 때만 고친다.** 단위가 정확히 1,000배 / 100만 배 어긋난
     경우처럼 확실할 때만 자동 보정하고, 애매하면 그 값만 빼고 씁니다
     (분기 전체를 버리지 않습니다 — 매출이 이상해도 영업이익은 쓸 수 있습니다).
  ③ **무엇을 했는지 남긴다.** 보정·제외한 내역을 진단에 기록해 화면에서 볼 수 있게 합니다.
"""

from __future__ import annotations

import math
import statistics

import config as cfg

# ---------------------------------------------------------------------------
# 품질 등급
# ---------------------------------------------------------------------------
Q_OK = "정상"
Q_FIXED = "보정됨"      # 단위 오류를 확실하게 잡아 고쳤음
Q_PARTIAL = "일부제외"  # 일부 값(예: 매출)만 신뢰할 수 없어 빼고 씀
Q_INVALID = "사용불가"  # 영업이익 자체가 없거나 이상해 계산에 쓸 수 없음


def _finite(value) -> bool:
    """숫자이고 무한대·NaN이 아닌지 확인합니다."""
    return isinstance(value, (int, float)) and math.isfinite(value)


# ---------------------------------------------------------------------------
# 개별 값 계산 (안전 버전)
# ---------------------------------------------------------------------------
def safe_margin_pct(revenue, op_income) -> float | None:
    """영업마진(%)을 계산하되, 말이 되는 값일 때만 돌려줍니다.

    영업이익이 매출보다 클 수는 없으므로 마진은 100%를 넘을 수 없습니다.
    범위를 벗어나면 None (= "이 분기 마진은 모른다")을 돌려줍니다.
    """
    if not (_finite(revenue) and _finite(op_income)) or revenue <= 0:
        return None
    margin = op_income / revenue * 100.0
    if not _finite(margin):
        return None
    if margin > cfg.MARGIN_MAX_PCT or margin < cfg.MARGIN_MIN_PCT:
        return None
    return margin


def safe_growth_pct(previous, current) -> float | None:
    """증가율(%)을 계산하되, 기저가 0 이하이거나 결과가 비현실적이면 None.

    적자(음수)에서 흑자로 갈 때의 증가율은 수학적으로 의미가 없고,
    기저가 아주 작으면 +461,711,116% 같은 숫자가 나옵니다.
    """
    if not (_finite(previous) and _finite(current)) or previous <= 0:
        return None
    growth = (current / previous - 1.0) * 100.0
    if not _finite(growth) or abs(growth) > cfg.GROWTH_ABS_MAX_PCT:
        return None
    return growth


# ---------------------------------------------------------------------------
# 분기 한 개 검사
# ---------------------------------------------------------------------------
def check_quarter(quarter: dict) -> dict:
    """분기 하나를 검사해 등급·사유·보정값을 돌려줍니다.

    반환: {"quality", "reasons": [...], "revenue": 보정된 매출 또는 None}
    원본을 바꾸지 않습니다 (판정만 합니다).
    """
    reasons: list[str] = []
    op_income = quarter.get("op_income")
    revenue = quarter.get("revenue")
    label = quarter.get("period_label", "?")

    # --- 영업이익: 이 값이 없으면 이 분기는 쓸 수 없습니다 ---
    if not _finite(op_income):
        return {
            "quality": Q_INVALID,
            "reasons": [f"{label}: 영업이익 값이 없습니다"],
            "revenue": None,
        }
    if abs(op_income) > cfg.MAX_PLAUSIBLE_USD:
        return {
            "quality": Q_INVALID,
            "reasons": [
                f"{label}: 영업이익 ${op_income:,.0f} 은(는) 현실적인 범위를 벗어납니다"
            ],
            "revenue": None,
        }

    # --- 매출: 없으면 없는 대로 두고, 있으면 단위가 맞는지 봅니다 ---
    if revenue is None:
        return {"quality": Q_OK, "reasons": [], "revenue": None}

    if not _finite(revenue) or revenue <= 0:
        return {
            "quality": Q_PARTIAL,
            "reasons": [f"{label}: 매출이 0 이하이거나 숫자가 아니어서 제외했습니다"],
            "revenue": None,
        }

    if safe_margin_pct(revenue, op_income) is not None:
        return {"quality": Q_OK, "reasons": [], "revenue": revenue}

    # 여기부터는 "매출과 영업이익의 크기가 서로 맞지 않는" 상태입니다.
    #
    # ⚠️ XBRL(근사치)에서 온 값은 단위를 고치면 안 됩니다.
    #    XBRL 숫자는 이미 절대 달러라 단위가 어긋날 수 없습니다. 크기가 안 맞는다면
    #    그건 단위 문제가 아니라 **엉뚱한 항목을 매출로 잡아온 것**입니다.
    #    여기서 1,000배를 곱해 "고쳤다"고 하면 틀린 값을 그럴듯하게 만들어
    #    오히려 더 위험합니다 (실제로 비율 값 102를 $102M로 승격시킨 적이 있습니다).
    if quarter.get("source") == cfg.SRC_APPROX:
        return {
            "quality": Q_PARTIAL,
            "reasons": [
                f"{label}: XBRL 매출 ${revenue:,.0f} 이 영업이익과 크기가 맞지 않습니다 "
                "(다른 항목을 매출로 잘못 가져왔을 가능성) — 매출을 계산에서 뺐습니다"
            ],
            "revenue": None,
        }

    # 보도자료에서 온 값은 표 단위(천/백만) 표기를 놓쳤을 수 있어, 그 경우만 고칩니다.
    for scale, name in ((1_000.0, "천 단위"), (1_000_000.0, "백만 단위")):
        candidate = revenue * scale
        if safe_margin_pct(candidate, op_income) is not None:
            return {
                "quality": Q_FIXED,
                "reasons": [
                    f"{label}: 매출이 {name}로 들어와 있어 ${revenue:,.0f} → "
                    f"${candidate:,.0f} 로 고쳤습니다"
                ],
                "revenue": candidate,
            }

    # 고칠 수 없으면 매출만 빼고 영업이익은 그대로 씁니다 (분기를 통째로 버리지 않음)
    reasons.append(
        f"{label}: 매출 ${revenue:,.0f} 과 영업이익 ${op_income:,.0f} 의 크기가 맞지 않아 "
        "매출을 계산에서 뺐습니다 (마진·매출 지표만 영향)"
    )
    return {"quality": Q_PARTIAL, "reasons": reasons, "revenue": None}


# ---------------------------------------------------------------------------
# 분기 목록 전체 검사 (파이프라인이 부르는 입구)
# ---------------------------------------------------------------------------
def validate_quarters(quarters: list[dict], report: dict | None = None) -> list[dict]:
    """분기 목록을 검사해 **계산에 쓸 수 있는 분기만** 돌려줍니다.

    · 단위가 확실히 어긋난 매출은 고쳐서 씁니다
    · 고칠 수 없는 매출은 그 분기에서만 빼고, 영업이익은 그대로 씁니다
    · 영업이익 자체가 없거나 이상한 분기는 제외합니다
    · 무엇을 했는지 report["quality_notes"] 에 남깁니다
    """
    if not quarters:
        return []

    kept: list[dict] = []
    notes: list[str] = []
    fixed_count = 0
    dropped_count = 0

    for quarter in quarters:
        verdict = check_quarter(quarter)
        notes.extend(verdict["reasons"])

        if verdict["quality"] == Q_INVALID:
            dropped_count += 1
            continue

        clean = dict(quarter)
        clean["quality"] = verdict["quality"]
        clean["revenue"] = verdict["revenue"]

        if verdict["quality"] == Q_FIXED:
            fixed_count += 1
        # 매출을 뺐으면 매출에서 나온 값들도 함께 지웁니다 (앞뒤가 맞아야 하므로)
        if clean["revenue"] is None:
            clean["gross_margin_pct"] = None

        kept.append(clean)

    # 계절성도 '먼저 판단'해서 넘깁니다 (점수 계산이 어떤 기준으로 볼지 정하는 근거)
    season = detect_seasonality(kept)
    for quarter in kept:
        quarter["seasonal"] = season["seasonal"]

    # 한 분기만 유별나게 튄 곳(일회성·인수합병)도 표시해 둡니다
    anomalies = detect_anomalies(kept)
    for index in anomalies["indexes"]:
        kept[index]["anomaly"] = True
    notes.extend(anomalies["reasons"])

    if report is not None:
        report["quality_notes"] = notes
        report["quality_fixed"] = fixed_count
        report["quality_dropped"] = dropped_count
        report["seasonality"] = season
        report["anomalies"] = len(anomalies["indexes"])
        if season["seasonal"]:
            notes.append(f"계절성: {season['reason']}")

    return kept


# ---------------------------------------------------------------------------
# 계절성 검사 — "이 종목은 전분기 대비로 판정해도 되는가"
# ---------------------------------------------------------------------------
def _swing(values: list[float]) -> float | None:
    """값들이 가운데에서 얼마나 흔들리는지 (표준편차 대신 중앙값 절대편차).

    한 분기만 튀어도 크게 흔들리는 표준편차와 달리, 잡음에 덜 흔들립니다.
    """
    if len(values) < 2:
        return None
    center = statistics.median(values)
    return statistics.median([abs(v - center) for v in values])


def detect_seasonality(quarters: list[dict]) -> dict:
    """분기마다 오르내리는 '계절 장사'인지 판정합니다.

    왜 필요한가:
      아이스크림 가게의 이익은 여름에 오르고 겨울에 내립니다. 이런 회사를
      **전분기 대비(QoQ)** 증가율로 보면 "가속 → 감속 → 가속"이 계속 반복되는데,
      모델은 그때마다 방향을 단정합니다. 백테스트에서 이런 종목의 적중률이
      **0%** 였습니다 — 틀리는 정도가 아니라 **거꾸로 맞히고 있었습니다.**

    판정 방법:
      같은 분기끼리 1년 간격으로 비교한 증가율(YoY)이, 전분기 대비 증가율(QoQ)보다
      **훨씬 안정적**이면 계절성이 있다고 봅니다. (계절 장사는 작년 같은 분기와
      비교해야 실체가 보입니다)

    반환: {"seasonal": bool, "reason": str, "qoq_swing": float|None, "yoy_swing": float|None}
    """
    values = [q.get("op_income") for q in quarters if q.get("op_income") is not None]
    if len(values) < cfg.SEASONALITY_MIN_QUARTERS:
        return {"seasonal": False, "reason": "분기가 부족해 계절성을 판단하지 않았습니다",
                "qoq_swing": None, "yoy_swing": None}

    qoq = [g for g in (safe_growth_pct(a, b) for a, b in zip(values, values[1:]))
           if g is not None]
    yoy = [g for g in (safe_growth_pct(values[i - 4], values[i])
                       for i in range(4, len(values))) if g is not None]

    if len(qoq) < 3 or len(yoy) < 2:
        return {"seasonal": False, "reason": "비교할 증가율이 부족합니다",
                "qoq_swing": None, "yoy_swing": None}

    qoq_swing, yoy_swing = _swing(qoq), _swing(yoy)
    if qoq_swing is None or yoy_swing is None:
        return {"seasonal": False, "reason": "흔들림을 계산할 수 없습니다",
                "qoq_swing": qoq_swing, "yoy_swing": yoy_swing}

    seasonal = qoq_swing > max(cfg.SEASONALITY_MIN_SWING_PP,
                               yoy_swing * cfg.SEASONALITY_SWING_RATIO)
    if seasonal:
        reason = (
            f"전분기 대비 증가율이 크게 출렁이는데(±{qoq_swing:.0f}%p) "
            f"작년 같은 분기와 비교하면 안정적입니다(±{yoy_swing:.0f}%p). "
            "계절 장사로 보여 전분기 대비 판정을 쓰지 않습니다."
        )
    else:
        reason = "계절성이 뚜렷하지 않아 전분기 대비로 판정합니다"

    return {"seasonal": seasonal, "reason": reason,
            "qoq_swing": qoq_swing, "yoy_swing": yoy_swing}


def detect_anomalies(quarters: list[dict]) -> dict:
    """한 분기만 유별나게 튄 곳(일회성 이익·인수합병 점프)을 찾습니다.

    왜 필요한가:
      · 회사를 인수하면 이익이 한 분기 만에 2배가 됩니다. 유기적 성장이 아닙니다.
      · 소송 합의금 같은 일회성 이익이 한 분기만 들어오기도 합니다.
      이런 분기를 그대로 두면 "가속!"이라고 외친 다음 분기에 "감속!"으로 뒤집힙니다.
      적대적 시나리오 검증에서 모델이 무계산 기준선보다 못했던 주된 이유입니다.

    판정 방법:
      이웃 분기들의 가운데 값과 견주어 몇 배나 벗어났는지 봅니다.
      (평균이 아니라 가운데 값을 쓰는 이유: 튄 값 자신이 평균을 끌어올리기 때문)

    반환: {"indexes": [튄 분기 번호...], "reasons": [...]}
    """
    values = [q.get("op_income") for q in quarters]
    indexes: list[int] = []
    reasons: list[str] = []

    for i, value in enumerate(values):
        if not _finite(value) or value <= 0:
            continue
        neighbors = [
            v for j, v in enumerate(values)
            if j != i and _finite(v) and v > 0 and abs(j - i) <= 3
        ]
        if len(neighbors) < 3:
            continue
        center = statistics.median(neighbors)
        if center <= 0:
            continue
        ratio = value / center
        if ratio > cfg.ANOMALY_JUMP_RATIO or ratio < 1.0 / cfg.ANOMALY_JUMP_RATIO:
            indexes.append(i)
            reasons.append(
                f"{quarters[i].get('period_label', i)}: 이웃 분기 중앙값의 "
                f"{ratio:.1f}배 — 일회성이거나 인수합병일 수 있어 방향 판정에서 뺍니다"
            )

    return {"indexes": indexes, "reasons": reasons}


# ---------------------------------------------------------------------------
# 전망 검사
# ---------------------------------------------------------------------------
def check_forward(forward_op, latest_op) -> tuple[bool, str]:
    """다음 분기 전망이 말이 되는 크기인지 봅니다.

    반환: (써도 되는가, 안 되면 그 이유)

    한 분기 만에 이익이 10배로 뛰거나 10분의 1로 줄어드는 일은
    거의 없습니다. 그런 값은 대개 계산이 깨진 것입니다.
    """
    if forward_op is None:
        return True, ""          # 전망이 없는 것은 오류가 아닙니다
    if not _finite(forward_op):
        return False, "전망값이 숫자가 아닙니다"
    if abs(forward_op) > cfg.MAX_PLAUSIBLE_USD:
        return False, f"전망 ${forward_op:,.0f} 은(는) 현실적인 범위를 벗어납니다"
    if not _finite(latest_op) or latest_op <= 0:
        return True, ""          # 비교 기준이 없으면 배수 검사는 건너뜁니다
    ratio = forward_op / latest_op
    if ratio > cfg.FORWARD_MAX_MULTIPLE or ratio < 1.0 / cfg.FORWARD_MAX_MULTIPLE:
        return False, (
            f"전망이 최근 실적의 {ratio:,.1f}배로 "
            f"{cfg.FORWARD_MAX_MULTIPLE:.0f}배 한도를 벗어납니다"
        )
    return True, ""
