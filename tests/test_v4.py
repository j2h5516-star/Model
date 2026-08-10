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
import time
import types
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
def test_guidance_from_last_row_is_used():
    """마지막 분기 행의 가이던스 = 다음(미발표) 분기 전망이므로 사용해야 함"""
    quarters = make_quarters([100 * M, 115 * M, 140 * M])
    quarters[-1]["guidance_text"] = "We expect revenue of $180 million to $190 million."

    text = fe.find_latest_guidance_text(quarters)
    assert "180 million" in text, text


def test_stale_guidance_is_rejected():
    """마지막 행이 XBRL 뼈대일 때 그 앞 행의 가이던스는 '이미 발표된 분기'의
    전망이므로 절대 쓰면 안 됨 — 쓰면 가속 중인 종목을 둔화로 오판함 (critical 수정)"""
    quarters = make_quarters([100 * M, 115 * M, 140 * M])
    quarters[-2]["guidance_text"] = "We expect revenue of $500 million."   # 낡은 가이던스
    quarters[-1]["guidance_text"] = ""   # 마지막 행 = XBRL 뼈대

    assert fe.find_latest_guidance_text(quarters) == ""


def test_stale_guidance_falls_back_to_consensus():
    """낡은 가이던스를 버리면 컨센서스 경로가 전망을 대신 만들어야 함"""
    quarters = make_quarters([100 * M, 110 * M, 120 * M, 130 * M])
    quarters[-2]["guidance_text"] = "We expect revenue of $500 million."
    quarters[-1]["guidance_text"] = ""

    with patch.dict(sys.modules, {"yfinance": _fake_yf(_FakeTicker)}):
        out = fe.estimate_forward("TEST", quarters)

    assert out["basis"] == cfg.SRC_ESTIMATE, out   # 가이던스가 아니라 추정
    assert out["forward_op_income"] is not None


def test_promoted_latest_8k_carries_fresh_guidance():
    """XBRL보다 늦게 나온 최신 8-K는 새 분기 행으로 승격되어
    그 안의 신선한 가이던스가 마지막 행에 와야 함"""
    xbrl = [_xbrl_row("2025-03-31", 100.0), _xbrl_row("2025-06-30", 120.0)]
    press = [{
        "ticker": "T", "filing_date": "2025-10-25", "period_label": "25 Q3",
        "revenue": 700.0, "op_income": 140.0, "gross_margin_pct": 55.0,
        "source": cfg.SRC_DIRECT, "gm_is_gaap": False, "filing_url": "u",
        "derivation": "d",
        "guidance_text": "We expect revenue of $800 million to $840 million.",
    }]

    merged = sf.merge_quarters(xbrl, press)

    assert len(merged) == 3, [r["filing_date"] for r in merged]
    assert merged[-1]["source"] == cfg.SRC_DIRECT
    assert merged[-1]["op_income"] == 140.0
    # 승격된 마지막 행의 가이던스가 실제로 전망에 쓰이는지
    assert "800 million" in fe.find_latest_guidance_text(merged)


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


def test_accel_is_not_judged_without_q2():
    """다다음 분기 자료가 없으면 '2분기 경로'를 판정하면 안 됨.

    예전에는 다음 분기 예측을 그대로 복사했는데, F_*와 F2_* 문자열이 같아
    화면에서 2분기까지 계산한 것처럼 보였습니다 (없는 근거를 있는 것처럼 표시).
    """
    result = _accel((100 * M, 115 * M, 140 * M), 180 * M, None)
    assert result["next2_qoq"] is None
    assert result["accel_label"] == cfg.F2_NONE, result
    assert result["label"] != cfg.F_NONE, result   # 다음 분기 예측은 그대로 살아 있어야 함
    assert "판정하지 않았습니다" in result["accel_detail"], result


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

    failed, stale = pipeline.refresh_price_store(store, ["AAA", "BBB"], fetch=fetch, now=1000.0)
    assert failed == [] and stale == []
    assert set(calls[0]) == {"AAA", "BBB", cfg.BENCHMARK}

    # 종목 하나 추가 → 새 종목만 요청해야 함 (핵심!)
    failed, stale = pipeline.refresh_price_store(
        store, ["AAA", "BBB", "NEW"], fetch=fetch, now=1010.0
    )
    assert failed == [] and stale == []
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
    failed, stale = pipeline.refresh_price_store(
        store, ["AAA", "BBB"], fetch=flaky_fetch, now=0.0, sleep=slept.append
    )
    assert failed == [] and stale == []
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


