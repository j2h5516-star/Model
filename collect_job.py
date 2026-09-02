"""
collect_job.py — 수집 로봇의 몸통 (깃허브 액션에서 매일 실행)
=============================================================

v2 구조(전략.md 4장)의 첫 몸입니다. 사람 조작 없이:

  ① SEC 8-K에서 조정 EPS 원문을 수집하고 (sec_fundamentals — v1 이식)
  ② 야후에서 일봉 주가를 받고 (market_data — v1 이식)
  ③ 검사·꾸리기를 거쳐 (measure_store — v1 이식)
  ④ data/measure/ 아래에 **파일로 쓴다** (커밋은 워크플로가 한다)

로봇의 실행 기록은 깃허브 액션 로그와 data/measure/robot_log.json 에
남습니다 — "새 코드 경로는 실행 흔적으로 증명한다" 규칙의 이행입니다.

절반 이상의 종목에서 실적 수집이 실패하면 종료코드 1 로 끝나
그날 커밋을 막고 실패를 드러냅니다 (반쯤 깨진 데이터로 덮어쓰지 않기).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import config as cfg
import data_health
import dataset
import judge
import market_data as md
import measure_engine
import measure_store
import consensus_feed
import vendor_compare
import vendor_feed
import sec_fundamentals as sf
import leadership
import sector_model


def collect_fundamentals(tickers: list[str], progress=print) -> list[dict]:
    """종목별 실적을 **절제된 병렬**로 수집합니다 (26차 개선).

    v1 사고(무절제 병렬 → SEC 403·"client has been closed")의 원인은 병렬
    자체가 아니라 ① set_identity 경쟁 ② 속도 무제한이었습니다. ①은
    sec_fundamentals 의 _identity_lock 이 막고(신원을 병렬 시작 **전에**
    한 번 설정해 두 번째 방어), ②는 일꾼 수를 config.COLLECT_WORKERS(=3)
    로 묶어 SEC 허용치(초당 10요청)의 절반 아래로 유지합니다.
    문서 캐시(증분 수집) 덕에 평상시 요청 수 자체도 적습니다.
    결과 목록은 입력 종목 순서 그대로 돌려줍니다 (재현성).
    """
    sf._ensure_identity()          # 병렬 시작 전에 신원 설정 — 경쟁 원천 차단
    reports: list[dict | None] = [None] * len(tickers)
    done_lock = threading.Lock()
    done = 0

    def _one(index: int, ticker: str) -> None:
        nonlocal done
        started = time.monotonic()
        try:
            _quarters, report = sf.get_fundamentals(ticker, use_cache=False)
        except Exception as exc:
            report = sf.new_report(ticker)
            report["first_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        report["seconds"] = round(time.monotonic() - started, 1)
        reports[index] = report
        with done_lock:
            done += 1
            progress(
                f"[{done}/{len(tickers)}] {ticker}: "
                f"직접공시 {report.get('merged_direct', 0)}건 · "
                f"조정EPS {report.get('adj_eps_ok', 0)}건 · "
                f"캐시 {report.get('cache_hits', 0)}/신규 "
                f"{report.get('cache_downloads', 0)} · "
                f"{report['seconds']}초"
                + (f" · ⚠️ {report['first_error']}" if report.get("first_error") else "")
            )

    with ThreadPoolExecutor(max_workers=cfg.COLLECT_WORKERS) as pool:
        for index, ticker in enumerate(tickers):
            pool.submit(_one, index, ticker)
    return [r for r in reports if r is not None]


def collect_prices(tickers: list[str], progress=print) -> dict:
    """일봉을 일괄 수집합니다. 야후가 일부를 거부하면 한 번 더 시도합니다."""
    wanted = list(dict.fromkeys(list(tickers) + [cfg.BENCHMARK]))
    daily_map, _failed = md.fetch_daily_data(wanted)
    missing = [t for t in wanted if t not in daily_map]
    if missing:
        progress(f"주가 재시도: {missing}")
        time.sleep(3.0)
        retry_map, _ = md.fetch_daily_data(missing)
        daily_map.update(retry_map)
    progress(f"주가 확보: {len(daily_map)}/{len(wanted)}종목")
    return daily_map


def success_enough(reports: list[dict], daily_map: dict, tickers: list[str]) -> bool:
    """커밋해도 될 만큼 수집됐는가 — 반쯤 깨진 날은 덮어쓰지 않습니다."""
    fund_ok = sum(1 for r in reports if r.get("adj_eps_ok", 0) > 0 or r.get("xbrl_quarters", 0) > 0)
    price_ok = sum(1 for t in tickers if t in daily_map)
    half = len(tickers) / 2.0
    return fund_ok >= half and price_ok >= half and cfg.BENCHMARK in daily_map


def write_files(files: dict[str, str], progress=print) -> None:
    for path, content in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        progress(f"기록: {path} ({len(content):,}자)")


def run(tickers: list[str] | None = None, progress=print) -> int:
    if tickers is None:
        tickers = list(cfg.TICKERS)
    progress(f"수집 로봇 시작 — {len(tickers)}종목 · {datetime.now(timezone.utc).isoformat()}")

    # 8-K 훑기에 시간 예산을 겁니다 (94차 — 10년 확장 안전장치).
    # 넘기면 옛 분기를 포기하고 멈춰, 그날 수집물과 원문 캐시를 꼭 남깁니다.
    # 자세한 이유는 config.COLLECT_BUDGET_MINUTES 주석에 적어 두었습니다.
    budget = getattr(cfg, "COLLECT_BUDGET_MINUTES", None)
    sf.set_collect_budget(budget)
    progress(f"8-K 훑기 시간 예산: {budget}분" if budget else "8-K 훑기 시간 예산: 없음")

    # 수집 벽시계를 잽니다 (142차) — 일꾼 수를 올릴 때마다 "빨라졌나"를
    # 짐작으로 말하지 않기 위해서입니다(91·98·106차 규칙: 짐작 전에 계기).
    # 종목별 seconds 는 이미 있지만 그 합은 **일꾼이 나눠 쓰기 전의 일감**
    # 이라 벽시계가 아닙니다. 둘을 함께 남겨야 병렬이 실제로 먹혔는지
    # 알 수 있습니다.
    수집시작 = time.monotonic()
    reports = collect_fundamentals(tickers, progress)
    수집벽시계 = round(time.monotonic() - 수집시작, 1)
    일감합 = round(sum(r.get("seconds") or 0 for r in reports), 1)
    내려받기 = sum(r.get("cache_downloads") or 0 for r in reports)
    # 접속 **시도** 총합 (145차) — 요청 속도는 이 값으로 재야 맞습니다.
    # 내려받기만 세면 "받아 봤는데 빈 결과"인 접속이 통째로 빠집니다.
    시도 = sum(r.get("fetch_attempts") or 0 for r in reports)
    음성기억 = sum(r.get("negative_cached") or 0 for r in reports)
    음성적중 = sum(r.get("negative_hits") or 0 for r in reports)
    수집계기 = {
        "일꾼": cfg.COLLECT_WORKERS,
        "코어": os.cpu_count(),
        "벽시계_분": round(수집벽시계 / 60.0, 1),
        "일감합_분": round(일감합 / 60.0, 1),
        # 일감합 ÷ 벽시계 = 실제로 몇 명분이 겹쳐 돌았나. 일꾼 수에
        # 가까우면 병렬이 먹힌 것이고, 1에 가까우면 서로 밀어낸 것입니다.
        "겹침배수": round(일감합 / 수집벽시계, 2) if 수집벽시계 else None,
        "내려받기": 내려받기,
        # SEC 허용치는 초당 10건. 이 값이 거기 가까워지면 일꾼을 더 올리면
        # 안 됩니다 (v1 사고: 속도 무제한 → 403).
        # ⚠️ 초당요청은 **시도**로 잽니다(145차). 내려받기로 재면 실제보다
        #    낮게 나와 일꾼 수를 잘못 판단합니다.
        "접속시도": 시도,
        "초당요청": round(시도 / 수집벽시계, 2) if 수집벽시계 else None,
        "초당요청_내려받기만": round(내려받기 / 수집벽시계, 2) if 수집벽시계 else None,
        "음성기억": 음성기억,
        "음성적중": 음성적중,
    }
    progress(
        f"⏱ 수집 계기 — 일꾼 {수집계기['일꾼']} · 코어 {수집계기['코어']} · "
        f"벽시계 {수집계기['벽시계_분']}분 · 일감합 {수집계기['일감합_분']}분 · "
        f"겹침 {수집계기['겹침배수']}배 · 초당 {수집계기['초당요청']}요청"
        f"(SEC 허용 10) · 접속 시도 {시도} · "
        f"실적문서아님 기억 {음성기억}·적중 {음성적중}"
    )
    시간초과 = [r["ticker"] for r in reports if r.get("시간초과")]
    if 시간초과:
        progress(
            f"⏱ 시간 예산에 걸려 옛 분기를 못 받은 종목 {len(시간초과)}개: "
            + ", ".join(시간초과[:12])
            + (" …" if len(시간초과) > 12 else "")
            + " — 다음 런이 캐시로 이어받습니다"
        )
    daily_map = collect_prices(tickers, progress)

    if not success_enough(reports, daily_map, tickers):
        progress("⛔ 절반 이상 실패 — 오늘 데이터로 덮어쓰지 않습니다 (종료코드 1)")
        return 1

    files, summary = measure_store.build_files(tickers, daily_map, reports)

    # 수집물 전체 건강검진 (108차) — **값을 바꾸지 않고 세기만** 합니다.
    #
    # data_quality 는 "한 분기 안에서 앞뒤가 맞나"를 보는데, 2026-08-18 에
    # 찾아낸 사고들은 전부 그 그물을 빠져나갔습니다 (칸이 통째로 빔 ·
    # 그럴듯한 쓰레기 · 고침의 부수 피해). 셋 다 **세어야만** 드러나므로
    # 로봇이 매일 세게 합니다. 자세한 내력은 data_health.py 머리말에.
    #
    # ⚠️ 어제 수집물은 **덮어쓰기 전에** 읽어야 합니다 (write_files 전).
    건강 = None
    try:
        어제 = None
        옛경로 = f"{cfg.MEASURE_DIR}/snapshot.json"
        if os.path.exists(옛경로):
            with open(옛경로, encoding="utf-8") as f:
                어제 = (json.load(f) or {}).get("eps")
        새 = json.loads(files[f"{cfg.MEASURE_DIR}/snapshot.json"])["eps"]
        건강 = data_health.report(새, 어제)
        채움 = 건강["채움률"]
        빈칸 = [k for k in data_health.WATCHED_FIELDS
                if (채움.get(k) or {}).get("찬칸") == 0]
        progress(
            "🩺 건강검진 — "
            + " · ".join(
                f"{k} {채움[k]['비율']}%" for k in ("revenue", "adj_eps", "gaap_eps")
            )
            + (f" · 어제 대비 바뀐 칸 {건강['어제 대비']['바뀐 칸']}"
               if 건강.get("어제 대비") else " · 어제 수집물 없음")
            + f" · 이상값 매출 {건강['이상값']['revenue']['건수']}칸"
            + (f" · ⚠️ 통째로 빈 칸: {', '.join(빈칸)}" if 빈칸 else "")
        )
    except Exception as exc:
        건강 = {"오류": f"{type(exc).__name__}: {str(exc)[:160]}"}
        progress(f"⚠️ 건강검진 실패: {건강['오류']}")

    # v3 5단계 — 수집 성공 시 등록된 판정(11차)을 자동 계산합니다 (사람 개입 없음).
    # 방금 만든 snapshot 내용으로 데이터 계층 → 측정 장치 → 자동 판정 순서.
    # ⚠️ 판정이 실패해도 그날의 **데이터 커밋은 막지 않습니다** — 데이터가 더
    #    귀합니다. 대신 실패를 로봇 기록에 남겨 조용히 넘어가지 않게 합니다.
    try:
        snap = json.loads(files[f"{cfg.MEASURE_DIR}/snapshot.json"])
        # 액면분할 환산 재료 (112차) — 디스크의 vendor.json 은 어제 것이라
        # 새 분할은 하루 늦게 반영됩니다 (분할은 드물어 감수).
        ds = dataset.build(snap, splits=dataset.load_splits())
        events, _skipped = measure_engine.collect_events(ds)
        # H10 (23차 등록) — 논갭 영업이익 단독 사건은 별도 목록으로
        op_events = measure_engine.collect_metric_events(ds, "op_income")
        verdict = judge.run(events, op_events=op_events)
        # H11·H11b (33차 등록) — 섹터 정배열 폭 모델
        breadth_events = sector_model.collect_breadth_events(ds)
        verdict["가설"].update(judge.judge_sector_breadth(breadth_events))
        # H18 (43차 등록) — 정배열 완성 시점의 52주선 이격도.
        # 42차 탐색에서 나온 후보라 등록일 뒤의 새 완성만 판정 표본입니다.
        # H22·H22b (109차 등록) — 신고점의 폭. 판정 표본은 등록일 뒤의
        # 새 발표만이며, 탐색에 쓴 표본은 '탐색표본(참고)'로 따로 적습니다.
        verdict["가설"].update(judge.judge_newhigh_margin(events))
        # H23 (116차 등록) — 깊은 게이지의 H5b. 100차 깊이 표를 보고 만든
        # 가설이라 판정 표본은 등록일 뒤의 새 발표만입니다.
        verdict["가설"].update(judge.judge_deep_gauge(events))
        # H24 (121차 등록) — 장세 조건부 첫돌파. 띠(20~60%)를 탐색 표를
        # 보고 골랐으므로 판정 표본은 등록일 뒤의 새 발표만입니다.
        sector_model.attach_market_breadth(ds, events)
        verdict["가설"].update(judge.judge_regime_breakout(events))
        # H25·H25b (124차 등록) — 런업(사전 60거래일 초과수익)과 월가
        # 서프라이즈. 문턱을 탐색 표를 보고 골랐으므로 판정 표본은
        # 등록일 뒤의 새 발표만입니다. 서프라이즈 재료는 디스크의
        # vendor.json(어제 것 — 분할과 같은 하루 늦음, 감수)에서 읽습니다.
        measure_engine.attach_runup(ds, events)
        vendor_compare.attach_street_surprise(
            events, vendor_feed.load(f"{cfg.MEASURE_DIR}/vendor.json"))
        verdict["가설"].update(judge.judge_momentum_beat(events))
        # H27·H28 (151차 등록) — 가뭄 끝 첫돌파 · 안 달린 첫돌파. 문턱
        # (8발표 · −15%)을 탐색 표를 보고 골랐으므로 판정 표본은 등록일
        # 뒤의 새 발표만입니다. "가뭄"은 사건에 이미 실려 오고(측정 장치),
        # "고가대비"만 여기서 붙입니다.
        measure_engine.attach_high52(ds, events)
        verdict["가설"].update(judge.judge_drought_breakout(events))
        verdict["가설"].update(judge.judge_position_breakout(events))
        # H31 (164차 등록) — 가속 ∧ 첫돌파. "가속"은 사건에 이미 실려
        # 옵니다(측정 장치). 등록 시점에 결과를 보지 않았지만 판정은
        # 다른 가설과 같게 등록일 뒤의 새 발표만 셉니다.
        verdict["가설"].update(judge.judge_accel_breakout(events))
        # H26 (143차 등록) — 창 125거래일 첫 돌파를 **탐색에 쓰지 않은 새
        # 종목**으로만 판정합니다. 목록이 비어 있으면(확장 전) 판정하지
        # 않고 그 사실을 적습니다 — 없는 것을 지어내지 않습니다.
        verdict["가설"][judge.H26_NAME] = judge.judge_newhigh_125(
            events, getattr(cfg, "UNIVERSE_V5_NEW", ()))
        # 원문 부탁 목록 갱신 (134차) — 115차 배관이 사람 손에만 매여 있어
        # 새 90종목의 오염 공시가 한 건도 못 들어갔습니다(133차 실측).
        # 이제 로봇이 매일 다시 적고, **다음 런이 그 원문을 담아 옵니다.**
        try:
            import audit_data
            audit_data.refresh_wanted(
                ds["quarters"],
                vendor_feed.load(f"{cfg.MEASURE_DIR}/vendor.json"),
                progress=progress)
        except Exception as exc:
            progress(f"⚠️ 부탁 목록 갱신 실패: {type(exc).__name__}: {str(exc)[:120]}")
        completions = sector_model.completion_events(ds)
        verdict["가설"].update(judge.judge_completion_gap(
            completions, sector_model.H18_START_DAY, sector_model.H18_GAP_MIN,
        ))
        # H18b (126차 등록) — 같은 신호, 표적만 1년(초과250). 주인 지적
        # ("완성은 1~2년 지속")을 126차 백테스트로 확인하고 등록했습니다.
        verdict["가설"].update(judge.judge_completion_gap_1y(
            completions, sector_model.H18B_START_DAY, sector_model.H18_GAP_MIN,
        ))
        # H29 (152차 등록) — 완성 ∧ 델타↑ ∧ 이격 상승 ∧ 이격도 30%+.
        # 문턱을 탐색 표를 보고 골랐으므로 등록일 뒤 새 완성만 센다.
        verdict["가설"].update(judge.judge_completion_combo(
            completions, sector_model.H29_START_DAY, sector_model.H18_GAP_MIN,
        ))
        # H30 (160차 등록) — 무너진 섹터가 다음 60일에 시장을 이기는가.
        # 주인 질문("AI 하락 중인데 비중을 옮겨야 하나")에서 나온 탐색을
        # 사전 등록한 것이라, 등록일 뒤 **새 시점만** 판정한다.
        verdict["가설"].update(judge.judge_sector_momentum(
            sector_model.sector_momentum_events(ds),
            sector_model.H30_START_DAY, sector_model.H30_WEAK_PP,
        ))
        # H19·H20·H21 (44차 등록) — 주도섹터 판정·전환·분기점.
        # 45차 확정 분류(config.GROUPS)로 매일 다시 셉니다. 표본이 국면
        # 단위라 오래 "판정 불가"로 남을 것이며, 그것을 그대로 적습니다.
        states = leadership.weekly_group_state(ds)
        timeline = leadership.leadership_timeline(states)
        switches = leadership.evaluate_switches(
            ds, leadership.switch_events(timeline))
        inflections = leadership.evaluate_inflections(
            ds, leadership.inflection_events(timeline))
        # H19b (46차 ⑦ 등록) — 완성 후 확인형. 기준선은 확인이 서지 않은
        # 모든 (묶음, 주)이며, 판정 표본은 등록일 뒤의 확인만입니다.
        confirmations = leadership.evaluate_confirmations(
            ds, leadership.confirmation_events(ds, states=states))
        fired = {(e["주"], e["묶음"]) for e in confirmations}
        members = leadership.group_members(ds, leadership.default_groups())
        baseline = []
        for row in states:
            if (row["주"], row["묶음"]) in fired:
                continue
            value = leadership.group_excess(
                ds, members.get(row["묶음"]) or [], row["주"])
            if value is not None:
                baseline.append(value)
        # 안정성 (52차 감사) — 잣대값을 조금 지워 봤을 때 주도가 얼마나
        # 바뀌는가. 실패해도 판정 자체는 계속되게 따로 감쌉니다.
        try:
            stability = leadership.stability_report(ds)
        except Exception as exc:
            stability = {"오류": f"{type(exc).__name__}: {str(exc)[:120]}"}
        verdict["가설"].update(judge.judge_leadership(
            timeline, switches, inflections,
            confirmations=confirmations, baseline=baseline,
            start_day=leadership.H19B_START_DAY, stability=stability))
        files[f"{cfg.MEASURE_DIR}/verdict.json"] = judge.to_json(verdict)
        verdict_note = " · ".join(
            f"{name}: {entry['판정']}" for name, entry in verdict["가설"].items()
        )
        progress(f"자동 판정 — {verdict_note}")
    except Exception as exc:
        verdict_note = f"판정 실패: {type(exc).__name__}: {str(exc)[:160]}"
        progress(f"⚠️ {verdict_note}")

    # 전망 축 ② — 야후 컨센서스 원장 (헌법 2장 제1조 개정, 2026-08-14).
    # 추가 전용: 오늘 스냅샷만 붙이고 과거 항목은 절대 고치지 않습니다.
    # 실패해도 그날의 데이터 커밋은 막지 않습니다 (관찰 전용 축).
    try:
        ledger = consensus_feed.load(f"{cfg.MEASURE_DIR}/consensus.json")
        consensus_note = consensus_feed.collect(
            tickers, ledger,
            as_of=datetime.now(timezone.utc).date().isoformat(),
            progress=progress,
        )
        files[f"{cfg.MEASURE_DIR}/consensus.json"] = consensus_feed.to_json(ledger)
    except Exception as exc:
        consensus_note = f"컨센서스 수집 실패: {type(exc).__name__}: {str(exc)[:160]}"
        progress(f"⚠️ {consensus_note}")

    # A축 소급 — 야후 보관 서프라이즈 기록 (31차). 실패해도 커밋 안 막음.
    try:
        archive = consensus_feed.load_surprises(f"{cfg.MEASURE_DIR}/surprise.json")
        surprise_note = consensus_feed.collect_surprises(tickers, archive,
                                                         progress=progress)
        files[f"{cfg.MEASURE_DIR}/surprise.json"] = consensus_feed.to_json(archive)
    except Exception as exc:
        surprise_note = f"서프라이즈 수집 실패: {type(exc).__name__}: {str(exc)[:160]}"
        progress(f"⚠️ {surprise_note}")

    # 두 번째 자 — 데이터 회사(야후) 분기표 (75차, 주인 질문에서 나온 것).
    # **snapshot.json 과 섞지 않습니다.** 우리 파서 값과 대조해 불일치를
    # 재기 위한 관찰 전용 축입니다. 실패해도 그날 커밋을 막지 않습니다.
    try:
        옛 = vendor_feed.load(f"{cfg.MEASURE_DIR}/vendor.json")
        보관, vendor_note = vendor_feed.collect(
            tickers, 옛,
            as_of=datetime.now(timezone.utc).date().isoformat(),
            progress=progress,
        )
        files[f"{cfg.MEASURE_DIR}/vendor.json"] = vendor_feed.to_json(보관)
        progress(vendor_note)
    except Exception as exc:
        vendor_note = f"두 번째 자 수집 실패: {type(exc).__name__}: {str(exc)[:160]}"
        progress(f"⚠️ {vendor_note}")

    # 로봇 실행 기록 — 다음 세션이 읽습니다
    log = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "tickers": len(tickers),
        # 수집 계기 (142차) — 일꾼을 올릴 때 "빨라졌나"를 재는 자리
        "수집계기": 수집계기,
        "summary": summary,
        "verdict": verdict_note,
        "consensus": consensus_note,
        "surprise": surprise_note,
        "vendor": vendor_note,
        # 수집물 전체 건강검진 (108차) — 채움률·어제 대비 변화·이상값.
        # 값은 안 바꾸고 세기만 합니다. 며칠 쌓아 정상 범위를 실측한 뒤
        # 경보 문턱을 사전 등록합니다.
        "건강검진": 건강,
        "per_ticker": [
            {
                "ticker": r.get("ticker"),
                "adj_eps_ok": r.get("adj_eps_ok", 0),
                "merged_direct": r.get("merged_direct", 0),
                "seconds": r.get("seconds"),
                "cache_hits": r.get("cache_hits", 0),
                "cache_downloads": r.get("cache_downloads", 0),
                # 145차 — 보이지 않던 SEC 접속을 드러내는 칸들
                "fetch_attempts": r.get("fetch_attempts", 0),
                "negative_cached": r.get("negative_cached", 0),
                "negative_hits": r.get("negative_hits", 0),
                "first_error": r.get("first_error", ""),
                # 91차 — XBRL 조회가 **조용히 0 건**을 돌려주는 자리를 찾기
                # 위한 계기. GAAP EPS 가 스냅샷에 한 건도 안 들어와 있는데
                # 오류 기록이 하나도 없어 원인을 짚을 수가 없었습니다.
                # 개념별 "받음·버림·모호·남음" 을 그대로 남깁니다.
                "xbrl_calls": r.get("xbrl_calls", []),
                "xbrl_rejected": r.get("xbrl_rejected", 0),
                # 94차 — 시간 예산에 걸려 옛 분기를 못 받았나. "10년치를
                # 받았다"고 짐작하지 말고 이 표시와 note 를 보세요.
                # 106차 계기 — 분기 목록은 논갭 영업이익에서만 만들어지므로,
                # 영업이익이 없는 분기의 매출은 찾아 놓고도 버려집니다.
                # 얼마나 버려지는지 세어 둡니다 (아직 고치지 않고 잽니다).
                "xbrl_orphan": r.get("xbrl_orphan") or {},
                # 156차 계기 — 4분기(연말) 행이 통째로 없어 생기는 "빠진
                # 분기" 79건의 원인을 가릅니다. 항목별로 연간값 수 ·
                # 채운 수 · 앞선 세 분기가 모자라 못 채운 수를 남깁니다.
                "q4_채움": r.get("q4_채움") or {},
                # 157차 계기 — 은행 매출 개념 후보가 실제로 있는지.
                # 값은 안 쓰고 세기만 합니다(뼈대는 그대로).
                "은행개념_후보": r.get("은행개념_후보") or {},
                # 159차 — 티커표에서 사라진 회사(ZI·CFLT·DFS·HOLX·X·HES)를
                # 이름으로 뒤져 본 결과. 값은 안 쓰고 기록만 합니다.
                # 번호로 연 종목은 몇 번으로 열었는지도 남깁니다.
                "사라진회사_검색": r.get("사라진회사_검색") or {},
                "회사번호로_열었음": r.get("회사번호로_열었음"),
                "시간초과": bool(r.get("시간초과")),
                "note": r.get("note", ""),
            }
            for r in reports
        ],
    }
    files[f"{cfg.MEASURE_DIR}/robot_log.json"] = json.dumps(log, ensure_ascii=False, indent=1)

    write_files(files, progress)
    progress(f"✅ 완료 — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
