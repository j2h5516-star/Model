"""
test_app_render.py — 대시보드 화면이 실제로 그려지는지 검증
==========================================================

인터넷에 접속하지 않고 가상 데이터를 넣어 app.py를 통째로 실행해 보고,
화면이 에러 없이 그려지는지 확인합니다.

실행: python3 tests/test_app_render.py
"""

import os
import sys
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import config as cfg  # noqa: E402
import pipeline  # noqa: E402
import scoring  # noqa: E402
from fixtures import make_quarters, trending_daily  # noqa: E402

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "app.py")
M = 1_000_000


def _fake_result(with_fundamentals: bool = True):
    """가상의 파이프라인 실행 결과를 만듭니다."""
    import market_data as md

    daily_map = {
        "AAA": trending_daily(900, 0.004),                      # 강한 상승
        "BBB": trending_daily(900, -0.003, start_price=100.0),  # 하락
        cfg.BENCHMARK: trending_daily(900, 0.001),
    }
    price_map, weekly_map = md.analyze_prices(daily_map, tickers=["AAA", "BBB"])

    scores = {}
    for ticker in ("AAA", "BBB"):
        if with_fundamentals:
            quarters = make_quarters(
                [100 * M, 115 * M, 140 * M], margins=[50.0, 50.5, 51.0]
            )
            quarters[-1]["source"] = cfg.SRC_DIRECT
            forward = {
                "forward_op_income": 170 * M,
                "revision": 1,
                "basis": cfg.SRC_GUIDANCE,
                "detail": "회사 가이던스 기준",
            }
        else:
            quarters, forward = [], {}
        scores[ticker] = scoring.build_score(ticker, quarters, forward, price_map[ticker])

    return {
        "ranking": pipeline.build_ranking_table(scores),
        "scores": scores,
        "weekly_map": weekly_map,
        "failed": ["CCC"],
        "no_fundamentals": [] if with_fundamentals else ["AAA", "BBB"],
        "updated_at": datetime(2026, 8, 9, 12, 0),
    }


def test_renders_with_full_data():
    """정상 데이터로 화면 전체가 에러 없이 그려져야 함"""
    st.cache_data.clear()
    with patch("pipeline.run_pipeline", return_value=_fake_result(True)):
        at = AppTest.from_file(APP_PATH)
        at.run(timeout=90)

        assert not at.exception, [e.value for e in at.exception]
        assert len(at.dataframe) >= 1, "순위 표가 없음"
        assert len(at.selectbox) >= 1, "종목 선택 박스가 없음"
        # 카드 그리드가 HTML로 그려졌는지 확인
        assert any("card-grid" in m.value for m in at.markdown), "종목 카드가 없음"
        # 데이터 실패 안내가 떴는지 확인
        assert any("CCC" in w.value for w in at.warning), "실패 종목 안내가 없음"


def test_ticker_switching():
    """다른 종목을 선택해도 에러 없이 다시 그려져야 함"""
    st.cache_data.clear()
    with patch("pipeline.run_pipeline", return_value=_fake_result(True)):
        at = AppTest.from_file(APP_PATH)
        at.run(timeout=90)
        at.selectbox[0].select("BBB")
        at.run(timeout=90)
        assert not at.exception, [e.value for e in at.exception]


def test_renders_without_fundamentals():
    """실적 데이터를 하나도 못 받아도 주가 지표만으로 화면이 그려져야 함"""
    st.cache_data.clear()
    with patch("pipeline.run_pipeline", return_value=_fake_result(False)):
        at = AppTest.from_file(APP_PATH)
        at.run(timeout=90)

        assert not at.exception, [e.value for e in at.exception]
        # 실적 없음 안내가 떴는지 확인
        assert any("실적 데이터를 찾지 못" in i.value for i in at.info), "실적 없음 안내가 없음"


def test_empty_scores_shows_message():
    """분석 가능한 종목이 0개면 에러 화면 대신 안내를 보여줘야 함"""
    st.cache_data.clear()
    empty = {
        "ranking": pipeline.build_ranking_table({}),
        "scores": {},
        "weekly_map": {},
        "failed": list(cfg.TICKERS),
        "no_fundamentals": [],
        "updated_at": datetime(2026, 8, 9, 12, 0),
    }
    with patch("pipeline.run_pipeline", return_value=empty):
        at = AppTest.from_file(APP_PATH)
        at.run(timeout=90)

        assert not at.exception, [e.value for e in at.exception]
        assert len(at.error) >= 1, "안내 메시지가 없음"


def test_pipeline_failure_is_handled():
    """파이프라인이 예외를 던져도 앱이 죽지 않고 안내를 보여줘야 함"""
    st.cache_data.clear()
    with patch("pipeline.run_pipeline", side_effect=RuntimeError("네트워크 오류")):
        at = AppTest.from_file(APP_PATH)
        at.run(timeout=90)

        assert not at.exception, [e.value for e in at.exception]
        assert len(at.error) >= 1, "에러 안내가 없음"


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
    print(f"\n화면 렌더링 테스트: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
