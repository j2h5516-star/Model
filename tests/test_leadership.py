"""
test_leadership.py — 주도섹터 모델 검증 (44차 등록 H19·H20·H21 의 구현)
=========================================================================

여기서 지키는 약속 (전부 44차 등록문에서 옮겨 적음):
  · 완성밀도는 **종목 수** 기준 — 한 종목이 두 번 완성해도 1로 센다
  · 주도섹터의 정배열이 깨져도 **내리지 않는다** — 도전자가 나타날 때만 바뀐다
  · 도전자는 조건 셋(3종목·30%·50%)을 **모두** 넘고 현 주도의 최근 최고점을
    넘어야 한다
  · 판단 불가(델타를 못 잼)는 실패로 세지 않는다 — 분모에서 뺀다
  · 분기점은 국면마다 **한 번만** 울린다

실행: python3 tests/test_leadership.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import leadership as ld  # noqa: E402


def state(week, group, count=3, usable=10, share=100.0, breadth=80.0,
          members=None):
    """weekly_group_state 가 내놓는 모양의 한 줄을 손으로 만듭니다."""
    density = count / usable * 100.0
    ok = (count >= ld.MIN_COMPLETIONS and density >= ld.DENSITY_MIN
          and share is not None and share >= ld.DELTA_SHARE_MIN)
    return {
        "주": week, "묶음": group, "완성수": count,
        "완성종목": members or [f"T{i}" for i in range(count)],
        "판단가능": usable, "완성밀도": round(density, 1),
        "델타동반": share,
        "주도점수": None if share is None else round(density * share / 100.0, 1),
        "델타폭": breadth, "조건충족": ok,
    }


# ---------------------------------------------------------------------------
# 상태 기계 — 주인 규칙의 핵심
# ---------------------------------------------------------------------------
def test_leader_survives_alignment_breakdown():
    """주도섹터가 조건을 잃어도, 도전자가 없으면 **내리지 않는다**."""
    rows = [state("2025-01-03", "A", count=5, usable=10)]
    for week in ("2025-01-10", "2025-01-17", "2025-01-24"):
        # A 는 완성 0건으로 무너지고, B 도 조건에 못 미친다
        rows.append(state(week, "A", count=0, usable=10, share=None))
        rows.append(state(week, "B", count=2, usable=10, share=100.0))
    timeline = ld.leadership_timeline(rows)
    assert [r["주도"] for r in timeline] == ["A", "A", "A", "A"], timeline
    assert timeline[-1]["사유"] == "주도 유지"


def test_switch_requires_beating_incumbent_peak():
    """도전자는 현 주도의 **최근 최고 점수**를 넘어야 한다 — 조건 충족만으론 부족."""
    rows = [state("2025-01-03", "A", count=8, usable=10)]        # 점수 80.0
    rows.append(state("2025-01-10", "A", count=8, usable=10))
    rows.append(state("2025-01-10", "B", count=5, usable=10))    # 점수 50.0 — 부족
    timeline = ld.leadership_timeline(rows)
    assert timeline[-1]["주도"] == "A", timeline

    rows.append(state("2025-01-17", "A", count=8, usable=10))
    rows.append(state("2025-01-17", "B", count=9, usable=10))    # 점수 90.0 — 넘음
    timeline = ld.leadership_timeline(rows)
    assert timeline[-1]["주도"] == "B", timeline
    assert "전환" in timeline[-1]["사유"]


def test_no_leader_until_conditions_met():
    """조건을 만족하는 묶음이 없으면 '주도 없음' — 억지로 뽑지 않는다."""
    rows = [state("2025-01-03", "A", count=2, usable=10),        # 완성 2 < 3
            state("2025-01-03", "B", count=3, usable=20),        # 밀도 15% < 30
            state("2025-01-03", "C", count=3, usable=10, share=40.0)]  # 동반 40% < 50
    timeline = ld.leadership_timeline(rows)
    assert timeline[0]["주도"] is None, timeline
    assert timeline[0]["사유"] == "주도 없음"


# ---------------------------------------------------------------------------
# 조건 판정 자체 — 손으로 만든 자료로 **코드가 실제로 지나가게** 한다
# ---------------------------------------------------------------------------
# 위의 state() 도우미는 조건충족을 스스로 계산하므로, 그것만 쓰면
# weekly_group_state 안의 판정 코드를 한 줄도 지나가지 않습니다
# (실제로 "조건 하나만 만족해도 주도"로 망가뜨려도 빨간 불이 안 났습니다).
# 아래 시험은 가짜 자료를 만들어 그 코드를 직접 지나가게 합니다.
from datetime import date as _date, timedelta as _timedelta  # noqa: E402


def _weekly_dates(n: int, start: str = "2023-01-02") -> list[str]:
    day = _date.fromisoformat(start)
    out = []
    for _ in range(n):
        out.append(day.isoformat())
        day += _timedelta(days=7)
    return out


def _rising(n: int, turn: int) -> list[float]:
    """turn 주까지 내려가다가 그 뒤로 오르는 주가 — 한 번 정배열을 완성합니다."""
    return ([200.0 - i for i in range(turn)]
            + [200.0 - turn + i * 4 for i in range(1, n - turn + 1)])


def _quarters(dates: list[str], rising: bool) -> list[dict]:
    """분기 12개. rising 이면 잣대값이 계속 늘고, 아니면 계속 줍니다."""
    rows = []
    day = _date.fromisoformat(dates[0])
    for i in range(12):
        value = 1.0 + i * 0.1 if rising else 5.0 - i * 0.1
        rows.append({"filing_date": day.isoformat(),
                     "announced_date": day.isoformat(),
                     "period_label": f"Q{i}", "adj_eps": value})
        day += _timedelta(days=91)
    return rows


def _fake_ds(n_weeks=120, n_group=10, n_rising=3, delta_up=3, stale=0):
    """묶음 G 에 n_group 종목. 그중 n_rising 개가 비슷한 시기에 정배열 완성.

    완성 종목 중 delta_up 개는 이익 델타 상승, 나머지는 하락.
    stale 개는 발표가 오래돼 **판단 불가**가 되게 합니다.
    """
    dates = _weekly_dates(n_weeks)
    prices = {"SPY": {"dates": dates, "close": [100.0] * n_weeks}}
    quarters = {}
    tickers = []
    for i in range(n_group):
        name = f"G{i}"
        tickers.append(name)
        if i < n_rising:
            prices[name] = {"dates": dates, "close": _rising(n_weeks, 70)}
            rising = i < delta_up
            rows = _quarters(dates, rising)
            if i >= n_group - stale:
                rows = rows[:4]              # 발표가 끊겨 신선도 초과
            quarters[name] = rows
        else:
            prices[name] = {"dates": dates,
                            "close": [100.0 - i * 0.01 for i in range(n_weeks)]}
            quarters[name] = _quarters(dates, False)
    return ({"tickers": tickers, "benchmark": "SPY",
             "prices": prices, "quarters": quarters},
            {t: "G" for t in tickers})


def _last_state(n_rising, delta_up, n_group=10):
    ds, groups = _fake_ds(n_group=n_group, n_rising=n_rising, delta_up=delta_up)
    rows = [r for r in ld.weekly_group_state(ds, groups) if r["완성수"] > 0]
    assert rows, "완성이 하나도 안 잡혔습니다 — 가짜 자료가 잘못됐습니다"
    return rows[0]


def test_condition_needs_all_three():
    """세 조건을 **모두** 넘어야 조건충족 — 하나라도 못 넘으면 False."""
    good = _last_state(n_rising=3, delta_up=3)
    assert good["완성수"] == 3 and good["완성밀도"] == 30.0, good
    assert good["델타동반"] == 100.0 and good["조건충족"] is True, good

    few = _last_state(n_rising=2, delta_up=2)               # 완성 2 < 3
    assert few["완성수"] == 2 and few["조건충족"] is False, few

    thin = _last_state(n_rising=3, delta_up=3, n_group=20)  # 밀도 15% < 30
    assert thin["완성밀도"] == 15.0 and thin["조건충족"] is False, thin

    weak = _last_state(n_rising=3, delta_up=1)              # 동반 33% < 50
    assert weak["델타동반"] == 33.3 and weak["조건충족"] is False, weak


def test_undecidable_delta_leaves_denominator():
    """델타를 못 재는 완성은 분모에서 빠집니다 — 실패로 세지 않습니다.

    완성 3종목 중 1개가 판단 불가, 나머지 2개가 상승이면
    동반 비율은 2/3(66.7%)이 아니라 **2/2(100%)** 여야 합니다.
    """
    ds, groups = _fake_ds(n_group=10, n_rising=3, delta_up=2, stale=8)
    rows = [r for r in ld.weekly_group_state(ds, groups) if r["완성수"] == 3]
    assert rows, "완성 3건짜리 주가 없습니다"
    assert rows[0]["델타동반"] == 100.0, rows[0]


def test_switch_events_lists_only_changes():
    rows = []
    for week, group, count in (("2025-01-03", "A", 5), ("2025-01-10", "A", 5),
                               ("2025-01-17", "B", 9), ("2025-01-24", "B", 9)):
        rows.append(state(week, group, count=count, usable=10))
    events = ld.switch_events(ld.leadership_timeline(rows))
    assert len(events) == 1 and events[0]["이전"] == "A" and events[0]["이후"] == "B"


# ---------------------------------------------------------------------------
# 분기점
# ---------------------------------------------------------------------------
def test_inflection_fires_once_per_regime():
    """국면 안에서 델타폭이 20%p 넘게 꺾이면 **첫 주에 한 번만** 울린다."""
    timeline = [
        {"주": "2025-01-03", "주도": "A", "델타폭": 90.0},
        {"주": "2025-01-10", "주도": "A", "델타폭": 85.0},   # 낙폭 5 — 아님
        {"주": "2025-01-17", "주도": "A", "델타폭": 60.0},   # 낙폭 30 — 울림
        {"주": "2025-01-24", "주도": "A", "델타폭": 40.0},   # 이미 울렸음
    ]
    events = ld.inflection_events(timeline)
    assert len(events) == 1 and events[0]["주"] == "2025-01-17", events
    assert events[0]["낙폭"] == 30.0


def test_inflection_resets_on_new_regime():
    """주도가 바뀌면 최고점도 새로 세고, 새 국면에서 다시 울릴 수 있다."""
    timeline = [
        {"주": "2025-01-03", "주도": "A", "델타폭": 90.0},
        {"주": "2025-01-17", "주도": "A", "델타폭": 60.0},
        {"주": "2025-01-24", "주도": "B", "델타폭": 95.0},
        {"주": "2025-02-07", "주도": "B", "델타폭": 70.0},
    ]
    events = ld.inflection_events(timeline)
    assert [e["묶음"] for e in events] == ["A", "B"], events


def test_inflection_ignores_undecidable_weeks():
    """델타폭을 못 잰 주는 건너뛴다 — 없는 하락을 만들지 않는다."""
    timeline = [
        {"주": "2025-01-03", "주도": "A", "델타폭": 90.0},
        {"주": "2025-01-10", "주도": "A", "델타폭": None},
        {"주": "2025-01-17", "주도": "A", "델타폭": 85.0},
    ]
    assert ld.inflection_events(timeline) == []


# ---------------------------------------------------------------------------
# 재료 계산 — 실물 자료로 불변 조건만
# ---------------------------------------------------------------------------
def test_density_never_exceeds_100_on_real_data():
    """같은 종목의 재완성을 중복으로 세면 밀도가 100%를 넘습니다 (실측 108.3%)."""
    import config as cfg
    import dataset
    path = os.path.join(cfg.MEASURE_DIR, "snapshot.json")
    if not os.path.exists(path):
        print("    (실물 snapshot 없음 — 건너뜀)")
        return
    ds = dataset.build(dataset.load(path))
    rows = ld.weekly_group_state(ds)
    assert rows, "상태가 하나도 안 나왔습니다"
    for row in rows:
        assert row["완성밀도"] <= 100.0, row
        assert len(set(row["완성종목"])) == len(row["완성종목"]), row
        assert row["완성수"] <= row["판단가능"], row


def test_small_groups_are_undecidable_on_real_data():
    """판단 가능 종목이 4개 미만인 묶음은 아예 값을 만들지 않습니다."""
    import config as cfg
    import dataset
    path = os.path.join(cfg.MEASURE_DIR, "snapshot.json")
    if not os.path.exists(path):
        print("    (실물 snapshot 없음 — 건너뜀)")
        return
    ds = dataset.build(dataset.load(path))
    for row in ld.weekly_group_state(ds):
        assert row["판단가능"] >= ld.MIN_MEMBERS, row


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {name} — {type(e).__name__}: {e}")
            failed += 1
    print(f"\n주도섹터 모델 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
