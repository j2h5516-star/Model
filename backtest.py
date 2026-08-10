"""
backtest.py — 모델이 과거에 실제로 맞았는지 되짚어 보기
=======================================================

**무엇을 하는가**

분기 실적 이력을 시간 순서대로 훑으면서, 각 시점에서 **그때까지의 자료만**
가지고 모델을 돌려 다음 분기를 예측하고, 실제로 어떻게 됐는지와 맞춰 봅니다.

    분기 1~4 만 보고 → 5분기 예측 → 실제 5분기와 비교
    분기 1~5 만 보고 → 6분기 예측 → 실제 6분기와 비교
    ...

이렇게 하면 "지금 화면에 보이는 판정이 과거에는 얼마나 맞았나"를 숫자로 볼 수 있습니다.

**정직하게 밝히는 한계**

  · 이 개발 환경은 SEC 접속이 막혀 있어 **실제 기업 데이터를 쓸 수 없습니다.**
    그래서 기본 시나리오는 사람이 만든 가상 데이터입니다.
    가상 데이터로 재는 것은 "시장에서 얼마나 맞나"가 아니라
    **"설계한 대로 동작하나"** 입니다. 둘은 다릅니다.
  · 실제 데이터가 있으면(`data/` 폴더의 캐시) 그것으로도 돌릴 수 있습니다.
  · 미래를 미리 본 적 없음(look-ahead 없음)은 코드로 보장합니다 —
    각 시점에서 quarters[:t] 만 넘깁니다.
"""

from __future__ import annotations

import math

import config as cfg
import data_quality as dq
import scoring


# 방향 잣대의 무시 구간(%p) — 이보다 작은 변화는 "그대로"로 봅니다
DIRECTION_EPS_PP = 0.5


def _label(change: float, threshold: float) -> str:
    """증가율 변화를 가속/감속/유지 중 하나로 딱 하나만 정해 줍니다."""
    if change > threshold:
        return cfg.D_ACCEL
    if change < -threshold:
        return cfg.D_DECEL
    return cfg.D_STEADY


def wilson_interval(hits: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """적중률의 95% 신뢰구간(%). 표본이 적을 때 한 숫자만 믿지 않도록 함께 봅니다."""
    if total <= 0:
        return (0.0, 0.0)
    p = hits / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, (center - half) * 100.0), min(100.0, (center + half) * 100.0))


# ---------------------------------------------------------------------------
# 한 종목의 이력을 시간 순서대로 되짚기
# ---------------------------------------------------------------------------
def walk_forward(quarters: list[dict], min_history: int = 4) -> list[dict]:
    """각 시점에서 '그때까지의 자료만'으로 판정하고 실제와 비교합니다.

    반환: 시점별 기록 목록
      {"as_of", "direction", "actual_next_qoq", "direction_correct", ...}
    """
    records: list[dict] = []
    if len(quarters) <= min_history:
        return records

    for cut in range(min_history, len(quarters)):
        past = quarters[:cut]          # ← 여기까지만 본다 (미래를 미리 보지 않음)
        actual_next = quarters[cut]

        delta = scoring.score_delta_acceleration(past)
        growths = delta.get("trace", {}).get("growths", [])
        last_qoq = growths[-1] if growths else None

        # 실현 증가율은 **모델이 쓴 기준과 같은 기준**으로 계산합니다.
        # 계절 장사는 작년 같은 분기와 비교(YoY)하도록 판정되므로,
        # 채점도 같은 기준으로 해야 앞뒤가 맞습니다.
        seasonal = delta.get("trace", {}).get("seasonal", False)
        base_index = -4 if seasonal else -1
        base = past[base_index] if len(past) >= abs(base_index) else None
        realized = (
            dq.safe_growth_pct(base.get("op_income"), actual_next.get("op_income"))
            if base is not None else None
        )

        # 판정이 맞았는가? — 두 가지 잣대로 함께 잽니다.
        #
        # 세 판정이 **겹치지 않게** 채점합니다. (예전에는 '가속=올랐으면 정답',
        #  '유지=±3%p 이내면 정답'이라 +2%p 같은 결과에 가속과 유지가 동시에
        #  정답이 됐고, 아무 계산도 않고 '유지'만 답해도 52%가 나왔습니다.)
        #
        #   ① 엄격(±3%p)  — 모델이 스스로 정한 문턱만큼 실제로 움직였는가
        #   ② 방향(±0.5%p) — 문턱까지는 아니어도 주장한 방향으로 움직였는가
        #
        # 두 잣대를 다 보여 주는 이유: 모델은 한 분기 변화가 3%p에 못 미쳐도
        # 여러 분기 추세가 우상향이면 '가속'이라 말하도록 설계돼 있습니다.
        # 엄격 잣대만 보면 그 설계가 통째로 오답이 되고, 방향 잣대만 보면
        # 너무 후해집니다. 둘 다 보여 주는 편이 정직합니다.
        strict_correct = direction_correct = None
        if realized is not None and last_qoq is not None:
            change = realized - last_qoq
            said = delta["direction"]
            if said in (cfg.D_ACCEL, cfg.D_DECEL, cfg.D_STEADY):
                strict_correct = said == _label(change, cfg.DELTA_THRESHOLD_PP)
                direction_correct = said == _label(change, DIRECTION_EPS_PP)
            # 혼조·판단불가는 맞고 틀림을 따지지 않습니다 (판단을 유보한 것이므로)

        records.append(
            {
                "as_of": past[-1].get("period_label", f"#{cut}"),
                "direction": delta["direction"],
                "last_qoq": last_qoq,
                "actual_next_qoq": realized,
                "change": (realized - last_qoq)
                if (realized is not None and last_qoq is not None) else None,
                "strict_correct": strict_correct,
                "direction_correct": direction_correct,
            }
        )

    return records


