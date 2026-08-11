"""
measurement.py — 2단계 측정: 조정 EPS 델타 ↔ 주가 (전략.md 7장의 사전 등록 실행)
================================================================================

답하려는 질문 (전략.md 0장):
  발표 시점의 조정 EPS 델타(가속)가 클수록, 이후 주가가 폭등하는가?

사전 등록된 규칙 (데이터를 보기 전에 전략.md 에 박제된 그대로):
  · 진입   = 발표일 **다음 거래일** 종가          (미래 보기 원천 차단)
  · 창     = 60거래일 (≈12주, 분기보다 짧아 창끼리 안 겹침)
  · 표적   = 시장(SPY) 대비 초과수익
  · 폭등   = 초과수익 +20%p 이상 (보조 정의 +30%p)
  · 기준선 = ① 같은 종목 무조건 60거래일 보유  ② 아무 발표나 사기
  · 통계   = 윌슨 신뢰구간 + 종목별 쪼개기
  · 판정   = 모델과 같은 코드(status.judge_delta)를 걸어가며 사용
             — 각 발표 시점에서 그때까지의 분기만 봅니다

입력: 수집 로봇이 저장소로 커밋한 실데이터 (data/measure/snapshot.json).
이 파일은 어떤 값도 만들어 내지 않습니다 — 있는 숫자끼리의 산수만 합니다.
"""

from __future__ import annotations

import bisect
import json
from datetime import date
from statistics import median

import config as cfg
import data_quality as dq
import status

# --- 사전 등록 상수 (전략.md 7장) ---
WINDOW_TRADING_DAYS = 60      # 주 창
SURGE_PP = 20.0               # 폭등 = 시장 대비 +20%p
SURGE_AUX_PP = 30.0           # 보조 정의
BASELINE_STEP = 5             # 기준선① 표본 간격 (5거래일마다 시작점 하나)

# 분기 간격 검사 — 연속된 분기로 인정할 날짜 간격 (일).
# 분기는 약 91일이지만, 분기말 행과 발표일 행이 섞여 있어 폭을 둡니다.
# 이 범위를 벗어나면 사이에 분기가 빠진 것이므로 TTM 합이 오염됩니다 →
# 구간을 끊어 각각 따로 판정합니다 (없으면 없는 채로 — 창작 금지).
SPACING_MIN_DAYS = 55
SPACING_MAX_DAYS = 150

# 지속 상승추세의 정의 — 데이터를 보기 전에 고정 (대시보드의 정배열 근사)
#   주봉 종가 > 26주 이동평균  그리고  13주선 > 26주선  이
#   TREND_SUSTAIN_WEEKS 주 연속이면 "지속상승". 역사가 모자라면 판단하지 않음.
TREND_MA_SHORT = 13
TREND_MA_LONG = 26
TREND_SUSTAIN_WEEKS = 13