def test_price_store_batch_refresh_on_expiry():
    """하나라도 만료되면 전체를 함께 갱신해야 함 (시점 혼재 → RS 왜곡 방지)"""
    store = {}
    calls = []
    fetch = _daily_fetch_counter(calls)

    pipeline.refresh_price_store(store, ["AAA", "BBB"], fetch=fetch, now=0.0)
    # 30분 뒤 NEW 추가 → NEW만
    pipeline.refresh_price_store(store, ["AAA", "BBB", "NEW"], fetch=fetch, now=1800.0)
    assert calls[1] == ["NEW"], calls[1]
    # 70분 시점: AAA/BBB/SPY는 만료, NEW는 아직 유효 —
    # 그래도 전체(NEW 포함)를 함께 받아 같은 시점의 주가로 맞춰야 함
    pipeline.refresh_price_store(store, ["AAA", "BBB", "NEW"], fetch=fetch, now=4200.0)
    assert set(calls[2]) == {"AAA", "BBB", "NEW", cfg.BENCHMARK}, calls[2]


def test_price_store_reports_stale_and_backs_off():
    """갱신 실패 시: 이전 데이터 유지 + stale로 보고 + 5분 뒤에만 재시도"""

    def dead_fetch(tickers):
        return {}, list(tickers)   # 야후 완전 장애

    calls = []
    fetch_ok = _daily_fetch_counter(calls)

    store = {}
    pipeline.refresh_price_store(store, ["AAA"], fetch=fetch_ok, now=0.0)

    # TTL 만료 후 장애 → stale 보고 + 데이터는 유지
    failed, stale = pipeline.refresh_price_store(
        store, ["AAA"], fetch=dead_fetch, now=4000.0, sleep=lambda s: None
    )
    assert failed == [], failed
    assert set(stale) == {"AAA", cfg.BENCHMARK}, stale
    assert "AAA" in store["data"]

    # 직후 재실행 → 백오프 때문에 fetch를 다시 부르면 안 됨 (rerun 폭주 방지)
    counter = {"n": 0}

    def counting_dead(tickers):
        counter["n"] += 1
        return {}, list(tickers)

    pipeline.refresh_price_store(store, ["AAA"], fetch=counting_dead, now=4010.0)
    assert counter["n"] == 0, "백오프 중인데 재시도함"

    # 5분(백오프) 경과 후에는 다시 시도해야 함
    pipeline.refresh_price_store(
        store, ["AAA"], fetch=counting_dead, now=4000.0 + pipeline.PRICE_RETRY_BACKOFF_SEC + 1,
        sleep=lambda s: None,
    )
    assert counter["n"] >= 1


def test_price_store_lock_prevents_duplicate_fetch():
    """두 세션이 동시에 갱신해도 다운로드는 한 번만 일어나야 함"""
    import threading

    store = {"lock": threading.Lock()}
    calls = []
    barrier = threading.Barrier(2)

    def slow_fetch(tickers):
        calls.append(list(tickers))
        return {t: make_daily([10, 11, 12]) for t in tickers}, []

    def worker():
        barrier.wait()
        pipeline.refresh_price_store(store, ["AAA"], fetch=slow_fetch, now=0.0)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 락 덕분에 두 번째 세션은 이미 채워진 저장소를 보고 그냥 지나가야 함
    assert len(calls) == 1, calls


