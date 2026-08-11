"""
breadth.py — 장세 폭(breadth) 게이지: 사이클을 잴 수 있는 것으로 바꾸기
======================================================================

질문(측정결과.md 5차): AI·반도체 같은 "사이클 장세"를 포착할 수 있는가?

사이클의 조작적 정의 = **한 무리의 종목이 동시에 좋아지는 현상.**
그래서 개별 종목이 아니라 **비율(폭)** 을 시계열로 잽니다.

  · 주가 폭   = 지속 상승추세(주봉, measurement.trend_state)인 종목 비율
  · 실적 폭   = 최근 발표가 TTM 신고점 돌파였던 종목 비율
  · 개선 폭   = 최근 발표의 분기 YoY 가 플러스였던 종목 비율

전부 **그 시점까지의 데이터만** 씁니다 (발표일·주가 모두 걸어가며 계산).

⚠️ 이 파일은 1단계 **관찰**입니다. 사이클 전환은 5년에 2번뿐이라
   통계적 채택이 아니라 "지금 어느 세상에 있는지"를 보는 게이지가
   목표입니다. 문턱·활용 규칙은 관찰 후 따로 사전 등록합니다.
"""

from __future__ import annotations

import bisect
import json
from datetime import date

import config as cfg
import data_quality as dq
import measurement as mm
import scoring

# 발표가 이 일수보다 오래됐으면 그 종목의 실적 상태는 "김빠진 것"으로 보고
# 분모에서 뺍니다 (한 분기 91일 + 발표 지연 여유).
EARNINGS_FRESH_DAYS = 140


def weekly_grid(daily_dates: list[str]) -> list[str]:
    """일봉 날짜에서 각 주의 마지막 거래일만 뽑습니다 (주간 격자)."""
    weeks: list[str] = []
    current = None
    for day in daily_dates:
        iso = date.fromisoformat(day).isocalendar()
        key = (iso[0], iso[1])
        if key != current:
            weeks.append(day)
            current = key
        else:
            weeks[-1] = day
    return weeks


def earnings_states(rows: list[dict]) -> list[tuple[str, bool, bool | None]]:
    """한 종목의 발표 시점 상태 목록: (발표일, 신고점 여부, 분기YoY 플러스 여부).

    각 발표 시점에서 **그때까지의 분기만** 보고 계산합니다.
    """
    runs, _ = mm.eps_runs(rows)
    out: list[tuple[str, bool, bool | None]] = []
    for run in runs:
        for idx, row in enumerate(run):
            announce = row.get("announced_date")
            if not announce:
                continue
            known = mm.to_scoring_rows(run[: idx + 1])
            ttm = scoring._ttm_series(known)
            if len(ttm) < 2:
                continue
            new_high = ttm[-1] > max(ttm[:-1])
            eps = [q["op_income"] for q in known]
            yoy = dq.safe_growth_pct(eps[-5], eps[-1]) if len(eps) >= 5 else None
            out.append((str(announce)[:10], new_high, None if yoy is None else yoy > 0))
    out.sort(key=lambda item: item[0])
    return out


def latest_state(states: list[tuple[str, bool, bool | None]], day: str):
    """그 날짜 기준 가장 최근 발표 상태 (신선하지 않으면 None)."""
    dates = [s[0] for s in states]
    index = bisect.bisect_right(dates, day) - 1
    if index < 0:
        return None
    announce, new_high, yoy_pos = states[index]
    age = (date.fromisoformat(day) - date.fromisoformat(announce)).days
    if age > EARNINGS_FRESH_DAYS:
        return None
    return new_high, yoy_pos


