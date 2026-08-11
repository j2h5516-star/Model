"""
test_verdict.py — 자동 판정기 검증 (사전 등록 규칙의 이행)
==========================================================

실행: python3 tests/test_verdict.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import verdict  # noqa: E402


def _events(specs):
    """(ticker, new_high, streak, excess) 목록으로 가짜 사건을 만듭니다."""
    return [
        {"ticker": t, "announced": f"2024-{i % 12 + 1:02d}-15",
         "new_high": nh, "newhigh_streak": st, "excess": ex}
        for i, (t, nh, st, ex) in enumerate(specs)
    ]


def test_adoption_needs_full_ci_separation():
    """채택은 신호 하한 > 기준선 상한일 때만 — 겹치면 미채택."""
    strong = _events([("NEW", True, 1, 50.0)] * 30 + [("NEW", False, 0, -10.0)] * 30)
    judged = verdict._judge(
        [e for e in strong if e["new_high"]], strong
    )
    assert judged["채택"] is True, judged      # 30/30 vs 30/60 — 구간 분리

    weak = _events([("NEW", True, 1, 50.0)] * 8 + [("NEW", False, 0, 50.0)] * 8
                   + [("NEW", False, 0, -10.0)] * 8)
    judged = verdict._judge([e for e in weak if e["new_high"]], weak)
    assert judged["채택"] is False, judged     # n=8 < 최소 표본 10


def test_small_sample_never_adopts():
    """표본 10건 미만이면 구간이 완전히 분리돼 보여도 채택하지 않아야 함."""
    signal = _events([("NEW", True, 1, 99.0)] * 9)               # 9건 전부 적중
    baseline = signal + _events([("NEW", False, 0, -10.0)] * 91)  # 기준선 9/100
    judged = verdict._judge(signal, baseline)
    # 신호 하한(약 70%) > 기준선 상한(약 16%) — 분리 자체는 성립하지만
    # 표본이 9건뿐이므로 반드시 미채택이어야 한다
    assert judged["신호"]["ci"][0] > judged["기준선"]["ci"][1], judged
    assert judged["채택"] is False, judged


def test_discovery_tickers_are_excluded_from_judgment():
    """발견에 쓰인 29종목의 사건은 판정 표본에 들어가면 안 됨 (재사용 금지)."""
    sample_discovery = next(iter(cfg.MEASURE_DISCOVERY_TICKERS))
    fake_events = _events([
        (sample_discovery, True, 1, 50.0),
        ("BRANDNEW", True, 1, 50.0),
    ])
    new_only = [e for e in fake_events
                if e["ticker"] not in cfg.MEASURE_DISCOVERY_TICKERS]
    assert len(new_only) == 1 and new_only[0]["ticker"] == "BRANDNEW"


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
    print(f"\n자동 판정 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
