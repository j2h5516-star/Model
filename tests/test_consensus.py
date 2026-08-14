"""
test_consensus.py — 야후 컨센서스 원장 검증 (헌법 2장 제1조 개정판)
====================================================================

개정 조건이 코드로 지켜지는지 확인합니다:
  · 원장은 추가 전용 — 과거 항목은 절대 바뀌지 않는다
  · 하루 한 번 · 값이 같으면 생략 · 시간 역행 금지
  · 오염(주당 상한 초과 · low≤avg≤high 위반)은 버린다 — 고치지 않는다
  · 수집 실패는 실패로 센다 — 값을 만들지 않는다

실행: python3 tests/test_consensus.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd  # noqa: E402

import consensus_feed as cf  # noqa: E402


def frame(rows: dict) -> pd.DataFrame:
    return pd.DataFrame.from_dict(rows, orient="index")


ROW1 = {"0q": {"avg": 1.0, "low": 0.9, "high": 1.1, "analysts": 4, "year_ago": 0.8}}
ROW2 = {"0q": {"avg": 1.2, "low": 1.0, "high": 1.4, "analysts": 5, "year_ago": 0.8}}


# ---------------------------------------------------------------------------
# 값 검사 — 오염은 버린다
# ---------------------------------------------------------------------------
def test_rows_guard_drops_per_share_limit():
    """주당 상한(100) 초과 추정치는 자릿수 붕괴 의심 — 행을 버립니다."""
    df = frame({
        "0q": {"avg": 2.5, "low": 2.0, "high": 3.0,
               "numberOfAnalysts": 12, "yearAgoEps": 1.9},
        "+1q": {"avg": 202.0, "low": 1.0, "high": 300.0,
                "numberOfAnalysts": 5, "yearAgoEps": 2.0},
    })
    rows, dropped = cf.rows_from_frame(df)
    assert "0q" in rows and rows["0q"]["avg"] == 2.5, rows
    assert rows["0q"]["analysts"] == 12
    assert "+1q" not in rows and any("상한" in d for d in dropped), dropped


def test_rows_guard_drops_inverted_range():
    """low > avg 인 표는 잘못 읽은 것 — 행을 버립니다."""
    df = frame({"0q": {"avg": 2.0, "low": 2.5, "high": 3.0,
                       "numberOfAnalysts": 3, "yearAgoEps": 1.0}})
    rows, dropped = cf.rows_from_frame(df)
    assert rows == {} and dropped, (rows, dropped)


def test_rows_missing_fields_become_none():
    """없는 값은 없음(None)으로 — 지어내지 않습니다."""
    df = frame({"0q": {"avg": 1.5}})
    rows, dropped = cf.rows_from_frame(df)
    assert rows["0q"]["low"] is None and rows["0q"]["analysts"] is None
    assert dropped == []


# ---------------------------------------------------------------------------
# 원장 — 추가 전용
# ---------------------------------------------------------------------------
def test_ledger_append_only_and_dedup():
    led = cf.empty_ledger()
    assert cf.append_snapshot(led, "TT", "2026-08-13", ROW1)
    frozen = json.dumps(led["tickers"]["TT"][0], sort_keys=True)

    assert not cf.append_snapshot(led, "TT", "2026-08-13", ROW2)  # 하루 한 번
    assert not cf.append_snapshot(led, "TT", "2026-08-14", ROW1)  # 값 동일 → 생략
    assert cf.append_snapshot(led, "TT", "2026-08-15", ROW2)      # 새 값 → 추가
    assert not cf.append_snapshot(led, "TT", "2026-08-01", ROW1)  # 시간 역행 금지
    assert not cf.append_snapshot(led, "TT", "2026-08-16", {})    # 빈 행 금지

    assert len(led["tickers"]["TT"]) == 2, led["tickers"]["TT"]
    assert json.dumps(led["tickers"]["TT"][0], sort_keys=True) == frozen  # 과거 불변


def test_collect_counts_failures_honestly():
    """수집 실패는 실패로 집계 — 원장에 아무것도 만들어 넣지 않습니다."""
    original = cf.fetch_one

    def fake(ticker):
        if ticker == "BAD":
            raise RuntimeError("네트워크 실패 흉내")
        return ({"0q": {"avg": 1.0, "low": None, "high": None,
                        "analysts": 2, "year_ago": None}}, [])

    cf.fetch_one = fake
    try:
        led = cf.empty_ledger()
        note = cf.collect(["AA", "BAD"], led, as_of="2026-08-14",
                          progress=lambda *a, **k: None)
    finally:
        cf.fetch_one = original
    assert "확보 1종목" in note and "실패 1종목" in note, note
    assert "AA" in led["tickers"] and "BAD" not in led["tickers"]


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
    print(f"\n컨센서스 원장 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
