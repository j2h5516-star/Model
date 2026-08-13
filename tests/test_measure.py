"""
test_measure.py — 측정용 실데이터 저장(연료 파이프라인) 검증
============================================================

전략.md 8장 1단계: 배포된 앱이 조정 EPS 시계열·일봉·파싱 실패 원문을
저장소로 커밋하는 기능을 인터넷 없이 검증합니다.

실행: python3 tests/test_measure.py
"""

import io
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import forward_estimates as fe  # noqa: E402
import measure_store  # noqa: E402
import sec_fundamentals as sf  # noqa: E402

M = 1_000_000


# ---------------------------------------------------------------------------
# 가짜 데이터 (인터넷 없이 검증하기 위한 것)
# ---------------------------------------------------------------------------
def _fake_quarters() -> list[dict]:
    return [
        {
            "ticker": "TEST",
            "filing_date": "2025-01-31",          # 분기 종료일
            "announced_date": "2025-03-04",       # 8-K 발표일 — 측정의 기준 시점
            "period_label": "25 Q3",
            "revenue": 135 * M,
            "op_income": 40 * M,
            "adj_eps": 0.45,
            "gaap_eps": 0.31,
            "source": cfg.SRC_DIRECT,
            "derivation": "이 긴 설명은 스냅샷에 들어가면 안 됩니다",
            "guidance_text": "이 긴 원문도 스냅샷에 들어가면 안 됩니다",
        },
        {
            "ticker": "TEST",
            "filing_date": "2025-04-30",
            "announced_date": None,               # 발표일 없는 분기 (파생 등)
            "period_label": "25 Q4",
            "revenue": None,                      # 없는 값은 없는 채로 (창작 금지)
            "op_income": None,
            "adj_eps": None,
            "gaap_eps": None,
            "source": cfg.SRC_APPROX,
        },
    ]


def _fake_daily() -> pd.DataFrame:
    dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
    return pd.DataFrame({"Close": [10.12345, 10.5, None]}, index=dates)


def _build(reports=None, tickers=None, daily=None):
    quarters_by_ticker = {"TEST": _fake_quarters()}
    return measure_store.build_files(
        tickers if tickers is not None else ["TEST"],
        daily if daily is not None else {"TEST": _fake_daily(), cfg.BENCHMARK: _fake_daily()},
        reports or [],
        load_quarters=lambda t: quarters_by_ticker.get(t),
    )


def _snapshot(files: dict) -> dict:
    return json.loads(files[f"{cfg.MEASURE_DIR}/snapshot.json"])


# ---------------------------------------------------------------------------
# 스냅샷 내용
# ---------------------------------------------------------------------------
def test_snapshot_keeps_announced_date():
    """발표일(announced_date)은 측정의 기준 시점 — 반드시 그대로 담겨야 함."""
    files, _ = _build()
    rows = _snapshot(files)["eps"]["TEST"]
    assert rows[0]["announced_date"] == "2025-03-04", rows[0]
    assert rows[0]["adj_eps"] == 0.45
    assert rows[0]["op_income"] == 40 * M     # 85.7% 일치 재측정용으로 함께 저장
    assert rows[1]["announced_date"] is None  # 없는 값은 없는 채로 (창작 금지)
    assert rows[1]["adj_eps"] is None


def test_snapshot_drops_bulky_text_fields():
    """긴 설명·원문 필드는 스냅샷에 들어가면 안 됨 (파일 크기 폭발 방지)."""
    files, _ = _build()
    rows = _snapshot(files)["eps"]["TEST"]
    for row in rows:
        assert "derivation" not in row
        assert "guidance_text" not in row


def test_prices_are_daily_and_rounded():
    """일봉 종가가 날짜와 짝을 이루고, 빈 날은 빠지고, 4자리로 반올림됨."""
    files, _ = _build()
    prices = _snapshot(files)["prices"]["TEST"]
    assert prices["dates"] == ["2025-01-02", "2025-01-03"]   # None 종가는 제외
    assert prices["close"] == [10.1235, 10.5]
    assert len(prices["dates"]) == len(prices["close"])