def wilson_interval(hits: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """윌슨 신뢰구간(%) — 표본이 작을 때도 과신하지 않는 적중률 구간 (v1 이식)."""
    if total == 0:
        return (0.0, 0.0)
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (z / denominator) * ((p * (1 - p) / total + z * z / (4 * total ** 2)) ** 0.5)
    return (max(0.0, centre - margin) * 100.0, min(1.0, centre + margin) * 100.0)


def _to_date(text: str) -> date:
    return date.fromisoformat(str(text)[:10])


# ---------------------------------------------------------------------------
# 분기 준비 — 조정 EPS 가 있는 행만, 연속 구간으로
# ---------------------------------------------------------------------------
def eps_runs(rows: list[dict]) -> tuple[list[list[dict]], int]:
    """조정 EPS 가 있는 분기를 날짜순으로 정렬해 **연속 구간**으로 쪼갭니다.

    반환: (구간 목록, 간격 문제로 끊어진 횟수)
    """
    usable = [
        r for r in rows
        if r.get("adj_eps") is not None and r.get("filing_date")
    ]
    usable.sort(key=lambda r: str(r["filing_date"]))

    runs: list[list[dict]] = []
    current: list[dict] = []
    breaks = 0
    for row in usable:
        if current:
            gap = (_to_date(row["filing_date"]) - _to_date(current[-1]["filing_date"])).days
            if not (SPACING_MIN_DAYS <= gap <= SPACING_MAX_DAYS):
                runs.append(current)
                current = []
                breaks += 1
        current.append(row)
    if current:
        runs.append(current)
    return runs, breaks


def to_scoring_rows(run: list[dict]) -> list[dict]:
    """스냅샷 행을 판정 코드가 읽는 형태로 바꿉니다 (조정 EPS 를 잣대로)."""
    return [
        {
            "period_label": r.get("period_label") or "",
            "filing_date": str(r["filing_date"]),
            "op_income": r["adj_eps"],          # 잣대 교체 — 값은 원문 그대로
            "basis": cfg.BASIS_ADJ_EPS,         # 저기저 문턱이 $0.05/주 로 잡히게
            "source": cfg.SRC_DIRECT,
        }
        for r in run
    ]


# ---------------------------------------------------------------------------
# 주가 창 — 발표 다음 거래일 진입, 60거래일 보유
# ---------------------------------------------------------------------------
def window_return(
    dates: list[str], closes: list[float], announce: str,
    window: int = WINDOW_TRADING_DAYS,
):
    """(수익률%, 진입일, 청산일) 을 돌려줍니다. 못 재면 (None, 이유, None)."""
    entry = bisect.bisect_right(dates, str(announce)[:10])   # 발표일 **다음** 거래일
    if entry >= len(dates):
        return None, "진입일 없음", None
    # 주가 이력이 발표보다 늦게 시작하면(5년 rolling 창 밖의 옛 발표),
    # "다음 거래일"이 실제로는 몇 달 뒤가 됩니다 — 그런 사건은 재지 않습니다.
    gap_days = (_to_date(dates[entry]) - _to_date(announce)).days
    if gap_days > 10:
        return None, "주가시작전", None
    exit_ = entry + window
    if exit_ >= len(dates):
        return None, "우측검열", None    # 창이 아직 안 끝남 — 세지 않고 개수만 기록
    pct = (closes[exit_] / closes[entry] - 1.0) * 100.0
    return pct, dates[entry], dates[exit_]


def weekly_closes(dates: list[str], closes: list[float], upto: str) -> list[float]:
    """발표일까지의 일봉을 주봉 종가로 바꿉니다 (한 주의 마지막 종가)."""
    weeks: list[float] = []
    current_week = None
    for day, close in zip(dates, closes):
        if day > upto:
            break
        iso = date.fromisoformat(day).isocalendar()
        key = (iso[0], iso[1])
        if key != current_week:
            weeks.append(close)
            current_week = key
        else:
            weeks[-1] = close
    return weeks


def trend_state(dates: list[str], closes: list[float], announce: str) -> str | None:
    """발표 시점의 주봉 추세: '지속상승' / '아님' / None(역사 부족).

    미래를 보지 않습니다 — 발표일까지의 주봉만 사용합니다.
    """
    weeks = weekly_closes(dates, closes, str(announce)[:10])
    need = TREND_MA_LONG + TREND_SUSTAIN_WEEKS
    if len(weeks) < need:
        return None
    for back in range(TREND_SUSTAIN_WEEKS):
        end = len(weeks) - back
        window_long = weeks[end - TREND_MA_LONG : end]
        window_short = weeks[end - TREND_MA_SHORT : end]
        ma_long = sum(window_long) / TREND_MA_LONG
        ma_short = sum(window_short) / TREND_MA_SHORT
        if not (weeks[end - 1] > ma_long and ma_short > ma_long):
            return "아님"
    return "지속상승"


def excess_return(prices: dict, spy: dict, announce: str):
    """종목 수익 − 같은 구간 SPY 수익 (%p). 못 재면 (None, 이유)."""
    stock_pct, entry_date, exit_date = window_return(prices["dates"], prices["close"], announce)
    if stock_pct is None:
        return None, entry_date          # entry_date 자리에 이유가 들어 있음
    spy_map_dates = spy["dates"]
    # SPY 는 같은 거래일 달력이므로 같은 날짜를 찾되, 혹시 빠진 날이면 직전 날로
    si = bisect.bisect_right(spy_map_dates, entry_date) - 1
    sj = bisect.bisect_right(spy_map_dates, exit_date) - 1
    if si < 0 or sj < 0 or si == sj:
        return None, "SPY 정렬 실패"
    spy_pct = (spy["close"][sj] / spy["close"][si] - 1.0) * 100.0
    return stock_pct - spy_pct, (entry_date, exit_date)


# ---------------------------------------------------------------------------
# 사건 수집 — 발표일마다 걸어가며 판정
# ---------------------------------------------------------------------------
def collect_events(snapshot: dict) -> tuple[list[dict], dict]:
    """모든 발표 사건에 (판정, 가속폭, 이후 초과수익) 을 붙입니다."""
    spy = snapshot["prices"].get(snapshot.get("benchmark", "SPY"))
    events: list[dict] = []
    skipped = {"우측검열": 0, "발표일없음": 0, "주가없음": 0, "구간끊김": 0,
               "중복발표": 0, "주가시작전": 0}

    for ticker in snapshot["tickers"]:
        prices = snapshot["prices"].get(ticker)
        rows = snapshot["eps"].get(ticker) or []
        if not prices or not prices.get("dates"):
            skipped["주가없음"] += len(rows)
            continue

        runs, breaks = eps_runs(rows)
        skipped["구간끊김"] += breaks
        seen_dates: set[str] = set()
        past_peak: float | None = None   # 구간이 끊겨도 TTM 정점은 잊지 않는다

        for run in runs:
            newhigh_streak = 0        # 첫돌파 셈 — 구간이 새로 시작하면 연속도 새로
            for idx, row in enumerate(run):
                # ★ 그 시점까지의 분기만 보고 판정 (발표된 분기 포함, 미래 제외).
                #   발표일 없는 분기도 TTM 정점 기억에는 참여해야 하므로
                #   발표일 검사보다 먼저 계산합니다.
                known = to_scoring_rows(run[: idx + 1])

                # H2 — 이번 발표로 TTM(4분기 합)이 **수집 기간 안의** 최고를
                # 넘었는가. 구간이 끊겨도 이전 정점과 비교한다 — 구간 안에서만
                # 비교하면 옛 정점보다 낮은 값이 '신고점'이 된다
                # (8차 감사에서 실제 27건 발견되어 고침).
                ttm = status.ttm_series(known)
                current_ttm = ttm[-1] if ttm else None
                new_high = (
                    current_ttm is not None
                    and past_peak is not None
                    and current_ttm > past_peak
                )
                if current_ttm is not None and (past_peak is None or current_ttm > past_peak):
                    past_peak = current_ttm
                newhigh_streak = newhigh_streak + 1 if new_high else 0

                announce = row.get("announced_date")
                if not announce:
                    skipped["발표일없음"] += 1
                    continue
                announce = str(announce)[:10]
                if announce in seen_dates:      # 원본 + 정정 발표 중복 방지
                    skipped["중복발표"] += 1
                    continue
                seen_dates.add(announce)

                judgment = status.judge_delta(known)
                growths = judgment.get("trace", {}).get("growths", [])
                accel_size = (
                    growths[-1] - growths[-2] if len(growths) >= 2 else None
                )

                # --- 탐색 배터리 요인 (전략.md v2 5장 — 문턱은 exploration 쪽에 고정) ---
                # 델타 변동성: 성장의 꾸준함. 최근 4개 증가율의 표준편차.
                growth_vol = None
                if len(growths) >= 4:
                    window4 = growths[-4:]
                    mean4 = sum(window4) / 4.0
                    growth_vol = (sum((g - mean4) ** 2 for g in window4) / 4.0) ** 0.5
                # 연속 가속: 증가율이 몇 분기 연속으로 커졌는가 (뒤에서부터 셈)
                accel_streak = 0
                for i in range(len(growths) - 1, 0, -1):
                    if growths[i] > growths[i - 1]:
                        accel_streak += 1
                    else:
                        break

                # --- 탐색용 요인 (exploration.py 가 묶어서 봅니다) ---
                # 전부 "그 시점까지 아는 숫자끼리의 산수"입니다. 없으면 None.
                eps_list = [q["op_income"] for q in known]
                q_qoq = dq.safe_growth_pct(eps_list[-2], eps_list[-1]) if len(eps_list) >= 2 else None
                q_yoy = dq.safe_growth_pct(eps_list[-5], eps_list[-1]) if len(eps_list) >= 5 else None
                ttm_yoy = (
                    dq.safe_growth_pct(sum(eps_list[-8:-4]), sum(eps_list[-4:]))
                    if len(eps_list) >= 8 else None
                )
                # TTM 흑자전환: 직전 TTM ≤ 0 인데 이번 TTM > 0
                turnaround = (
                    len(ttm) >= 2 and ttm[-2] <= 0 < ttm[-1]
                ) if ttm else False
                # 매출 요인 — 최근 8분기 매출이 전부 있을 때만 (창작 금지: 빈칸 추정 안 함)
                revs = [r.get("revenue") for r in run[: idx + 1]]
                rev_ttm_yoy = rev_new_high = None
                if len(revs) >= 8 and all(v is not None for v in revs[-8:]):
                    rev_ttm_yoy = dq.safe_growth_pct(sum(revs[-8:-4]), sum(revs[-4:]))
                    rev_ttms = [
                        sum(revs[i - 3 : i + 1])
                        for i in range(3, len(revs))
                        if all(v is not None for v in revs[i - 3 : i + 1])
                    ]
                    if len(rev_ttms) >= 2:
                        rev_new_high = rev_ttms[-1] > max(rev_ttms[:-1])

                excess, detail = excess_return(prices, spy, announce)
                if excess is None:
                    if detail in ("우측검열", "주가시작전"):
                        skipped[detail] += 1
                    continue

                # H3 — 회사가 이번 발표에서 준 다음 분기 가이던스 EPS 대비
                # 이번 실제 EPS 의 증감 (스냅샷에 가이던스가 없으면 없음)
                guid_mid = row.get("guid_eps_mid")
                guid_growth = (
                    dq.safe_growth_pct(eps_list[-1], guid_mid)
                    if guid_mid is not None else None
                )

                events.append(
                    {
                        "ticker": ticker,
                        "announced": announce,
                        "direction": judgment["direction"],
                        "accel_size": accel_size,
                        "new_high": new_high,
                        "newhigh_streak": newhigh_streak,
                        "growth_vol": growth_vol,
                        "accel_streak": accel_streak,
                        "uptrend": trend_state(prices["dates"], prices["close"], announce),
                        "guid_growth": guid_growth,
                        "q_qoq": q_qoq,
                        "q_yoy": q_yoy,
                        "ttm_yoy": ttm_yoy,
                        "turnaround": turnaround,
                        "rev_ttm_yoy": rev_ttm_yoy,
                        "rev_new_high": rev_new_high,
                        "excess": excess,
                        "window": detail,
                    }
                )
    return events, skipped


# ---------------------------------------------------------------------------
# 기준선
# ---------------------------------------------------------------------------
def baseline_hold(snapshot: dict) -> dict:
    """기준선① — 발표와 무관하게 아무 날이나 사서 60거래일 들고 있기."""
    spy = snapshot["prices"].get(snapshot.get("benchmark", "SPY"))
    hits20 = hits30 = total = 0
    for ticker in snapshot["tickers"]:
        prices = snapshot["prices"].get(ticker)
        if not prices or not prices.get("dates"):
            continue
        dates, closes = prices["dates"], prices["close"]
        for start in range(0, len(dates) - WINDOW_TRADING_DAYS - 1, BASELINE_STEP):
            stock_pct = (closes[start + WINDOW_TRADING_DAYS] / closes[start] - 1.0) * 100.0
            si = bisect.bisect_right(spy["dates"], dates[start]) - 1
            sj = bisect.bisect_right(spy["dates"], dates[start + WINDOW_TRADING_DAYS]) - 1
            if si < 0 or sj <= si:
                continue
            spy_pct = (spy["close"][sj] / spy["close"][si] - 1.0) * 100.0
            excess = stock_pct - spy_pct
            total += 1
            hits20 += excess >= SURGE_PP
            hits30 += excess >= SURGE_AUX_PP
    return {"n": total, "rate20": _rate(hits20, total), "rate30": _rate(hits30, total),
            "wilson20": wilson_interval(hits20, total)}


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total * 100.0, 1) if total else None


