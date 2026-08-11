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
import urllib.error

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import measure_store  # noqa: E402
import sec_fundamentals as sf  # noqa: E402
import storage  # noqa: E402

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
# GitHub 커밋 (가짜 네트워크로 성공·실패 경로 확인)
# ---------------------------------------------------------------------------
class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code: int):
    return urllib.error.HTTPError("url", code, "msg", None, io.BytesIO(b"{}"))


def test_commit_file_new_file_ok():
    """파일이 아직 없으면(404) 새로 만들며 성공해야 함."""
    calls = []

    def opener(request, timeout=0):
        calls.append(request)
        if request.get_method() == "GET":
            raise _http_error(404)
        return _FakeResponse(b"{}")

    ok, msg = storage.commit_file_github(
        "data/measure/snapshot.json", "{}", "토큰", "user/repo", "메시지", opener=opener
    )
    assert ok, msg
    assert calls[-1].get_method() == "PUT"
    payload = json.loads(calls[-1].data.decode())
    assert payload["message"] == "메시지"
    assert "sha" not in payload               # 새 파일에는 sha 가 없어야 함


def test_commit_file_updates_existing_with_sha():
    """이미 있는 파일은 버전 번호(sha)를 붙여 덮어써야 함."""
    calls = []

    def opener(request, timeout=0):
        calls.append(request)
        if request.get_method() == "GET":
            return _FakeResponse(json.dumps({"sha": "abc123"}).encode())
        return _FakeResponse(b"{}")

    ok, _ = storage.commit_file_github(
        "user_tickers.json", "{}", "토큰", "user/repo", "메시지", opener=opener
    )
    assert ok
    payload = json.loads(calls[-1].data.decode())
    assert payload["sha"] == "abc123"


def test_commit_file_permission_error_reported():
    """토큰 권한 문제(403)는 실패로 알리고, 이유를 사용자 말로 전달."""
    def opener(request, timeout=0):
        raise _http_error(403)

    ok, msg = storage.commit_file_github(
        "x.json", "{}", "토큰", "user/repo", "메시지", opener=opener
    )
    assert not ok
    assert "403" in msg


def test_commit_tickers_still_works_via_generic():
    """기존 종목 목록 저장이 범용 함수 위에서 그대로 동작해야 함."""
    calls = []

    def opener(request, timeout=0):
        calls.append(request)
        if request.get_method() == "GET":
            raise _http_error(404)
        return _FakeResponse(b"{}")

    ok, _ = storage.commit_tickers_github(["nvda", "NVDA", "amd"], "토큰", "user/repo",
                                          opener=opener)
    assert ok
    payload = json.loads(calls[-1].data.decode())
    import base64
    saved = json.loads(base64.b64decode(payload["content"]).decode())
    assert saved["tickers"] == ["NVDA", "AMD"]     # 대문자 통일 + 중복 제거


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