def test_benchmark_included_for_excess_return():
    """기준지수(SPY)가 없으면 초과수익을 못 재므로 반드시 포함."""
    files, _ = _build()
    assert cfg.BENCHMARK in _snapshot(files)["prices"]


def test_missing_ticker_stays_empty_not_fabricated():
    """캐시가 없는 종목은 빈 목록으로 — 지어내지 않고, 요약에 이름을 밝힘."""
    files, summary = _build(tickers=["TEST", "NOCACHE"])
    snap = _snapshot(files)
    assert snap["eps"]["NOCACHE"] == []
    assert "NOCACHE" in summary


def test_raw_failure_texts_become_files():
    """파싱 실패 원문이 종목·날짜 이름의 파일로 담김 (파서 수정의 실물 자료)."""
    reports = [
        {
            "ticker": "TEST",
            "raw_texts": [
                {"filing_date": "2025-03-04", "url": "https://예시", "text": "원문 내용"}
            ],
        }
    ]
    files, summary = _build(reports=reports)
    path = f"{cfg.MEASURE_DIR}/raw/TEST_2025-03-04.txt"
    assert path in files, list(files)
    assert "원문 내용" in files[path]
    assert "https://예시" in files[path]      # 출처를 함께 남김
    assert "1건" in summary


# ---------------------------------------------------------------------------
# 실패 원문 보관 (sec_fundamentals 쪽)
# ---------------------------------------------------------------------------
def test_keep_raw_text_respects_caps():
    """종목당 건수 상한과 글자 수 상한을 지켜야 저장소가 무한히 안 커짐."""
    report = sf.new_report("TEST")
    long_text = "가" * (cfg.MEASURE_RAW_TEXT_CAP + 500)
    for i in range(cfg.MEASURE_RAW_MAX + 3):
        sf._keep_raw_text(report, f"2025-0{i + 1}-01", "url", long_text)
    assert len(report["raw_texts"]) == cfg.MEASURE_RAW_MAX
    for kept in report["raw_texts"]:
        assert len(kept["text"]) == cfg.MEASURE_RAW_TEXT_CAP


# ---------------------------------------------------------------------------
# 가이던스 EPS 읽기 (H3 측정용 — 실물 문장으로 검증)
# ---------------------------------------------------------------------------
def test_guidance_eps_real_sndk_range():
    """실물 샌디스크 2026-08-05 — 분기 가이던스 EPS 범위를 그대로 읽어야 함."""
    text = (
        "Expect first quarter 2027 revenue to be in the range of $10.30 billion "
        "to $10.80 billion, with expected Non-GAAP diluted net income per share "
        "to be in the range of $44.00 to $46.00."
    )
    guid = fe.parse_guidance_eps(text)
    assert guid["low"] == 44.0 and guid["high"] == 46.0, guid
    assert guid["mid"] == 45.0


def test_guidance_eps_annual_sentence_is_skipped():
    """연간(full year) 가이던스를 분기 것으로 착각하면 안 됨."""
    text = (
        "For the full year 2026, the company expects non-GAAP earnings per "
        "share in the range of $70.00 to $72.00 for the fourth quarter update."
    )
    guid = fe.parse_guidance_eps(text)
    assert guid["mid"] is None, guid


def test_guidance_eps_absent_when_company_gives_none():
    """실물 CRDO — 매출·GM 가이던스만 주는 회사는 없음(None)이 정답 (창작 금지)."""
    text = (
        "First Quarter of Fiscal Year 2027 Financial Outlook. "
        "Revenue is expected to be between $465.0 million and $475.0 million. "
        "Non-GAAP gross margin is expected to be between 67.0% and 69.0%."
    )
    guid = fe.parse_guidance_eps(text)
    assert guid["mid"] is None, guid


def test_guidance_eps_gaap_only_is_not_taken():
    """GAAP EPS 가이던스만 있으면 조정 EPS 가이던스가 아님 → 없음."""
    text = (
        "For the second quarter, GAAP diluted earnings per share is expected "
        "to be in the range of $0.09 to $0.11."
    )
    guid = fe.parse_guidance_eps(text)
    assert guid["mid"] is None, guid


