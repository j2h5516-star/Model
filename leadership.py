"""
leadership.py — 주도섹터 모델 (44차 사전 등록 H19·H20·H21 의 구현)
====================================================================

저장소 주인의 정의를 기계가 실행할 수 있게 옮긴 것입니다.
**여기서 문턱을 고치지 않습니다** — 고치려면 측정결과.md 에 새 H번호로
다시 등록해야 합니다 (44차 ⑥).

이 파일이 하는 일:
  ① 매주, 묶음마다 "완성수 · 완성밀도 · 델타동반 · 주도점수"를 계산
  ② 주인의 전환 규칙대로 주도섹터 상태 기계를 돌림
     — **주도섹터의 정배열이 깨져도 내리지 않는다.**
       도전자가 나타날 때만 바뀐다.
  ③ 주도 국면 안에서 델타폭이 꺾인 첫 주(분기점)를 찾음

경계 (설계도.md 3장): 판정(채택/미채택)은 judge.py 가 합니다.
이 파일은 상태와 사건을 만들 뿐, 스스로 결론 내지 않습니다.
"""

from __future__ import annotations

import bisect
from datetime import date

import config as cfg
import sector_model as sm

# --- 44차 사전 등록 상수 (옮겨 적음 — 여기서 고치지 않는다) ---
WINDOW_WEEKS = 13          # 완성을 세는 창 (한 분기)
MIN_COMPLETIONS = 3        # "여러 종목이 함께"의 최소 조건
DENSITY_MIN = 30.0         # 완성밀도 문턱 (%)
DELTA_SHARE_MIN = 50.0     # 델타동반 문턱 (%)
MIN_MEMBERS = 4            # 판단 가능 종목이 이보다 적으면 판단 불가
DELTA_DROP_PP = 20.0       # 분기점 — 국면 최댓값 대비 이만큼 떨어지면
TARGET_DAYS = 60           # 표적 창 (측정 기본형)
SWITCH_WIN_PP = 10.0       # H20 성공 = 새 주도 − 옛 주도 ≥ 이 값
BREAK_LOSS_PP = -10.0      # H21 성공 = 직후 − 직전 ≤ 이 값


def default_groups() -> dict[str, str]:
    """묶음표. 45차 분류가 config 에 들어오면 그것을, 없으면 옛 섹터를 씁니다."""
    return dict(getattr(cfg, "GROUPS", None) or cfg.SECTORS)


# ---------------------------------------------------------------------------
# 재료 — 완성 사건과 델타 상태를 주(週) 격자에 올린다
# ---------------------------------------------------------------------------
def week_grid(ds: dict) -> list[str]:
    """기준지수의 주 마지막 거래일 목록 — 모든 묶음이 같은 날짜로 비교됩니다."""
    dates = ds["prices"][ds["benchmark"]]["dates"]
    return [dates[i] for i in sm.weekly_indices(dates)]