def test_price_store_evicts_removed_tickers():
    """삭제한 종목이 저장소에 무한히 쌓이지 않아야 함"""
    store = {}
    calls = []
    fetch = _daily_fetch_counter(calls)

    many = [f"T{i:03d}" for i in range(pipeline.PRICE_STORE_CAP + 10)]
    pipeline.refresh_price_store(store, many, fetch=fetch, now=0.0)
    # 이제 소수만 사용 → 저장소가 상한 이하로 정리돼야 함
    pipeline.refresh_price_store(store, ["T000"], fetch=fetch, now=10.0)
    assert len(store["data"]) <= pipeline.PRICE_STORE_CAP, len(store["data"])
    assert "T000" in store["data"]


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
    """짝을 못 찾은 '과거' 8-K는 진단 리포트에 건수와 사유가 남아야 함

    (최신 8-K는 새 분기로 승격되므로, 과거 시점의 짝 없는 8-K로 검증합니다)
    """
    xbrl = [_xbrl_row("2025-06-30", 100.0)]
    press = [
        _press_row("2025-07-25", 111.0),          # 6월 분기와 짝지어짐
        _press_row("2025-01-10", 999.0),          # 과거인데 어느 분기와도 안 맞음
    ]
    report = sf.new_report("T")
    merged = sf.merge_quarters(xbrl, press, report)

    assert merged[-1]["source"] == cfg.SRC_DIRECT
    assert report["unpaired_press"] == 1, report
    assert "2025-01-10" in report["pair_note"], report["pair_note"]


# ---------------------------------------------------------------------------
# 승격 규칙의 이중 계상 방지 (재검증에서 발견된 회귀 수정)
# ---------------------------------------------------------------------------
def test_late_annual_report_absorbed_not_duplicated():
    """마지막 분기 발표가 110일 뒤에 나와도(늦은 연간 보고) 같은 분기가
    두 행으로 이중 계상되면 안 됨 — 매출이 거의 같으면 흡수해야 함"""
    xbrl = [_xbrl_row("2025-09-30", 90.0), _xbrl_row("2025-12-31", 100.0)]
    press = [{
        "ticker": "T", "filing_date": "2026-04-20", "period_label": "25 Q4",
        "revenue": 505.0,   # XBRL 행 매출(100×5=500)과 1% 차이 = 같은 분기
        "op_income": 111.0, "gross_margin_pct": 55.0,
        "source": cfg.SRC_DIRECT, "gm_is_gaap": False, "filing_url": "u",
        "derivation": "d", "guidance_text": "g",
    }]

    merged = sf.merge_quarters(xbrl, press)

    assert len(merged) == 2, [r["filing_date"] for r in merged]   # 행이 늘면 안 됨
    assert merged[-1]["source"] == cfg.SRC_DIRECT                  # 흡수됨
    assert merged[-1]["op_income"] == 111.0


def test_next_quarter_8k_still_promoted_when_revenue_differs():
    """매출이 크게 다르면(다음 분기 발표) 흡수하지 않고 승격해야 함"""
    xbrl = [_xbrl_row("2025-06-30", 120.0)]   # 매출 600
    press = [{
        "ticker": "T", "filing_date": "2025-10-25", "period_label": "25 Q3",
        "revenue": 700.0,   # 600 대비 17% 차이 = 다른 분기
        "op_income": 140.0, "gross_margin_pct": 55.0,
        "source": cfg.SRC_DIRECT, "gm_is_gaap": False, "filing_url": "u",
        "derivation": "d", "guidance_text": "g",
    }]

    merged = sf.merge_quarters(xbrl, press)

    assert len(merged) == 2, merged            # 새 분기로 승격
    assert merged[0]["source"] == cfg.SRC_APPROX   # 기존 행은 그대로
    assert merged[-1]["op_income"] == 140.0


