"""
exploration.py — 탐색 측정: 어떤 공시 데이터가 주가 상승과 같이 움직이는가
==========================================================================

⚠️ **이 파일의 좋은 숫자는 채택 근거가 아닙니다.**
   요인 여러 개를 한꺼번에 재면 우연히 좋아 보이는 것이 반드시 나옵니다
   (동전 열 개를 던지면 하나쯤은 앞면만 나옵니다). 그래서:

   1. 여기서 좋아 보인 요인은 **새 가설로 사전 등록**하고,
   2. **새로 쌓이는 데이터**로 다시 확인한 뒤에만 화면에 올립니다.
   3. 잰 요인은 좋든 나쁘든 **전부** 기록합니다 (좋은 것만 골라 보고 금지).

시간 분할: 사건을 발표일 순으로 앞/뒤 절반으로 나눠, 두 쪽 모두에서
방향이 유지되는지 봅니다. 한쪽에서만 좋은 요인은 우연일 가능성이 큽니다.

구간 경계는 데이터를 보기 전에 아래 상수로 고정했습니다.
"""

from __future__ import annotations

import json

import measurement as mm

# 구간 경계 — 실행 전에 고정 (바꾸려면 근거와 함께 새 판으로)
TTM_YOY_HI = 40.0     # TTM 이익이 1년 전보다 40% 이상 = 고성장
TTM_YOY_LO = 10.0     # 10% 미만 = 저성장
QOQ_HI = 10.0
QOQ_LO = -10.0


def _bucket_growth(value, hi, lo) -> str:
    if value is None:
        return "없음"
    if value >= hi:
        return "높음"
    if value < lo:
        return "낮음"
    return "중간"


# 요인 정의: 이름 → (사건 → 그룹 이름)
FACTORS = {
    "델타 방향": lambda e: e["direction"],
    "TTM 신고점": lambda e: "돌파" if e["new_high"] else "아님",
    "TTM YoY 성장률": lambda e: _bucket_growth(e["ttm_yoy"], TTM_YOY_HI, TTM_YOY_LO),
    "분기 YoY": lambda e: _bucket_growth(e["q_yoy"], TTM_YOY_HI, TTM_YOY_LO),
    "분기 QoQ": lambda e: _bucket_growth(e["q_qoq"], QOQ_HI, QOQ_LO),
    "TTM 흑자전환": lambda e: "전환" if e["turnaround"] else "아님",
    "매출 TTM YoY": lambda e: _bucket_growth(e["rev_ttm_yoy"], TTM_YOY_HI, TTM_YOY_LO),
    "매출 TTM 신고점": lambda e: (
        "없음" if e["rev_new_high"] is None else ("돌파" if e["rev_new_high"] else "아님")
    ),
}


def explore(snapshot: dict) -> dict:
    events, skipped = mm.collect_events(snapshot)
    events.sort(key=lambda e: e["announced"])
    half = len(events) // 2
    first, second = events[:half], events[half:]
    base_all = mm._group_stats(events)

    out = {
        "사건수": len(events),
        "건너뜀": skipped,
        "기준선_모든발표": base_all,
        "시간분할": {
            "앞시기": (events[0]["announced"], events[half - 1]["announced"]) if half else None,
            "뒤시기": (events[half]["announced"], events[-1]["announced"]) if half else None,
        },
        "요인": {},
    }

    for name, key_fn in FACTORS.items():
        groups: dict[str, dict] = {}
        for label in sorted({key_fn(e) for e in events}):
            grp = [e for e in events if key_fn(e) == label]
            stats = mm._group_stats(grp)
            stats["앞시기"] = mm._group_stats([e for e in first if key_fn(e) == label])
            stats["뒤시기"] = mm._group_stats([e for e in second if key_fn(e) == label])
            groups[label] = stats
        out["요인"][name] = groups
    return out


def print_report(result: dict) -> None:
    base = result["기준선_모든발표"]
    print(f"사건 {result['사건수']}건 · 기준선(모든 발표) 폭등률 {base['rate20']}% "
          f"[{base['wilson20'][0]:.1f}~{base['wilson20'][1]:.1f}] · 건너뜀 {result['건너뜀']}")
    print(f"시간분할: 앞 {result['시간분할']['앞시기']} / 뒤 {result['시간분할']['뒤시기']}")
    for name, groups in result["요인"].items():
        print(f"\n■ {name}")
        for label, s in groups.items():
            if not s["n"]:
                continue
            lo, hi = s["wilson20"]
            front = s["앞시기"]["rate20"]
            back = s["뒤시기"]["rate20"]
            print(f"  {label:8s} n={s['n']:3d}  폭등 {s['rate20']:5.1f}% [{lo:4.1f}~{hi:5.1f}]"
                  f"  중앙 {s['median_excess']:+6.1f}%p  앞 {front!s:>5s}% / 뒤 {back!s:>5s}%")


if __name__ == "__main__":
    with open("data/measure/snapshot.json", encoding="utf-8") as f:
        snap = json.load(f)
    print_report(explore(snap))
