"""
test_status.py — 상태 판정 핵심(status.py) 검증 (v1 test_phase 에서 이식·재구성)
==============================================================================

실행: python3 tests/test_status.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import status  # noqa: E402

M = 1_000_000


def make(values, label_prefix="Q"):
    return [
        {
            "period_label": f"{label_prefix}{i + 1}",
            "filing_date": f"20{22 + i // 4}-{(i % 4) * 3 + 1:02d}-15",
            "op_income": v * M,
            "source": cfg.SRC_DIRECT,
        }
        for i, v in enumerate(values)
    ]


# ---------------------------------------------------------------------------
# TTM 시계열
# ---------------------------------------------------------------------------
def test_ttm_series_sums_four_quarters():
    ttm = status.ttm_series(make([10, 20, 30, 40, 50]))
    assert ttm == [100 * M, 140 * M], ttm


def test_ttm_growth_keeps_only_last_contiguous_segment():
    """구멍(적자 1년치)을 이어붙이면 없는 가속이 생김 — 마지막 구간만 써야 함."""
    values = [100, 100, 100, 100, -1000, 100, 100, 100, 100, 100]
    growths = status.ttm_growth_series(make(values))
    assert all(abs(g) < 1.0 for g in growths), growths   # 제자리 장사 — 가속 없음


def test_new_high_detection():
    assert status.is_ttm_new_high(make([10, 10, 10, 10, 30])) is True
    assert status.is_ttm_new_high(make([30, 30, 30, 30, 10])) is False
    assert status.is_ttm_new_high(make([10, 10, 10])) is None     # 판단 불능은 None


# ---------------------------------------------------------------------------
# 델타 방향 판정
# ---------------------------------------------------------------------------
def test_accelerating_growth_is_accel():
    """증가율이 점점 커지는 시계열은 '가속'이어야 함."""
    values = [100, 105, 112, 122, 137, 160, 195, 250, 330]
    result = status.judge_delta(make(values))
    assert result["direction"] == cfg.D_ACCEL, result


def test_steady_growth_is_not_accel():
    """일정한 비율로 크는 회사는 가속이 아님 (유지 또는 약한 신호)."""
    values = [100 * 1.05 ** i for i in range(9)]
    result = status.judge_delta(make(values))
    assert result["direction"] in (cfg.D_STEADY, cfg.D_WEAK_ACCEL, cfg.D_WEAK_DECEL), result


def test_decelerating_growth_is_decel():
    values = [100, 200, 380, 640, 950, 1200, 1350, 1420, 1450]
    result = status.judge_delta(make(values))
    assert result["direction"] in (cfg.D_DECEL, cfg.D_WEAK_DECEL), result


def test_insufficient_data_is_unknown():
    result = status.judge_delta(make([100, 110]))
    assert result["direction"] == cfg.D_UNKNOWN
    assert result["trace"].get("insufficient")


def test_trace_reports_growth_mode():
    """무슨 자로 쟀는지(TTM/QoQ)가 반드시 기록되어야 함 (v1 자 어긋남 사고)."""
    long_series = status.judge_delta(make([100, 105, 112, 122, 137, 160, 195, 250, 330]))
    assert long_series["trace"]["growth_mode"] == "TTM"
    short_series = status.judge_delta(make([100, 120, 150, 200, 280]))
    assert short_series["trace"]["growth_mode"] in ("QoQ", "YoY")


def test_pending_anomaly_defers_judgment():
    """최근 분기가 '정체불명 급등'이면 방향을 미뤄야 함."""
    quarters = make([100, 105, 110, 116, 122, 128, 135, 600])
    quarters[-1]["anomaly"] = "pending"
    result = status.judge_delta(quarters)
    assert result["direction"] == cfg.D_UNKNOWN
    assert result["trace"].get("anomaly")


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
    print(f"\n상태 판정 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