def test_duplicate_8k_same_quarter_promoted_once():
    """같은 새 분기에 8-K가 2건(원본+정정)이어도 승격은 1건만 되어야 함"""
    xbrl = [_xbrl_row("2025-06-30", 120.0)]
    original = {
        "ticker": "T", "filing_date": "2025-10-25", "period_label": "25 Q3",
        "revenue": 700.0, "op_income": 140.0, "gross_margin_pct": 55.0,
        "source": cfg.SRC_DIRECT, "gm_is_gaap": False, "filing_url": "u",
        "derivation": "d", "guidance_text": "g",
    }
    correction = dict(original, filing_date="2025-11-01", op_income=141.0)

    report = sf.new_report("T")
    merged = sf.merge_quarters(xbrl, [original, correction], report)

    assert len(merged) == 2, [r["filing_date"] for r in merged]
    assert merged[-1]["op_income"] == 140.0        # 최초 발표가 채택됨
    assert report["unpaired_press"] == 1, report   # 정정본은 미사용으로 기록


# ---------------------------------------------------------------------------
# 리뷰에서 확정된 나머지 수정들
# ---------------------------------------------------------------------------
def test_nan_velocity_is_filtered():
    """eps_trend에 NaN이 섞여도 리비전 속도가 오염되지 않아야 함"""

    class _NanTicker(_FakeTicker):
        def __init__(self, symbol):
            super().__init__(symbol)
            self.eps_trend = pd.DataFrame(
                {"current": [float("nan")], "30daysAgo": [0.50]}, index=["0q"]
            )
            self.eps_revisions = pd.DataFrame(
                {"upLast30days": [0], "downLast30days": [3]}, index=["0q"]
            )

    with patch.dict(sys.modules, {"yfinance": _fake_yf(_NanTicker)}):
        out = fe.fetch_consensus("TEST")

    assert out["revision_velocity_pct"] is None, out
    assert out["revision"] == -1, out   # 건수 폴백으로 하향이 잡혀야 함


def test_tiny_eps_denominator_skips_velocity():
    """EPS 분모가 5센트 미만이면 속도를 계산하지 않아야 함 (노이즈 폭주 방지)"""

    class _TinyTicker(_FakeTicker):
        def __init__(self, symbol):
            super().__init__(symbol)
            self.eps_trend = pd.DataFrame(
                {"current": [0.03], "30daysAgo": [0.01]}, index=["0q"]
            )

    with patch.dict(sys.modules, {"yfinance": _fake_yf(_TinyTicker)}):
        out = fe.fetch_consensus("TEST")

    assert out["revision_velocity_pct"] is None, out


def test_margin_excludes_loss_quarters():
    """턴어라운드 종목: 적자 분기가 평균 마진을 오염시키면 안 됨"""
    # 적자 3분기 + 흑자 1분기(마진 10/135≈7.4%)
    quarters = make_quarters(
        [-80 * M, -60 * M, -40 * M, 10 * M],
        revenues=[100 * M, 110 * M, 120 * M, 135 * M],
    )
    avg = fe.average_operating_margin(quarters, n=4)
    assert avg is not None and avg > 0, avg          # 음수 마진이 아니어야 함
    assert abs(avg - (10 / 135 * 100)) < 0.01, avg   # 흑자 분기만 반영

    # 흑자 분기가 하나도 없으면 None (억지 전망 금지)
    all_loss = make_quarters([-80 * M, -60 * M], revenues=[100 * M, 110 * M])
    assert fe.average_operating_margin(all_loss) is None


