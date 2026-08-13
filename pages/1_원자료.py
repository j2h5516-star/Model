"""
pages/1_원자료.py — 원자료 화면 (메인 계기판에서 클릭해 들어오는 페이지)
========================================================================

저장소 주인 요청(2026-08-13): 종목별 발표 목록은 메인 화면이 아니라
별도 페이지에서 본다. 여기는 **사실(원자료)만** 보여 주는 곳입니다 —
판단·점수·추천 없음 (설계도.md ④ 경계는 메인과 동일).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st

import app
import dataset
import measure_engine as me

st.set_page_config(page_title="원자료", layout="centered")
st.title("원자료 — 종목별 발표")
st.caption(
    "수집된 사실을 그대로 보여 주는 화면입니다. 여기 있는 어떤 상태도 "
    "그 자체로 매수 판단의 근거가 아닙니다 — 판정 현황은 메인 화면에 있습니다."
)

try:
    ds = dataset.build(dataset.load())
except (FileNotFoundError, ValueError) as exc:
    st.error(f"데이터를 읽지 못했습니다: {exc}")
    st.stop()

verdict = app.load_json("verdict.json")
is_v3 = app.verdict_is_v3(verdict)

# --- 최근 발표 (메인에서 옮겨 온 목록) ---
st.subheader(f"최근 {app.RECENT_DAYS}일 발표")
recent = app.recent_ticker_rows(ds)
if not recent:
    st.write("최근 발표 없음")
else:
    for row in recent:
        ttm = row["TTM 조정EPS"]
        yard = "" if row["잣대"] == "조정 EPS" else f" · 잣대: {row['잣대']}"
        st.markdown(
            f"**{row['종목']}** ({row['섹터']}) · {row['발표일']}{yard}  \n"
            f"{row['상태']}"
            + (f" · 최근 4분기 ${ttm:,}" if ttm is not None else "")
        )
st.caption("· 첫 신기록(H2b) — " + app.hypothesis_note(
    verdict if is_v3 else None, "H2b_신고점_첫돌파"))

# --- 전 종목 현재 상태 ---
st.subheader("전 종목 현재 상태")
st.caption("각 종목의 마지막 발표 기준. 잣대는 12차 등록의 사다리로 자동 배정됩니다.")

yard_names = {"adj_eps": "조정 EPS", "adjusted_ebitda": "조정 EBITDA",
              "gaap_eps": "GAAP EPS"}
table_rows = []
for ticker in sorted(ds["tickers"]):
    quarters = ds["quarters"].get(ticker) or []
    yardstick = me.yardstick_of(quarters)
    if yardstick is None:
        table_rows.append({"종목": ticker,
                           "섹터": app.cfg.SECTORS.get(ticker, "미분류"),
                           "잣대": "측정 제외 (이력 부족)",
                           "마지막 발표": "—", "상태": "—"})
        continue
    states = me.earnings_states(quarters, field=yardstick)
    if not states:
        table_rows.append({"종목": ticker,
                           "섹터": app.cfg.SECTORS.get(ticker, "미분류"),
                           "잣대": yard_names[yardstick],
                           "마지막 발표": "—", "상태": "발표일 없음"})
        continue
    last = states[-1]
    if not last["decidable"]:
        status = "판단 불가 (이력 부족)"
    elif last["newhigh_streak"] == 1:
        status = "신고점 첫 돌파"
    elif last["new_high"]:
        status = f"신고점 (연속 {last['newhigh_streak']}번째)"
    else:
        status = "신고점 아님"
    table_rows.append({"종목": ticker,
                       "섹터": app.cfg.SECTORS.get(ticker, "미분류"),
                       "잣대": yard_names[yardstick],
                       "마지막 발표": last["announced"], "상태": status})

# 섹터별로 묶어 표시 (모바일 폭에서 표 대신 줄글 — 열 가림 방지)
by_sector: dict = {}
for row in table_rows:
    by_sector.setdefault(row["섹터"], []).append(row)
for sector in sorted(by_sector):
    with st.expander(f"{sector} ({len(by_sector[sector])}종목)"):
        for row in by_sector[sector]:
            yard = "" if row["잣대"] == "조정 EPS" else f" · {row['잣대']}"
            st.markdown(
                f"**{row['종목']}**{yard} · {row['마지막 발표']}  \n{row['상태']}"
            )

st.caption(
    "· 없는 값은 없음으로 둡니다. '판단 불가'는 최근 연속 분기가 4개를 "
    "못 채워 TTM(최근 4분기 합)을 계산할 수 없는 상태입니다 — 분기가 "
    "쌓이면 자동 복구됩니다."
)