def summarize(records: list[dict]) -> dict:
    """되짚기 기록을 요약합니다.

    적중률 하나만 크게 내놓지 않습니다. 표본이 적으면 그 숫자는 흔들리고,
    아무 계산도 하지 않는 답안("항상 유지")이 비슷한 점수를 낼 수도 있기 때문입니다.
    그래서 **원분수 · 신뢰구간 · 기준선**을 함께 돌려줍니다.
    """
    judged = [r for r in records if r["strict_correct"] is not None]
    n = len(judged)
    strict_hits = sum(1 for r in judged if r["strict_correct"])
    dir_hits = sum(1 for r in judged if r["direction_correct"])

    by_direction: dict[str, dict] = {}
    for record in judged:
        bucket = by_direction.setdefault(record["direction"], {"판정수": 0, "적중": 0})
        bucket["판정수"] += 1
        bucket["적중"] += 1 if record["strict_correct"] else 0
    for bucket in by_direction.values():
        bucket["적중률(%)"] = round(bucket["적중"] / bucket["판정수"] * 100.0, 1)

    # 기준선 — 아무 계산도 하지 않고 한 가지 답만 계속 내는 답안의 성적.
    # 모델이 이보다 못하면 그 판정은 의미가 없습니다.
    baselines: dict[str, int] = {}
    for fixed in (cfg.D_ACCEL, cfg.D_DECEL, cfg.D_STEADY):
        baselines[f"항상 '{fixed}'"] = sum(
            1 for r in judged
            if _label(r["change"], cfg.DELTA_THRESHOLD_PP) == fixed
        )

    low, high = wilson_interval(strict_hits, n)

    return {
        "시점수": len(records),
        "판정한 시점": n,
        "적중": strict_hits,
        "적중(원분수)": f"{strict_hits}/{n}" if n else "0/0",
        "적중률(%)": round(strict_hits / n * 100.0, 1) if n else None,
        "적중률 95%구간": (round(low, 1), round(high, 1)) if n else None,
        "방향만 맞춘 비율(%)": round(dir_hits / n * 100.0, 1) if n else None,
        "기준선(무계산 답안)": {
            name: {"적중": hits, "적중률(%)": round(hits / n * 100.0, 1) if n else None}
            for name, hits in baselines.items()
        },
        "유보(혼조·판단불가)": len(records) - n,
        "방향별": by_direction,
    }


