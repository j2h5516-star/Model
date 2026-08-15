"""
app.py — 계기판 (v3 6단계, 설계도.md ④)
=========================================

로봇이 커밋한 데이터(verdict.json · snapshot.json · robot_log.json)를
**읽어서 그대로** 보여 줍니다.

경계 (설계도.md 3장):
  · 자체 계산·점수·추천을 만들지 않습니다 (v1 붕괴의 원인 — 사고 1)
  · 신호를 판단처럼 보이게 하지 않습니다. 모든 상태 옆에 그 상태의
    **과거 실측 폭등률·기준선·판정 상태**를 나란히 적습니다 (정직화 —
    전략.md 2장 제3조)
  · 채택된 신호가 없으면 "채택된 신호 없음"을 그대로 표시합니다

화면 검증: 모바일 폭 412px 실렌더에서 글자 잘림·열 가림 없음을
확인하고 커밋합니다 (CLAUDE.md 2장).

실행: streamlit run app.py
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import config as cfg
import dataset
import measure_engine as me

# 화면에 "최근 발표"로 보여 줄 기간 (표시용 — 측정 규칙 아님)
RECENT_DAYS = 45

# v3 판정 장치(11차 등록)가 쓰는 가설 이름 — verdict.json 이 이 이름들을
# 갖고 있지 않으면 v2 유물이므로 화면에 그렇게 밝힙니다.
V3_HYPOTHESES = (
    "H2_신고점", "H2b_신고점_첫돌파", "H5_실적폭_고정20",
    "H5b_실적폭_중앙값", "H6_결합_H5bxH2b",
)
# 화면 이름은 쉬운 한국어로, 괄호 안 H번호는 등록 문서와 잇는 꼬리표입니다.
HYPOTHESIS_LABELS = {
    "H2_신고점": "이익 신기록 돌파 (H2)",
    "H2b_신고점_첫돌파": "이익 첫 신기록 돌파 (H2b)",
    "H5_실적폭_고정20": "시장 게이지 20% 고정 문턱 (H5)",
    "H5b_실적폭_중앙값": "시장 게이지 — 평소보다 높음 (H5b)",
    "H6_결합_H5bxH2b": "시장 좋음 × 첫 신기록 (H6)",
    "H7_EBITDA_첫돌파": "EBITDA 첫 신기록 (H7)",
    "H8_GAAPEPS_첫돌파": "GAAP EPS 첫 신기록 (H8)",
    "H9_저평가_첫신기록": "첫 신기록 × 주가 52주선 아래 (H9)",
    "H10_논갭영업이익_저평가_첫신기록": "영업이익 첫 신기록 × 52주선 아래 (H10)",
    "H11_섹터정배열폭_60": "섹터 정배열 폭 60% 첫 돌파 (H11)",
    "H11b_섹터정배열폭_80": "섹터 정배열 폭 80% 첫 돌파 (H11b)",
}

# 미채택·대기 신호를 화면에서 쉬운 말로 풀어 주는 설명 (지시 1의 이행).
# "무엇을 재봤는가 / 왜 안 쓰는가"를 한 줄씩 적습니다.
HYPOTHESIS_DETAILS = {
    "H2_신고점": (
        "이익(TTM)이 과거 최고를 넘은 발표를 사면 이기는가? — 신기록 자체만으로는 "
        "아무 발표나 산 것과 차이가 없었습니다."),
    "H2b_신고점_첫돌파": (
        "그중에서도 **처음** 넘은 발표만 골라도 되는가? — 연속 돌파보다 낫지만 "
        "기준선과 갈라질 만큼은 아니었습니다."),
    "H5_실적폭_고정20": (
        "시장 전체에서 신기록이 20% 이상 나오는 '좋은 장세'에 사면 되는가? — "
        "고정 문턱으로는 갈라지지 않았습니다."),
    "H5b_실적폭_중앙값": (
        "장세가 '그 시장의 평소보다' 좋을 때 사면 되는가? — 한때 채택됐다가 "
        "데이터가 늘자 미채택으로 뒤집혔습니다 (장치가 정직하게 작동한 사례)."),
    "H6_결합_H5bxH2b": (
        "좋은 장세 × 첫 신기록을 겹치면 되는가? — 두 조건을 다 만족하는 발표가 "
        "아직 10건이 안 돼 판정을 미루고 있습니다."),
    "H7_EBITDA_첫돌파": (
        "조정 EPS 를 발표하지 않는 회사는 EBITDA 로 같은 것을 봅니다 — "
        "해당 회사가 적어 표본이 아직 부족합니다."),
    "H8_GAAPEPS_첫돌파": (
        "EBITDA 도 없으면 GAAP EPS 로 봅니다 — 기준선과 갈라지지 않았습니다."),
    "H11_섹터정배열폭_60": (
        "섹터의 주가가 무리로 정배열되면(60% 돌파) 그 뒤 1년이 좋은가? — "
        "**오히려 나빴습니다**(9.4% vs 기준선 26.8%). 다 오른 뒤라 늦습니다."),
    "H11b_섹터정배열폭_80": (
        "더 강한 합의(80%)면 다른가? — 그런 경우가 7건뿐이라 판정 불가입니다."),
}


# ---------------------------------------------------------------------------
# 순수 도우미 — 화면 없이도 시험할 수 있게 분리
# ---------------------------------------------------------------------------
def load_json(name: str) -> dict | None:
    path = os.path.join(cfg.MEASURE_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def verdict_is_v3(verdict: dict | None) -> bool:
    """verdict.json 이 11차 등록의 새 판정인지 (아니면 v2 유물인지)."""
    if not verdict or "가설" not in verdict:
        return False
    return all(name in verdict["가설"] for name in V3_HYPOTHESES)


def verdict_rows(verdict: dict | None) -> list[dict]:
    """판정 현황 표의 행 (신규 표본 기준 — 등록된 판정 표본)."""
    if not verdict or "가설" not in verdict:
        return []
    rows = []
    for name, entry in verdict["가설"].items():
        judged = entry.get("신규(판정)") or {}
        signal = judged.get("신호") or {}
        base = judged.get("기준선") or {}
        rows.append(
            {
                "가설": HYPOTHESIS_LABELS.get(name, name),
                "판정": entry.get("판정", "?"),
                "신호": (
                    f"{signal.get('rate')}% (n={signal.get('n')})"
                    if signal.get("n") else "—"
                ),
                "기준선": (
                    f"{base.get('rate')}%" if base.get("n") else "—"
                ),
            }
        )
    return rows


def adopted_names(verdict: dict | None) -> list[str]:
    if not verdict or "가설" not in verdict:
        return []
    return [
        HYPOTHESIS_LABELS.get(name, name)
        for name, entry in verdict["가설"].items()
        if entry.get("판정") == "채택"
    ]


def recent_ticker_rows(ds: dict, today: str | None = None) -> list[dict]:
    """최근 RECENT_DAYS 일 안에 발표한 종목의 사실 상태 (판단 아님)."""
    if today is None:
        today = ds["prices"][ds["benchmark"]]["dates"][-1]
    cutoff = (date.fromisoformat(today) - timedelta(days=RECENT_DAYS)).isoformat()
    yard_names = {"adj_eps": "조정 EPS", "adjusted_ebitda": "조정 EBITDA",
                  "gaap_eps": "GAAP EPS"}
    rows = []
    for ticker in ds["tickers"]:
        quarters = ds["quarters"].get(ticker) or []
        # 측정 잣대(12차 사다리). 미달 종목도 화면에는 사실을 보여 주되
        # 측정 제외임이 드러나게 조정 EPS 기준으로 표시합니다.
        yardstick = me.yardstick_of(quarters) or "adj_eps"
        states = me.earnings_states(quarters, field=yardstick)
        if not states:
            continue
        last = states[-1]
        if last["announced"] < cutoff:
            continue
        if not last["decidable"]:
            status = "판단 불가 (이력 부족)"
        elif last["newhigh_streak"] == 1:
            status = "신고점 첫 돌파"
        elif last["new_high"]:
            status = f"신고점 (연속 {last['newhigh_streak']}번째)"
        else:
            status = "신고점 아님"
        rows.append(
            {
                "종목": ticker,
                "섹터": cfg.SECTORS.get(ticker, "미분류"),
                "잣대": yard_names[yardstick],
                "발표일": last["announced"],
                "TTM 조정EPS": (
                    round(last["ttm"], 2) if last["ttm"] is not None else None
                ),
                "상태": status,
            }
        )
    rows.sort(key=lambda r: r["발표일"], reverse=True)
    return rows


def gauge_now(ds: dict) -> dict:
    """현재 게이지 사실: 값 · H5b 상태 (판단 불가 포함)."""
    series = me.gauge_series(ds)
    today = ds["prices"][ds["benchmark"]]["dates"][-1]
    value = me.gauge_at(series, today)
    h5b = me.gauge_h5b_on(series, today)
    return {"value": value, "h5b": h5b, "asof": today}


def sector_gauge_rows(ds: dict) -> list[dict]:
    """섹터별 실적 폭 (관찰용 — 사전 등록된 가설이 아님, 판정에 안 씀).

    각 섹터에 대해 시장 게이지와 같은 계산을 그 섹터 종목만으로 하고,
    "평소(자기 이력 중앙값) 대비"도 같은 방법으로 봅니다.
    게이지가 높은 섹터부터 정렬합니다 — 신기록이 무리 지어 나오는
    무리(주도 후보)가 위로 옵니다.
    """
    today = ds["prices"][ds["benchmark"]]["dates"][-1]
    by_sector: dict[str, list[str]] = {}
    for ticker in ds["tickers"]:
        by_sector.setdefault(cfg.SECTORS.get(ticker, "미분류"), []).append(ticker)

    rows = []
    for sector, members in by_sector.items():
        series = me.gauge_series(ds, tickers=members)
        value = me.gauge_at(series, today)
        above = me.gauge_h5b_on(series, today)
        rows.append(
            {
                "섹터": sector,
                "종목수": len(members),
                "게이지": value,
                "평소대비": {True: "평소보다 높음", False: "평소 이하",
                             None: "판단 불가"}[above],
            }
        )
    rows.sort(key=lambda r: (r["게이지"] is not None, r["게이지"] or 0), reverse=True)
    return rows


GUID_FRESH_DAYS = 140      # 가이던스 신선도 — 게이지(FRESH_DAYS)와 같은 기준
SPREAD_MIN_N = 3           # 이보다 표본이 적은 섹터는 "표본 부족"으로 표시


def forward_spread_rows(ds: dict, ledger: dict | None,
                        today: str | None = None,
                        metric: str = "eps") -> list[dict]:
    """종목별 전망 스프레드 = (가이던스 중간값 − 컨센서스 평균) / |컨센서스|.

    관찰 전용 (헌법 제1조 개정 조건 — 판정·점수에 안 씀).
    짝짓기 규칙: 마지막 실적 발표에서 나온 다음 분기 가이던스 ↔ 컨센서스
    "이번 분기(0q)" 추정 (둘 다 "다음에 발표될 분기"를 가리킴 — 발표
    주기 정렬 가정, 27차 한계에 기록). 조건:
      · 가이던스 발표가 GUID_FRESH_DAYS 안 (철 지난 가이던스 제외)
      · 컨센서스는 원장 마지막 스냅샷 (원장이 비면 그 종목은 제외)
    """
    if today is None:
        today = ds["prices"][ds["benchmark"]]["dates"][-1]
    tickers_ledger = (ledger or {}).get("tickers", {})
    rows = []
    guid_key = "guid_eps_mid" if metric == "eps" else "guid_rev_mid"
    cons_key = "avg" if metric == "eps" else "rev_avg"
    for ticker in ds["tickers"]:
        quarters = ds["quarters"].get(ticker) or []
        guided = [r for r in quarters
                  if r.get(guid_key) is not None and r.get("announced_date")]
        if not guided:
            continue
        last = max(guided, key=lambda r: r["announced_date"])
        age = (date.fromisoformat(today)
               - date.fromisoformat(last["announced_date"])).days
        if age > GUID_FRESH_DAYS or age < 0:
            continue
        entries = tickers_ledger.get(ticker) or []
        if not entries:
            continue
        cons = (entries[-1].get("rows") or {}).get("0q") or {}
        avg = cons.get(cons_key)
        if not avg:
            continue                      # 컨센서스 없음/0 — 제외
        spread = (last[guid_key] - avg) / abs(avg) * 100.0
        rows.append({
            "ticker": ticker,
            "섹터": cfg.SECTORS.get(ticker, "미분류"),
            "테마": cfg.theme_of(ticker),
            "가이던스": last[guid_key],
            "컨센서스": avg,
            "스프레드%": round(spread, 1),
            "발표일": last["announced_date"],
            "컨센서스일": entries[-1].get("as_of"),
        })
    return rows


def sector_spread_rows(ds: dict, ledger: dict | None,
                       today: str | None = None,
                       group_key: str = "섹터",
                       metric: str = "eps") -> list[dict]:
    """섹터(또는 테마)별 전망 스프레드 중앙값 — 주도 섹터 관찰용.

    스프레드가 큰 순서로 정렬하되, 표본이 SPREAD_MIN_N 미만인 그룹은
    "표본 부족"을 함께 표시합니다 (숨기지 않고 정직하게).
    """
    per_ticker = forward_spread_rows(ds, ledger, today, metric=metric)
    groups: dict[str, list[dict]] = {}
    for row in per_ticker:
        groups.setdefault(row[group_key], []).append(row)
    out = []
    for name, members in groups.items():
        spreads = sorted(r["스프레드%"] for r in members)
        mid = spreads[len(spreads) // 2] if len(spreads) % 2 else round(
            (spreads[len(spreads) // 2 - 1] + spreads[len(spreads) // 2]) / 2, 1)
        out.append({
            group_key: name,
            "종목수": len(members),
            "스프레드중앙%": mid,
            "표본": "표본 부족" if len(members) < SPREAD_MIN_N else "충분",
            "종목": ", ".join(r["ticker"] for r in members),
        })
    out.sort(key=lambda r: r["스프레드중앙%"], reverse=True)
    return out


def hypothesis_note(verdict: dict | None, name: str) -> str:
    """정직화 문구: 그 상태의 과거 실측 + 판정 상태 (verdict 에서 복사)."""
    if not verdict or name not in (verdict.get("가설") or {}):
        return "과거 실측 없음 (판정 대기)"
    entry = verdict["가설"][name]
    judged = entry.get("신규(판정)") or {}
    signal = judged.get("신호") or {}
    base = judged.get("기준선") or {}
    if not signal.get("n"):
        return f"판정: {entry.get('판정', '?')}"
    return (
        f"이 상태의 과거 폭등률 {signal.get('rate')}% (n={signal.get('n')}) · "
        f"기준선 {base.get('rate')}% · 판정: {entry.get('판정', '?')}"
    )


# ---------------------------------------------------------------------------
# 화면 (streamlit)
# ---------------------------------------------------------------------------
# --- 정배열 폭 구간별 과거 실측 (34차 탐색 — 등록 근거 아님, 표시용) ---
# 숫자는 34차 실행 출력에서 복사. 화면에 "탐색값"임을 반드시 함께 적습니다.
BREADTH_ZONES = [
    (60.0, 101.0, "정배열 완성 구간", 7.7,
     "무리 전체가 이미 정배열 — 과거 1년 폭등률이 가장 낮았습니다"),
    (40.0, 60.0, "쌓이는 중 (역사적 최적)", 33.3,
     "절반쯤 정배열 — 과거 1년 폭등률이 가장 높았던 구간"),
    (25.0, 40.0, "초기", 25.8, "이제 모이기 시작하는 단계"),
    (0.0, 25.0, "약함", 27.0, "정배열 종목이 드문 상태"),
]
BREADTH_BASELINE = 26.8      # 34차 기준선 (모든 섹터·주, n=1,872)


def surprise_sector_rows(surprise: dict | None, quarters: int = 2) -> list[dict]:
    """섹터별 실적 서프라이즈 중앙값 (야후 보관 기록 — 관찰용).

    최근 quarters 개 분기만 씁니다. 값이 없으면 빈 목록.
    """
    from statistics import median

    entries = (surprise or {}).get("tickers") or {}
    groups: dict[str, list[float]] = {}
    for ticker, rows in entries.items():
        sector = cfg.SECTORS.get(ticker, "미분류")
        for row in rows[-quarters:]:
            if row.get("surprise_pct") is not None:
                groups.setdefault(sector, []).append(row["surprise_pct"])
    out = [{"섹터": name, "건수": len(values),
            "중앙%": round(median(values), 1)}
           for name, values in groups.items()]
    out.sort(key=lambda r: r["중앙%"], reverse=True)
    return out


ZONE_COLORS = {
    "쌓이는 중 (역사적 최적)": "#2E9E5B",   # 초록 — 과거 가장 좋았던 구간
    "초기": "#3B82C4",                      # 파랑 — 모이기 시작
    "약함": "#8A8F98",                      # 회색 — 아직 아님
    "정배열 완성 구간": "#C4553B",          # 주황빨강 — 과거 가장 나빴던 구간
    "판단 불가": "#4A4F58",
}


def sorted_bar_chart(labels, values, value_title, colors=None,
                     positive_negative=False):
    """값 순으로 정렬된 가로 막대 그래프 (모바일 412px 기준).

    st.bar_chart 는 축을 가나다순으로 다시 정렬해 버려 '한눈에'가 깨집니다.
    그래서 알테어로 정렬 순서를 직접 고정합니다.
    colors: 라벨별 색 (없으면 값의 부호로 색을 나눔)
    """
    import altair as alt
    import pandas as pd

    frame = pd.DataFrame({"이름": list(labels), "값": list(values)})
    if colors:
        frame["색"] = [colors.get(label, "#3B82C4") for label in labels]
        color = alt.Color("색:N", scale=None, legend=None)
    elif positive_negative:
        frame["색"] = ["#2E9E5B" if v >= 0 else "#C4553B" for v in values]
        color = alt.Color("색:N", scale=None, legend=None)
    else:
        color = alt.value("#3B82C4")
    return (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            y=alt.Y("이름:N", sort=list(labels), title=None,
                    axis=alt.Axis(labelLimit=120)),
            x=alt.X("값:Q", title=value_title),
            color=color,
            tooltip=["이름", "값"],
        )
        .properties(height=max(200, 30 * len(frame)))
    )


def breadth_zone(breadth: float | None) -> dict:
    """폭 값 → 구간 이름·과거 실측 폭등률 (34차 탐색값)."""
    if breadth is None:
        return {"zone": "판단 불가", "rate": None, "note": "이력이 부족합니다"}
    for low, high, name, rate, note in BREADTH_ZONES:
        if low <= breadth < high:
            return {"zone": name, "rate": rate, "note": note}
    return {"zone": "판단 불가", "rate": None, "note": ""}


def live_signal_rows(ds: dict, days: int = 90) -> list[dict]:
    """지금 채택 신호(H9·H10)가 켜진 종목 — 최근 days 일 발표 중.

    H9 = 잣대 TTM 첫 신기록 ∧ 주봉 종가 < 52주선
    H10 = 논갭 영업이익 첫 신기록 ∧ 같은 조건
    """
    today = ds["prices"][ds["benchmark"]]["dates"][-1]
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    rows = []
    for ticker in ds["tickers"]:
        quarters = ds["quarters"].get(ticker) or []
        prices = ds["prices"].get(ticker)
        if not prices or not prices.get("dates"):
            continue
        hits = []
        yardstick = me.yardstick_of(quarters)
        if yardstick:
            states = me.earnings_states(quarters, field=yardstick)
            if states and states[-1]["announced"] >= cutoff \
                    and states[-1]["newhigh_streak"] == 1 \
                    and me.below_52wk_ma(prices, states[-1]["announced"]) is True:
                hits.append("H9")
        op_rows = [r for r in quarters if r.get("op_income") is not None]
        if len(op_rows) >= me.LADDER_MIN_QUARTERS:
            op_states = me.earnings_states(quarters, field="op_income")
            if op_states and op_states[-1]["announced"] >= cutoff \
                    and op_states[-1]["newhigh_streak"] == 1 \
                    and me.below_52wk_ma(prices, op_states[-1]["announced"]) is True:
                hits.append("H10")
        if hits:
            last = (me.earnings_states(quarters, field=yardstick or "op_income")
                    or [{}])[-1]
            rows.append({
                "종목": ticker,
                "섹터": cfg.SECTORS.get(ticker, "미분류"),
                "신호": " · ".join(hits),
                "발표일": last.get("announced", ""),
            })
    rows.sort(key=lambda r: r["발표일"], reverse=True)
    return rows


def main():
    import pandas as pd
    import streamlit as st
    import sector_model as sm

    st.set_page_config(page_title="상승 섹터 포착 계기판", layout="centered")
    st.title("상승 섹터 포착 계기판")

    verdict = load_json("verdict.json")
    log = load_json("robot_log.json")
    consensus = load_json("consensus.json")
    surprise = load_json("surprise.json")
    is_v3 = verdict_is_v3(verdict)
    snapshot = dataset.load()
    ds = dataset.build(snapshot)

    if log:
        st.caption(f"로봇 마지막 수집: {str(log.get('ran_at', '?'))[:16]} UTC · "
                   f"{log.get('summary', '')}")
    price_dates = ds["prices"][ds["benchmark"]]["dates"]
    st.caption(f"측정 기간: {price_dates[0]} ~ {price_dates[-1]} "
               f"(주가 {len(price_dates):,}거래일 · 약 5년) · "
               "전망: 다음 1분기 (가이던스·컨센서스)")

    # =====================================================================
    # 1. 메인 — 앞으로 상승할 섹터 후보 (정배열 폭 모델, 33·34차)
    # =====================================================================
    st.header("① 앞으로 상승할 섹터 후보")
    st.caption(
        "각 섹터에서 **주가가 완전 정배열**(주봉 종가 > 4주 > 13주 > 26주 > "
        "52주선)인 종목의 비율입니다. 옆 숫자는 **그 구간이 과거에 1년 뒤 "
        "시장을 20%p 이상 이긴 비율**(34차 탐색값)입니다."
    )

    breadth_rows = sm.current_breadth(ds)
    measured = [r for r in breadth_rows if r["폭"] is not None]
    if measured:
        st.altair_chart(
            sorted_bar_chart(
                [r["섹터"] for r in measured],
                [r["폭"] for r in measured],
                "정배열 폭 (%)",
                colors={r["섹터"]: ZONE_COLORS[breadth_zone(r["폭"])["zone"]]
                        for r in measured},
            ),
            use_container_width=True,
        )
        st.caption(
            "🟩 쌓이는 중(40~59%) — 과거 1년 폭등률 33.3%로 최고 · "
            "🟥 정배열 완성(60%+) — 7.7%로 최저 · 🟦 초기 · ⬜ 약함"
        )

    for row in breadth_rows:
        zone = breadth_zone(row["폭"])
        if row["폭"] is None:
            st.markdown(f"**{row['섹터']}** ({row['종목수']}종목) — 판단 불가 (이력 부족)")
            continue
        moved = ""
        if row["직전폭"] is not None:
            delta = row["폭"] - row["직전폭"]
            moved = f" · 지난주 대비 {delta:+.0f}%p"
        st.markdown(
            f"**{row['섹터']}** {row['폭']:.0f}% ({row['종목수']}종목){moved}  \n"
            f"{row['상태']} — {zone['zone']} · 이 구간의 과거 1년 폭등률 "
            f"**{zone['rate']}%** (기준선 {BREADTH_BASELINE}%)  \n"
            f"<span style='color:gray;font-size:0.85em'>{zone['note']}</span>",
            unsafe_allow_html=True,
        )

    st.warning(
        "**이 모델의 판정 상태 (정직화)**  \n"
        "· **H11(폭 60% 첫 돌파) — 미채택**: 실측 9.4% vs 기준선 26.8%. "
        "정배열이 다 찬 뒤 사는 것은 **오히려 불리**했습니다 (33차).  \n"
        "· **H12(폭 40~59% 진입) — 판정 대기**: 탐색에서 33.3%로 가장 좋았으나 "
        "탐색값은 근거가 못 되어, 2026-08-14 이후 새 신호로만 판정합니다 (34차).  \n"
        "· 따라서 위 순위는 **아직 매수 근거가 아닌 관찰**입니다."
    )

    # =====================================================================
    # 1-2. AI 사이클 추적 (37차) — 저장소 주인 관찰 국면의 실제 순서
    # =====================================================================
    st.header("① -2 사이클 추적 — 정배열·이익 델타·주가")
    st.caption(
        "AI 사이클 종목 묶음의 **정배열 폭**(주가가 정배열인 비율)과 "
        "**이익 델타 폭**(직전 분기보다 이익이 는 비율), 그리고 "
        "**상대수익**(2024년 말 기준 SPY 대비 누적)을 함께 봅니다. "
        "세 선의 **순서**가 이 모델의 핵심 질문입니다."
    )
    ai_members, non_ai = sm.ai_members(ds)
    ai_series = sm.cycle_series(ds, ai_members, "2024-12-31", since="2025-01-01")
    non_series = sm.cycle_series(ds, non_ai, "2024-12-31", since="2025-01-01")
    if ai_series:
        import altair as alt
        frames = []
        for label, series in (("AI 정배열 폭", ai_series), ("AI 이익 델타 폭", ai_series)):
            key = "정배열폭" if "정배열" in label else "델타폭"
            frames += [{"월": r["월"][:7], "선": label, "값": r[key]} for r in series]
        width_df = pd.DataFrame(frames)
        st.altair_chart(
            alt.Chart(width_df).mark_line(point=True).encode(
                x=alt.X("월:N", title=None, axis=alt.Axis(labelAngle=-60)),
                y=alt.Y("값:Q", title="폭 (%)"),
                color=alt.Color("선:N", title=None,
                                scale=alt.Scale(range=["#2E9E5B", "#E0A030"]),
                                legend=alt.Legend(orient="top")),
            ).properties(height=230),
            use_container_width=True)
        rel_df = pd.DataFrame(
            [{"월": r["월"][:7], "선": "AI 사이클", "값": r["상대수익"]}
             for r in ai_series if r["상대수익"] is not None]
            + [{"월": r["월"][:7], "선": "비AI", "값": r["상대수익"]}
               for r in non_series if r["상대수익"] is not None])
        st.altair_chart(
            alt.Chart(rel_df).mark_line(point=True).encode(
                x=alt.X("월:N", title=None, axis=alt.Axis(labelAngle=-60)),
                y=alt.Y("값:Q", title="SPY 대비 누적 (%p)"),
                color=alt.Color("선:N", title=None,
                                scale=alt.Scale(range=["#C4553B", "#8A8F98"]),
                                legend=alt.Legend(orient="top")),
            ).properties(height=230),
            use_container_width=True)
        low = min(ai_series, key=lambda r: r["정배열폭"])
        st.info(
            f"**실측 순서 (37차)**: AI 정배열 폭 최저는 **{low['월'][:7]} "
            f"{low['정배열폭']}%** — 이익 델타 폭도 이 무렵 바닥이었고, "
            "**주가가 먼저 돌아선 뒤** 정배열과 델타가 뒤따랐습니다. "
            "'정배열·델타가 먼저, 주가가 나중'이 아니라 **반대 순서**입니다."
        )

    # =====================================================================
    # 2. 메인 — 채택된 신호
    # =====================================================================
    st.header("② 채택된 신호")
    adopted = adopted_names(verdict) if is_v3 else []
    if not is_v3:
        st.warning("판정 파일이 v3 형식이 아닙니다 — 다음 로봇 수집 때 갱신됩니다.")
    elif adopted:
        st.success("채택: " + " · ".join(adopted))
        for name, entry in (verdict.get("가설") or {}).items():
            if entry.get("판정") != "채택":
                continue
            judged = entry.get("신규(판정)") or {}
            s, b = judged.get("신호") or {}, judged.get("기준선") or {}
            st.markdown(
                f"**{HYPOTHESIS_LABELS.get(name, name)}**  \n"
                f"이 신호가 켜진 발표는 60거래일 뒤 시장을 20%p 이상 이긴 비율이 "
                f"**{s.get('rate')}%** (n={s.get('n')}), 아무 발표나 샀을 때는 "
                f"{b.get('rate')}% 였습니다.  \n"
                f"앞시기 {entry.get('신규_앞시기', {}).get('rate')}% · "
                f"뒤시기 {entry.get('신규_뒤시기', {}).get('rate')}%"
            )
        live = live_signal_rows(ds)
        st.markdown("**지금 이 신호가 켜진 종목** (최근 90일 발표)")
        if not live:
            st.caption("현재 없음 — 새 실적 발표를 기다립니다.")
        else:
            st.dataframe(pd.DataFrame(live), width="stretch", hide_index=True)
    else:
        st.info("채택된 신호 없음 — 어떤 상태도 매수 판단의 근거가 아닙니다.")
    st.caption(
        "⚠️ 채택 표본에는 그 가설을 찾아낸 탐색 종목이 섞여 있습니다. "
        "완전한 독립 확인은 등록 이후 새 발표가 쌓여야 완성됩니다."
    )

    # =====================================================================
    # 3. 섹터 한눈에 보기 (차트)
    # =====================================================================
    st.header("③ 섹터 한눈에 보기")

    st.subheader("실적 신기록 폭")
    st.caption("최근 발표한 종목 중 이익 신기록이 나온 비율 (관찰).")
    gauge_rows = [r for r in sector_gauge_rows(ds) if r["게이지"] is not None]
    if gauge_rows:
        st.altair_chart(
            sorted_bar_chart([r["섹터"] for r in gauge_rows],
                             [r["게이지"] for r in gauge_rows], "신기록 폭 (%)"),
            use_container_width=True)

    st.subheader("실적 서프라이즈 (컨센서스 대비)")
    st.caption("최근 2분기 발표가 애널리스트 추정을 몇 % 넘겼는지 (중앙값, 관찰).")
    sur_rows = surprise_sector_rows(surprise)
    if sur_rows:
        st.altair_chart(
            sorted_bar_chart([r["섹터"] for r in sur_rows],
                             [r["중앙%"] for r in sur_rows],
                             "서프라이즈 중앙 (%)", positive_negative=True),
            use_container_width=True)
        st.caption("⚠️ 적자 근처 종목은 % 가 크게 튑니다 (분모가 0에 가까움).")
    else:
        st.caption("아직 서프라이즈 원장이 비어 있습니다.")

    st.subheader("전망 스프레드 (가이던스 − 컨센서스)")
    st.caption("회사가 시장 기대보다 높게 부를수록 큰 값 (다음 1분기, 관찰).")
    spread_rows = sector_spread_rows(ds, consensus)
    if spread_rows:
        st.altair_chart(
            sorted_bar_chart([r["섹터"] for r in spread_rows],
                             [r["스프레드중앙%"] for r in spread_rows],
                             "스프레드 중앙 (%)", positive_negative=True),
            use_container_width=True)
        for row in spread_rows:
            if row["표본"] == "표본 부족":
                st.caption(f"⚠️ {row['섹터']}: {row['종목수']}종목뿐 — 표본 부족")
    else:
        st.caption("신선한 가이던스와 컨센서스가 함께 있는 종목이 아직 없습니다.")

    # =====================================================================
    # 4. 접기 — 미채택·판정 대기 신호 상세
    # =====================================================================
    with st.expander("④ 미채택·판정 대기 신호 — 무엇을 재봤고 왜 안 쓰는가"):
        st.caption(
            "아래는 사전 등록해 측정했으나 **채택 기준(신호 구간이 기준선 "
            "구간과 완전히 갈라짐, n≥10)을 넘지 못한** 신호들입니다. "
            "판단·점수·추천에 쓰지 않되, 버리지 않고 로봇이 계속 재판정합니다."
        )
        for name, entry in (verdict.get("가설") or {}).items():
            if entry.get("판정") == "채택":
                continue
            judged = entry.get("신규(판정)") or {}
            s, b = judged.get("신호") or {}, judged.get("기준선") or {}
            label = HYPOTHESIS_LABELS.get(name, name)
            detail = HYPOTHESIS_DETAILS.get(name, "")
            rate_text = (
                f"실측 {s.get('rate')}% (n={s.get('n')}) vs 기준선 {b.get('rate')}%"
                if s.get("n") else "표본이 아직 없습니다"
            )
            st.markdown(
                f"**{label}** — {entry.get('판정', '?')}  \n"
                f"{detail}  \n"
                f"<span style='color:gray;font-size:0.9em'>{rate_text}</span>",
                unsafe_allow_html=True,
            )

    # =====================================================================
    # 5. 원자료·용어·한계
    # =====================================================================
    st.page_link("pages/1_원자료.py",
                 label="원자료 보기 — 종목별 최근 발표·전 종목 상태 →")

    with st.expander("용어 풀이"):
        st.markdown(
            "- **완전 정배열** — 주봉 종가가 4·13·26·52주 이동평균 위에 있고, "
            "그 이동평균들도 짧은 것부터 순서대로 위에 있는 상태\n"
            "- **정배열 폭** — 그 섹터 종목 중 완전 정배열인 비율\n"
            "- **TTM 조정 EPS** — 최근 4개 분기 조정 주당순이익 합 "
            "(회사가 보도자료에 직접 발표한 숫자만)\n"
            "- **첫 신기록** — TTM 이익이 과거 최고를 처음 넘은 발표\n"
            "- **52주선 아래** — 주가가 자기 1년 평균 아래 (아직 안 오른 상태)\n"
            "- **컨센서스** — 애널리스트들의 실적 추정 평균 (야후)\n"
            "- **가이던스** — 회사가 직접 발표한 다음 분기 전망\n"
            "- **서프라이즈** — 실제 실적이 컨센서스를 넘긴 정도\n"
            "- **폭등** — 60거래일(1년 모델은 250거래일) 수익이 SPY보다 +20%p 이상\n"
            "- **기준선** — 아무 때나 샀을 때의 같은 비율 (비교 대상)\n"
            "- **n** — 표본 수 · **윌슨 구간** — 적중률의 신뢰 범위 "
            "(표본이 적을수록 넓어져 과신을 막습니다)\n"
            "- **채택/미채택/판정 불가** — 신호 구간이 기준선 구간과 완전히 "
            "갈라지면 채택, 겹치면 미채택, 표본 10건 미만이면 판정 불가\n"
            "- **H번호** — 사전 등록된 가설의 일련번호 (측정결과.md 와 잇는 꼬리표)\n"
            "- **UTC** — 국제 표준시. 한국 시각보다 9시간 늦습니다"
        )

    with st.expander("한계 (감추지 않습니다)"):
        st.markdown(
            "- 채택되지 않은 신호는 판단·점수·추천에 쓰지 않습니다.\n"
            "- 정배열 폭 모델의 과거 실측(34차)은 **탐색값**이라 채택 근거가 "
            "아닙니다. H12 는 등록 이후 새 신호로만 판정합니다.\n"
            "- 컨센서스·서프라이즈는 야후 제공값입니다. 서프라이즈 소급분은 "
            "야후가 사후 보관한 기록이라 우리가 직접 박제한 원장과 구분해 둡니다.\n"
            "- 조정 EPS 를 발표하지 않는 종목은 EBITDA·GAAP EPS 잣대로 넘어가거나 "
            "'판단 불가'로 나옵니다 — 없는 값은 없음으로 둡니다.\n"
            "- 같은 시기의 사건들은 같은 장세를 공유하므로 통계 구간이 실제보다 "
            "좁게 나올 수 있습니다."
        )


if __name__ == "__main__":
    main()