def group_members(ds: dict, groups: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ticker in ds["tickers"]:
        out.setdefault(groups.get(ticker, "미분류"), []).append(ticker)
    return out


def delta_state_series(ds: dict) -> dict[str, list[tuple[str, bool]]]:
    """{종목: [(발표일, 델타 상승 여부)]} — sector_model 의 장치를 그대로 씁니다."""
    return {t: sm._delta_series(ds, t) for t in ds["tickers"]}


def _delta_at(series: list[tuple[str, bool]], day: str) -> bool | None:
    """그 날까지 발표된 **가장 최근** 델타. 신선도를 넘으면 판단 불가."""
    days = [s[0] for s in series]
    index = bisect.bisect_right(days, day) - 1
    if index < 0:
        return None
    gap = (date.fromisoformat(day) - date.fromisoformat(days[index])).days
    if gap > sm.DELTA_FRESH_DAYS:
        return None
    return series[index][1]


def _measurable_from(ds: dict, ticker: str) -> str | None:
    """이 종목의 정배열을 판단할 수 있게 되는 **첫 날**. 영영 못 재면 None.

    주마다 이걸 다시 계산하면 (주 × 종목 × 거래일) 이 되어 매우 느립니다.
    종목당 한 번만 구해 두고 날짜 비교만 합니다 (실측 1분52초 → 몇 초).
    """
    prices = ds["prices"].get(ticker)
    if not prices or not prices.get("dates"):
        return None
    dates = prices["dates"]
    weeks = sm.weekly_indices(dates)
    if len(weeks) < sm.WARMUP_WEEKS:
        return None
    return dates[weeks[sm.WARMUP_WEEKS - 1]]


# ---------------------------------------------------------------------------
# ① 매주 묶음 상태 (H19 의 재료)
# ---------------------------------------------------------------------------
def weekly_group_state(ds: dict, groups: dict[str, str] | None = None) -> list[dict]:
    """[{주, 묶음, 완성수, 완성밀도, 델타동반, 주도점수, 델타폭, 조건충족}]

    · 완성수   = 최근 13주 안의 정배열 완성 종목 수
    · 델타동반 = 그 완성 종목 중 델타 상승을 동반한 비율(%)
                 — 델타를 못 재는 완성은 **분모에도 넣지 않습니다**
                   (판단 불가를 실패로 세면 없는 사실을 만드는 것)
    · 델타폭   = 그 묶음 **전체** 중 최근 발표에서 델타가 오른 비율(%)
                 — H21 분기점이 보는 값
    """
    groups = groups or default_groups()
    members = group_members(ds, groups)
    completions = sm.completion_events(ds)
    deltas = delta_state_series(ds)
    grid = week_grid(ds)

    # 완성 사건을 묶음별 (날짜, 종목, 델타) 로 정리
    by_group: dict[str, list[tuple[str, str, bool | None]]] = {}
    for event in completions:
        name = groups.get(event["ticker"], "미분류")
        by_group.setdefault(name, []).append(
            (event["day"], event["ticker"], event["델타"])
        )
    for rows in by_group.values():
        rows.sort()
    ready = {t: _measurable_from(ds, t) for t in ds["tickers"]}

    out: list[dict] = []
    for index, day in enumerate(grid):
        if index < WINDOW_WEEKS:
            continue                      # 창을 다 채우지 못한 주 = 판단 불가
        start = grid[index - WINDOW_WEEKS]
        for name, tickers in members.items():
            usable = [t for t in tickers
                      if ready.get(t) is not None and ready[t] <= day]
            if len(usable) < MIN_MEMBERS:
                continue                  # 판단 불가 — 값을 만들지 않는다
            usable_set = set(usable)
            # 등록문은 "정배열을 완성한 **종목 수**"입니다. 한 종목이 창 안에서
            # 깨졌다 다시 완성하면 사건은 2건이지만 **종목은 1개**입니다.
            # 중복으로 세면 완성밀도가 100%를 넘습니다 (실측: 금융 108.3%).
            # → 종목별로 **가장 최근** 완성만 남깁니다.
            latest: dict[str, tuple[str, str, bool | None]] = {}
            for row in by_group.get(name, []):
                if start < row[0] <= day and row[1] in usable_set:
                    latest[row[1]] = row      # 정렬돼 있으므로 뒤엣것이 최신
            recent = sorted(latest.values())
            count = len(recent)
            density = count / len(usable) * 100.0
            # 델타를 못 재는 완성은 분모에서 뺍니다 (판단 불가를 실패로 세면
            # 없는 사실을 만드는 것). 그런데 **뺀 뒤 남은 것이 너무 적으면
            # 그것도 사실을 만드는 것**입니다 — 46차 감사 실물: 금융 묶음은
            # 완성 9종목 중 델타를 잴 수 있는 것이 3종목뿐인데(나머지 6종목은
            # EPS 값 자체가 수집되지 않음) 그 3종목이 전부 상승이라
            # "완성 종목의 100%가 델타 동반"으로 찍혔고, 그 힘으로 금융이
            # 주도섹터를 차지했습니다.
            # → 판단 가능한 완성이 MIN_COMPLETIONS 미만이면 **판단 불가**로
            #   두어 조건을 충족시키지 않습니다 (이 파일의 다른 곳과 같은 규칙).
            decidable = [row for row in recent if row[2] is not None]
            share = (
                sum(1 for row in decidable if row[2]) / len(decidable) * 100.0
                if len(decidable) >= MIN_COMPLETIONS else None
            )
            # 델타폭 — 묶음 전체 기준 (H21)
            states = [_delta_at(deltas.get(t) or [], day) for t in usable]
            known = [s for s in states if s is not None]
            breadth = (
                sum(1 for s in known if s) / len(known) * 100.0
                if len(known) >= MIN_MEMBERS else None
            )
            ok = (
                count >= MIN_COMPLETIONS
                and density >= DENSITY_MIN
                and share is not None and share >= DELTA_SHARE_MIN
            )
            out.append({
                "주": day,
                "묶음": name,
                "완성수": count,
                "완성종목": [row[1] for row in recent],
                # H19b 가 "완성 **이후** 첫 발표"를 찾으려면 종목별 완성일이
                # 필요합니다 (완성일에는 몰라도 확인일에는 아는 값).
                "완성일": {row[1]: row[0] for row in recent},
                "판단가능": len(usable),
                "완성밀도": round(density, 1),
                "델타동반": None if share is None else round(share, 1),
                "주도점수": round(density * share / 100.0, 1) if share is not None else None,
                "델타폭": None if breadth is None else round(breadth, 1),
                "조건충족": ok,
            })
    return out


# ---------------------------------------------------------------------------
# ② 주도섹터 상태 기계 (H20 — 주인 규칙 그대로)
# ---------------------------------------------------------------------------
def leadership_timeline(states: list[dict]) -> list[dict]:
    """주별 주도섹터 [{주, 주도, 점수, 사유}].

    **핵심**: 주도섹터의 정배열이 깨져도 내리지 않습니다.
    도전자(조건 셋을 모두 만족하고 점수가 더 높은 묶음)가 나타날 때만 바뀝니다.
    """
    by_week: dict[str, list[dict]] = {}
    for row in states:
        by_week.setdefault(row["주"], []).append(row)

    leader: str | None = None
    recent_scores: list[float] = []       # 현 주도의 최근 13주 점수
    timeline: list[dict] = []
    for day in sorted(by_week):
        rows = by_week[day]
        qualified = [r for r in rows if r["조건충족"]]
        current = next((r for r in rows if r["묶음"] == leader), None)
        if current is not None and current["주도점수"] is not None:
            recent_scores.append(current["주도점수"])
            recent_scores = recent_scores[-WINDOW_WEEKS:]
        incumbent_best = max(recent_scores) if recent_scores else 0.0

        reason = "주도 유지" if leader else "주도 없음"
        if leader is None:
            if qualified:
                best = max(qualified, key=lambda r: r["주도점수"])
                leader = best["묶음"]
                recent_scores = [best["주도점수"]]
                reason = "최초 주도 등장"
        else:
            challengers = [
                r for r in qualified
                if r["묶음"] != leader and r["주도점수"] > incumbent_best
            ]
            if challengers:
                best = max(challengers, key=lambda r: r["주도점수"])
                reason = f"전환: {leader} → {best['묶음']}"
                leader = best["묶음"]
                recent_scores = [best["주도점수"]]
        row = next((r for r in rows if r["묶음"] == leader), None)
        timeline.append({
            "주": day,
            "주도": leader,
            "점수": row["주도점수"] if row else None,
            "완성수": row["완성수"] if row else None,
            "델타폭": row["델타폭"] if row else None,
            "사유": reason,
        })
    return timeline


def switch_events(timeline: list[dict]) -> list[dict]:
    """전환이 실제로 일어난 주만 뽑습니다 (H20 의 사건 목록)."""
    out = []
    previous = None
    for row in timeline:
        if previous is not None and row["주도"] != previous:
            out.append({"주": row["주"], "이전": previous, "이후": row["주도"]})
        previous = row["주도"]
    return out


# ---------------------------------------------------------------------------
# ③ 분기점 (H21 — 델타 하락)
# ---------------------------------------------------------------------------
def inflection_events(timeline: list[dict]) -> list[dict]:
    """주도 국면마다, 델타폭이 국면 최댓값 대비 20%p 이상 떨어진 **첫** 주."""
    out: list[dict] = []
    current: str | None = None
    peak: float | None = None
    fired = False
    for row in timeline:
        if row["주도"] != current:
            current, peak, fired = row["주도"], None, False
        value = row["델타폭"]
        if current is None or value is None:
            continue
        if peak is None or value > peak:
            peak = value
        elif not fired and peak - value >= DELTA_DROP_PP:
            fired = True
            out.append({"주": row["주"], "묶음": current,
                        "최고": peak, "현재": value,
                        "낙폭": round(peak - value, 1)})
    return out


# ---------------------------------------------------------------------------
# ④ 표적 — 묶음 동일가중 초과수익
# ---------------------------------------------------------------------------
def group_excess(ds: dict, members: list[str], day: str,
                 days: int = TARGET_DAYS, backward: bool = False) -> float | None:
    """묶음 동일가중 평균 초과수익(%p). backward=True 면 그 주 **이전** 창.

    유효 종목이 MIN_MEMBERS 미만이면 없음 — 만들지 않습니다.
    """
    spy = ds["prices"][ds["benchmark"]]
    values = []
    for ticker in members:
        prices = ds["prices"].get(ticker)
        if not prices or not prices.get("dates"):
            continue
        if backward:
            value = _backward_excess(prices, spy, day, days)
        else:
            value = sm._forward_excess(prices, spy, day, days)
        if value is not None:
            values.append(value)
    if len(values) < MIN_MEMBERS:
        return None
    return sum(values) / len(values)


def _backward_excess(prices: dict, spy: dict, day: str, days: int) -> float | None:
    """그 주까지의 직전 days 거래일 초과수익 — H21 의 '분기점 직전'."""
    dates, closes = prices["dates"], prices["close"]
    end = bisect.bisect_right(dates, day) - 1
    start = end - days
    if end < 0 or start < 0:
        return None
    stock = (closes[end] / closes[start] - 1.0) * 100.0
    si = bisect.bisect_right(spy["dates"], dates[start]) - 1
    sj = bisect.bisect_right(spy["dates"], dates[end]) - 1
    if si < 0 or sj <= si:
        return None
    market = (spy["close"][sj] / spy["close"][si] - 1.0) * 100.0
    return stock - market


def evaluate_switches(ds: dict, events: list[dict],
                      groups: dict[str, str] | None = None) -> list[dict]:
    """H20 표적: 전환 뒤 60거래일, 새 주도 − 옛 주도 (%p)."""
    groups = groups or default_groups()
    members = group_members(ds, groups)
    out = []
    for event in events:
        after = group_excess(ds, members.get(event["이후"]) or [], event["주"])
        before = group_excess(ds, members.get(event["이전"]) or [], event["주"])
        gap = None if after is None or before is None else round(after - before, 1)
        out.append({**event,
                    "새주도_초과": None if after is None else round(after, 1),
                    "옛주도_초과": None if before is None else round(before, 1),
                    "차이": gap,
                    "성공": None if gap is None else gap >= SWITCH_WIN_PP})
    return out


def evaluate_inflections(ds: dict, events: list[dict],
                         groups: dict[str, str] | None = None) -> list[dict]:
    """H21 표적: 분기점 직전 60거래일 vs 직후 60거래일 (같은 묶음)."""
    groups = groups or default_groups()
    members = group_members(ds, groups)
    out = []
    for event in events:
        pool = members.get(event["묶음"]) or []
        before = group_excess(ds, pool, event["주"], backward=True)
        after = group_excess(ds, pool, event["주"])
        gap = None if before is None or after is None else round(after - before, 1)
        out.append({**event,
                    "직전": None if before is None else round(before, 1),
                    "직후": None if after is None else round(after, 1),
                    "차이": gap,
                    "성공": None if gap is None else gap <= BREAK_LOSS_PP})
    return out


# ---------------------------------------------------------------------------
# ⑤ H19b — **완성 후 확인형** (46차 ⑦ 등록)
# ---------------------------------------------------------------------------
# 46차에서 실측한 것: 저장소 주인이 지목한 광통신 국면에서 정배열 완성은
# 넘치게 일어났는데(밀도 42.9~71.4%) 이익 델타는 **완성 뒤 5~61일**에 왔다.
# "동시에"가 아니라 "완성 → 다음 실적에서 확인"이 실제 순서였다.
# 그래서 확인 시점을 신호로 삼는 판을 따로 등록해 나란히 잰다.
#
# ⚠️ H19 를 폐기하지 않는다. 어느 시점이 맞는지는 새 데이터가 정한다.
H19B_START_DAY = "2026-08-15"   # 이 날 **뒤**의 확인만 판정 표본 (원칙 5)


def confirmation_events(ds: dict, groups: dict[str, str] | None = None,
                        states: list[dict] | None = None) -> list[dict]:
    """[{주, 묶음, 확인종목, 상승, 판단가능, 완성수, 완성밀도}] — 확인이 선 첫 주.

    한 묶음이 "완성 무리"(최근 13주 완성 3종목 이상 ∧ 밀도 30% 이상) 상태일 때,
    그 완성 종목들의 **완성 이후 첫 발표**를 모아 델타 상승이 과반이 되는
    **첫 주**를 잡습니다. 무리가 흩어지면(조건이 깨지면) 다시 잡을 수 있게
    초기화합니다.

    완성일에는 알 수 없지만 **확인일에는 실제로 알 수 있는** 값만 씁니다 —
    사후 정보가 아닙니다.
    """
    groups = groups or default_groups()
    if states is None:
        states = weekly_group_state(ds, groups)
    deltas = delta_state_series(ds)

    by_week: dict[str, list[dict]] = {}
    for row in states:
        by_week.setdefault(row["주"], []).append(row)

    fired: set[str] = set()          # 지금 무리에서 이미 확인이 선 묶음
    out: list[dict] = []
    for day in sorted(by_week):
        for row in by_week[day]:
            name = row["묶음"]
            cluster = (row["완성수"] >= MIN_COMPLETIONS
                       and row["완성밀도"] >= DENSITY_MIN)
            if not cluster:
                fired.discard(name)      # 무리가 흩어짐 — 다음 무리에서 다시
                continue
            if name in fired:
                continue
            # 완성 종목마다 **완성 이후 첫 발표**의 델타를 찾습니다.
            confirmed, decidable = [], []
            for ticker in row["완성종목"]:
                series = deltas.get(ticker) or []
                after = [s for s in series if s[0] > row["완성일"].get(ticker, "")
                         and s[0] <= day]
                if not after:
                    continue
                decidable.append(ticker)
                if after[0][1]:
                    confirmed.append(ticker)
            if len(decidable) < MIN_COMPLETIONS:
                continue                  # 판단 불가 — 값을 만들지 않는다
            if len(confirmed) / len(decidable) * 100.0 < DELTA_SHARE_MIN:
                continue
            fired.add(name)
            out.append({
                "주": day, "묶음": name,
                "확인종목": confirmed, "상승": len(confirmed),
                "판단가능": len(decidable),
                "완성수": row["완성수"], "완성밀도": row["완성밀도"],
            })
    return out


def evaluate_confirmations(ds: dict, events: list[dict],
                           groups: dict[str, str] | None = None) -> list[dict]:
    """H19b 표적: 확인 다음 거래일부터 60거래일, 묶음 동일가중 초과수익."""
    groups = groups or default_groups()
    members = group_members(ds, groups)
    out = []
    for event in events:
        excess = group_excess(ds, members.get(event["묶음"]) or [], event["주"])
        out.append({**event,
                    "초과": None if excess is None else round(excess, 1),
                    "성공": None if excess is None else excess >= SWITCH_WIN_PP})
    return out