def test_rollover_shift_when_consensus_points_at_reported_quarter():
    """컨센서스 '0q'가 방금 발표된 분기를 가리키면 한 분기 당겨야 함"""

    class _StaleTicker(_FakeTicker):
        def __init__(self, symbol):
            super().__init__(symbol)
            # 0q 매출 = 마지막 실제 분기 매출(650M)과 거의 동일 → 미롤오버 상황
            self.revenue_estimate = pd.DataFrame(
                {"avg": [650 * M, 720 * M], "numberOfAnalysts": [20, 18]},
                index=["0q", "+1q"],
            )

    quarters = make_quarters(
        [100 * M, 110 * M, 120 * M, 130 * M],
        revenues=[500 * M, 550 * M, 600 * M, 650 * M],
    )
    with patch.dict(sys.modules, {"yfinance": _fake_yf(_StaleTicker)}):
        out = fe.estimate_forward("TEST", quarters)

    # 시프트 후: 다음 분기 = 720M × 평균마진(20%) = 144M, 다다음은 없음
    assert abs(out["forward_op_income"] - 144 * M) < 1, out["forward_op_income"]
    assert out["forward_op_income_2"] is None, out
    assert any("당김" in e for e in out["consensus"]["errors"]), out["consensus"]["errors"]


def test_q2_uses_guidance_implied_margin():
    """다음 분기가 가이던스 기반이면 다다음 분기도 같은 마진 기준을 써야 함
    (마진 기준이 다르면 매출이 그대로여도 가짜 반등 신호가 생김)"""
    quarters = make_quarters(
        [158 * M, 158 * M, 158 * M, 158 * M],
        revenues=[500 * M, 500 * M, 500 * M, 500 * M],   # 평균 마진 31.6%
    )
    # 가이던스: 매출 $510M, 영업마진 26% (보수적)
    quarters[-1]["guidance_text"] = (
        "Business Outlook. We expect revenue of $505 million to $515 million "
        "and non-GAAP operating margin of approximately 26%."
    )

    class _FlatTicker(_FakeTicker):
        def __init__(self, symbol):
            super().__init__(symbol)
            self.revenue_estimate = pd.DataFrame(
                {"avg": [510 * M, 510 * M], "numberOfAnalysts": [15, 14]},
                index=["0q", "+1q"],
            )

    with patch.dict(sys.modules, {"yfinance": _fake_yf(_FlatTicker)}):
        out = fe.estimate_forward("TEST", quarters)

    assert out["basis"] == cfg.SRC_GUIDANCE, out
    # 두 분기 모두 가이던스 내재 마진(26%) 기준 → 매출이 같으니 이익도 같아야 함
    assert abs(out["forward_op_income"] - out["forward_op_income_2"]) < 1e-3, (
        out["forward_op_income"], out["forward_op_income_2"],
    )


def test_recovery_path_labeled_rebound_not_accel():
    """감익 중인 종목의 회복 경로는 '가속 후 둔화'(정점 신호)가 아니라
    '둔화 후 반등'(바닥 신호)으로 표시해야 함"""
    quarters = make_quarters([100 * M, 111 * M, 100 * M])   # 최근 QoQ −9.9%
    delta = scoring.score_delta_acceleration(quarters)
    forward = {
        "forward_op_income": 105 * M,      # 다음 +5.0% (회복)
        "forward_op_income_2": 105 * M,    # 다다음 0.0%
        "basis": cfg.SRC_ESTIMATE, "basis_2": cfg.SRC_ESTIMATE,
    }
    result = scoring.predict_delta(quarters, forward, delta)

    assert result["label"] == cfg.F_REBOUND, result           # 단분기: 반등
    assert result["accel_label"] == cfg.F2_REBOUND, result    # 2분기: 반등 (모순 제거)


def test_loss_base_forward_scoring():
    """최근 분기가 적자일 때: 흑자 전환 전망 > 적자 축소 > 적자 확대 순으로
    점수가 달라야 하고, '전망을 구하지 못했다'는 거짓 문구가 나오면 안 됨"""
    quarters = make_quarters([50 * M, 20 * M, -10 * M])

    turn = scoring.score_forward(quarters, {"forward_op_income": 18 * M, "revision": 0})
    shrink = scoring.score_forward(quarters, {"forward_op_income": -3 * M, "revision": 0})
    worse = scoring.score_forward(quarters, {"forward_op_income": -25 * M, "revision": 0})

    assert turn["score"] > shrink["score"] > worse["score"], (turn, shrink, worse)
    assert "구하지 못했습니다" not in turn["detail"], turn["detail"]
    assert "흑자 전환" in turn["detail"], turn["detail"]



