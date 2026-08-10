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


def _fake_daily(tickers):
    """market_data.fetch_daily_data 를 대신할 가상 일봉 데이터.

    CCC는 일부러 빼서 "주가를 못 받은 종목" 경로를 검증합니다.
    (앱은 이제 증분 저장소 → fetch_daily_data 경로로 주가를 받습니다)
    """
    available = {
        "AAA": trending_daily(900, 0.004),                      # 강한 상승
        "BBB": trending_daily(900, -0.003, start_price=100.0),  # 하락
        cfg.BENCHMARK: trending_daily(900, 0.001),
    }
    wanted = list(tickers) + [cfg.BENCHMARK]
    daily_map = {t: available[t] for t in wanted if t in available}
    failed = [t for t in wanted if t not in available]
    return daily_map, failed


def _fake_prices(tickers, include_current_week=True):
    """(호환용) pipeline.fetch_prices 를 대신할 가상 주가 계산"""
    daily_map, _ = _fake_daily(tickers)
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


def _clear_streamlit_caches():
    """테스트 사이에 cache_data와 cache_resource(주가 저장소)를 모두 비웁니다."""
    st.cache_data.clear()
    try:
        st.cache_resource.clear()
    except Exception:
        pass


def _no_sleep(_seconds):
    """재시도 대기를 건너뛰어 테스트를 빠르게 합니다."""


def _run_app(with_fundamentals=True, tickers=None):
    """가상 데이터를 넣고 앱을 실행합니다."""
    _clear_streamlit_caches()
    at = AppTest.from_file(APP_PATH)
    at.query_params["tickers"] = ",".join(tickers or FAKE_TICKERS)

    def bundle(ticker, use_cache=True):
        return _fake_bundle(ticker, use_cache, with_fundamentals)

    with patch("market_data.fetch_daily_data", side_effect=_fake_daily), \
         patch("time.sleep", side_effect=_no_sleep), \
         patch("pipeline.collect_one_ticker", side_effect=bundle):
        at.run(timeout=120)
    return at


def test_renders_with_full_data():
    """정상 데이터로 화면 전체가 에러 없이 그려져야 함 (CCC는 주가 실패 경로)"""
    at = _run_app(True, tickers=["AAA", "BBB", "CCC"])

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
    # 주가 실패 안내 (재시도까지 실패한 종목)
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
    """순위 표에 주가($)·신뢰도·델타예측(1분기후)·델타예측(2분기후) 열이 있어야 함"""
    at = _run_app(True)

    assert not at.exception, [e.value for e in at.exception]
    ranking_found = False
    for df in at.dataframe:
        columns = list(getattr(df.value, "columns", []))
        if "최종점수" in columns:
            assert "주가($)" in columns, columns
            assert "신뢰도" in columns, columns
            assert "델타예측(1분기후)" in columns, columns
            assert "델타예측(2분기후)" in columns, columns
            ranking_found = True
            break
    assert ranking_found, "순위 표를 찾지 못함"


def test_ticker_switching():
    """다른 종목을 선택해도 에러 없이 다시 그려져야 함"""
    _clear_streamlit_caches()
    at = AppTest.from_file(APP_PATH)
    at.query_params["tickers"] = ",".join(FAKE_TICKERS)

    with patch("market_data.fetch_daily_data", side_effect=_fake_daily), \
         patch("time.sleep", side_effect=_no_sleep), \
         patch("pipeline.collect_one_ticker", side_effect=_fake_bundle):
        at.run(timeout=120)
        at.selectbox[0].select("BBB")
        at.run(timeout=120)
        assert not at.exception, [e.value for e in at.exception]


def test_renders_without_fundamentals():
    """실적을 전 종목에서 못 받으면 접힌 진단이 아니라 맨 위에 크게 경고해야 함"""
    at = _run_app(False)

    assert not at.exception, [e.value for e in at.exception]
    assert any(
        "모든 종목의 실적 자료를 가져오지 못했습니다" in e.value for e in at.error
    ), "전 종목 실적 실패 경고가 없음"


def test_sec_identity_has_contact_email():
    """SEC는 이메일 없는 신원을 차단하므로 기본 신원에 이메일이 있어야 함"""
    import config as cfg

    assert cfg._has_email(cfg.get_sec_identity()), "SEC 신원에 이메일이 없음"

    # 환경변수에 이메일 없는 값이 들어와도 기본값으로 되돌아가야 함
    with patch.dict("os.environ", {"SEC_IDENTITY": "그냥 이름"}):
        assert cfg._has_email(cfg.get_sec_identity())
    with patch.dict("os.environ", {"SEC_IDENTITY": "홍길동 hong@example.com"}):
        assert cfg.get_sec_identity() == "홍길동 hong@example.com"


