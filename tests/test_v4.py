"""
test_v4.py — v4 신규 기능 검증
==============================

  · 월가 컨센서스 수집 (가짜 yfinance 로 검증)
  · 가이던스 역탐색 버그 수정
  · 델타가속예측 (2분기 경로)
  · 리비전 속도 점수
  · 주가 증분 저장소 (티커 추가 시 새 종목만 수집)
  · 종목 목록 저장/불러오기
  · 8-K 짝짓기 창 확대·실패 기록

실행: python3 tests/test_v4.py
"""

import json
import os
import sys
import tempfile
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import forward_estimates as fe  # noqa: E402
import pipeline  # noqa: E402
import scoring  # noqa: E402
import sec_fundamentals as sf  # noqa: E402
import storage  # noqa: E402
from fixtures import make_daily, make_quarters  # noqa: E402

M = 1_000_000


# ---------------------------------------------------------------------------
# 가짜 yfinance (네트워크 없이 컨센서스 검증)
# ---------------------------------------------------------------------------
class _FakeTicker:
    """yfinance.Ticker 를 흉내 냅니다."""

    def __init__(self, symbol):
        index = ["0q", "+1q", "0y", "+1y"]
        self.revenue_estimate = pd.DataFrame(
            {"avg": [500 * M, 550 * M, 2000 * M, 2400 * M],
             "numberOfAnalysts": [21, 19, 24, 22]},
            index=index,
        )
        self.earnings_estimate = pd.DataFrame(
            {"avg": [1.25, 1.40, 5.0, 6.1], "numberOfAnalysts": [22, 20, 25, 23]},
            index=index,
        )
        self.eps_trend = pd.DataFrame(
            {"current": [1.25, 1.40, 5.0, 6.1],
             "30daysAgo": [1.20, 1.35, 4.9, 5.9]},
            index=index,
        )
        self.eps_revisions = pd.DataFrame(
            {"upLast30days": [8, 6, 9, 7], "downLast30days": [1, 2, 1, 2]},
            index=index,
        )


class _EmptyTicker:
    """컨센서스가 하나도 없는 종목 (야후가 거부하거나 커버리지 없음)."""

    revenue_estimate = None
    earnings_estimate = None
    eps_trend = None
    eps_revisions = None


def _fake_yf(ticker_class):
    """yfinance 모듈 흉내."""
    import types

    module = types.ModuleType("yfinance")
    module.Ticker = ticker_class
    return module


# ---------------------------------------------------------------------------
# 컨센서스 수집
# ---------------------------------------------------------------------------
def test_consensus_two_quarters():
    """매출 컨센서스를 다음 분기·다다음 분기 모두 가져와야 함"""
    with patch.dict(sys.modules, {"yfinance": _fake_yf(_FakeTicker)}):
        out = fe.fetch_consensus("TEST")

    assert out["revenue_0q"] == 500 * M, out
    assert out["revenue_1q"] == 550 * M, out
    assert out["analysts_0q"] == 21, out
    # 리비전 속도: (1.25 − 1.20) / 1.20 × 100 ≈ +4.17%
    assert abs(out["revision_velocity_pct"] - 4.1667) < 0.01, out
    assert out["revision"] == 1, out       # 속도가 +0.5% 초과 → 상향
    assert out["errors"] == [], out


def test_consensus_empty_records_errors():
    """컨센서스가 없으면 실패 사유를 errors에 남겨야 함 (진단 패널용)"""
    with patch.dict(sys.modules, {"yfinance": _fake_yf(_EmptyTicker)}):
        out = fe.fetch_consensus("TEST")

    assert out["revenue_0q"] is None
    assert out["errors"], out             # 사유가 최소 1개는 있어야 함


# ---------------------------------------------------------------------------
# 가이던스 역탐색 (전망이 통째로 비던 버그의 수정)
# ---------------------------------------------------------------------------
def test_guidance_found_in_previous_quarter():
    """마지막 분기 행(XBRL 뼈대)에 가이던스가 없어도 그 앞 행에서 찾아야 함"""
    quarters = make_quarters([100 * M, 115 * M, 140 * M])
    quarters[-2]["guidance_text"] = "We expect revenue of $180 million to $190 million."
    quarters[-1]["guidance_text"] = ""   # 최근 행은 XBRL 뼈대 (과거 버그의 원인)

    text = fe.find_latest_guidance_text(quarters)
    assert "180 million" in text, text


def test_guidance_too_old_is_ignored():
    """2분기 넘게 과거의 가이던스는 이미 지난 분기 전망이므로 쓰지 않아야 함"""
    quarters = make_quarters([100 * M, 115 * M, 140 * M, 175 * M])
    quarters[0]["guidance_text"] = "We expect revenue of $120 million."   # 4분기 전
    quarters[-1]["guidance_text"] = ""
    quarters[-2]["guidance_text"] = ""

    assert fe.find_latest_guidance_text(quarters) == ""


