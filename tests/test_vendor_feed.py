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
    # 이 표(epsActual 꼴)는 옛 창구이고 줄 이름이 **분기 종료일**입니다
    got = vf.announcements_from_frame(표)
    assert [r["period_end"] for r in got] == ["2022-10-21", "2023-01-24"]
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


def test_깊은_창구의_열이름도_읽는다():
    """심판을 깊은 창구로 바꿨습니다 (105차). 열 이름이 다릅니다.

    ⚠️ 왜 바꿨나: 야후 심판이 우리 5,851행 중 **962행(16%)** 만 보고
    있었습니다. 손익계산서 창구는 yfinance 소스에 *"Yahoo returns maximum
    4 years or 5 quarters, regardless of start_dt"* 라고 적힌 **하드
    한계**라 못 늘립니다. 반면 발표 기록 창구(get_earnings_dates)는
    `limit` 을 받고 야후가 **100까지** 허용합니다 — 약 25년.

    두 창구의 열 이름이 다르므로 **둘 다** 읽어야 합니다.
      earnings_history    → epsActual · epsEstimate   (실측 4건뿐)
      get_earnings_dates  → Reported EPS · EPS Estimate
    """
    import pandas as pd

    깊은창구 = pd.DataFrame(
        {"EPS Estimate": [1.00, 1.10], "Reported EPS": [1.05, 1.20],
         "Surprise(%)": [5.0, 9.1]},
        index=pd.to_datetime(["2017-02-01", "2017-05-02"]),
    )
    행 = vf.announcements_from_frame(깊은창구, "발표일")
    assert [r["announced_date"] for r in 행] == ["2017-02-01", "2017-05-02"]
    assert [r["street_eps"] for r in 행] == [1.05, 1.20]
    assert [r["street_estimate"] for r in 행] == [1.00, 1.10]

    # 옛 창구도 그대로 읽혀야 합니다 (되돌아갈 길을 남겨 뒀으므로)
    옛창구 = pd.DataFrame(
        {"epsEstimate": [2.00], "epsActual": [2.10]},
        index=pd.to_datetime(["2026-05-01"]),
    )
    # 옛 창구의 줄 이름은 **분기 종료일**이므로 이름도 그렇게 붙습니다
    행2 = vf.announcements_from_frame(옛창구)
    assert 행2 == [{"period_end": "2026-05-01", "날짜뜻": "분기끝",
                   "street_eps": 2.10, "street_estimate": 2.00}]
    assert "announced_date" not in 행2[0], (
        "분기 종료일을 발표일이라고 이름 붙이면 우리 발표일과 한 칸도 "
        "안 맞습니다 (실물 NVDA 로 확인한 결함)"
    )


def test_아직_안_나온_발표는_담지_않는다():
    """깊은 창구는 **앞으로 있을 발표**도 함께 줍니다 (값이 아직 없음).

    둘 다 없는 줄은 심판으로 쓸 수 없으므로 담지 않습니다 — 값을
    지어내는 것이 아니라 빈 줄을 안 싣는 것입니다. 추정만 있는 줄은
    남깁니다 (그것도 사실이므로).
    """
    import pandas as pd

    frame = pd.DataFrame(
        {"EPS Estimate": [1.00, 1.30, None], "Reported EPS": [1.05, None, None]},
        index=pd.to_datetime(["2026-05-01", "2026-08-01", "2026-11-01"]),
    )
    행 = vf.announcements_from_frame(frame, "발표일")
    assert [r["announced_date"] for r in 행] == ["2026-05-01", "2026-08-01"], 행
    assert 행[1]["street_eps"] is None and 행[1]["street_estimate"] == 1.30


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