# ---------------------------------------------------------------------------
# v4.1 — SEC 신원 설정 (전 종목 실적이 통째로 비던 사고의 원인)
# ---------------------------------------------------------------------------
def test_identity_is_set_only_once_under_threads():
    """여러 종목을 동시에 받아도 SEC 신원 설정은 한 번만 일어나야 함.

    edgartools의 set_identity()는 공용 접속 창구를 닫고 새로 엽니다.
    수집 도중 두 번째 호출이 일어나면 다른 종목의 요청이 끊깁니다.
    """
    import threading

    calls = []
    barrier = threading.Barrier(4)

    def fake_set_identity(value):
        calls.append(value)
        time.sleep(0.02)   # 창구를 다시 여는 동안을 흉내

    sf._configured_identity = None
    fake_module = types.SimpleNamespace(set_identity=fake_set_identity)

    def worker():
        barrier.wait()          # 네 스레드가 정확히 동시에 출발
        sf._ensure_identity()

    with patch.dict(sys.modules, {"edgar": fake_module}):
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(calls) == 1, f"set_identity가 {len(calls)}번 호출됨 (1번이어야 함)"


def test_identity_change_does_not_close_open_connections():
    """신원을 고치면 반영은 되되, 이미 열린 접속 창구를 닫으면 안 됨.

    set_identity()를 다시 부르면 여러 종목이 함께 쓰는 접속 창구가 닫혀
    수집 중이던 요청이 끊깁니다. 두 번째부터는 환경변수만 바꿉니다
    (edgartools는 요청할 때마다 EDGAR_IDENTITY를 읽습니다).
    """
    calls = []
    fake_module = types.SimpleNamespace(set_identity=lambda v: calls.append(v))

    sf._configured_identity = None
    with patch.dict(sys.modules, {"edgar": fake_module}):
        with patch.dict(os.environ, {"SEC_IDENTITY": "첫번째 a@example.com"}):
            sf._ensure_identity()
            sf._ensure_identity()          # 같은 값 → 아무것도 하지 않음
            assert calls == ["첫번째 a@example.com"], calls
        with patch.dict(os.environ, {"SEC_IDENTITY": "두번째 b@example.com"}):
            sf._ensure_identity()          # 값이 바뀜 → 창구는 그대로, 환경변수만 갱신
            assert calls == ["첫번째 a@example.com"], f"창구를 닫는 호출이 또 일어남: {calls}"
            assert os.environ["EDGAR_IDENTITY"] == "두번째 b@example.com"

    sf._configured_identity = None


def test_sec_identity_alias_follows_env():
    """예전 이름 cfg.SEC_IDENTITY 도 지금 값을 따라가야 함 (import 시점 고정 금지)"""
    with patch.dict(os.environ, {"SEC_IDENTITY": "홍길동 hong@example.com"}):
        assert cfg.SEC_IDENTITY == "홍길동 hong@example.com"
    assert cfg.SEC_IDENTITY == cfg.DEFAULT_SEC_IDENTITY


def test_email_check_rejects_incomplete_addresses():
    """이메일 판별이 '이메일@주소' 같은 미완성 값을 통과시키면 안 됨"""
    assert not cfg._has_email("이름 이메일@주소")     # 점(.)이 없는 도메인
    assert not cfg._has_email("그냥 이름")
    assert not cfg._has_email("")
    assert cfg._has_email("홍길동 hong@example.com")
    assert cfg._has_email("Trend Dashboard a.b-c@sub.example.co.kr")


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
