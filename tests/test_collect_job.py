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

        # ── 빈 결과 두 갈래 (145차) ────────────────────────────────
        # ⓐ **예외가 났고** 비었다 → 일시적 망 실패일 수 있으므로 기억하지
        #    않고 매번 다시 시도합니다 (원래 걱정 그대로 유지).
        def fake_empty_error(filing, report=None):
            calls["n"] += 1
            sf._record_error(report, "시험", RuntimeError("망 실패"))
            return "", "", False

        sf._earnings_text = fake_empty_error
        깨짐 = _FakeFiling("0001-23-000099")
        보고 = sf.new_report("TT")
        sf._earnings_text_cached("TT", 깨짐, 보고)
        sf._earnings_text_cached("TT", 깨짐, 보고)
        assert calls["n"] == 3, f"망 실패를 영구 박제했습니다: {calls}"

        # ⓑ **예외 없이** 비었다 → 실적 문서가 아닌 8-K(인사·배당·계약).
        #    다시 받아도 결과가 같으므로 기억해 접속을 줄입니다.
        #    실측 근거: 비자(V)는 내려받기 0건인데 905초가 걸렸습니다 —
        #    보이지 않는 접속이 그만큼 있었다는 뜻입니다.
        sf._earnings_text = fake_empty
        아님 = _FakeFiling("0001-23-000123")
        보고2 = sf.new_report("TT")
        sf._earnings_text_cached("TT", 아님, 보고2)
        sf._earnings_text_cached("TT", 아님, 보고2)
        assert calls["n"] == 4, f"실적 문서 아님을 기억하지 못했습니다: {calls}"
        assert 보고2["negative_cached"] == 1, 보고2
        assert 보고2["negative_hits"] == 1, 보고2
        assert 보고2["cache_downloads"] == 0, "빈 결과를 내려받기로 세면 안 됩니다"
        assert 보고2["fetch_attempts"] == 1, 보고2
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
# 예산 초과여도 캐시된 원문은 읽는다 (153차)
# ---------------------------------------------------------------------------
# 왜 시험하나: 예산에 걸리는 종목이 날마다 달라서, 예전(통째로 break)에는
# 캐시에 멀쩡히 있는 옛 분기 값이 사라졌다 돌아왔다 했습니다
# (08-25/26/27 실측: GWW 조정 EPS 10→27→10칸). 예산이 미루는 것은
# **새 내려받기뿐**이어야 하고, 그 판별은 _text_in_cache 가 합니다.
class _가짜공시:
    def __init__(self, accession):
        self.accession_no = accession


def test_캐시확인은_네트워크_없이_판별한다(tmp_dir=None):
    import tempfile, json as _json
    import config as _cfg
    import sec_fundamentals as sf

    옛경로 = _cfg.RAW8K_CACHE_DIR
    with tempfile.TemporaryDirectory() as 임시:
        _cfg.RAW8K_CACHE_DIR = 임시
        try:
            공시 = _가짜공시("0001-23-000045")
            # ① 캐시 없음 → False
            assert sf._text_in_cache("AAA", 공시) is False
            # ② 현재 판 번호로 저장돼 있으면 → True
            path = sf._raw8k_cache_path("AAA", "0001-23-000045")
            with open(path, "w", encoding="utf-8") as f:
                _json.dump({"v": _cfg.RAW8K_CACHE_VERSION, "text": "실적",
                            "source": "ex99", "had_exhibit": True}, f)
            assert sf._text_in_cache("AAA", 공시) is True
            # ③ 옛 판 번호는 없는 것으로 (읽어도 다시 받게 되므로)
            with open(path, "w", encoding="utf-8") as f:
                _json.dump({"v": -999, "text": "실적"}, f)
            assert sf._text_in_cache("AAA", 공시) is False
            # ④ 공시번호가 없으면 판단 불가 → False (값을 만들지 않는다)
            assert sf._text_in_cache("AAA", _가짜공시(None)) is False
        finally:
            _cfg.RAW8K_CACHE_DIR = 옛경로