def run(quarters_by_ticker: dict[str, list[dict]], min_history: int = 4) -> dict:
    """여러 종목을 한꺼번에 되짚어 보고 종목별·전체 요약을 돌려줍니다."""
    per_ticker: dict[str, dict] = {}
    all_records: list[dict] = []

    for ticker, quarters in quarters_by_ticker.items():
        records = walk_forward(quarters, min_history=min_history)
        per_ticker[ticker] = summarize(records)
        all_records.extend(records)

    overall = summarize(all_records)

    # 시점 21개는 서로 독립이 아닙니다 (한 종목의 겹치는 창에서 나옵니다).
    # 진짜 독립 단위는 '종목'이므로 종목별 적중률의 평균도 함께 봅니다.
    rates = [s["적중률(%)"] for s in per_ticker.values() if s["적중률(%)"] is not None]
    overall["종목평균 적중률(%)"] = round(sum(rates) / len(rates), 1) if rates else None
    overall["독립 종목 수"] = len(rates)

    return {"전체": overall, "종목별": per_ticker}


# ---------------------------------------------------------------------------
# 가상 시나리오 만들기 (실데이터를 쓸 수 없을 때)
# ---------------------------------------------------------------------------
def make_series(op_incomes: list[float], label_prefix: str = "Q") -> list[dict]:
    """영업이익 목록을 분기 목록으로 바꿉니다 (매출은 마진 25%로 역산)."""
    quarters = []
    for index, op in enumerate(op_incomes, start=1):
        quarters.append(
            {
                "period_label": f"{label_prefix}{index}",
                "filing_date": f"20{20 + index // 4:02d}-{((index - 1) % 4) * 3 + 1:02d}-01",
                "op_income": op,
                "revenue": op / 0.25 if op > 0 else None,
                "gross_margin_pct": 55.0,
                "source": cfg.SRC_DIRECT,
            }
        )
    return quarters


def standard_scenarios() -> dict[str, list[dict]]:
    """모델이 설계대로 도는지 보기 위한 표준 시나리오 묶음."""
    M = 1_000_000
    return {
        # 증가율이 계속 커짐 → '가속'이 맞아야 함
        "꾸준한가속": make_series(
            [100 * M, 112 * M, 128 * M, 150 * M, 180 * M, 220 * M, 275 * M, 350 * M]
        ),
        # 증가율이 계속 작아짐 → '감속'이 맞아야 함
        "꾸준한감속": make_series(
            [100 * M, 140 * M, 175 * M, 200 * M, 215 * M, 224 * M, 229 * M, 231 * M]
        ),
        # 같은 속도 → '유지'
        "일정성장": make_series([100 * M * (1.1 ** i) for i in range(8)]),
        # 한 분기만 튀는 잡음 → 혼조가 나와야 하고, 가속으로 단정하면 안 됨
        "한분기잡음": make_series(
            [100 * M, 110 * M, 121 * M, 190 * M, 133 * M, 146 * M, 161 * M, 177 * M]
        ),
        # 계절성 (4분기마다 반복)
        "계절성": make_series(
            [100 * M, 130 * M, 90 * M, 160 * M, 110 * M, 143 * M, 99 * M, 176 * M]
        ),
        # 적자에서 흑자 전환
        "턴어라운드": make_series(
            [-30 * M, -15 * M, -5 * M, 8 * M, 25 * M, 45 * M, 70 * M, 100 * M]
        ),
    }


# ---------------------------------------------------------------------------
# 적대적 시나리오 — 모델을 깨뜨리려고 만든 것
# ---------------------------------------------------------------------------
# 표준 시나리오는 "설계대로 도는가"를 봅니다. 아래 시나리오는 반대로
# **"어떤 모양의 실적에서 모델이 사람과 다른 말을 하는가"** 를 찾기 위한 것입니다.
#
# 각 항목의 `expect` 는 **사람이 보기에 타당한 답**입니다. 모델의 판정이 이것과
# 어긋나면 적중률과 무관하게 결함으로 봅니다 (적중률이 높아도 이유가 틀렸을 수
# 있고, 적중률이 낮아도 정직하게 유보한 것일 수 있기 때문입니다).


