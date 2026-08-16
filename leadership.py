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
# ---------------------------------------------------------------------------
# 주도점수를 매기는 세 가지 방식 (57차 등록 — 기본값은 바뀌지 않는다)
# ---------------------------------------------------------------------------
# 왜 필요한가: 이 장치는 매주 **최고점 하나**를 뽑는다. 그런데 완성밀도도
# 델타동반도 **비율**이라, 분모가 얇으면 값이 크게 튄다. 최고점 뽑기에서는
# 평균이 높은 쪽이 아니라 **잘 튀는 쪽**이 이긴다.
#
# 얼마나 심한가 (57차 실측): 160종목을 섹터와 **무관하게** 같은 크기의
# 묶음에 무작위로 재배치해도, 9종목 이하 묶음이 주도 주의 중앙 76.6%를
# 가져간다. 섹터 정보가 0인데도 그렇다 — 장치의 성질이다.
#
# 그래서 "표본이 얇으면 잘한 것을 곧이곧대로 믿지 않고 깎아서 본다"를
# 점수에 넣는다. 이 저장소가 채택 기준에서 이미 쓰는 **윌슨 95% 하한**을
# 그대로 가져다 쓴다 (새 문턱·새 손잡이를 만들지 않는다).
#
# ⚠️ 기본값은 "raw" — 지금까지와 **글자 그대로 같다.** 나머지 둘은 사전
#    등록(H22·H23) 뒤 새 데이터로만 판정한다. 5년 표본에서 잰 결과는
#    전부 탐색이며 채택 근거가 아니다 (원칙 5).
SCORE_MODES = ("raw", "wilson_product", "wilson_single")


def _score(mode: str, count: int, usable: int,
           up: int, decidable: int) -> float | None:
    """주도점수. count/usable = 완성밀도, up/decidable = 델타동반.

    · raw            — 완성밀도 × 델타동반 ÷ 100 (지금까지의 방식)
    · wilson_product — 두 비율을 각각 윌슨 하한으로 깎은 뒤 곱한다 (H22)
    · wilson_single  — 실은 비율이 **하나**다. 델타를 못 잰 완성을 관측된
                       비율로 안분하면 점수 = (실효적중수 ÷ 판단가능) 이다.
                       그 하나에만 윌슨을 씌운다 (H23).
    """
    if decidable <= 0 or usable <= 0:
        return None
    if mode == "raw":
        return round(count / usable * 100.0 * (up / decidable), 1)
    import judge
    if mode == "wilson_product":
        return round(judge.wilson_interval(count, usable)[0]
                     * judge.wilson_interval(up, decidable)[0] / 100.0, 1)
    if mode == "wilson_single":
        return round(judge.wilson_interval(count * up / decidable, usable)[0], 1)
    raise ValueError(f"모르는 점수 방식: {mode}")