def test_예산초과_분기는_break_가_아니라_continue_다():
    """수리의 핵심이 코드에 남아 있는지 — 예산 초과 가지에서 통째로
    멈추면(break) 캐시된 옛 분기까지 잃습니다. 소스를 직접 확인합니다."""
    import inspect
    import sec_fundamentals as sf

    src = inspect.getsource(sf)
    i = src.find("153차 수리")
    assert i > 0, "153차 수리 주석이 사라졌습니다"
    가지 = src[i:i + 1200]
    assert "_text_in_cache" in 가지, "예산 가지가 캐시 확인 없이 동작합니다"
    assert "continue" in 가지, "예산 가지가 continue 가 아닙니다"


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
        report["parsed_ok"] += 1   # 훑을 때마다 실적 1건을 얻은 셈으로
        return "", "", False       # 본문 없음 — 파싱까지 가지 않게

    가짜edgar = types.ModuleType("edgar")
    가짜edgar.Company = _가짜회사
    옛edgar = sys.modules.get("edgar")
    옛텍스트 = sf._earnings_text_cached
    옛신원 = sf._ensure_identity
    옛바닥 = cfg.COLLECT_BUDGET_FLOOR

    sys.modules["edgar"] = 가짜edgar
    sf._earnings_text_cached = 가짜텍스트
    sf._ensure_identity = lambda: None
    # 바닥은 여기서 재려는 것이 아니므로 낮춰 둔다 (바닥은 따로 시험한다).
    # 조기 종료(EARLY_STOP_PARSED)에 먼저 걸리지 않게 그것도 100 위로 올린다.
    cfg.COLLECT_BUDGET_FLOOR = 1
    옛조기 = cfg.EARLY_STOP_PARSED
    cfg.EARLY_STOP_PARSED = 10_000
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
        cfg.COLLECT_BUDGET_FLOOR = 옛바닥
        cfg.EARLY_STOP_PARSED = 옛조기
        sf.set_collect_budget(None)
        sf._earnings_text_cached = 옛텍스트
        sf._ensure_identity = 옛신원
        if 옛edgar is None:
            sys.modules.pop("edgar", None)
        else:
            sys.modules["edgar"] = 옛edgar


def test_시간예산은_바닥을_못_깎는다():
    """뒷쪽 종목이 최신 분기까지 잃는 것을 막는가 (94차 ⑦).

    예산은 **전체 시계**로 재는데 종목은 순서대로 처리된다. 바닥이 없으면
    예산을 넘긴 뒤 차례가 온 종목은 8-K 를 **한 건도** 못 훑어 옛 분기가
    아니라 **최신 분기까지** 잃는다 — 표본을 늘리려다 있던 표본을 깎는
    최악이다. 그래서 바닥을 채우기 전에는 예산이 작동하면 안 된다.

    시험 방법: 마감을 **이미 지난 시각**으로 걸어 두고(= 뒷쪽 종목 상황)
    훑기를 돌린다. 바닥(3건)을 채울 때까지는 훑고, 채운 직후 멈춰야 한다.
    """
    import sys
    import types
    sf = cj.sf

    본_공시 = {"n": 0}

    class _가짜공시:
        def __init__(self, 번호):
            self.accession_no = f"0002-23-{번호:06d}"
            self.filing_date = "2020-01-01"

    class _가짜회사:
        def __init__(self, ticker):
            pass

        def get_filings(self, **_kw):
            return [_가짜공시(i) for i in range(50)]

    def 가짜텍스트(ticker, filing, report=None):
        본_공시["n"] += 1
        report["parsed_ok"] += 1      # 훑을 때마다 실적 1건을 얻은 셈으로
        return "", "", False

    가짜edgar = types.ModuleType("edgar")
    가짜edgar.Company = _가짜회사
    옛edgar = sys.modules.get("edgar")
    옛텍스트 = sf._earnings_text_cached
    옛신원 = sf._ensure_identity
    옛바닥 = cfg.COLLECT_BUDGET_FLOOR

    sys.modules["edgar"] = 가짜edgar
    sf._earnings_text_cached = 가짜텍스트
    sf._ensure_identity = lambda: None
    cfg.COLLECT_BUDGET_FLOOR = 3
    try:
        # 마감이 **이미 지난** 상황 (뒷쪽 종목) — 시계를 아주 크게 돌려 둔다
        눈금 = {"n": 10_000}
        sf.set_collect_budget(1 / 60.0, clock=lambda: 0)   # 마감 = 1초
        본_공시["n"] = 0
        보고 = sf.new_report("TT")
        sf.fetch_earnings_8k("TT", start_date="2016-09-15", report=보고,)
        assert 본_공시["n"] >= 3, (
            f"바닥 3건을 채우기 전에 멈췄다 ({본_공시['n']}건) — "
            "뒷쪽 종목이 최신 분기까지 잃는다"
        )
        assert 본_공시["n"] < 50, f"바닥을 채운 뒤에도 안 멈췄다 ({본_공시['n']}건)"
        assert 보고["시간초과"] is True
    finally:
        cfg.COLLECT_BUDGET_FLOOR = 옛바닥
        sf.set_collect_budget(None)
        sf._earnings_text_cached = 옛텍스트
        sf._ensure_identity = 옛신원
        if 옛edgar is None:
            sys.modules.pop("edgar", None)
        else:
            sys.modules["edgar"] = 옛edgar


# ---------------------------------------------------------------------------
# 일꾼 수와 수집 계기 (142차) — "빨라졌나"를 짐작으로 말하지 않기 위해
# ---------------------------------------------------------------------------
def test_일꾼_수는_SEC_허용치_안에_머문다():
    """v1 사고(무절제 병렬 → SEC 403·창구 닫힘)의 재발을 막는 상한입니다.

    실측 근거(2026-08-21 런): 내려받기 6,890건 ÷ 벽시계 182분 =
    **초당 0.63요청** — 일꾼 3일 때 SEC 허용(초당 10건)의 6%. 일꾼을
    두 배로 올려도 초당 1.3건 수준이라 허용치의 13% 입니다.

    이 시험은 "몇이 정답인가"가 아니라 **터무니없이 큰 수로 올리는 것을
    막는** 난간입니다. 올릴 때는 반드시 실측을 보고 올립니다."""
    import config as cfg
    assert 1 <= cfg.COLLECT_WORKERS <= 10, \
        f"일꾼 {cfg.COLLECT_WORKERS}명은 SEC 허용치를 위협합니다"


