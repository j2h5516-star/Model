"""
test_backtest.py — 워크포워드 백테스트 검증
===========================================

백테스트가 "미래를 미리 보지 않는지"와, 표준 시나리오에서 모델이
설계대로 판정하는지를 확인합니다.

실행: python3 tests/test_backtest.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest  # noqa: E402
import config as cfg  # noqa: E402

M = 1_000_000


def test_no_look_ahead():
    """각 시점의 판정은 '그때까지의 자료'만으로 나와야 함.

    뒤쪽 분기를 아무리 바꿔도 앞쪽 시점의 판정은 그대로여야 합니다.
    """
    base = backtest.make_series([100 * M, 112 * M, 128 * M, 150 * M, 180 * M, 220 * M])
    tampered = [dict(q) for q in base]
    tampered[-1]["op_income"] = 5 * M          # 마지막 분기를 폭락으로 바꿈

    first = backtest.walk_forward(base)
    second = backtest.walk_forward(tampered)

    # 마지막 시점을 뺀 나머지 판정은 동일해야 합니다
    assert [r["direction"] for r in first[:-1]] == [r["direction"] for r in second[:-1]]


def test_steady_growth_is_judged_steady():
    """같은 속도로 크는 회사는 '유지'로 판정하고, 그 판정이 맞아야 함"""
    series = backtest.make_series([100 * M * (1.1 ** i) for i in range(8)])
    records = backtest.walk_forward(series)
    summary = backtest.summarize(records)

    assert all(r["direction"] == cfg.D_STEADY for r in records), records
    assert summary["적중률(%)"] == 100.0, summary


def test_accelerating_series_is_judged_accelerating():
    """증가율이 계속 커지는 회사는 '가속'으로 판정해야 함"""
    series = backtest.make_series(
        [100 * M, 112 * M, 128 * M, 150 * M, 180 * M, 220 * M, 275 * M, 350 * M]
    )
    records = backtest.walk_forward(series)

    assert all(r["direction"] == cfg.D_ACCEL for r in records), records
    assert backtest.summarize(records)["적중률(%)"] == 100.0


def test_summary_counts_are_consistent():
    """요약의 숫자끼리 앞뒤가 맞아야 함"""
    result = backtest.run(backtest.standard_scenarios())
    total = result["전체"]

    assert total["판정한 시점"] + total["유보(혼조·판단불가)"] == total["시점수"]
    assert total["적중"] <= total["판정한 시점"]
    counted = sum(b["판정수"] for b in total["방향별"].values())
    assert counted == total["판정한 시점"], (counted, total)


def test_empty_and_short_input():
    """자료가 모자라면 조용히 빈 결과를 돌려줘야 함 (예외 금지)"""
    assert backtest.walk_forward([]) == []
    assert backtest.walk_forward(backtest.make_series([100 * M, 110 * M])) == []


def test_broken_units_do_not_produce_absurd_records():
    """단위가 깨진 분기가 섞여도 말도 안 되는 증가율이 기록되면 안 됨"""
    series = backtest.make_series([100 * M, 110 * M, 121 * M, 133 * M, 146 * M])
    series[3]["op_income"] = 146.0          # 단위가 깨진 분기 (달러 단위 유실)

    records = backtest.walk_forward(series)
    for record in records:
        value = record["actual_next_qoq"]
        assert value is None or abs(value) <= cfg.GROWTH_ABS_MAX_PCT, record


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
    print(f"\n백테스트 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
