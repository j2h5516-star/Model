"""
test_judge.py — 자동 판정 검증 · v3 5단계
==========================================

11차 사전 등록의 채택 기준이 그대로 적용되는지 확인합니다:
  · 채택 = 신호 윌슨 하한 > 기준선 윌슨 상한 (완전 분리)
  · 신호 n<10 이면 "판정 불가" — 억지 결론 금지
  · 판정 표본 = 신규 종목만 (발견 29종목 제외)
  · H5b·H6 는 게이지 판단 불가 사건을 표본에서 제외

실행: python3 tests/test_judge.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import judge  # noqa: E402


def event(ticker="NEWCO", announced="2025-01-01", excess=0.0,
          new_high=False, streak=0, h5=False, h5b=False):
    return {"ticker": ticker, "announced": announced, "excess": excess,
            "new_high": new_high, "newhigh_streak": streak,
            "h5": h5, "h5b": h5b}


# ---------------------------------------------------------------------------
# 윌슨 구간 — 알려진 성질로 검산
# ---------------------------------------------------------------------------
def test_wilson_known_properties():
    low, high = judge.wilson_interval(0, 100)
    assert low == 0.0 and high < 5.0                 # 0/100 → 0 근처
    low, high = judge.wilson_interval(100, 100)
    assert high > 99.9 and low > 95.0                # 100/100 → 100 근처
    low, high = judge.wilson_interval(5, 10)
    assert low < 50.0 < high                         # 5/10 → 50 을 품는 넓은 구간
    assert judge.wilson_interval(0, 0) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# 채택 기준 — 분리·겹침·표본 부족
# ---------------------------------------------------------------------------
def test_adoption_requires_separation():
    """신호가 압도적으로 좋고 표본이 충분하면 채택."""
    # 신호 20건 전부 폭등, 기준선(신호 포함 100건)은 20% 근처
    signal = [event(excess=50.0) for _ in range(20)]
    rest = [event(excess=0.0) for _ in range(80)]
    result = judge._judge(signal, signal + rest)
    assert result["판정"] == "채택", result


def test_overlap_is_not_adopted():
    """구간이 겹치면 미채택 — 우연과 구분 안 되는 우위는 믿지 않습니다."""
    signal = [event(excess=50.0 if i % 4 == 0 else 0.0) for i in range(20)]
    rest = [event(excess=50.0 if i % 5 == 0 else 0.0) for i in range(80)]
    result = judge._judge(signal, signal + rest)
    assert result["판정"] == "미채택", result


def test_small_sample_is_undecidable():
    """신호 n<10 이면 아무리 좋아 보여도 '판정 불가'."""
    signal = [event(excess=50.0) for _ in range(9)]
    rest = [event(excess=0.0) for _ in range(80)]
    result = judge._judge(signal, signal + rest)
    assert result["판정"] == "판정 불가", result


# ---------------------------------------------------------------------------
# 표본 분리 — 신규(판정) vs 전체(참고)
# ---------------------------------------------------------------------------
def test_discovery_tickers_are_excluded_from_judgment_sample():
    discovery = sorted(cfg.MEASURE_DISCOVERY_TICKERS)[0]
    events = (
        [event(ticker=discovery, excess=50.0) for _ in range(30)]
        + [event(ticker="NEWCO", excess=0.0) for _ in range(30)]
    )
    result = judge.run(events)
    assert result["표본"]["신규(판정)"] == 30
    assert result["표본"]["전체(참고)"] == 60


def test_h6_uses_only_gauge_decidable_events():
    """H5b 판단 불가(None) 사건은 H5b·H6 표본에서 빠져야 합니다."""
    decidable = [event(h5b=True, streak=1, excess=50.0) for _ in range(5)]
    warmup = [event(h5b=None, streak=1, excess=50.0) for _ in range(20)]
    result = judge.run(decidable + warmup)
    h6 = result["가설"]["H6_결합_H5bxH2b"]["신규(판정)"]
    assert h6["기준선"]["n"] == 5, h6            # 워밍업 20건 제외
    assert h6["판정"] == "판정 불가"             # 신호 5건 < 10


def test_time_halves_are_reported():
    events = [event(announced=f"2025-0{1 + i % 9}-01", new_high=(i % 2 == 0),
                    excess=25.0 if i % 2 == 0 else 0.0) for i in range(40)]
    result = judge.run(events)
    h2 = result["가설"]["H2_신고점"]
    assert "신규_앞시기" in h2 and "신규_뒤시기" in h2
    assert h2["신규_앞시기"]["n"] + h2["신규_뒤시기"]["n"] == 20 + 20 - 20
    # (앞 20 + 뒤 20 사건 중 신호만 세므로 합계는 신호 사건 수 20)


def test_verdict_covers_all_registered_hypotheses():
    result = judge.run([event() for _ in range(12)])
    for name in ("H2_신고점", "H2b_신고점_첫돌파", "H5_실적폭_고정20",
                 "H5b_실적폭_중앙값", "H6_결합_H5bxH2b"):
        assert name in result["가설"], name
        assert result["가설"][name]["판정"] in ("채택", "미채택", "판정 불가")


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
    print(f"\n자동 판정 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