def test_수집_계기가_로봇기록에_남는다():
    """일꾼을 올린 뒤 **실제로 빨라졌는지**는 벽시계로만 알 수 있습니다.
    종목별 seconds 의 합은 일꾼이 나눠 쓰기 **전의 일감**이라 벽시계가
    아닙니다. 둘을 함께 남겨야 병렬이 먹혔는지 알 수 있습니다."""
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "collect_job.py"), encoding="utf-8") as f:
        text = f.read()
    for 조각 in ('"수집계기"', "벽시계_분", "일감합_분", "겹침배수", "초당요청"):
        assert 조각 in text, f"수집 계기에 {조각} 가 없습니다"
    # 계기는 **기록에 실려야** 합니다 — 화면에만 찍고 버리면 다음 세션이
    # 못 봅니다 (로봇 기록이 유일한 인수인계 통로입니다).
    자리 = text.index('"수집계기": 수집계기')
    조립 = text.index('"per_ticker"')
    assert 자리 < 조립, "수집 계기가 로봇 기록 조립부 안에 없습니다"


def test_겹침배수_계산이_맞다():
    """겹침배수 = 일감합 ÷ 벽시계. 일꾼 수에 가까우면 병렬이 먹힌 것이고
    1에 가까우면 서로 밀어낸 것입니다 — 이 숫자로 다음 판단을 합니다."""
    일감합, 벽시계 = 600.0, 200.0
    assert round(일감합 / 벽시계, 2) == 3.0



# ---------------------------------------------------------------------------
# 보이지 않던 SEC 접속 (145차) — 성공만 세고 있었다
# ---------------------------------------------------------------------------
def test_접속_시도를_성공만이_아니라_전부_센다():
    """실측: 비자(V)는 **내려받기 0건**으로 기록됐는데 905초가 걸렸습니다.
    캐시 적중은 141건뿐이라 파싱으로는 설명이 안 됩니다(파싱은 건당 53ms).

    원인: 받아 봤는데 실적 문서가 아니어서 **빈 결과**인 공시는
    cache_downloads 를 올리지 않았습니다 — 접속은 했는데 한 건도 안
    세어졌습니다. 그러면 SEC 요청 속도가 실제보다 낮게 보이고, 그 숫자로
    일꾼 수를 정하면 위험합니다(v1 사고: 속도 무제한 → 403)."""
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "sec_fundamentals.py"), encoding="utf-8") as f:
        코드 = f.read()
    자리 = 코드.index('report["fetch_attempts"] = report.get("fetch_attempts", 0) + 1')
    호출 = 코드.index("text, source, had_exhibit = _earnings_text(filing, report)")
    assert 자리 < 호출, "접속 시도를 실제 접속 **전에** 세야 합니다"
    with open(os.path.join(root, "collect_job.py"), encoding="utf-8") as f:
        로봇 = f.read()
    assert '"초당요청": round(시도 / 수집벽시계' in 로봇, \
        "요청 속도를 내려받기가 아니라 **시도**로 재야 합니다"


def test_음성캐시는_예외가_났으면_기억하지_않는다():
    """빈 결과를 캐시하지 않던 원래 이유는 옳습니다 — **일시적 망 실패가
    '이 공시엔 텍스트 없음'으로 영구 박제**되는 것을 막기 위해서입니다.

    그래서 예외가 한 건이라도 났으면 기억하지 않고, 받아 보긴 했는데
    실적 보도자료가 안 붙은 8-K(인사·배당·계약 공시)만 기억합니다."""
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "sec_fundamentals.py"), encoding="utf-8") as f:
        코드 = f.read()
    assert "if accession and not text and 에러후 == 에러전:" in 코드, \
        "예외가 났을 때도 음성 캐시를 남기고 있습니다"
    # 캐시 판을 올리면 그 기억이 통째로 무효화되어야 합니다
    앞 = 코드.index("if accession and not text and 에러후 == 에러전:")
    뒤 = 코드.index("if accession and text:", 앞)
    assert "cfg.RAW8K_CACHE_VERSION" in 코드[앞:뒤], \
        "음성 캐시에 판 번호가 없어 무효화할 수 없습니다"


def test_음성캐시_적중은_따로_센다():
    """기억이 실제로 쓰이는지(= 접속을 줄였는지) 봐야 효과를 압니다."""
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "sec_fundamentals.py"), encoding="utf-8") as f:
        코드 = f.read()
    assert 'report["negative_hits"]' in 코드, "음성 캐시 적중을 안 셉니다"
    with open(os.path.join(root, "collect_job.py"), encoding="utf-8") as f:
        로봇 = f.read()
    for 칸 in ('"음성기억"', '"음성적중"', '"접속시도"'):
        assert 칸 in 로봇, f"로봇 기록에 {칸} 가 없습니다"



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