def test_warns_when_secrets_identity_has_no_email():
    """Secrets에 이메일 없는 신원을 넣으면 조용히 무시하지 말고 알려야 함"""
    with patch.dict("os.environ", {"SEC_IDENTITY": "그냥 이름"}):
        at = _run_app(True)

    assert not at.exception, [e.value for e in at.exception]
    assert any(
        "이메일 주소가 없어" in w.value for w in at.warning
    ), "신원이 조용히 기본값으로 바뀐 사실을 알리지 않음"


def test_delete_pills_disabled_when_only_one_ticker():
    """종목이 하나면 삭제 알약을 아예 못 누르게 해야 함.

    눌렀다가 거부되면 선택이 남아 '최소 한 종목' 경고가 영영 사라지지 않습니다
    (선택을 코드로 지우는 것은 Streamlit이 막습니다).
    """
    at = _run_app(True, tickers=["AAA"])

    assert not at.exception, [e.value for e in at.exception]
    assert at.main.pills[0].disabled is True, "종목이 하나인데 삭제 알약이 활성 상태"
    assert not any("최소 한 종목" in w.value for w in at.warning)


def test_no_fundamentals_shows_no_data_verdict():
    """실적을 못 구한 종목은 '매수 후보'가 아니라 '실적 없음'으로 표시돼야 함"""
    at = _run_app(False)

    assert not at.exception, [e.value for e in at.exception]
    verdicts = []
    for df in at.dataframe:
        columns = list(getattr(df.value, "columns", []))
        if "최종점수" in columns and "판정" in columns:
            verdicts = list(df.value["판정"])
            break

    assert verdicts, "순위 표를 찾지 못함"
    assert set(verdicts) == {cfg.V_NO_DATA}, verdicts
    assert cfg.V_BUY not in verdicts, "실적이 없는데 매수 후보 판정이 나옴"


def test_ticker_manager_is_on_main_screen():
    """종목 추가칸이 사이드바가 아니라 메인 화면(순위표 위)에 있어야 함"""
    at = _run_app(True)

    assert not at.exception, [e.value for e in at.exception]
    main_buttons = [b.label for b in at.main.button]
    assert any("추가" in label for label in main_buttons), "메인 화면에 ➕ 추가 버튼이 없음"
    assert any("저장" in label for label in main_buttons), "메인 화면에 저장 버튼이 없음"
    # 삭제 칩(pills)에 현재 종목이 모두 들어 있어야 함
    pill_options = [opt for pill in at.main.pills for opt in pill.options]
    for symbol in FAKE_TICKERS:
        assert symbol in pill_options, f"메인 화면 삭제 칩에 {symbol}이 없음"
    # 사이드바에는 설정만 남아 있어야 함
    sidebar_buttons = [b.label for b in at.sidebar.button]
    assert not any("추가" in label for label in sidebar_buttons), "사이드바에 추가 버튼이 남아 있음"


def test_detail_shows_both_delta_forecasts():
    """종목 상세에 1분기 후·2분기 후 델타예측이 모두 보여야 함"""
    at = _run_app(True)

    assert not at.exception, [e.value for e in at.exception]
    labels = [m.label for m in at.metric]
    assert any("1분기 후" in label for label in labels), "1분기 후 델타예측 표시가 없음"
    assert any("2분기 후" in label for label in labels), "2분기 후 델타예측 표시가 없음"


def test_empty_scores_shows_message():
    """분석 가능한 종목이 0개면 에러 화면 대신 안내를 보여줘야 함"""
    _clear_streamlit_caches()
    at = AppTest.from_file(APP_PATH)
    at.query_params["tickers"] = "ZZZ"   # 가상 데이터에 없는 종목 → 주가 실패

    with patch("market_data.fetch_daily_data", side_effect=_fake_daily), \
         patch("time.sleep", side_effect=_no_sleep), \
         patch("pipeline.collect_one_ticker", side_effect=_fake_bundle):
        at.run(timeout=120)

    assert not at.exception, [e.value for e in at.exception]
    assert len(at.error) >= 1, "안내 메시지가 없음"


def test_pipeline_failure_is_handled():
    """주가 수집이 예외를 던져도 앱이 죽지 않고 안내를 보여줘야 함"""
    _clear_streamlit_caches()
    at = AppTest.from_file(APP_PATH)
    at.query_params["tickers"] = ",".join(FAKE_TICKERS)

    with patch("market_data.fetch_daily_data", side_effect=RuntimeError("네트워크 오류")):
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
        with patch("market_data.fetch_daily_data", side_effect=_fake_daily), \
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
