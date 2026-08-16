"""주도섹터 장치의 **매개변수판** 복제본.

목적: 창 길이(WINDOW)와 연속 확인(STREAK)을 바꿔 가며 돌려 보기 위한 것.
기본값으로 돌리면 leadership.py 의 진짜 장치와 **완전히 일치**해야 한다
(원칙 6 — 장치를 먼저 의심한다. 일치를 먼저 검산하고 나서 실험한다).
"""
from __future__ import annotations
import bisect
from datetime import date
import config as cfg
import sector_model as sm
import leadership as L


def prep(ds, groups=None):
    """창 길이와 무관한 재료를 한 번만 만들어 둔다 (재계산 비용 절감)."""
    groups = groups or L.default_groups()
    members = L.group_members(ds, groups)
    completions = sm.completion_events(ds)
    deltas = L.delta_state_series(ds)
    grid = L.week_grid(ds)
    by_group = {}
    for e in completions:
        by_group.setdefault(groups.get(e["ticker"], "미분류"), []).append(
            (e["day"], e["ticker"], e["델타"]))
    for rows in by_group.values():
        rows.sort()
    ready = {t: L._measurable_from(ds, t) for t in ds["tickers"]}
    # 창 길이와 무관하므로 (종목, 주) 델타 상태를 한 번만 캐시해 둔다.
    # 값은 leadership._delta_at 와 **완전히 같아야** 한다 (아래에서 검산).
    dcache = {}
    for t in ds["tickers"]:
        series = deltas.get(t) or []
        days = [s[0] for s in series]
        for day in grid:
            i = bisect.bisect_right(days, day) - 1
            v = None
            if i >= 0:
                gap = (date.fromisoformat(day) - date.fromisoformat(days[i])).days
                if gap <= sm.DELTA_FRESH_DAYS:
                    v = series[i][1]
            dcache[(t, day)] = v
    return dict(groups=groups, members=members, by_group=by_group,
                deltas=deltas, grid=grid, ready=ready, ds=ds, dcache=dcache)


def states(P, window=L.WINDOW_WEEKS, min_comp=L.MIN_COMPLETIONS,
           density_min=L.DENSITY_MIN, share_min=L.DELTA_SHARE_MIN,
           min_members=L.MIN_MEMBERS):
    grid, out = P["grid"], []
    for index, day in enumerate(grid):
        if index < window:
            continue
        start = grid[index - window]
        for name, tickers in P["members"].items():
            usable = [t for t in tickers
                      if P["ready"].get(t) is not None and P["ready"][t] <= day]
            if len(usable) < min_members:
                continue
            usable_set = set(usable)
            latest = {}
            for row in P["by_group"].get(name, []):
                if start < row[0] <= day and row[1] in usable_set:
                    latest[row[1]] = row
            recent = sorted(latest.values())
            count = len(recent)
            density = count / len(usable) * 100.0
            decidable = [r for r in recent if r[2] is not None]
            share = (sum(1 for r in decidable if r[2]) / len(decidable) * 100.0
                     if len(decidable) >= min_comp else None)
            st = [P["dcache"][(t, day)] for t in usable]
            known = [s for s in st if s is not None]
            breadth = (sum(1 for s in known if s) / len(known) * 100.0
                       if len(known) >= min_members else None)
            ok = (count >= min_comp and density >= density_min
                  and share is not None and share >= share_min)
            out.append({
                "주": day, "묶음": name, "완성수": count,
                "완성종목": [r[1] for r in recent],
                "완성일": {r[1]: r[0] for r in recent},
                "판단가능": len(usable), "완성밀도": round(density, 1),
                "델타동반": None if share is None else round(share, 1),
                "주도점수": round(density * share / 100.0, 1) if share is not None else None,
                "델타폭": None if breadth is None else round(breadth, 1),
                "조건충족": ok,
            })
    return out


def timeline(st, memory=L.WINDOW_WEEKS, streak=1):
    """streak=1 이면 원본과 같다. streak=k 면 도전자가 k주 **연속**으로
    현 주도의 직전 memory주 최고점을 넘어야 교체된다."""
    by_week = {}
    for r in st:
        by_week.setdefault(r["주"], []).append(r)
    leader = None
    recent = []
    pending = {}          # {묶음: 연속 성공 주수}
    out = []
    for day in sorted(by_week):
        rows = by_week[day]
        qualified = [r for r in rows if r["조건충족"]]
        current = next((r for r in rows if r["묶음"] == leader), None)
        if leader is not None:
            recent.append(current["주도점수"] if current else None)
            recent = recent[-memory:]
        known = [s for s in recent if s is not None]
        incumbent_best = max(known) if known else 0.0
        reason = "주도 유지" if leader else "주도 없음"
        if leader is None:
            if qualified:
                best = max(qualified, key=lambda r: r["주도점수"])
                leader = best["묶음"]; recent = [best["주도점수"]]
                pending = {}
                reason = "최초 주도 등장"
        else:
            challengers = [r for r in qualified
                           if r["묶음"] != leader and r["주도점수"] > incumbent_best]
            names = {r["묶음"] for r in challengers}
            pending = {k: v for k, v in pending.items() if k in names}
            for r in challengers:
                pending[r["묶음"]] = pending.get(r["묶음"], 0) + 1
            ripe = [r for r in challengers if pending.get(r["묶음"], 0) >= streak]
            if ripe:
                best = max(ripe, key=lambda r: r["주도점수"])
                reason = f"전환: {leader} → {best['묶음']}"
                leader = best["묶음"]; recent = [best["주도점수"]]
                pending = {}
        row = next((r for r in rows if r["묶음"] == leader), None)
        out.append({"주": day, "주도": leader,
                    "점수": row["주도점수"] if row else None,
                    "완성수": row["완성수"] if row else None,
                    "델타폭": row["델타폭"] if row else None,
                    "사유": reason})
    return out