def test_estimate_forward_uses_consensus_for_q2():
    """다다음 분기는 컨센서스 매출 × 평균 마진으로 계산돼야 함"""
    # 마진: 영업이익/매출 = 20% (revenues 기본값이 op×5)
    quarters = make_quarters([100 * M, 110 * M, 120 * M, 130 * M])
    with patch.dict(sys.modules, {"yfinance": _fake_yf(_FakeTicker)}):
        out = fe.estimate_forward("TEST", quarters)

    # q1: 가이던스 없음 → 컨센서스 500M × 20% = 100M
    assert out["basis"] == cfg.SRC_ESTIMATE, out
    assert abs(out["forward_op_income"] - 100 * M) < 1, out
    # q2: 550M × 20% = 110M
    assert out["basis_2"] == cfg.SRC_ESTIMATE, out
    assert abs(out["forward_op_income_2"] - 110 * M) < 1, out


# ---------------------------------------------------------------------------
# 델타가속예측 (2분기 경로)
# ---------------------------------------------------------------------------
def _accel(ops, f1, f2):
    quarters = make_quarters(list(ops))
    delta = scoring.score_delta_acceleration(quarters)
    forward = {
        "forward_op_income": f1, "forward_op_income_2": f2,
        "basis": cfg.SRC_GUIDANCE, "basis_2": cfg.SRC_ESTIMATE,
    }
    return scoring.predict_delta(quarters, forward, delta)


def test_accel_path_keep():
    """실제 +21.7% → 다음 +28.6% → 다다음 +30.6% = 가속 지속"""
    result = _accel((100 * M, 115 * M, 140 * M), 180 * M, 235 * M)
    assert result["accel_label"] == cfg.F2_ACCEL_KEEP, result


def test_accel_path_then_slow():
    """다음 분기는 가속인데 다다음에서 꺾이면 '가속 후 둔화' (정점 신호)"""
    result = _accel((100 * M, 115 * M, 140 * M), 180 * M, 200 * M)
    # 21.7 → 28.6 → 11.1
    assert result["accel_label"] == cfg.F2_ACCEL_SLOW, result


def test_accel_path_rebound():
    """다음 분기 꺾였다가 다다음에 회복하면 '둔화 후 반등' (바닥 신호)"""
    result = _accel((100 * M, 115 * M, 140 * M), 150 * M, 190 * M)
    # 21.7 → 7.1 → 26.7
    assert result["accel_label"] == cfg.F2_REBOUND, result


def test_accel_path_decel_keep():
    """두 분기 모두 증가율이 계속 낮아지면 '둔화 지속'"""
    result = _accel((100 * M, 115 * M, 140 * M), 150 * M, 152 * M)
    # 21.7 → 7.1 → 1.3
    assert result["accel_label"] == cfg.F2_DECEL_KEEP, result


def test_accel_falls_back_without_q2():
    """다다음 분기 자료가 없으면 다음 분기 예측을 그대로 써야 함"""
    result = _accel((100 * M, 115 * M, 140 * M), 180 * M, None)
    assert result["next2_qoq"] is None
    assert result["accel_label"] == result["label"], result


# ---------------------------------------------------------------------------
# 리비전 속도 점수
# ---------------------------------------------------------------------------
def test_revision_velocity_scoring():
    """속도 +5%는 만점, −5%는 0점, 0%는 중간이어야 함"""
    quarters = make_quarters([100 * M])

    def score_with(velocity):
        return scoring.score_forward(
            quarters,
            {"forward_op_income": 120 * M, "revision": 0,
             "revision_velocity_pct": velocity},
        )["score"]

    high, mid, low = score_with(5.0), score_with(0.0), score_with(-5.0)
    assert high > mid > low, (high, mid, low)
    assert abs((high - low) - cfg.W_FORWARD * 0.33) < 0.15, (high, low)


# ---------------------------------------------------------------------------
# 주가 증분 저장소 (티커 추가 버그의 해결)
# ---------------------------------------------------------------------------
def _daily_fetch_counter(calls):
    """호출된 종목을 기록하는 가짜 fetch"""

    def fetch(tickers):
        calls.append(list(tickers))
        return {t: make_daily([10, 11, 12, 13, 14]) for t in tickers}, []

    return fetch


def test_price_store_fetches_only_missing():
    """이미 저장된 종목은 다시 받지 않고, 새 종목만 받아야 함"""
    store = {}
    calls = []
    fetch = _daily_fetch_counter(calls)

    failed = pipeline.refresh_price_store(store, ["AAA", "BBB"], fetch=fetch, now=1000.0)
    assert failed == []
    assert set(calls[0]) == {"AAA", "BBB", cfg.BENCHMARK}

    # 종목 하나 추가 → 새 종목만 요청해야 함 (핵심!)
    failed = pipeline.refresh_price_store(store, ["AAA", "BBB", "NEW"], fetch=fetch, now=1010.0)
    assert failed == []
    assert calls[1] == ["NEW"], calls[1]