def weekly_group_state(ds: dict, groups: dict[str, str] | None = None,
                       score_mode: str = "raw") -> list[dict]:
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
            up = sum(1 for row in decidable if row[2])
            share = (
                up / len(decidable) * 100.0
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
                # 관문(조건충족)은 늘 생비율로 판단합니다 — 사전 등록 문턱은
                # 손대지 않고, **순위를 매기는 점수만** 방식을 고릅니다.
                "주도점수": (None if share is None else
                             _score(score_mode, count, len(usable),
                                    up, len(decidable))),
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
    # 현 주도의 **직전 13주** 점수 (44차 등록문 그대로). 판단 불가 주는
    # None 으로 자리를 채웁니다 — 자리를 안 채우면 "최근 판단 가능한 13개"가
    # 되어 창이 시간과 무관하게 늘어납니다.
    # 51차 실측: 데이터센터 묶음이 10주 넘게 판단 불가인데도 옛 점수가
    # 살아남아 도전자를 막고 있었습니다. 등록문의 "직전 13주"는 시간입니다.
    recent_scores: list[float | None] = []
    timeline: list[dict] = []
    for day in sorted(by_week):
        rows = by_week[day]
        qualified = [r for r in rows if r["조건충족"]]
        current = next((r for r in rows if r["묶음"] == leader), None)
        if leader is not None:
            recent_scores.append(current["주도점수"] if current else None)
            recent_scores = recent_scores[-WINDOW_WEEKS:]
        known = [s for s in recent_scores if s is not None]
        incumbent_best = max(known) if known else 0.0

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


# ---------------------------------------------------------------------------
# H22·H23 사전 등록 (62차 — 데이터를 더 보기 전에 적는다)
# ---------------------------------------------------------------------------
# 무엇을 다투는가: 주도점수를 생비율의 곱(현행 raw)으로 매길 것인가,
# 표본 두께를 반영한 윌슨 하한으로 매길 것인가.
#   H22 = wilson_product (두 비율을 각각 깎아 곱함)
#   H23 = wilson_single  (실은 비율 하나이므로 거기에만 윌슨)
#
# ⚠️ **채택 기준은 "맞히는 힘"이다. 흔들리지 않음이 아니다.** (주인 결정)
#   표적·기준선·채택 기준은 H20 과 **완전히 동일**하다 —
#   창 60거래일 · 성공 = 새 주도 − 옛 주도 ≥ +10%p ·
#   채택 = 신호 윌슨 하한 > 기준선 상한 · n≥10.
#   안정성(흔들림)은 **채택 근거가 아니다.** 장치의 성질이므로 관찰 지표로만
#   계속 기록한다.
#
#   이 순서를 미리 못 박는 이유: 탐색 표본에서 윌슨판은 **흔들림은 줄었지만
#   표적 성적은 오히려 나빴다**(전환 후 60거래일 성공 6/16 → 2/15).
#   지금 정해 두지 않으면 나중에 결과를 보고 유리한 쪽을 고르게 된다(원칙 5).
#
# 기본값은 raw 그대로다. 윌슨판이 **표적에서 raw 를 이겨야** 본판이 된다.
# H20(raw)을 폐기하지 않는다 — H19b 선례대로 나란히 잰다.
H22_START_DAY = "2026-08-16"    # 이 날 **뒤**에 생기는 전환만 판정 표본
H22_SCORE_MODE = "wilson_product"
H23_SCORE_MODE = "wilson_single"
# 판정 가능 시점을 미리 정직하게 적는다: 전환은 연 4.2건이므로 n≥10 에
# 약 2.4년, 채택 기준까지는 5년 이상 걸리거나 영영 안 될 수도 있다.



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


# ---------------------------------------------------------------------------
# ⑥ 안정성 — 이 판정이 얼마나 흔들리는가 (52차 감사의 요구)
# ---------------------------------------------------------------------------
# 감사단이 찾아낸 가장 무거운 사실: **데이터를 완벽히 고쳐도 판정이
# 재현되지 않는다.** 실제로 쓰이는 잣대값 2,490개 중 무작위 6%만 지워도
# 주도섹터가 210주 중 중앙값 54주(25.7%) 바뀐다.
#
# ⚠️ 57차 정정 — 여기에 적혀 있던 원인 설명이 **틀렸었다.**
#   옛 주석: "원인은 표본 두께다 (주도 묶음 완성수 중앙 3 = 문턱과 같고,
#             1·2위 점수차 중앙 7.9)"
#   실행값: 완성수 중앙 6 · 점수차 중앙 7.8 (52차 감사단의 숫자를 그대로
#           옮겨 적고 우리 장치 출력으로 검산하지 않은 탓).
#   그리고 원인 자체가 두께가 아니다 — 잣대를 6% 지워도 **완성수·완성밀도·
#   판단가능 종목수가 바뀐 칸은 0개**이고, 바뀌는 것은 델타동반뿐이다.
#   완성은 주가 정배열로만 정해지므로 잣대 삭제가 밀도에 닿을 길이 없다.
#   → 흔들림의 원인은 **델타동반의 분모**(델타를 잴 수 있는 완성 수,
#     중앙 4 · 주도 묶음은 3)이지 묶음 종목 수가 아니다.
#     이 둘은 서로 다른 분모다 (57차에서 섞어 쓴 것을 바로잡음).
#
# 참고 — _drop_random_values 는 잣대 3종을 모두 지우지만, 종목마다 실제로
# 쓰는 잣대는 사다리로 하나만 정해진다. 그래서 "284칸 삭제"라고 적으면
# 오해를 부른다: 판정에 닿는 것은 그중 152칸이다. 다만 삭제가 무작위라
# 분자·분모가 같은 비율로 줄어 **실효 선량은 2,490칸의 6.10%** 로 의도한
# 6% 와 같다 (감사단은 "2배 부풀림"이라고 했으나 검산 결과 아니었다).
#
# 그래서 주도섹터를 말할 때는 **얼마나 흔들리는지를 반드시 함께** 적는다.
# 이것은 신호가 아니라 **그 신호를 믿어도 되는가**에 대한 사실이다.
#
# ⚠️ 문턱(MIN_COMPLETIONS 등)은 건드리지 않는다. 결과를 보고 문턱을
#    만지면 사전 등록 규율 위반이다 (44차 ⑥).
STABILITY_DROP_RATE = 0.06     # 잣대값을 이 비율만큼 무작위로 지워 본다
STABILITY_TRIALS = 8           # 몇 번 반복하는가
STABILITY_SEED = 20260816      # 재현 가능하게 고정 (실행마다 같은 답)


def _drop_random_values(ds: dict, rate: float, seed: int) -> dict:
    """잣대 칸을 무작위로 rate 만큼 지운 **사본**을 만듭니다 (원본 불변)."""
    import random
    rng = random.Random(seed)
    fields = ("adj_eps", "adjusted_ebitda", "gaap_eps")
    quarters = {}
    for ticker, rows in ds["quarters"].items():
        copied = []
        for row in rows:
            new = dict(row)
            for field in fields:
                if new.get(field) is not None and rng.random() < rate:
                    new[field] = None
            copied.append(new)
        quarters[ticker] = copied
    return {**ds, "quarters": quarters}


def stability_report(ds: dict, groups: dict[str, str] | None = None,
                     trials: int = STABILITY_TRIALS,
                     rate: float = STABILITY_DROP_RATE) -> dict:
    """잣대값을 조금 지워 봤을 때 주도섹터가 얼마나 바뀌는지 잽니다.

    돌려주는 것 (전부 사실, 판단 아님):
      · 바뀐주_중앙값 / 최소 / 최대  — 210주 중 몇 주가 달라졌나
      · 마지막주_불일치 — 지금 지목한 주도가 몇 번이나 달라졌나
      · 완성수_중앙값 · 점수차_중앙값 · 동점주 — 왜 흔들리는지의 재료
    """
    groups = groups or default_groups()
    base_states = weekly_group_state(ds, groups)
    base_line = leadership_timeline(base_states)
    base_map = {row["주"]: row["주도"] for row in base_line}
    base_last = base_line[-1]["주도"] if base_line else None

    changed, last_mismatch = [], 0
    for trial in range(trials):
        shaken = _drop_random_values(ds, rate, STABILITY_SEED + trial)
        line = leadership_timeline(weekly_group_state(shaken, groups))
        differ = sum(1 for row in line if base_map.get(row["주"]) != row["주도"])
        changed.append(differ)
        if line and line[-1]["주도"] != base_last:
            last_mismatch += 1

    # 왜 흔들리는가 — 표본 두께
    by_week: dict[str, list[dict]] = {}
    for row in base_states:
        by_week.setdefault(row["주"], []).append(row)
    counts, gaps, ties = [], [], 0
    for day, rows in by_week.items():
        qualified = sorted((r for r in rows if r["조건충족"]),
                           key=lambda r: -r["주도점수"])
        if not qualified:
            continue
        counts.append(qualified[0]["완성수"])
        if len(qualified) > 1:
            gap = qualified[0]["주도점수"] - qualified[1]["주도점수"]
            gaps.append(gap)
            if gap == 0:
                ties += 1
    from statistics import median
    return {
        "판정주수": len(base_line),
        "지운비율": rate,
        "반복": trials,
        "바뀐주_중앙값": int(median(changed)) if changed else 0,
        "바뀐주_최소": min(changed) if changed else 0,
        "바뀐주_최대": max(changed) if changed else 0,
        "바뀐비율_중앙값": (round(median(changed) / len(base_line) * 100.0, 1)
                            if changed and base_line else None),
        "마지막주_불일치": last_mismatch,
        "완성수_중앙값": int(median(counts)) if counts else None,
        "점수차_중앙값": round(median(gaps), 1) if gaps else None,
        "동점주": ties,
    }
