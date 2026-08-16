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

# 가설 정의 (11차·12차 등록): 이름 → (표본 필터, 신호 조건)
#   표본 필터: 이 가설에서 판단 가능한 사건인가.
#   · H2~H6 은 **조정 EPS 잣대** 사건만 (11차 정의 그대로 — 12차에서 재확인)
#   · H7·H8 은 각각 EBITDA·GAAP EPS 잣대 그룹 — 기준선도 같은 그룹 (12차)
#   신호 조건: 그 표본 안에서 신호 그룹에 드는가
def _adj(e):
    return e.get("잣대", "adj_eps") == "adj_eps"


HYPOTHESES: dict[str, tuple] = {
    "H2_신고점": (_adj, lambda e: e["new_high"]),
    "H2b_신고점_첫돌파": (_adj, lambda e: e["newhigh_streak"] == 1),
    "H5_실적폭_고정20": (
        lambda e: _adj(e) and e["h5"] is not None,
        lambda e: e["h5"] is True,
    ),
    "H5b_실적폭_중앙값": (
        lambda e: _adj(e) and e["h5b"] is not None,
        lambda e: e["h5b"] is True,
    ),
    "H6_결합_H5bxH2b": (
        lambda e: _adj(e) and e["h5b"] is not None,
        lambda e: e["h5b"] is True and e["newhigh_streak"] == 1,
    ),
    "H7_EBITDA_첫돌파": (
        lambda e: e.get("잣대") == "adjusted_ebitda",
        lambda e: e["newhigh_streak"] == 1,
    ),
    "H8_GAAPEPS_첫돌파": (
        lambda e: e.get("잣대") == "gaap_eps",
        lambda e: e["newhigh_streak"] == 1,
    ),
    # H9 (21차 등록): 잣대 TTM 첫 신고점 ∧ 주봉 종가 < 52주 이동평균.
    # 표본 = 52주선 판단 가능 사건 (이력 52주 미만 제외)
    "H9_저평가_첫신기록": (
        lambda e: e.get("below52") is not None,
        lambda e: e["newhigh_streak"] == 1 and e["below52"] is True,
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


# H10 (23차 등록): 논갭 영업이익 TTM 첫 신고점 ∧ 주봉 종가 < 52주 이동평균.
# 사건 목록이 H2~H9(잣대 사다리)와 **별도**라서 run 의 두 번째 인자로 받습니다.
H10_NAME = "H10_논갭영업이익_저평가_첫신기록"
_H10_ELIGIBLE = lambda e: e.get("below52") is not None    # noqa: E731
_H10_CONDITION = (                                        # noqa: E731
    lambda e: e["newhigh_streak"] == 1 and e["below52"] is True
)


def run(events: list[dict], op_events: list[dict] | None = None) -> dict:
    """사건 목록 전체에 등록된 가설을 판정합니다.

    op_events: 논갭 영업이익 단독 사건 목록 (collect_metric_events).
    주면 H10 도 판정합니다 — 기준선은 그 목록(같은 잣대 그룹)만 씁니다.
    """
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

    if op_events is not None:
        new_op = [
            e for e in op_events
            if e["ticker"] not in cfg.MEASURE_DISCOVERY_TICKERS
        ]
        entry = {}
        for s_name, group in {"신규(판정)": new_op, "전체(참고)": op_events}.items():
            pool = [e for e in group if _H10_ELIGIBLE(e)]
            entry[s_name] = _judge(
                [e for e in pool if _H10_CONDITION(e)], pool
            )
        judged_pool = [e for e in new_op if _H10_ELIGIBLE(e)]
        front, back = _halves(judged_pool)
        entry["신규_앞시기"] = _stats([e for e in front if _H10_CONDITION(e)])
        entry["신규_뒤시기"] = _stats([e for e in back if _H10_CONDITION(e)])
        entry["판정"] = entry["신규(판정)"]["판정"]
        out["가설"][H10_NAME] = entry
    return out


# H11·H11b (33차 등록): 섹터 정배열 폭이 처음 문턱을 넘긴 주 →
# 250거래일 뒤 섹터 동일가중 초과수익. 사건 목록이 종목 발표가 아니라
# **(섹터, 주)** 라서 별도 인자로 받습니다.
H11_NAME = "H11_섹터정배열폭_60"
H11B_NAME = "H11b_섹터정배열폭_80"
_H11_SURGE_PP = 20.0        # 33차 등록 — 1년 창의 폭등 문턱


def _sector_stats(group: list[dict]) -> dict:
    n = len(group)
    hits = sum(1 for e in group if e["excess"] >= _H11_SURGE_PP)
    low, high = wilson_interval(hits, n)
    return {"n": n, "rate": round(hits / n * 100.0, 1) if n else None,
            "ci": [round(low, 1), round(high, 1)]}


def _judge_sector(signal: list[dict], baseline: list[dict]) -> dict:
    signal_stats = _sector_stats(signal)
    base_stats = _sector_stats(baseline)
    if signal_stats["n"] < MIN_SIGNAL_N:
        verdict = "판정 불가"
    elif signal_stats["ci"][0] > base_stats["ci"][1]:
        verdict = "채택"
    else:
        verdict = "미채택"
    return {"신호": signal_stats, "기준선": base_stats, "판정": verdict}


def judge_sector_breadth(events: list[dict]) -> dict:
    """H11·H11b 판정 — 기준선은 판단 가능한 모든 (섹터, 주)."""
    out: dict = {}
    for name, key in ((H11_NAME, "cross60"), (H11B_NAME, "cross80")):
        signal = [e for e in events if e.get(key)]
        entry = {"신규(판정)": _judge_sector(signal, events)}
        front, back = _halves(events)
        entry["신규_앞시기"] = _sector_stats([e for e in front if e.get(key)])
        entry["신규_뒤시기"] = _sector_stats([e for e in back if e.get(key)])
        entry["판정"] = entry["신규(판정)"]["판정"]
        out[name] = entry
    return out


# H18 (43차 등록): 정배열 **완성** 시점의 52주선 이격도.
# 42차 탐색에서 나온 후보이므로 **탐색 표본은 판정에 못 씁니다**(원칙 5).
# 등록일 뒤에 새로 생긴 완성 사건만 세고, 그 전 것은 참고로만 붙입니다.
H18_NAME = "H18_완성시_52주선이격도"
_H18_SURGE_PP = 20.0        # 측정 기본형 — 폭등 = SPY 대비 +20%p


def _completion_stats(group: list[dict]) -> dict:
    n = len(group)
    hits = sum(1 for e in group if e["초과60"] >= _H18_SURGE_PP)
    low, high = wilson_interval(hits, n)
    return {"n": n, "rate": round(hits / n * 100.0, 1) if n else None,
            "ci": [round(low, 1), round(high, 1)]}


def judge_completion_gap(events: list[dict], start_day: str,
                         gap_min: float) -> dict:
    """H18 판정 — 정배열 완성 사건 중 이격도가 문턱 이상인 군.

    입력은 sector_model.completion_events 의 사건 목록입니다.
    표적이 아직 안 끝난 사건(초과60 없음)과 이격도를 못 잰 사건은
    표본에서 뺍니다 — 값을 만들지 않습니다.
    """
    usable = [e for e in events
              if e.get("초과60") is not None and e.get("이격도") is not None]
    out: dict = {}
    for label, pool in (
        ("신규(판정)", [e for e in usable if e["day"] > start_day]),
        ("탐색표본(참고)", [e for e in usable if e["day"] <= start_day]),
    ):
        signal = [e for e in pool if e["이격도"] >= gap_min]
        signal_stats = _completion_stats(signal)
        base_stats = _completion_stats(pool)
        if signal_stats["n"] < MIN_SIGNAL_N:
            verdict = "판정 불가"
        elif signal_stats["ci"][0] > base_stats["ci"][1]:
            verdict = "채택"
        else:
            verdict = "미채택"
        out[label] = {"신호": signal_stats, "기준선": base_stats,
                      "판정": verdict}
    out["판정"] = out["신규(판정)"]["판정"]
    out["등록일"] = start_day
    return {H18_NAME: out}


# H19·H20·H21 (44차 등록): 주도섹터 판정 · 전환 · 분기점.
# 사건 단위가 "국면"이라 표본이 매우 작습니다. 44차에서 **미리 적은 대로**
# n≥10 에 오래 못 미칠 것이므로, 여기서는 억지 결론을 내지 않고
# 사실(국면 목록·성공/실패)과 "판정 불가"를 그대로 기록만 합니다.
H19_NAME = "H19_주도섹터_판정"
H20_NAME = "H20_주도섹터_전환"
H21_NAME = "H21_주도섹터_분기점"


H19B_NAME = "H19b_주도섹터_완성후확인"


def judge_leadership(timeline: list[dict], switches: list[dict],
                     inflections: list[dict],
                     confirmations: list[dict] | None = None,
                     baseline: list[float] | None = None,
                     start_day: str | None = None,
                     stability: dict | None = None) -> dict:
    """44차 등록의 세 가설을 기록합니다 (판정은 채택 기준 그대로 적용).

    입력은 leadership.py 가 만든 목록입니다. 이 함수는 세지 않은 것을
    만들지 않습니다 — 표적을 못 잰 사건은 분모에서 빠집니다.
    """
    def _verdict(events: list[dict]) -> dict:
        usable = [e for e in events if e.get("성공") is not None]
        hits = sum(1 for e in usable if e["성공"])
        low, high = wilson_interval(hits, len(usable))
        return {
            "n": len(usable),
            "성공": hits,
            "rate": round(hits / len(usable) * 100.0, 1) if usable else None,
            "ci": [round(low, 1), round(high, 1)],
            "판정": "판정 불가" if len(usable) < MIN_SIGNAL_N else (
                "채택" if low > 50.0 else "미채택"
            ),
        }

    현재 = timeline[-1] if timeline else {}
    out = {
        H19_NAME: {
            "현재_주도": 현재.get("주도"),
            "기준주": 현재.get("주"),
            "점수": 현재.get("점수"),
            "완성수": 현재.get("완성수"),
            "델타폭": 현재.get("델타폭"),
            "국면수": len({r["주도"] for r in timeline if r.get("주도")}),
            # 52차 감사의 요구 — 이 판정이 얼마나 흔들리는지를 **함께** 적는다.
            # 없으면 사람이 "지금 주도는 X" 만 읽고 그것이 한두 종목으로
            # 뒤집힌다는 사실을 모른다.
            "안정성": stability,
            "판정": "판정 불가",     # 44차 ⑤ — 국면 표본으로는 채택 불가
        },
        H20_NAME: {**_verdict(switches), "사건": switches},
        H21_NAME: {**_verdict(inflections), "사건": inflections},
    }
    if confirmations is not None:
        # H19b (46차 ⑦ 등록) — 탐색에서 나온 후보이므로 **등록일 뒤**의
        # 확인만 판정 표본입니다. 그 전 것은 참고 칸에만 넣습니다 (원칙 5).
        cut = start_day or ""
        new_only = [e for e in confirmations if e.get("주", "") > cut]
        entry = {
            "신규(판정)": _verdict(new_only),
            "탐색표본(참고)": _verdict(
                [e for e in confirmations if e.get("주", "") <= cut]),
            "등록일": cut,
        }
        if baseline:
            hits = sum(1 for v in baseline if v >= 10.0)
            low, high = wilson_interval(hits, len(baseline))
            entry["기준선(참고)"] = {
                "n": len(baseline),
                "rate": round(hits / len(baseline) * 100.0, 1),
                "ci": [round(low, 1), round(high, 1)],
            }
        entry["판정"] = entry["신규(판정)"]["판정"]
        out[H19B_NAME] = entry
    return out


def to_json(verdict: dict) -> str:
    return json.dumps(verdict, ensure_ascii=False, indent=1)