def test_guidance_eps_loss_range_in_parens():
    """괄호 표기 적자 가이던스는 음수로 읽어야 함."""
    text = (
        "For the third quarter, non-GAAP net loss per share is expected to be "
        "in the range of ($0.10) to ($0.05)."
    )
    guid = fe.parse_guidance_eps(text)
    assert guid["low"] == -0.10 and guid["high"] == -0.05, guid


def test_snapshot_rows_carry_guidance_fields():
    """스냅샷 분기 행에 가이던스 EPS 가 원문에서 읽혀 담겨야 함."""
    quarters = [
        {
            "filing_date": "2026-06-30",
            "announced_date": "2026-08-05",
            "adj_eps": 39.25,
            "source": cfg.SRC_DIRECT,
            "guidance_text": (
                "Expect first quarter 2028 non-GAAP diluted net income per "
                "share to be in the range of $44.00 to $46.00."
            ),
        },
        {"filing_date": "2026-03-31", "adj_eps": 30.0, "source": cfg.SRC_DIRECT},
    ]
    rows = measure_store.eps_rows(quarters)
    assert rows[0]["guid_eps_mid"] == 45.0, rows[0]
    assert rows[1]["guid_eps_mid"] is None      # 가이던스 없으면 없음


# ---------------------------------------------------------------------------
# 매출·조정 EBITDA 가이던스 (대책 2 — EPS 가이던스를 안 주는 회사 커버)
# ---------------------------------------------------------------------------
def test_guidance_revenue_real_amba_between_range():
    """실물 AMBA 2024-08-27 — 'between $77.0 million and $81.0 million'.

    ① 'and' 구분자(between 형식)를 읽어야 하고
    ② "third quarter of fiscal year 2025" 는 연간이 아니라 분기 전망입니다
       (fiscal year 라는 낱말만 보고 버리면 안 됩니다).
    """
    text = (
        "Based on information available as of today, Ambarella is offering "
        "the following guidance for the third quarter of fiscal year 2025, "
        "ending October 31, 2024: \n \n\n•   Revenue is expected to be "
        "between $77.0 million and $81.0 million.\n"
    )
    guid = fe.parse_guidance_revenue(text)
    assert guid["low"] == 77.0e6 and guid["high"] == 81.0e6, guid
    assert guid["mid"] == 79.0e6, guid


def test_guidance_revenue_annual_only_is_skipped():
    """분기 언급이 없는 연간 전망은 분기 가이던스로 쓰면 안 됩니다."""
    text = "For the full year, the company expects revenue of $4.5 billion to $4.7 billion."
    guid = fe.parse_guidance_revenue(text)
    assert guid["mid"] is None, guid


def test_guidance_ebitda_range():
    """ZETA류 — 매출·조정 EBITDA 로만 가이던스를 주는 회사의 형식."""
    text = (
        "For the fourth quarter, the company expects revenue of $295 million "
        "to $305 million and adjusted EBITDA of $58.0 million to $62.0 million."
    )
    guid = fe.parse_guidance_ebitda(text)
    assert guid["low"] == 58.0e6 and guid["high"] == 62.0e6, guid
    assert guid["mid"] == 60.0e6, guid


def test_snapshot_rows_carry_dollar_guidance_fields():
    """매출·EBITDA 가이던스가 snapshot 분기 행까지 도착해야 합니다 (배관 시험)."""
    quarters = [{
        "filing_date": "2026-06-30",
        "adj_eps": 1.0,
        "source": cfg.SRC_DIRECT,
        "guidance_text": (
            "For the second quarter, the company expects revenue to be "
            "between $500 million and $520 million and adjusted EBITDA "
            "of $100.0 million to $110.0 million."
        ),
    }]
    row = measure_store.eps_rows(quarters)[0]
    assert row["guid_rev_mid"] == 510.0e6, row
    assert row["guid_ebitda_mid"] == 105.0e6, row


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
    print(f"\n측정 저장 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)

