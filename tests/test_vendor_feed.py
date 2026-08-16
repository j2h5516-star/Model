"""
tests/test_vendor_feed.py — 두 번째 자(야후 분기표) 검증 (75차)

이 축의 약속:
  ① 표에 있는 값만 **그대로** 옮긴다 (없는 칸은 "없음")
  ② 월가 기준 EPS 를 회사 조정 EPS 인 척하지 않는다 (이름을 구분)
  ③ 과거를 말없이 고쳐도 **몇 칸이 바뀌었는지 센다**
  ④ 한 종목이 실패해도 나머지는 계속한다 (관찰 전용 축)

실행: python3 tests/test_vendor_feed.py
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vendor_feed as vf  # noqa: E402


def 분기표(**행):
    """야후 분기표 흉내 — 열이 분기 종료일, 행이 항목."""
    기본 = {
        "Total Revenue": [533_700_000.0, 506_600_000.0],
        "Cost Of Revenue": [449_400_000.0, 429_600_000.0],
        "Gross Profit": [84_300_000.0, 77_000_000.0],
        "Diluted EPS": [-0.40, -0.11],
    }
    기본.update(행)
    return pd.DataFrame(기본, index=["2026-03-31", "2025-12-31"]).T


def test_rows_are_copied_not_computed():
    """표에 있는 값은 그대로, 없는 줄은 '없음' 이어야 합니다."""
    got = vf.quarters_from_frame(분기표())
    assert len(got) == 2
    최근 = got[-1]
    assert 최근["period_end"] == "2026-03-31"
    assert 최근["revenue"] == 533_700_000.0
    assert 최근["gaap_eps"] == -0.40
    assert 최근["op_income"] is None, "표에 없는 줄은 없음이어야 합니다"
    assert 최근["net_income"] is None


def test_gross_margin_is_the_two_numbers_divided():
    """매출총이익률은 표의 두 값을 나눈 것입니다 (창작이 아님)."""
    최근 = vf.quarters_from_frame(분기표())[-1]
    assert abs(최근["gross_margin_pct"] - 15.7955) < 0.001, 최근


def test_missing_revenue_gives_no_margin():
    """매출이 없으면 이익률은 '없음' — 0으로 채우지 않습니다."""
    표 = 분기표(**{"Total Revenue": [None, None]})
    for q in vf.quarters_from_frame(표):
        assert q["gross_margin_pct"] is None, q


def test_nan_becomes_none():
    """야후 표의 빈 칸(NaN)이 그대로 흘러가면 안 됩니다."""
    표 = 분기표(**{"Diluted EPS": [float("nan"), 0.31]})
    got = vf.quarters_from_frame(표)
    assert got[-1]["gaap_eps"] is None, got[-1]
    assert got[0]["gaap_eps"] == 0.31


def test_empty_frame_is_not_a_crash():
    """표가 아예 없어도 무너지지 않고 빈 목록입니다."""
    assert vf.quarters_from_frame(None) == []
    assert vf.quarters_from_frame(pd.DataFrame()) == []
    assert vf.announcements_from_frame(None) == []


def test_street_eps_is_named_apart_from_company_adjusted_eps():
    """월가 기준 EPS 를 회사 조정 EPS 인 척하면 안 됩니다.

    이름이 `adj_eps` 가 되면 다음 세션이 헌법 1조를 어기고 섞어 씁니다.
    """
    표 = pd.DataFrame(
        {"epsEstimate": [1.10, 0.95], "epsActual": [1.19, 1.02]},
        index=pd.to_datetime(["2023-01-24", "2022-10-21"]),
    )
    got = vf.announcements_from_frame(표)
    assert [r["announced_date"] for r in got] == ["2022-10-21", "2023-01-24"]
    assert got[-1]["street_eps"] == 1.19
    assert "adj_eps" not in got[-1], "회사 조정 EPS 와 이름이 겹치면 안 됩니다"


def test_retroactive_changes_are_counted():
    """데이터 회사가 과거를 고쳐 쓰면 **몇 칸인지 세야** 합니다.

    우리 모델의 핵심은 '**첫** 신기록 돌파'라서, 과거 최고점이 나중에
    바뀌면 과거 판정이 통째로 달라집니다. 그래서 감시합니다.
    """
    전 = {"quarters": [{"period_end": "2026-03-31", "revenue": 100.0,
                       "gaap_eps": 1.0}]}
    후 = {"quarters": [{"period_end": "2026-03-31", "revenue": 110.0,
                       "gaap_eps": 1.0},
                      {"period_end": "2026-06-30", "revenue": 120.0}]}
    assert vf.changed_cells(전, 후) == 1, "고쳐진 1칸을 세야 합니다"
    assert vf.changed_cells(전, 전) == 0
    # 새로 생긴 분기는 '바뀜'이 아닙니다
    assert vf.changed_cells({"quarters": []}, 후) == 0


def test_one_bad_ticker_does_not_stop_the_rest():
    """한 종목이 실패해도 나머지는 계속 받아야 합니다 (관찰 전용 축)."""
    원래 = vf.fetch
    try:
        def 가짜(ticker):
            if ticker == "BAD":
                raise RuntimeError("야후 응답 없음")
            return {"quarters": [{"period_end": "2026-03-31", "revenue": 1.0}],
                    "announcements": []}
        vf.fetch = 가짜
        보관, 요약 = vf.collect(["AAA", "BAD", "BBB"], {}, "2026-08-17",
                              progress=lambda *_: None)
        assert set(보관["tickers"]) == {"AAA", "BBB"}, 보관["tickers"]
        assert "2종목 성공" in 요약 and "1종목 실패" in 요약, 요약
    finally:
        vf.fetch = 원래


def test_failed_ticker_keeps_its_old_record():
    """오늘 못 받았다고 어제 받아 둔 것을 버리면 안 됩니다."""
    원래 = vf.fetch
    try:
        def 가짜(ticker):
            raise RuntimeError("야후 응답 없음")
        vf.fetch = 가짜
        옛 = {"tickers": {"AAA": {"quarters": [{"period_end": "2026-03-31"}],
                                "as_of": "2026-08-16"}}}
        보관, _ = vf.collect(["AAA"], 옛, "2026-08-17", progress=lambda *_: None)
        assert 보관["tickers"]["AAA"]["as_of"] == "2026-08-16", 보관
    finally:
        vf.fetch = 원래


def test_archive_says_it_is_not_merged():
    """보관본은 '섞지 않는다'는 사실을 스스로 말해야 합니다.

    다음 세션이 이 파일을 snapshot 처럼 쓰면 헌법 1조가 깨집니다.
    """
    원래 = vf.fetch
    try:
        vf.fetch = lambda t: {"quarters": [], "announcements": []}
        보관, _ = vf.collect(["AAA"], {}, "2026-08-17", progress=lambda *_: None)
        assert "섞지 않습니다" in 보관["설명"], 보관["설명"]
        assert "월가 기준" in 보관["설명"], 보관["설명"]
    finally:
        vf.fetch = 원래


if __name__ == "__main__":
    tests = [
        (n, f) for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
    ]
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
    print(f"\n두 번째 자(야후 분기표) 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
