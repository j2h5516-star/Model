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

import config as cfg
import data_quality as dq
import scoring


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

        realized = dq.safe_growth_pct(
            past[-1].get("op_income"), actual_next.get("op_income")
        )

        # 판정이 맞았는가?
        #
        # '가속'이라는 말은 "증가율이 더 올라간다"는 뜻이므로, 다음 분기 증가율이
        # 직전보다 **올랐는지**로 채점합니다. (예전에는 "3%p 넘게 올라야 정답"으로
        # 채점했는데, 모델은 3%p 미만이라도 여러 분기 추세가 우상향이면 '가속'이라
        # 말하도록 설계돼 있어 기준이 서로 어긋났습니다. 채점 기준을 모델의
        # 주장에 맞췄습니다.)
        correct = None
        if realized is not None and last_qoq is not None:
            change = realized - last_qoq
            if delta["direction"] == cfg.D_ACCEL:
                correct = change > 0
            elif delta["direction"] == cfg.D_DECEL:
                correct = change < 0
            elif delta["direction"] == cfg.D_STEADY:
                correct = abs(change) <= cfg.DELTA_THRESHOLD_PP
            # 혼조·판단불가는 맞고 틀림을 따지지 않습니다 (판단을 유보한 것이므로)

        records.append(
            {
                "as_of": past[-1].get("period_label", f"#{cut}"),
                "direction": delta["direction"],
                "last_qoq": last_qoq,
                "actual_next_qoq": realized,
                "direction_correct": correct,
            }
        )

    return records


def summarize(records: list[dict]) -> dict:
    """되짚기 기록을 사람이 읽을 수 있는 요약으로 만듭니다."""
    judged = [r for r in records if r["direction_correct"] is not None]
    hits = sum(1 for r in judged if r["direction_correct"])

    by_direction: dict[str, dict] = {}
    for record in judged:
        bucket = by_direction.setdefault(
            record["direction"], {"판정수": 0, "적중": 0}
        )
        bucket["판정수"] += 1
        bucket["적중"] += 1 if record["direction_correct"] else 0

    for bucket in by_direction.values():
        bucket["적중률(%)"] = round(bucket["적중"] / bucket["판정수"] * 100.0, 1)

    return {
        "시점수": len(records),
        "판정한 시점": len(judged),
        "적중": hits,
        "적중률(%)": round(hits / len(judged) * 100.0, 1) if judged else None,
        "유보(혼조·판단불가)": len(records) - len(judged),
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

    return {"전체": summarize(all_records), "종목별": per_ticker}


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