def build_series(snapshot: dict) -> dict:
    """주간 폭 시계열을 만듭니다. 분모 10종목 미만인 주는 버립니다."""
    spy = snapshot["prices"][snapshot.get("benchmark", "SPY")]
    weeks = weekly_grid(spy["dates"])
    states_by_ticker = {
        t: earnings_states(snapshot["eps"].get(t) or []) for t in snapshot["tickers"]
    }

    series = {"week": [], "price_pct": [], "newhigh_pct": [], "yoy_pct": [], "spy": []}
    spy_map = dict(zip(spy["dates"], spy["close"]))

    for week in weeks:
        up = evaluable = 0
        for ticker in snapshot["tickers"]:
            prices = snapshot["prices"].get(ticker)
            if not prices:
                continue
            state = mm.trend_state(prices["dates"], prices["close"], week)
            if state is None:
                continue
            evaluable += 1
            up += state == "지속상승"

        nh = nh_total = yoy = yoy_total = 0
        for ticker in snapshot["tickers"]:
            state = latest_state(states_by_ticker[ticker], week)
            if state is None:
                continue
            new_high, yoy_pos = state
            nh_total += 1
            nh += bool(new_high)
            if yoy_pos is not None:
                yoy_total += 1
                yoy += bool(yoy_pos)

        if evaluable < 10 or nh_total < 10:
            continue
        series["week"].append(week)
        series["price_pct"].append(round(up / evaluable * 100.0, 1))
        series["newhigh_pct"].append(round(nh / nh_total * 100.0, 1))
        series["yoy_pct"].append(
            round(yoy / yoy_total * 100.0, 1) if yoy_total >= 10 else None
        )
        series["spy"].append(spy_map.get(week))
    return series


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 20:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def lead_lag(series: dict, max_lag_weeks: int = 26) -> list[tuple[int, float]]:
    """실적 폭(신고점 비율)을 몇 주 밀었을 때 주가 폭과 가장 닮는가.

    지연(lag) > 0 = 실적 폭이 **앞선다** (실적을 lag주 뒤로 밀면 주가와 겹침).
    """
    price, earn = series["price_pct"], series["newhigh_pct"]
    out = []
    for lag in range(-max_lag_weeks, max_lag_weeks + 1):
        if lag >= 0:
            xs, ys = earn[: len(earn) - lag or None], price[lag:]
        else:
            xs, ys = earn[-lag:], price[: len(price) + lag]
        r = _pearson(xs, ys)
        if r is not None:
            out.append((lag, round(r, 3)))
    return out


def crossings(weeks: list[str], values: list[float], threshold: float) -> list[str]:
    """문턱을 아래→위로 통과한 날짜들 (게이지가 '켜진' 순간)."""
    out = []
    for previous, current, week in zip(values, values[1:], weeks[1:]):
        if previous is not None and current is not None and previous < threshold <= current:
            out.append(week)
    return out


if __name__ == "__main__":
    with open("data/measure/snapshot.json", encoding="utf-8") as f:
        snap = json.load(f)
    s = build_series(snap)
    print(f"주간 {len(s['week'])}개 ({s['week'][0]} ~ {s['week'][-1]})")
    print(f"{'주':10s} {'주가폭%':>6s} {'신고점폭%':>7s} {'YoY+폭%':>7s}")
    for i, week in enumerate(s["week"]):
        if i % 4 == 0:   # 4주 간격으로만 출력
            yoy_text = s["yoy_pct"][i] if s["yoy_pct"][i] is not None else "-"
            print(f"{week:10s} {s['price_pct'][i]:6.1f} {s['newhigh_pct'][i]:7.1f} {yoy_text!s:>7s}")

    pairs = lead_lag(s)
    best = max(pairs, key=lambda p: p[1])
    print("\n선후 관계 (신고점 폭 ↔ 주가 폭):")
    print(f"  가장 닮는 지연 = {best[0]:+d}주 (상관 {best[1]})  — 양수면 실적이 앞섬")
    for lag in (-13, -8, -4, 0, 4, 8, 13):
        r = dict(pairs).get(lag)
        print(f"  지연 {lag:+3d}주: 상관 {r}")

    print("\n게이지가 켜진 순간 (아래→위 통과):")
    print("  주가 폭 30% 돌파:", crossings(s["week"], s["price_pct"], 30.0))
    print("  신고점 폭 20% 돌파:", crossings(s["week"], s["newhigh_pct"], 20.0))