def test_price_store_retries_missing():
    """야후가 일부 종목을 거부하면 한 번 더 시도해야 함"""
    attempts = {"n": 0}

    def flaky_fetch(tickers):
        attempts["n"] += 1
        if attempts["n"] == 1:
            # 첫 시도: BBB 누락 (야후가 조용히 거부하는 상황)
            return {t: make_daily([10, 11, 12]) for t in tickers if t != "BBB"}, ["BBB"]
        return {t: make_daily([10, 11, 12]) for t in tickers}, []

    slept = []
    store = {}
    failed = pipeline.refresh_price_store(
        store, ["AAA", "BBB"], fetch=flaky_fetch, now=0.0, sleep=slept.append
    )
    assert failed == []
    assert "BBB" in store["data"], store["data"].keys()
    assert slept == [2.0], slept          # 재시도 전 대기했는지


def test_price_store_expires_after_ttl():
    """1시간이 지나면 다시 받아와야 함 (오래된 주가 방지)"""
    store = {}
    calls = []
    fetch = _daily_fetch_counter(calls)

    pipeline.refresh_price_store(store, ["AAA"], fetch=fetch, now=0.0)
    pipeline.refresh_price_store(store, ["AAA"], fetch=fetch, now=pipeline.PRICE_TTL_SEC + 1)
    assert len(calls) == 2, calls


# ---------------------------------------------------------------------------
# 종목 목록 저장/불러오기
# ---------------------------------------------------------------------------
def test_storage_roundtrip():
    """저장한 목록을 다시 불러올 수 있어야 함 (대소문자·중복 정리 포함)"""
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(storage, "LOCAL_FILE", os.path.join(tmp, "t.json")), \
             patch.object(storage, "REPO_FILE", os.path.join(tmp, "repo.json")):
            assert storage.load_saved_tickers() is None   # 아직 없음
            assert storage.save_tickers_local(["nvda", "AAPL", "nvda", " msft "])
            assert storage.load_saved_tickers() == ["NVDA", "AAPL", "MSFT"]


def test_storage_repo_file_fallback():
    """서버 파일이 없으면 저장소 커밋 파일(user_tickers.json)을 읽어야 함"""
    with tempfile.TemporaryDirectory() as tmp:
        repo_file = os.path.join(tmp, "repo.json")
        with open(repo_file, "w") as f:
            json.dump({"tickers": ["TSLA", "AMD"]}, f)
        with patch.object(storage, "LOCAL_FILE", os.path.join(tmp, "none.json")), \
             patch.object(storage, "REPO_FILE", repo_file):
            assert storage.load_saved_tickers() == ["TSLA", "AMD"]


def test_storage_broken_file_is_safe():
    """저장 파일이 깨져 있어도 예외 없이 None을 돌려줘야 함"""
    with tempfile.TemporaryDirectory() as tmp:
        broken = os.path.join(tmp, "broken.json")
        with open(broken, "w") as f:
            f.write("{깨진 JSON")
        with patch.object(storage, "LOCAL_FILE", broken), \
             patch.object(storage, "REPO_FILE", os.path.join(tmp, "none.json")):
            assert storage.load_saved_tickers() is None


# ---------------------------------------------------------------------------
# 8-K 짝짓기 창 확대 + 실패 기록
# ---------------------------------------------------------------------------
def _xbrl_row(period_end, op):
    return {"ticker": "T", "filing_date": period_end, "period_label": period_end[2:7],
            "revenue": op * 5, "op_income": op, "gross_margin_pct": 50.0,
            "source": cfg.SRC_APPROX, "gm_is_gaap": True, "filing_url": "",
            "derivation": "", "guidance_text": ""}


def _press_row(filing_date, op):
    return {"ticker": "T", "filing_date": filing_date, "period_label": "라벨",
            "revenue": op * 5, "op_income": op, "gross_margin_pct": 55.0,
            "source": cfg.SRC_DIRECT, "gm_is_gaap": False, "filing_url": "u",
            "derivation": "d", "guidance_text": "g"}


def test_pairing_window_covers_late_annual_filers():
    """분기 종료 후 90~120일에 나온 발표(10-K 동반)도 짝지어야 함"""
    xbrl = [_xbrl_row("2025-12-31", 100.0)]
    press = [_press_row("2026-04-05", 111.0)]   # 95일 뒤 (예전 90일 창에서는 실패)

    merged = sf.merge_quarters(xbrl, press)
    assert merged[0]["source"] == cfg.SRC_DIRECT, merged[0]


def test_unpaired_press_recorded_in_report():
    """짝을 못 찾은 8-K는 진단 리포트에 건수와 사유가 남아야 함"""
    xbrl = [_xbrl_row("2025-03-31", 100.0)]
    press = [
        _press_row("2025-04-25", 111.0),          # 짝지어짐
        _press_row("2025-09-20", 999.0),          # 어느 분기와도 안 맞음
    ]
    report = sf.new_report("T")
    merged = sf.merge_quarters(xbrl, press, report)

    assert merged[0]["source"] == cfg.SRC_DIRECT
    assert report["unpaired_press"] == 1, report
    assert "2025-09-20" in report["pair_note"], report["pair_note"]


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
    print(f"\nv4 테스트: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