def _group_stats(group: list[dict]) -> dict:
    n = len(group)
    hits20 = sum(1 for e in group if e["excess"] >= SURGE_PP)
    hits30 = sum(1 for e in group if e["excess"] >= SURGE_AUX_PP)
    return {
        "n": n,
        "rate20": _rate(hits20, n),
        "wilson20": wilson_interval(hits20, n) if n else (0.0, 0.0),
        "rate30": _rate(hits30, n),
        "median_excess": round(median(e["excess"] for e in group), 1) if n else None,
    }


# ---------------------------------------------------------------------------
# 종합
# ---------------------------------------------------------------------------
def run_measurement(snapshot: dict) -> dict:
    events, skipped = collect_events(snapshot)

    accel = [e for e in events if e["direction"] in (cfg.D_ACCEL, cfg.D_WEAK_ACCEL)]
    decel = [e for e in events if e["direction"] in (cfg.D_DECEL, cfg.D_WEAK_DECEL)]
    steady = [e for e in events if e["direction"] == cfg.D_STEADY]
    mixed = [e for e in events if e["direction"] == cfg.D_MIXED]
    unknown = [e for e in events if e["direction"] == cfg.D_UNKNOWN]

    # H1 의 단조 검사 — 가속 사건을 가속폭 순으로 3등분해 폭등 비율 비교
    sized = sorted((e for e in accel if e["accel_size"] is not None),
                   key=lambda e: e["accel_size"])
    third = len(sized) // 3
    terciles = None
    if third >= 2:      # 한 구간에 최소 2개는 있어야 비율이 의미를 가짐
        terciles = [
            _group_stats(sized[:third]),
            _group_stats(sized[third: 2 * third]),
            _group_stats(sized[2 * third:]),
        ]

    per_ticker = {}
    for event in accel:
        per_ticker.setdefault(event["ticker"], []).append(event)

    return {
        "사건수": len(events),
        "건너뜀": skipped,
        "그룹": {
            "가속(강+약)": _group_stats(accel),
            "유지": _group_stats(steady),
            "혼조": _group_stats(mixed),
            "감속(강+약)": _group_stats(decel),
            "판단불가": _group_stats(unknown),
        },
        "H2_신고점돌파": _group_stats([e for e in events if e.get("new_high")]),
        "H2_신고점아님": _group_stats([e for e in events if not e.get("new_high")]),
        "기준선②_모든발표": _group_stats(events),
        "기준선①_아무날이나": baseline_hold(snapshot),
        "가속폭_3등분": terciles,
        "가속_종목별": {t: _group_stats(g) for t, g in sorted(per_ticker.items())},
        "가속_사건들": [
            {k: e[k] for k in ("ticker", "announced", "direction", "accel_size", "excess")}
            for e in sorted(accel, key=lambda e: e["announced"])
        ],
    }


if __name__ == "__main__":
    with open("data/measure/snapshot.json", encoding="utf-8") as f:
        snap = json.load(f)
    result = run_measurement(snap)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
