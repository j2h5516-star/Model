"""
judge.py — 자동 판정 (v3 5단계, 설계도.md ③)
=============================================

측정 장치가 만든 사건 목록에 **11차 사전 등록의 채택 기준**을 적용해
data/measure/verdict.json 으로 기록합니다. 수집 로봇이 매일 수집 성공
직후 이것을 돌립니다 — 사람이 재판정을 미루거나 유리하게 고를 여지가
없습니다 (헌법 2장 제3조).

판정 규칙 (측정결과.md 11차 — 옮겨 적음):
  · 판정 표본 = 신규 종목(발견 29종목 제외)의 발표 사건. 전체는 참고
  · 채택 = 신호 윌슨 95% 하한 > 같은 표본 기준선(모든 발표) 윌슨 상한,
    그리고 신호 n≥10. n<10 이면 "판정 불가" — 억지로 결론 내지 않는다
  · 앞/뒤 시간 분할(발표일순 반분)을 항상 병기
  · H5b·H6 는 게이지 판단 불가(워밍업 부족) 사건을 표본에서 제외

경계 (설계도.md 3장): 측정 규칙은 측정 장치가, 판정 기준은 등록 문서가
소유합니다. 이 파일은 등록된 기준을 **적용만** 합니다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import config as cfg
import measure_engine as me

MIN_SIGNAL_N = 10   # 사전 등록: 이보다 작으면 "판정 불가"

# 가설 정의 (11차 등록): 이름 → (표본 필터, 신호 조건)
#   표본 필터: 이 가설에서 판단 가능한 사건인가 (H5b·H6 워밍업 제외 규칙)
#   신호 조건: 그 표본 안에서 신호 그룹에 드는가
HYPOTHESES: dict[str, tuple] = {
    "H2_신고점": (lambda e: True, lambda e: e["new_high"]),
    "H2b_신고점_첫돌파": (lambda e: True, lambda e: e["newhigh_streak"] == 1),
    "H5_실적폭_고정20": (lambda e: e["h5"] is not None, lambda e: e["h5"] is True),
    "H5b_실적폭_중앙값": (lambda e: e["h5b"] is not None, lambda e: e["h5b"] is True),
    "H6_결합_H5bxH2b": (
        lambda e: e["h5b"] is not None,
        lambda e: e["h5b"] is True and e["newhigh_streak"] == 1,
    ),
}


def wilson_interval(hits: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """윌슨 신뢰구간(%) — 표본이 작을 때도 과신하지 않는 적중률 구간."""
    if total == 0:
        return (0.0, 0.0)
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (z / denominator) * (
        (p * (1 - p) / total + z * z / (4 * total ** 2)) ** 0.5
    )
    return (max(0.0, centre - margin) * 100.0, min(1.0, centre + margin) * 100.0)


def _stats(group: list[dict]) -> dict:
    n = len(group)
    hits = sum(1 for e in group if e["excess"] >= me.SURGE_PP)
    low, high = wilson_interval(hits, n)
    return {
        "n": n,
        "rate": round(hits / n * 100.0, 1) if n else None,
        "ci": [round(low, 1), round(high, 1)],
    }


def _judge(signal: list[dict], baseline: list[dict]) -> dict:
    """등록된 채택 기준 적용: 신호 하한 > 기준선 상한, n≥10."""
    signal_stats = _stats(signal)
    base_stats = _stats(baseline)
    if signal_stats["n"] < MIN_SIGNAL_N:
        verdict = "판정 불가"     # 표본 부족 — 기다린다 (11차 등록)
    elif signal_stats["ci"][0] > base_stats["ci"][1]:
        verdict = "채택"
    else:
        verdict = "미채택"
    return {"신호": signal_stats, "기준선": base_stats, "판정": verdict}


def _halves(events: list[dict]) -> tuple[list[dict], list[dict]]:
    ordered = sorted(events, key=lambda e: e["announced"])
    half = len(ordered) // 2
    return ordered[:half], ordered[half:]


def run(events: list[dict]) -> dict:
    """사건 목록 전체에 등록된 가설을 판정합니다."""
    new_events = [
        e for e in events if e["ticker"] not in cfg.MEASURE_DISCOVERY_TICKERS
    ]
    samples = {"신규(판정)": new_events, "전체(참고)": events}

    out: dict = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "규칙": (
            "11차 사전 등록 — 채택 = 신호 윌슨 하한 > 기준선 윌슨 상한 "
            "(신규 표본, n≥10. n<10 은 판정 불가)"
        ),
        "표본": {name: len(group) for name, group in samples.items()},
        "가설": {},
    }
    for h_name, (eligible, condition) in HYPOTHESES.items():
        entry: dict = {}
        for s_name, group in samples.items():
            pool = [e for e in group if eligible(e)]
            entry[s_name] = _judge([e for e in pool if condition(e)], pool)
        judged_pool = [e for e in new_events if eligible(e)]
        front, back = _halves(judged_pool)
        entry["신규_앞시기"] = _stats([e for e in front if condition(e)])
        entry["신규_뒤시기"] = _stats([e for e in back if condition(e)])
        entry["판정"] = entry["신규(판정)"]["판정"]
        out["가설"][h_name] = entry
    return out


def to_json(verdict: dict) -> str:
    return json.dumps(verdict, ensure_ascii=False, indent=1)
