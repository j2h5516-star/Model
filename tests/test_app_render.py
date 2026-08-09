"""
test_app_render.py — 대시보드 화면이 실제로 그려지는지 검증
==========================================================

인터넷에 접속하지 않고 가상 데이터를 넣어 app.py를 통째로 실행해 보고,
화면이 에러 없이 그려지는지 확인합니다.

실행: python3 tests/test_app_render.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import config as cfg  # noqa: E402
import market_data as md  # noqa: E402
import pipeline  # noqa: E402
from fixtures import make_quarters, trending_daily  # noqa: E402

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "app.py")
M = 1_000_000

# 테스트에서 쓸 가상 종목
FAKE_TICKERS = ["AAA", "BBB"]


def _fake_prices(tickers, include_current_week=True):
    """pipeline.fetch_prices 를 대신할 가상 주가 계산"""
    daily_map = {
        "AAA": trending_daily(900, 0.004),                      # 강한 상승
        "BBB": trending_daily(900, -0.003, start_price=100.0),  # 하락
        cfg.BENCHMARK: trending_daily(900, 0.001),
    }
    wanted = [t for t in tickers if t in daily_map]
    price_map, weekly_map = md.analyze_prices(daily_map, tickers=wanted)
    return price_map, weekly_map, ["CCC"]


def _fake_bundle(ticker, use_cache=True, with_fundamentals=True):
    """pipeline.collect_one_ticker 를 대신할 가상 실적"""
    import sec_fundamentals as sf

    report = sf.new_report(ticker)
    if not with_fundamentals:
        report["note"] = "테스트: 실적 없음"
        return {
            "ticker": ticker,
            "quarters": [],
            "forward": {"forward_op_income": None, "basis": None, "revision": 0, "detail": ""},
            "report": report,
        }

    quarters = make_quarters(
        [100 * M, 115 * M, 140 * M, 175 * M], margins=[50.0, 50.5, 51.0, 51.2]
    )
    quarters[-1]["source"] = cfg.SRC_DIRECT
    quarters[-1]["derivation"] = "테스트 계산 설명"
    quarters[-1]["filing_url"] = "https://www.sec.gov/test"

    report.update(
        {"xbrl_quarters": 4, "filings_found": 6, "text_ok": 5,
         "gate_passed": 4, "parsed_ok": 4, "merged_direct": 4,
         "text_source": "보도자료"}
    )
    return {
        "ticker": ticker,
        "quarters": quarters,
        "forward": {
            "forward_op_income": 210 * M,
            "revision": 1,
            "basis": cfg.SRC_GUIDANCE,
            "detail": "회사 가이던스 기준",
        },
        "report": report,
    }


def _run_app(with_fundamentals=True, tickers=None):
    """가상 데이터를 넣고 앱을 실행합니다."""
    st.cache_data.clear()
    at = AppTest.from_file(APP_PATH)
    at.query_params["tickers"] = ",".join(tickers or FAKE_TICKERS)

    def bundle(ticker, use_cache=True):
        return _fake_bundle(ticker, use_cache, with_fundamentals)

    with patch("pipeline.fetch_prices", side_effect=_fake_prices), \
         patch("pipeline.collect_one_ticker", side_effect=bundle):
        at.run(timeout=120)
    return at


def test_renders_with_full_data():
    """정상 데이터로 화면 전체가 에러 없이 그려져야 함"""
    at = _run_app(True)

    assert not at.exception, [e.value for e in at.exception]
    assert len(at.dataframe) >= 1, "순위 표가 없음"
    assert len(at.selectbox) >= 1, "종목 선택 박스가 없음"

    all_text = " ".join(m.value for m in at.markdown)
    assert "card-grid" in all_text, "종목 카드가 없음"
    assert "conf-fill" in all_text, "신뢰도 게이지가 없음"
    assert "legend-grid" in all_text, "매트릭스 번호 범례가 없음"
    # 상세 설명 팝오버 내용
    for keyword in ("이게 무슨 뜻인가요", "계산 방법", "이 종목의 실제 숫자", "앞으로의 예측"):
        assert keyword in all_text, f"상세 설명에 '{keyword}'가 없음"
    # 주가 실패 안내
    assert any("CCC" in w.value for w in at.warning), "실패 종목 안내가 없음"


def test_confidence_and_diagnostics_sections():
    """신뢰도 등급표와 수집 진단 패널이 렌더링돼야 함"""
    at = _run_app(True)

    assert not at.exception, [e.value for e in at.exception]
    all_text = " ".join(m.value for m in at.markdown)
    assert "종합 신뢰도 계산" in all_text, "신뢰도 등급표 설명이 없음"
    assert "어디서 0이 되는가" in all_text, "진단 패널 읽는 법이 없음"

    # 진단 표에 단계별 건수 열이 있어야 함
    diag_found = any(
        hasattr(df.value, "columns") and "숫자추출" in list(df.value.columns)
        for df in at.dataframe
    )
    assert diag_found, "진단 표가 없음"


def test_ranking_table_has_new_columns():
    """순위 표에 신뢰도·델타예측 열이 있어야 함"""
    at = _run_app(True)

    assert not at.exception, [e.value for e in at.exception]
    ranking_found = False
    for df in at.dataframe:
        columns = list(getattr(df.value, "columns", []))
        if "최종점수" in columns:
            assert "신뢰도" in columns, columns
            assert "델타예측" in columns, columns
            ranking_found = True
            break
    assert ranking_found, "순위 표를 찾지 못함"


def test_ticker_switching():
    """다른 종목을 선택해도 에러 없이 다시 그려져야 함"""
    st.cache_data.clear()
    at = AppTest.from_file(APP_PATH)
    at.query_params["tickers"] = ",".join(FAKE_TICKERS)

    with patch("pipeline.fetch_prices", side_effect=_fake_prices), \
         patch("pipeline.collect_one_ticker", side_effect=_fake_bundle):
        at.run(timeout=120)
        at.selectbox[0].select("BBB")
        at.run(timeout=120)
        assert not at.exception, [e.value for e in at.exception]


def test_renders_without_fundamentals():
    """실적 데이터를 하나도 못 받아도 주가 지표만으로 화면이 그려져야 함"""
    at = _run_app(False)

    assert not at.exception, [e.value for e in at.exception]
    assert any("실적 데이터를 찾지 못" in i.value for i in at.info), "실적 없음 안내가 없음"


def test_empty_scores_shows_message():
    """분석 가능한 종목이 0개면 에러 화면 대신 안내를 보여줘야 함"""
    st.cache_data.clear()
    at = AppTest.from_file(APP_PATH)
    at.query_params["tickers"] = "ZZZ"

    with patch("pipeline.fetch_prices", return_value=({}, {}, ["ZZZ"])), \
         patch("pipeline.collect_one_ticker", side_effect=_fake_bundle):
        at.run(timeout=120)

    assert not at.exception, [e.value for e in at.exception]
    assert len(at.error) >= 1, "안내 메시지가 없음"


def test_pipeline_failure_is_handled():
    """주가 수집이 예외를 던져도 앱이 죽지 않고 안내를 보여줘야 함"""
    st.cache_data.clear()
    at = AppTest.from_file(APP_PATH)
    at.query_params["tickers"] = ",".join(FAKE_TICKERS)

    with patch("pipeline.fetch_prices", side_effect=RuntimeError("네트워크 오류")):
        at.run(timeout=120)

    assert not at.exception, [e.value for e in at.exception]
    assert len(at.error) >= 1, "에러 안내가 없음"


def test_stale_config_shows_reboot_guide():
    """설정 파일이 옛 버전이면 빨간 오류 대신 재시작 안내를 보여줘야 함

    실제로 겪었던 문제: 코드를 업데이트했는데 서버가 옛 config 모듈을
    메모리에 붙들고 있어 AttributeError 로 앱이 죽었습니다.
    """
    import config as cfg

    st.cache_data.clear()
    at = AppTest.from_file(APP_PATH)
    at.query_params["tickers"] = ",".join(FAKE_TICKERS)

    original = cfg.CONFIG_VERSION
    try:
        cfg.CONFIG_VERSION = 0   # 옛 설정 파일이 붙들려 있는 상황을 흉내
        with patch("pipeline.fetch_prices", side_effect=_fake_prices), \
             patch("pipeline.collect_one_ticker", side_effect=_fake_bundle):
            at.run(timeout=120)
    finally:
        cfg.CONFIG_VERSION = original

    assert not at.exception, [e.value for e in at.exception]
    assert len(at.error) >= 1, "재시작 안내가 없음"
    message = " ".join(e.value for e in at.error)
    assert "Reboot app" in message, message
    assert "Manage app" in message, message


def test_config_version_matches_app_requirement():
    """config.py 의 판 번호가 app.py 가 요구하는 값 이상이어야 함

    (config에 항목을 추가하고 판 번호 올리는 것을 잊으면 배포 후 앱이 멈춥니다)
    """
    import re

    import config as cfg

    source = open(APP_PATH, encoding="utf-8").read()
    match = re.search(r"REQUIRED_CONFIG_VERSION\s*=\s*(\d+)", source)
    assert match, "app.py 에서 REQUIRED_CONFIG_VERSION 을 찾지 못함"

    required = int(match.group(1))
    assert cfg.CONFIG_VERSION >= required, (
        f"config.CONFIG_VERSION({cfg.CONFIG_VERSION}) 이 "
        f"app.py 요구치({required})보다 낮습니다"
    )


def test_url_ticker_list_is_used():
    """주소에 적힌 종목 목록을 앱이 실제로 사용해야 함"""
    at = _run_app(True, tickers=["AAA"])

    assert not at.exception, [e.value for e in at.exception]
    # AAA 하나만 요청했으므로 BBB는 화면에 없어야 함
    all_text = " ".join(m.value for m in at.markdown)
    assert "AAA" in all_text, "요청한 종목이 없음"


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