def make_series_ex(
    op_incomes: list[float],
    revenues: list[float] | None = None,
    labels: list[str] | None = None,
    margin: float = 0.25,
    source: str | None = None,
) -> list[dict]:
    """make_series 의 확장판 — 매출·분기이름을 직접 지정할 수 있습니다.

    단위 오류·분기 결측 같은 시나리오는 매출을 따로 넣거나 분기를 빼야
    만들 수 있어서, 매출을 마진으로만 역산하는 make_series 로는 표현할 수 없습니다.
    """
    quarters = []
    for offset, op in enumerate(op_incomes):
        index = offset + 1
        label = labels[offset] if labels else f"Q{index}"
        if revenues is not None:
            revenue = revenues[offset]
        else:
            revenue = op / margin if (op is not None and op > 0) else None
        quarters.append(
            {
                "period_label": label,
                "filing_date": f"20{20 + index // 4:02d}-{((index - 1) % 4) * 3 + 1:02d}-01",
                "op_income": op,
                "revenue": revenue,
                "gross_margin_pct": 55.0,
                "source": source or cfg.SRC_DIRECT,
            }
        )
    return quarters


def adversarial_scenarios() -> dict[str, dict]:
    """모델을 깨뜨리려고 만든 시나리오 묶음.

    반환: {이름: {"quarters": [...], "expect": "사람이 보기에 타당한 답"}}
    """
    M = 1_000_000
    scenarios: dict[str, dict] = {}

    # ① 인수합병 — 매출·이익이 한 분기 만에 2배. 유기적 성장이 아닙니다.
    scenarios["인수합병_2배점프"] = {
        "quarters": make_series_ex(
            [100 * M, 105 * M, 110 * M, 116 * M, 232 * M, 244 * M, 256 * M, 269 * M]
        ),
        "expect": "합병 분기의 +100%는 유기적 가속이 아니므로 '가속' 단정 금지. "
                  "합병 후에도 분기 +5% 성장이 유지되므로 '감속'은 더더욱 틀림",
    }

    # ② 일회성 이익 — 소송 합의금 등으로 한 분기만 급등했다가 복귀
    scenarios["일회성이익_한분기"] = {
        "quarters": make_series_ex(
            [100 * M, 106 * M, 112 * M, 119 * M, 300 * M, 126 * M, 134 * M, 142 * M]
        ),
        "expect": "일회성 스파이크 앞뒤로 '가속'과 '감속'을 번갈아 단정하면 안 됨. "
                  "본업은 내내 분기 +6%",
    }

    # ③ 일회성 손실 — 구조조정비로 한 분기만 급락했다가 복귀
    scenarios["일회성손실_한분기"] = {
        "quarters": make_series_ex(
            [100 * M, 106 * M, 112 * M, 119 * M, 20 * M, 126 * M, 134 * M, 142 * M]
        ),
        "expect": "손실 분기 다음의 +530%는 '회복'이지 '가속'이 아님",
    }

    # ④ 저가 기저 — 이익 $1M → $3M. 증가율 +200%지만 의미 없음
    scenarios["저가기저_1M에서3M"] = {
        "quarters": make_series_ex([0.8 * M, 1.0 * M, 1.5 * M, 2.0 * M, 3.0 * M]),
        "expect": "금액이 $3M — 증가율이 커도 '가속 · 매수 후보'로 올리면 안 됨",
    }

    # ⑤ 적자 ↔ 흑자 왕복
    scenarios["적자흑자_왕복"] = {
        "quarters": make_series_ex(
            [-20 * M, 15 * M, -10 * M, 25 * M, -5 * M, 30 * M, -12 * M, 35 * M]
        ),
        "expect": "흑자와 적자를 오가면 QoQ 증가율 자체가 성립하지 않음 → '판단불가/혼조'가 정직",
    }

    # ⑥ 적자 한 분기가 끼어든 정상 성장 (증가율은 오히려 둔화 중)
    scenarios["적자1회후_성장둔화"] = {
        "quarters": make_series_ex(
            [100 * M, -5 * M, 50 * M, 55 * M, 60 * M, 65 * M]
        ),
        "expect": "이후 증가율이 +10% → +9.1% → +8.3% 로 낮아지므로 '가속' 만점은 틀림",
    }

    # ⑦ 분기 결측 — 10-K 연간만 신고돼 Q4 역산에 실패한 상태
    ops = [100 * M, 110 * M, 121 * M, 133 * M, 146 * M, 161 * M, 177 * M, 195 * M]
    labels = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"]
    del ops[3], labels[3]
    scenarios["분기결측_Q4역산실패"] = {
        "quarters": make_series_ex(ops, labels=labels),
        "expect": "Q3→Q5 는 반년 증가율이므로 분기 증가율로 쓰면 안 됨 → 결측을 알리고 유보",
    }

    # ⑧ 매출 단위 오류 (아래로) — 표의 '백만 단위' 표기를 놓친 경우
    quarters = make_series_ex([100 * M, 110 * M, 121 * M, 133 * M, 146 * M, 161 * M])
    quarters[3]["revenue"] = quarters[3]["revenue"] / 1_000_000
    scenarios["매출단위오류_아래로"] = {
        "quarters": quarters,
        "expect": "검사 단계가 백만 배를 되돌려 고쳐야 함",
    }

    # ⑧-b 매출 단위 오류 (위로) — 천 단위 표를 절대금액으로 읽은 경우
    quarters = make_series_ex([100 * M, 110 * M, 121 * M, 133 * M, 146 * M, 161 * M])
    quarters[5]["revenue"] = quarters[5]["revenue"] * 1_000
    scenarios["매출단위오류_위로"] = {
        "quarters": quarters,
        "expect": "검사 단계가 막아야 함. 막지 못하면 매출 YoY 가 +146,263% 로 화면에 나감",
    }

    # ⑧-c 영업이익 단위 오류 — 달러 단위가 유실된 분기
    quarters = make_series_ex([100 * M, 110 * M, 121 * M, 133 * M, 146 * M, 161 * M])
    quarters[3]["op_income"] = 133.0
    scenarios["영업이익단위오류"] = {
        "quarters": quarters,
        "expect": "$121M 옆의 $133 은 명백히 단위 오류 → 검사 단계가 잡아야 함. "
                  "놓치면 가짜 −100% 가 증가율 이력에 남음",
    }

    # ⑨ 정체 후 급반등
    scenarios["정체후급반등"] = {
        "quarters": make_series_ex(
            [100 * M, 101 * M, 100 * M, 102 * M, 101 * M, 150 * M, 210 * M, 290 * M]
        ),
        "expect": "반등이 3분기 이어졌으므로 '가속'은 타당",
    }

    # ⑩ 급성장 후 절벽
    scenarios["급성장후절벽"] = {
        "quarters": make_series_ex(
            [100 * M, 140 * M, 200 * M, 280 * M, 380 * M, 150 * M, 140 * M, 135 * M]
        ),
        "expect": "절벽 뒤에는 '감속', 그리고 매출이 줄고 있으므로 "
                  "'매출 증가 속도가 빨라지는 중'이라고 말하면 안 됨",
    }

    # ⑪ 계절성 + 성장 (QoQ로는 지그재그, YoY로는 +15%)
    scenarios["계절성_성장동반"] = {
        "quarters": make_series_ex(
            [100 * M, 130 * M, 90 * M, 170 * M, 115 * M, 150 * M, 104 * M, 196 * M]
        ),
        "expect": "계절 패턴이면 QoQ 방향 단정 금지 — 성수기 분기에 만점(40/40)을 주면 안 됨",
    }

    return scenarios


def run_adversarial(min_history: int = 4) -> dict:
    """적대적 시나리오를 돌리고, 시나리오별 판정 이력과 요약을 돌려줍니다.

    ⚠️ 적중률만 보지 마세요. 이 시나리오들은 "적중률이 높아도 이유가 틀린" 경우를
    잡으려고 만든 것이라, 각 시나리오의 `expect`(사람이 보기에 타당한 답)와
    `directions`(모델이 실제로 한 말)를 나란히 읽어야 의미가 있습니다.
    """
    result: dict[str, dict] = {}
    for name, info in adversarial_scenarios().items():
        records = walk_forward(info["quarters"], min_history=min_history)
        result[name] = {
            "expect": info["expect"],
            "directions": [(r["as_of"], r["direction"]) for r in records],
            "요약": summarize(records),
            "records": records,
        }
    return result
