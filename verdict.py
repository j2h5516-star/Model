"""
verdict.py — 자동 판정: 사전 등록 가설을 데이터가 매일 스스로 재판정
====================================================================

전략.md v2 로드맵 6단계. 수집 로봇이 수집 직후 이 판정을 돌려
data/measure/verdict.json 으로 커밋하고, 계기판이 그대로 표시합니다.
사람이 재판정을 미루거나 유리하게 고를 여지가 없습니다.

판정 규칙 (측정결과.md 7차 사전 등록 — 새 종목 데이터를 보기 전에 고정):

  · 판정 표본 = **신규 종목**(발견에 쓰인 29종목 제외)의 발표 사건만.
    신호가 본 적 없는 종목의 과거 = 교차 방향의 새 데이터입니다.
  · 가설: H2(TTM 신고점) · H2b(신고점 **첫** 돌파) · H5(실적 폭 ≥20%)
  · 채택 = 신호 그룹 윌슨 **하한** > 같은 표본 기준선(모든 발표) 윌슨 **상한**
  · 한계 명시: 신규 종목도 같은 장세를 공유 — 시간 방향 검증은 별개로 계속.
"""

from __future__ import annotations

import bisect
import json
from datetime import datetime, timezone

import breadth
import config as cfg
import measurement as mm


def _stats(group: list[dict]) -> dict:
    n = len(group)
    hits = sum(1 for e in group if e["excess"] >= mm.SURGE_PP)
    low, high = mm.wilson_interval(hits, n)
    return {
        "n": n,
        "rate": round(hits / n * 100.0, 1) if n else None,
        "ci": [round(low, 1), round(high, 1)],
    }


def _judge(signal: list[dict], baseline: list[dict]) -> dict:
    """사전 등록된 채택 기준: 신호 하한 > 기준선 상한 (구간 완전 분리)."""
    signal_stats = _stats(signal)
    base_stats = _stats(baseline)
    adopted = (
        signal_stats["n"] >= 10                       # 최소한의 표본은 있어야 판정
        and signal_stats["ci"][0] > base_stats["ci"][1]
    )
    return {"신호": signal_stats, "기준선": base_stats, "채택": adopted}


def _halves(events: list[dict]):
    ordered = sorted(events, key=lambda e: e["announced"])
    half = len(ordered) // 2
    return ordered[:half], ordered[half:]


def run(snapshot: dict) -> dict:
    """스냅샷 하나로 전 가설을 판정합니다."""
    events, _skipped = mm.collect_events(snapshot)

    # H5 조건(장세 게이지)은 전체 유니버스의 폭으로 계산합니다
    series = breadth.build_series(snapshot)
    weeks, gauge_values = series["week"], series["newhigh_pct"]

    def gauge_on(announce: str) -> bool | None:
        index = bisect.bisect_right(weeks, announce) - 1
        if index < 0:
            return None
        return gauge_values[index] >= cfg.BREADTH_ON_PCT

    hypotheses = {
        "H2_신고점": lambda e: e["new_high"],
        "H2b_신고점_첫돌파": lambda e: e.get("newhigh_streak") == 1,
        "H5_실적폭_ON": lambda e: gauge_on(e["announced"]) is True,
    }

    new_events = [e for e in events if e["ticker"] not in cfg.MEASURE_DISCOVERY_TICKERS]
    samples = {"신규(판정)": new_events, "전체(참고)": events}

    out: dict = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "규칙": "채택 = 신호 윌슨 하한 > 기준선 윌슨 상한 (신규 표본, n≥10)",
        "표본": {name: len(group) for name, group in samples.items()},
        "가설": {},
    }
    for h_name, condition in hypotheses.items():
        entry: dict = {}
        for s_name, group in samples.items():
            entry[s_name] = _judge([e for e in group if condition(e)], group)
        front, back = _halves(new_events)
        entry["신규_앞시기"] = _stats([e for e in front if condition(e)])
        entry["신규_뒤시기"] = _stats([e for e in back if condition(e)])
        entry["판정"] = "채택" if entry["신규(판정)"]["채택"] else "미채택"
        out["가설"][h_name] = entry
    return out


if __name__ == "__main__":
    with open(f"{cfg.MEASURE_DIR}/snapshot.json", encoding="utf-8") as f:
        snap = json.load(f)
    result = run(snap)
    with open(f"{cfg.MEASURE_DIR}/verdict.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    for name, entry in result["가설"].items():
        judged = entry["신규(판정)"]
        print(f"{name}: {entry['판정']} — 신호 {judged['신호']['rate']}% "
              f"{judged['신호']['ci']} vs 기준선 {judged['기준선']['rate']}% "
              f"{judged['기준선']['ci']} (n={judged['신호']['n']})")
