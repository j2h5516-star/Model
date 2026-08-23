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
import re
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

    # ⚠️ 조정 EPS 기준에서는 마진 검사를 쓸 수 없습니다.
    #
    #   이 검사는 "영업이익 ÷ 매출이 −500%~100% 안에 드는가" 입니다. 그런데
    #   영업이익 자리에 **주당 달러**(0.87)가 들어오면 어떤 매출을 넣어도
    #   비율이 0% 근처가 되어 **무조건 통과**합니다. 매출이 100만 배 작게
    #   들어와도 잡지 못합니다 — 이 모듈이 존재하는 이유 자체가 무력화됩니다.
    #
    #   그래서 EPS 기준에서는 매출을 **이웃 분기와 견주어** 검사합니다.
    #   매출은 어느 기준에서든 언제나 달러이므로 이 방법은 항상 유효합니다.
    if quarter.get("basis") == cfg.BASIS_ADJ_EPS:
        neighbor = quarter.get("_neighbor_revenue")
        if neighbor and neighbor > 0:
            ratio = revenue / neighbor
            if not (1.0 / cfg.EPS_BASIS_REVENUE_RATIO <= ratio <= cfg.EPS_BASIS_REVENUE_RATIO):
                return {
                    "quality": Q_PARTIAL,
                    "reasons": [
                        f"{label}: 매출 ${revenue:,.0f} 이 이웃 분기"
                        f"(${neighbor:,.0f})의 {ratio:.4g}배라 단위가 어긋난 것으로 "
                        "보고 매출을 계산에서 뺐습니다"
                    ],
                    "revenue": None,
                }
        return {"quality": Q_OK, "reasons": [], "revenue": revenue}

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
    #
    # ⚠️ 마진만 보고 고치면 **정상 회사를 망칩니다.**
    #    매출을 키우면 마진은 언제나 0 쪽으로 움직이므로, "마진이 너무 음수다"는
    #    상태는 **항상** 보정에 성공합니다. 그래서 이 코드는 "매출이 천 단위로
    #    들어왔다"와 "이 회사가 매출보다 손실이 크다"를 구분하지 못하고,
    #    후자를 언제나 전자로 판정했습니다.
    #
    #    실제 피해: 매출 $20M · 논갭 영업손실 −$150M 인 바이오텍(정상적인 모습)의
    #    매출이 $20,000M 로 부풀려지고 "보정됨" 배지까지 달렸습니다. 일부 분기만
    #    보정되면 화면에 "매출 −99.8%"라는 가짜 붕괴가 뜹니다.
    #
    # 그래서 **이웃 분기와 견주어** 진짜 단위 오류일 때만 고칩니다.
    # 단위 오류는 한 분기만 정확히 1,000배(또는 100만 배) 어긋납니다.
    # 손실이 큰 회사는 매출이 이웃 분기와 비슷합니다 — 그것이 구분점입니다.
    neighbor = quarter.get("_neighbor_revenue")
    for scale, name in ((1_000.0, "천 단위"), (1_000_000.0, "백만 단위")):
        candidate = revenue * scale
        if safe_margin_pct(candidate, op_income) is None:
            continue
        if neighbor and neighbor > 0:
            # 고친 값이 이웃과 같은 자릿수여야 진짜 단위 오류입니다.
            if not (1.0 / cfg.REVENUE_NEIGHBOR_RATIO
                    <= candidate / neighbor
                    <= cfg.REVENUE_NEIGHBOR_RATIO):
                continue
        elif neighbor is not None:
            continue      # 이웃을 알 수 없으면 함부로 고치지 않습니다
        return {
            "quality": Q_FIXED,
            "reasons": [
                f"{label}: 매출이 {name}로 들어와 있어 ${revenue:,.0f} → "
                f"${candidate:,.0f} 로 고쳤습니다"
            ],
            "revenue": candidate,
        }

    # 이웃과 크기가 맞으면 매출 자체는 멀쩡합니다 — 손실이 큰 회사일 뿐입니다.
    # 이런 분기의 매출을 버리면 매출 성장 지표가 통째로 무너집니다.
    if neighbor and neighbor > 0:
        ratio = revenue / neighbor
        if 1.0 / cfg.REVENUE_NEIGHBOR_RATIO <= ratio <= cfg.REVENUE_NEIGHBOR_RATIO:
            return {
                "quality": Q_OK,
                "reasons": [
                    f"{label}: 영업손실이 매출보다 크지만(마진 "
                    f"{op_income / revenue * 100.0:,.0f}%), 매출이 이웃 분기"
                    f"(${neighbor:,.0f})와 크기가 맞아 그대로 씁니다"
                ],
                "revenue": revenue,
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

    # 매출을 이웃 분기와 견주기 위한 기준값을 분기마다 만듭니다.
    #
    # 두 가지에 씁니다.
    #   ① 조정 EPS 기준: 마진 검사를 쓸 수 없으므로 이것으로 대신합니다
    #   ② 논갭 영업이익 기준: "진짜 단위 오류"와 "손실이 큰 정상 회사"를 가릅니다
    #
    # ⚠️ 기준값은 **자기 자신을 뺀** 나머지의 중앙값입니다.
    #    자기를 넣으면 분기가 하나뿐일 때 깨진 값이 스스로를 "이웃과 같다"고
    #    보증해 검사가 무력화됩니다(실제로 테스트가 이것을 잡았습니다).
    #    중앙값을 쓰는 이유는 바로 옆 분기가 하필 깨진 값이어도 흔들리지 않기 위함입니다.
    #    이웃이 하나도 없으면(분기 1개) 기준값 없이 예전 방식으로 판단합니다.
    all_revenues = [
        q.get("revenue") for q in quarters
        if _finite(q.get("revenue")) and q.get("revenue", 0) > 0
    ]

    for quarter in quarters:
        others = [r for r in all_revenues if r is not quarter.get("revenue")]
        if others:
            quarter["_neighbor_revenue"] = statistics.median(others)
        else:
            quarter.pop("_neighbor_revenue", None)
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

    # 튄 분기를 찾아 스파이크(데이터 오류)와 계단(구조 변화)으로 나눠 표시합니다
    anomalies = detect_anomalies(kept)
    for index in anomalies["spikes"]:
        kept[index]["anomaly"] = "spike"     # 이 분기 자체를 판정에서 뺍니다
    for index in anomalies["steps"]:
        kept[index]["anomaly"] = "step"      # 그 한 번의 증가율만 뺍니다
    for index in anomalies["pending"]:
        kept[index]["anomaly"] = "pending"   # 판별 불가 — 방향 판정을 미룹니다
    notes.extend(anomalies["reasons"])

    # 한 종목 안에서 '논갭 영업이익의 정의'가 섞였는지 확인합니다
    consistency = check_source_consistency(kept)
    if consistency["mixed"]:
        notes.append(consistency["reason"])

    if report is not None:
        report["quality_notes"] = notes
        report["quality_fixed"] = fixed_count
        report["quality_dropped"] = dropped_count
        report["seasonality"] = season
        report["anomalies"] = (
            len(anomalies["spikes"]) + len(anomalies["steps"]) + len(anomalies["pending"])
        )
        report["spikes"] = len(anomalies["spikes"])
        report["steps"] = len(anomalies["steps"])
        report["source_consistency"] = consistency
        if season["seasonal"]:
            notes.append(f"계절성: {season['reason']}")

    return kept


# ---------------------------------------------------------------------------
# 계절성 검사 — "이 종목은 전분기 대비로 판정해도 되는가"
# ---------------------------------------------------------------------------
def fiscal_quarter_of(quarter: dict) -> tuple[int, int] | None:
    """이 분기가 '몇 년도 몇 번째 회계분기'인지 알아냅니다.

    작년 같은 분기와 짝지으려면 **회계분기**를 알아야 합니다.
    "인덱스로 4칸 뒤"는 분기가 하나만 빠져도 완전히 어긋납니다
    (실제로 ZETA 예시에서 +20%/+25% 여야 할 값이 +108%/-40% 로 망가졌습니다).

    **기간종료일을 먼저 봅니다. 분기 이름은 못 믿습니다** (150차-T).

    예전에는 이름("25 Q3")을 먼저 믿었습니다. 실데이터를 세어 보니
    **652칸(8.8%)·122종목의 이름이 그 종목 안에서조차 어긋났습니다.**
    이름은 보도자료 본문에서 글자로 뽑는데(`extract_period_label`),
    본문이 직전 분기·작년 같은 분기를 함께 말하면 엉뚱한 것을 집습니다.
    실물:

        SLB  '20 Q4' 라는 이름이 붙은 행 **4개** —
             기간끝 2021-03-31 · 2021-06-30 · 2021-09-30 · 2021-12-31
        INTC '18 Q2' 라는 이름이 붙은 행 **3개** —
             기간끝 2018-03-31 · 2018-06-30 · 2018-12-29

    이 함수를 쓰는 두 곳은 **분기 번호로 묶기만** 합니다(계절성 판정 ·
    이상값의 '같은 분기 평소'). 묶는 데 필요한 것은 "같은 분기끼리 같은
    번호, 다른 분기끼리 다른 번호"뿐입니다. 기간종료일의 월은 그것을
    **언제나** 지킵니다. 회계연도가 달력과 어긋나는 회사(예: CRDO 는
    4월 결산이라 회계 Q3 이 1월에 끝남)도 **어긋난 폭이 일정**하므로
    묶음은 그대로 맞습니다.

    이름은 화면 표시용으로 남겨 둡니다 — 지우지 않습니다. 다만
    **계산에는 쓰지 않습니다**(헌법 1조: 검사에서 탈락한 값은 고치지 말고
    버린다).

    기간종료일이 아예 없을 때만 이름으로 돌아갑니다.
    """
    # ① 기간종료일 (XBRL 이 준 구조값 — 글자로 뽑은 것이 아니라 믿을 수 있음)
    date_text = str(quarter.get("period_end") or quarter.get("filing_date") or "")
    match = re.search(r"(\d{4})-(\d{2})", date_text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        return (year, (month - 1) // 3 + 1)

    # ② 기간종료일이 없을 때만 이름으로
    label = str(quarter.get("period_label") or "")
    match = re.search(r"^(\d{2})/(\d{2})", label)          # "25/09" 형태
    if match:
        return (2000 + int(match.group(1)), (int(match.group(2)) - 1) // 3 + 1)
    match = re.search(r"(\d{2,4})\s*Q([1-4])", label, re.I)  # "25 Q3" 형태
    if match:
        year = int(match.group(1))
        return (year + 2000 if year < 100 else year, int(match.group(2)))
    return None


def detect_seasonality(quarters: list[dict]) -> dict:
    """분기마다 실적이 오르내리는 '계절 장사'인지 판정합니다.

    왜 필요한가:
      아이스크림 가게의 이익은 여름에 오르고 겨울에 내립니다. 이런 회사를
      **전분기 대비(QoQ)** 로 보면 "가속 → 감속 → 가속"이 끝없이 반복되는데,
      모델은 그때마다 방향을 단정합니다.
      ZETA 실데이터: 전분기 대비로 보면 매년 Q1 에 -71%p '대폭 감속',
      Q2 에 +81%p '대폭 가속'. 사업은 아무것도 안 바뀌었는데도요.

    판정 방법 — 계절성의 정의 그대로:
      같은 회계분기끼리 묶어서, **분기 사이의 차이**가 **같은 분기 안의 차이**보다
      훨씬 크면 계절성입니다.
        ZETA: Q1 [-40.3, -38.8] · Q2 [+26.2, +40.5] · Q3 [+39.2, +31.4] · Q4 [+31.3, +29.7]
              분기 사이 31.5 / 분기 안 3.2 = 10.0배 → 계절성
        균등성장: 0.0배 → 계절성 아님

    반환: {"seasonal", "reason", "ratio", "between", "within", "groups"}
    """
    usable = [q for q in quarters if _finite(q.get("op_income"))]
    if len(usable) < cfg.SEASONALITY_MIN_QUARTERS:
        return {"seasonal": False, "ratio": None, "between": None, "within": None,
                "groups": {},
                "reason": "분기가 부족해 계절성을 판단하지 않았습니다"}

    # 전분기 대비 증가율을 '그 분기가 몇 번째 회계분기인가'로 묶습니다
    groups: dict[int, list[float]] = {}
    for previous, current in zip(usable, usable[1:]):
        growth = safe_growth_pct(previous.get("op_income"), current.get("op_income"))
        key = fiscal_quarter_of(current)
        if growth is not None and key is not None:
            groups.setdefault(key[1], []).append(growth)

    if len(groups) < 3:
        return {"seasonal": False, "ratio": None, "between": None, "within": None,
                "groups": {},
                "reason": "분기 종류가 부족해 계절성을 판단하지 않았습니다"}

    # ⚠️ **두 번 이상 관찰된 분기만** 씁니다.
    #    계절성은 "해마다 되풀이된다"는 뜻입니다. 한 번밖에 못 본 분기는
    #    되풀이되는지 알 수가 없습니다.
    #    이걸 빼먹었을 때 실제로 생긴 일: 인수합병으로 한 분기만 +117% 뛴 회사가
    #    그 분기 하나 때문에 '계절 장사'로 찍혔습니다. 그 그룹은 표본이 1개라
    #    분기 안 차이는 0인데 분기 사이 차이만 잔뜩 키웠기 때문입니다.
    #    그러면 이상 감지가 "계절 흐름이겠지" 하며 인수합병을 그냥 지나칩니다.
    repeated = {key: values for key, values in groups.items() if len(values) > 1}
    if len(repeated) < 2:
        return {"seasonal": False, "ratio": None, "between": None, "within": None,
                "groups": groups,
                "reason": ("같은 분기를 두 번 이상 관찰한 것이 부족해 "
                           "계절성을 판단하지 않았습니다")}

    means = [statistics.mean(values) for values in repeated.values()]
    between = statistics.pstdev(means) if len(means) > 1 else 0.0
    inner = [statistics.pstdev(v) for v in repeated.values()]
    within = statistics.mean(inner) if inner else 0.0

    # 분기 안의 차이가 0에 가까우면 나눗셈이 폭발하므로 바닥을 깔아 둡니다
    ratio = between / max(within, 0.5)
    seasonal = between >= cfg.SEASONALITY_BETWEEN_MIN_PP and ratio >= cfg.SEASONALITY_RATIO

    if seasonal:
        reason = (
            f"같은 분기끼리는 비슷한데(±{within:.0f}%p) 분기별로는 크게 다릅니다"
            f"(±{between:.0f}%p, {ratio:.1f}배). 계절 장사로 보여 "
            "전분기 대신 **작년 같은 분기**와 비교해 판정합니다."
        )
    else:
        reason = "계절성이 뚜렷하지 않아 전분기 대비로 판정합니다"

    return {
        "seasonal": seasonal,
        "reason": reason,
        "ratio": round(ratio, 1),
        "between": round(between, 1),
        "within": round(within, 1),
        "groups": {k: [round(x, 1) for x in v] for k, v in sorted(groups.items())},
    }


def detect_anomalies(quarters: list[dict]) -> dict:
    """유별나게 튄 분기를 찾고, **스파이크와 계단을 구분**합니다.

    왜 구분해야 하는가 (논갭 영업이익의 정의부터 따져 보면):

      · **진짜 일회성 항목**(소송 합의금·구조조정비·자산손상·인수 관련 비용)은
        논갭 영업이익에서 **정의상 이미 빠져 있습니다.** 그러니 논갭 영업이익이
        한 분기만 튀었다가 되돌아오는 모양은 나올 수가 없습니다.
        그런 모양이 보인다면 그건 사건이 아니라 **데이터 오류**입니다
        (파싱 실수·분기 짝짓기 오류·단위 오류).
        → 그 분기는 계산에서 뺍니다.

      · **인수합병**은 다릅니다. 인수한 사업의 이익은 진짜이고 계속 이어집니다.
        그래서 논갭 영업이익은 **계단처럼 한 번 올라가 그 수준을 유지**합니다.
        문제는 계단이 생긴 그 한 분기의 증가율만 폭등하고 다음 분기엔 정상으로
        돌아온다는 것입니다. 델타로 보면 "가속!" 다음 분기 "감속!"이 됩니다.
        사업 모멘텀은 아무것도 안 바뀌었는데도요.
        → 분기는 살리고 **그 한 번의 증가율만** 판정에서 뺍니다.

    ⚠️ 무엇을 기준으로 "튀었다"고 할 것인가 — 여기서 한 번 크게 틀렸습니다.

      처음에는 **이익의 크기**를 이웃 분기와 견줬습니다("직전 3분기 중앙값의 1.8배를
      넘으면 튄 것"). 그런데 빠르게 크는 회사는 원래 매 분기 그만큼 커집니다.
      분기마다 40%씩 늘면 3분기 전 대비 2배가 되므로, **정상적으로 잘 크는 회사의
      모든 분기가 이상 분기로 찍힙니다.** 실제로 CRDO 는 6개 분기 연속으로
      "데이터 오류"라고 표시됐습니다. 찾으려던 것과 정반대의 결과입니다.

      그래서 기준을 **이익의 크기 → 증가 속도**로 바꿉니다.
      "이 회사가 평소 크던 속도에 견줘 이번 분기만 유별났는가"를 봅니다.
      평소 40%씩 크는 회사가 이번에도 40% 컸다면 아무 일도 없는 것입니다.

    반환: {"spikes": [분기번호...], "steps": [분기번호...], "reasons": [...]}
    """
    values = [q.get("op_income") for q in quarters]
    spikes: list[int] = []
    steps: list[int] = []
    pending: list[int] = []      # 아직 스파이크인지 계단인지 알 수 없는 최근 분기
    reasons: list[str] = []

    # 분기별 '증가 배수' (1.4 = 40% 증가). 직전이 적자면 계산할 수 없어 비워 둡니다.
    multiples: list[float | None] = [None] * len(values)
    for i in range(1, len(values)):
        previous, value = values[i - 1], values[i]
        if _finite(previous) and _finite(value) and previous > 0 and value > 0:
            multiples[i] = value / previous

    known = [m for m in multiples if m is not None]
    if len(known) < 3:
        # 증가율을 3개도 못 내면 '평소 속도'라는 것이 없어 비교할 대상이 없습니다.
        return {"spikes": [], "steps": [], "pending": [], "reasons": []}

    # ⚠️ 계절 장사는 '평소 속도'가 분기마다 다릅니다.
    #    ZETA 는 매년 1분기에 이익이 -40% 로 꺼졌다가 2분기에 복구됩니다.
    #    전체 평균과 견주면 이 정상적인 계절 흐름이 매년 "데이터 오류"로 찍혀
    #    1분기가 통째로 지워집니다. 그래서 계절 장사로 판정된 종목은
    #    **작년·재작년의 같은 분기**와만 견줍니다.
    seasonal = quarters[0].get("seasonal") if quarters else None
    if seasonal is None:
        seasonal = detect_seasonality(quarters)["seasonal"]

    fq_of = [fiscal_quarter_of(q) for q in quarters]

    for i, value in enumerate(values):
        multiple = multiples[i]
        if multiple is None:
            continue

        # 이 회사가 '평소' 크던 속도 — 자기 자신은 빼고 중앙값을 냅니다.
        # (자기를 포함하면 표본이 작을 때 자기가 중앙값이 되어 절대 안 걸립니다)
        if seasonal and fq_of[i] is not None:
            peers = [
                m for j, m in enumerate(multiples)
                if m is not None and j != i
                and fq_of[j] is not None and fq_of[j][1] == fq_of[i][1]
            ]
            # 같은 분기 짝이 2개는 있어야 '이 분기의 평소'라고 말할 수 있습니다.
            # 모자라면 판정하지 않습니다 — 계절 흐름을 오류로 지우는 것보다 낫습니다.
            if len(peers) < 2:
                continue
            others = peers
        else:
            others = [m for j, m in enumerate(multiples) if m is not None and j != i]
        if len(others) < 2:
            continue
        usual = statistics.median(others)
        if usual <= 0:
            continue

        # 조건 ① 이 회사 평소 속도에 견줘 유별났는가
        jumped = multiple / usual
        if 1.0 / cfg.ANOMALY_JUMP_RATIO <= jumped <= cfg.ANOMALY_JUMP_RATIO:
            continue    # 평소와 비슷하게 컸음 — 아무 일도 없음

        # 조건 ② 그 자체로도 크게 움직였는가
        # 이것이 없으면 폭증하는 회사의 정상 분기가 지워집니다 (config 설명 참고).
        big_move = (
            multiple >= cfg.ANOMALY_MIN_MOVE_RATIO
            or multiple <= 1.0 / cfg.ANOMALY_MIN_MOVE_RATIO
        )
        if not big_move:
            continue

        label = quarters[i].get("period_label", i)
        grew_pct = (multiple - 1) * 100
        usual_pct = (usual - 1) * 100
        after = [v for v in values[i + 1:i + 4] if _finite(v) and v > 0]
        previous_level = values[i - 1]
        went_up = multiple > usual

        # 뒤 분기들이 새 자리를 지키는가, 원래 자리로 되돌아오는가
        later = statistics.median(after) if after else None
        if later is None:
            returned = None
        elif went_up:
            returned = later <= previous_level * 1.3        # 뛰기 전으로 복귀
        else:
            returned = later >= previous_level * 0.7        # 떨어지기 전으로 복귀

        # ── 이익이 크게 늘어난 경우 ────────────────────────────────────────
        if went_up:
            # ⚠️ 가장 최근 분기는 **뒤가 없어서** 스파이크인지 계단인지 알 수 없습니다.
            #    그런데 하필 우리가 가장 궁금한 분기입니다.
            #    모르는 것을 아는 척하지 않고 '아직 알 수 없음'으로 두고 방향을 유보합니다.
            if returned is None:
                pending.append(i)
                reasons.append(
                    f"{label}: 이익이 {grew_pct:+.0f}% 로 유별나게 늘었습니다"
                    f" (이 회사가 평소 크던 속도는 {usual_pct:+.0f}%). 다음 분기가 아직 없어 "
                    "일시적인 것인지 새 수준으로 올라선 것인지 알 수 없습니다. "
                    "다음 실적발표까지 방향 판정을 미룹니다."
                )
            elif returned:
                spikes.append(i)
                reasons.append(
                    f"{label}: 이익이 {grew_pct:+.0f}% 로 튀었다가 곧바로 되돌아옵니다"
                    f" (평소 속도 {usual_pct:+.0f}%). 논갭 영업이익은 일회성 항목이 이미 빠진 "
                    "값이라 이런 모양이 나올 수 없으므로 **데이터 오류**로 보고 이 분기를 "
                    "계산에서 뺍니다."
                )
            else:
                steps.append(i)
                reasons.append(
                    f"{label}: 이익이 {grew_pct:+.0f}% 로 한 번 올라선 뒤 그 수준을 지킵니다"
                    f" (평소 속도 {usual_pct:+.0f}%). 인수합병 같은 구조 변화로 사업 규모 "
                    "자체가 커진 모양입니다. 커진 것은 진짜지만 '점점 빨라지는 중'은 아니므로 "
                    "그 한 번의 증가율만 판정에서 뺍니다."
                )
            continue

        # ── 이익이 크게 줄어든 경우 ────────────────────────────────────────
        # ⚠️ 여기서 한 번 더 조심해야 합니다.
        #    이익이 무너져 그대로 눌러앉는 것은 **우리가 가장 알아야 할 신호**입니다.
        #    이것까지 "구조 변화"라며 계산에서 빼면, 모델은 나빠지는 회사를 보고도
        #    아무 말을 하지 않게 됩니다. 그래서 아래로 꺾인 분기는
        #    **곧바로 원래대로 되돌아온 경우에만** 데이터 오류로 봅니다.
        if returned:
            spikes.append(i)
            reasons.append(
                f"{label}: 이익이 {grew_pct:+.0f}% 로 급감했다가 곧바로 원래대로 "
                f"돌아옵니다 (평소 속도 {usual_pct:+.0f}%). 한 분기만 푹 꺼졌다 복구되는 "
                "모양은 데이터 오류일 가능성이 높아 이 분기를 계산에서 뺍니다."
            )
        # 되돌아오지 않았거나(진짜 악화) 아직 알 수 없으면 **손대지 않습니다**.

    return {"spikes": spikes, "steps": steps, "pending": pending, "reasons": reasons}


def check_source_consistency(quarters: list[dict]) -> dict:
    """한 종목 안에서 '논갭 영업이익의 정의'가 섞였는지 봅니다.

    델타 모델은 값의 크기가 아니라 **값의 변화**를 봅니다. 그래서 절대 정확도보다
    한 종목 안에서 정의가 일관된 것이 훨씬 중요합니다.

    회사 공식 논갭 영업이익(직접공시)과 XBRL 근사치는 보통 5~20% 차이가 납니다.
    분기마다 출처가 왔다 갔다 하면 **실제로는 아무 변화가 없어도 증가율에
    ±20%p 짜리 가짜 신호**가 생깁니다. 델타 문턱이 3%p 인데 말입니다.

    반환: {"mixed", "counts", "switches", "reason"}
    """
    sources = [q.get("source") for q in quarters if q.get("source")]
    if len(sources) < 2:
        return {"mixed": False, "counts": {}, "switches": 0, "reason": ""}

    counts: dict[str, int] = {}
    for source in sources:
        counts[source] = counts.get(source, 0) + 1

    switches = sum(1 for a, b in zip(sources, sources[1:]) if a != b)
    mixed = len(counts) > 1

    reason = ""
    if mixed:
        breakdown = " · ".join(f"{k} {v}개" for k, v in counts.items())
        reason = (
            f"이 종목의 분기들이 서로 다른 방법으로 만들어졌습니다 ({breakdown}, "
            f"{switches}번 바뀜). 방법이 다르면 값의 기준이 달라져, 실제로는 변화가 "
            "없어도 증가율에 가짜 신호가 생깁니다. 델타 판정을 그대로 믿지 마세요."
        )

    return {"mixed": mixed, "counts": counts, "switches": switches, "reason": reason}


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
    # ⚠️ **적자 전환 전망은 버리면 안 됩니다.**
    #
    #   전망이 음수면 배수가 음수가 되어 하한(1/10)에 걸렸습니다. 그래서
    #   "다음 분기 적자 전환" 가이던스가 통째로 버려지고 '전망 없음'이 됐는데,
    #   '전망 없음'에는 중간 점수가 붙습니다. 결과가 이렇게 뒤집혔습니다.
    #
    #       -9% 소폭 감소 전망 → 2.8점
    #       적자 전환 전망     → 8.6점   ← 더 나쁜데 3배 높음
    #
    #   대시보드가 잡으려는 바로 그 신호(급격한 감속·적자 전환)가
    #   "자료 없음"으로 세탁되어 중간 점수를 받고 있었습니다.
    #
    #   적자 전환은 **계산이 깨진 것이 아니라 진짜 나쁜 소식**입니다.
    #   크기만 현실적이면 그대로 씁니다 (위의 절대 크기 검사는 이미 지났습니다).
    if forward_op <= 0:
        if abs(forward_op) > latest_op * cfg.FORWARD_MAX_MULTIPLE:
            return False, (
                f"적자 전망 ${forward_op:,.0f} 이 최근 실적의 "
                f"{cfg.FORWARD_MAX_MULTIPLE:.0f}배를 넘습니다"
            )
        return True, ""

    ratio = forward_op / latest_op
    if ratio > cfg.FORWARD_MAX_MULTIPLE or ratio < cfg.FORWARD_MIN_RATIO:
        return False, (
            f"전망이 최근 실적의 {ratio:,.1f}배로 "
            f"{cfg.FORWARD_MAX_MULTIPLE:.0f}배 한도를 벗어납니다"
        )
    return True, ""
