"""
test_collect_job.py — 수집 로봇 검증 (인터넷 없이 가짜로)
========================================================

실행: python3 tests/test_collect_job.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd  # noqa: E402

import collect_job as cj  # noqa: E402
import config as cfg  # noqa: E402


def _quiet(*_args, **_kwargs):
    pass


def _fake_daily():
    dates = pd.to_datetime(["2026-08-05", "2026-08-06", "2026-08-07"])
    return pd.DataFrame({"Close": [10.0, 10.5, 11.0]}, index=dates)


# ---------------------------------------------------------------------------
# 반쯤 깨진 날 방어 — 로봇의 핵심 안전장치
# ---------------------------------------------------------------------------
def test_success_enough_requires_half_fundamentals():
    """실적이 절반 넘게 실패하면 그날 데이터로 덮어쓰면 안 됨."""
    tickers = ["A", "B", "C", "D"]
    daily = {t: _fake_daily() for t in tickers} | {cfg.BENCHMARK: _fake_daily()}
    good = {"adj_eps_ok": 3, "xbrl_quarters": 0}
    bad = {"adj_eps_ok": 0, "xbrl_quarters": 0}
    assert cj.success_enough([good, good, bad, bad], daily, tickers)
    assert not cj.success_enough([good, bad, bad, bad], daily, tickers)


def test_success_enough_requires_benchmark():
    """기준지수(SPY)가 없으면 초과수익을 못 재므로 실패로 판정."""
    tickers = ["A", "B"]
    good = {"adj_eps_ok": 3, "xbrl_quarters": 0}
    daily_no_spy = {t: _fake_daily() for t in tickers}
    assert not cj.success_enough([good, good], daily_no_spy, tickers)


def test_success_enough_requires_half_prices():
    tickers = ["A", "B", "C", "D"]
    good = {"adj_eps_ok": 3, "xbrl_quarters": 0}
    daily = {"A": _fake_daily(), cfg.BENCHMARK: _fake_daily()}   # 주가 1/4 뿐
    assert not cj.success_enough([good] * 4, daily, tickers)


# ---------------------------------------------------------------------------
# 전체 실행 (가짜 수집기로)
# ---------------------------------------------------------------------------
def test_run_writes_snapshot_and_log(tmp_dir="/tmp/claude-0/robot_test"):
    """로봇이 스냅샷과 실행 기록을 파일로 남기고 0으로 끝나야 함."""
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)
    cwd = os.getcwd()
    os.chdir(tmp_dir)
    try:
        tickers = ["TT", "UU"]
        fake_rows = [
            {"filing_date": "2026-04-30", "announced_date": "2026-05-28",
             "adj_eps": 1.0, "source": cfg.SRC_DIRECT},
        ]
        original_get = cj.sf.get_fundamentals
        original_load = cj.measure_store.sf.load_cache
        original_fetch = cj.md.fetch_daily_data
        cj.sf.get_fundamentals = lambda t, use_cache=False: (
            fake_rows, {**cj.sf.new_report(t), "adj_eps_ok": 1, "merged_direct": 1}
        )
        cj.measure_store.sf.load_cache = lambda t: fake_rows
        cj.md.fetch_daily_data = lambda wanted: (
            {t: _fake_daily() for t in wanted}, []
        )
        try:
            code = cj.run(tickers, progress=_quiet)
        finally:
            cj.sf.get_fundamentals = original_get
            cj.measure_store.sf.load_cache = original_load
            cj.md.fetch_daily_data = original_fetch

        assert code == 0
        with open(f"{cfg.MEASURE_DIR}/snapshot.json", encoding="utf-8") as f:
            snap = json.load(f)
        assert snap["tickers"] == tickers
        assert snap["eps"]["TT"][0]["adj_eps"] == 1.0
        with open(f"{cfg.MEASURE_DIR}/robot_log.json", encoding="utf-8") as f:
            log = json.load(f)
        assert log["tickers"] == 2
        assert log["per_ticker"][0]["adj_eps_ok"] == 1
        # v3 5단계 — 수집 성공 시 자동 판정도 기록되어야 합니다.
        # (가짜 데이터는 분기 1개뿐이라 사건 0건 → 전 가설 '판정 불가'가 정답)
        assert "verdict" in log, log.keys()
        with open(f"{cfg.MEASURE_DIR}/verdict.json", encoding="utf-8") as f:
            verdict = json.load(f)
        assert "H2b_신고점_첫돌파" in verdict["가설"]
        assert verdict["가설"]["H2b_신고점_첫돌파"]["판정"] == "판정 불가"
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_run_aborts_when_mostly_failed():
    """수집이 대부분 실패한 날은 종료코드 1 — 파일을 쓰지 않아야 함."""
    tickers = ["TT", "UU"]
    original_get = cj.sf.get_fundamentals
    original_fetch = cj.md.fetch_daily_data
    cj.sf.get_fundamentals = lambda t, use_cache=False: ([], cj.sf.new_report(t))
    cj.md.fetch_daily_data = lambda wanted: ({}, list(wanted))
    try:
        code = cj.run(tickers, progress=_quiet)
    finally:
        cj.sf.get_fundamentals = original_get
        cj.md.fetch_daily_data = original_fetch
    assert code == 1


# ---------------------------------------------------------------------------
# 26차 개선 — 증분 수집(원문 캐시) + 절제된 병렬
# ---------------------------------------------------------------------------
class _FakeFiling:
    def __init__(self, accession):
        self.accession_no = accession


def test_raw8k_cache_serves_second_read(tmp_dir="/tmp/claude-0/raw8k_test"):
    """같은 공시는 두 번째부터 캐시에서 읽고(다운로드 1회),
    빈 결과는 캐시하지 않아야 합니다 (일시 실패의 영구화 방지)."""
    import shutil
    sf = cj.sf
    original_dir = cfg.RAW8K_CACHE_DIR
    original_fetch = sf._earnings_text
    calls = {"n": 0}
    cfg.RAW8K_CACHE_DIR = tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)

    def fake_ok(filing, report=None):
        calls["n"] += 1
        return "실적 본문", "보도자료", True

    def fake_empty(filing, report=None):
        calls["n"] += 1
        return "", "", False

    try:
        sf._earnings_text = fake_ok
        filing = _FakeFiling("0001-23-000045")
        report = sf.new_report("TT")
        assert sf._earnings_text_cached("TT", filing, report) == ("실적 본문", "보도자료", True)
        assert sf._earnings_text_cached("TT", filing, report) == ("실적 본문", "보도자료", True)
        assert calls["n"] == 1, calls          # 두 번째는 캐시에서
        assert report["cache_hits"] == 1 and report["cache_downloads"] == 1, report

        sf._earnings_text = fake_empty
        empty = _FakeFiling("0001-23-000099")
        sf._earnings_text_cached("TT", empty)
        sf._earnings_text_cached("TT", empty)
        assert calls["n"] == 3, calls          # 빈 결과는 매번 다시 시도
    finally:
        sf._earnings_text = original_fetch
        cfg.RAW8K_CACHE_DIR = original_dir
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_collect_fundamentals_parallel_keeps_order():
    """병렬 수집이 결과를 입력 종목 순서 그대로, 빠짐없이 돌려줘야 합니다."""
    import time as _t
    tickers = ["T1", "T2", "T3", "T4", "T5", "T6"]
    original_get = cj.sf.get_fundamentals

    def fake(ticker, use_cache=False):
        _t.sleep(0.05 if ticker in ("T1", "T4") else 0.0)   # 완료 순서 뒤섞기
        report = cj.sf.new_report(ticker)
        report["adj_eps_ok"] = 1
        return [], report

    cj.sf.get_fundamentals = fake
    try:
        reports = cj.collect_fundamentals(tickers, progress=_quiet)
    finally:
        cj.sf.get_fundamentals = original_get
    assert [r["ticker"] for r in reports] == tickers, [r.get("ticker") for r in reports]


# ---------------------------------------------------------------------------
# 수집 시간 예산 (94차) — 10년 확장의 안전장치
# ---------------------------------------------------------------------------
# 왜 시험하나: 이 장치가 고장 나는 방향은 두 가지고, 둘 다 조용합니다.
#   ① 마감을 안 걸면 → 180분에 강제 종료 → 그날 수집물과 캐시가 통째로 소멸
#   ② 마감이 항상 걸리면 → 매 런이 즉시 멈춰 데이터가 늘지 않음
# 그래서 "마감 없으면 안 멈춘다"와 "마감 넘기면 멈춘다"를 둘 다 못박습니다.
def test_시간예산_없으면_멈추지_않는다():
    import sec_fundamentals as sf

    sf.set_collect_budget(None)
    try:
        assert sf._budget_over() is False
    finally:
        sf.set_collect_budget(None)


def test_시간예산_넘기면_멈춘다():
    import sec_fundamentals as sf

    시계 = [1000.0]                      # 가짜 시계 (진짜로 기다리지 않기)
    sf.set_collect_budget(10, clock=lambda: 시계[0])   # 10분 = 600초
    try:
        시계[0] = 1000.0 + 599.0
        assert sf._budget_over(clock=lambda: 시계[0]) is False, "아직 마감 전인데 멈췄다"
        시계[0] = 1000.0 + 601.0
        assert sf._budget_over(clock=lambda: 시계[0]) is True, "마감을 넘겼는데 안 멈췄다"
    finally:
        sf.set_collect_budget(None)


def test_시간예산_0이하는_무제한():
    import sec_fundamentals as sf

    sf.set_collect_budget(0)
    try:
        assert sf._budget_over() is False
    finally:
        sf.set_collect_budget(None)


def test_시간초과_종목이_로봇기록에_남는다():
    """잘린 종목을 조용히 넘기지 않는가 — 짐작 대신 기록으로 보게."""
    import sec_fundamentals as sf

    report = sf.new_report("XYZ")
    assert report["시간초과"] is False, "새 리포트는 시간초과가 아니어야 한다"
    report["시간초과"] = True
    남긴다 = [r["ticker"] for r in [report] if r.get("시간초과")]
    assert 남긴다 == ["XYZ"]


def test_시간예산이_8K_훑기를_실제로_멈춘다():
    """장치가 **실제로 그 자리에서 실행되는지** 증명한다 (헌법 검증 규칙).

    앞의 세 시험은 `_budget_over()` 자체가 옳게 답하는지만 봤다. 그것이
    참이어도 **훑기 반복문이 그 함수를 부르지 않으면** 아무 일도 안 일어난다.
    이 저장소는 "고친 코드가 실행 불가능한 자리에 있던" 사고를 이미 겪었다.
    그래서 가짜 공시 100건을 물려 fetch_earnings_8k 를 직접 돌리고,
    마감을 넘긴 뒤 **몇 건에서 멈췄는지**를 센다.
    """
    import sys
    import types
    sf = cj.sf

    본_공시 = {"n": 0}

    class _가짜공시:
        def __init__(self, 번호):
            self.accession_no = f"0001-23-{번호:06d}"
            self.filing_date = "2020-01-01"

    class _가짜회사:
        def __init__(self, ticker):
            pass

        def get_filings(self, **_kw):
            return [_가짜공시(i) for i in range(100)]

    def 가짜텍스트(ticker, filing, report=None):
        본_공시["n"] += 1
        return "", "", False      # 본문 없음 — 파싱까지 가지 않게

    가짜edgar = types.ModuleType("edgar")
    가짜edgar.Company = _가짜회사
    옛edgar = sys.modules.get("edgar")
    옛텍스트 = sf._earnings_text_cached
    옛신원 = sf._ensure_identity

    sys.modules["edgar"] = 가짜edgar
    sf._earnings_text_cached = 가짜텍스트
    sf._ensure_identity = lambda: None
    try:
        # ① 마감 없음 — 100건을 끝까지 훑어야 한다
        sf.set_collect_budget(None)
        본_공시["n"] = 0
        보고 = sf.new_report("TT")
        sf.fetch_earnings_8k("TT", start_date="2016-09-15", report=보고)
        assert 본_공시["n"] == 100, f"마감이 없는데 {본_공시['n']}건에서 멈췄다"
        assert 보고["시간초과"] is False

        # ② 5건을 본 뒤 마감이 오게 하는 가짜 시계
        눈금 = {"n": 0}

        def 시계():
            눈금["n"] += 1
            return 눈금["n"]

        # 시작 시점 1, 예산 4초 → 마감 5. 여섯 번째 확인부터 넘긴다.
        sf.set_collect_budget(4 / 60.0, clock=시계)
        본_공시["n"] = 0
        보고2 = sf.new_report("TT")
        sf.fetch_earnings_8k("TT", start_date="2016-09-15", report=보고2)
        assert 본_공시["n"] < 100, "마감을 넘겼는데 100건을 다 훑었다 — 반복문이 예산을 안 본다"
        assert 보고2["시간초과"] is True, "잘렸는데 기록에 안 남았다"
        assert "시간 예산" in 보고2["note"], 보고2["note"]
    finally:
        sf.set_collect_budget(None)
        sf._earnings_text_cached = 옛텍스트
        sf._ensure_identity = 옛신원
        if 옛edgar is None:
            sys.modules.pop("edgar", None)
        else:
            sys.modules["edgar"] = 옛edgar


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
    print(f"\n수집 로봇 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
