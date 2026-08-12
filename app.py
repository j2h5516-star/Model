"""
app.py — v2 계기판 (뷰어 전용)
==============================

전략.md v2 4장의 세 번째 몸입니다. 이 앱은 **읽기만** 합니다:

  수집 로봇(깃허브 액션)이 매일 data/measure/ 에 커밋한 스냅샷을 읽어
  장세 게이지와 종목 상태표를 보여줍니다. 조회 시점에 SEC·야후 접속이
  전혀 없어 빠르고, 로봇이 멈추면 데이터 나이로 그 사실이 드러납니다.

v1과 달라진 것 (측정 5회의 결론 — 측정결과.md):
  · 점수 없음 — "가속 확인 후 매수"는 3회 측정 모두 기준선 이하였습니다.
    여기 있는 모든 표시는 **상태 설명**이지 매수 신호가 아닙니다.
  · 저장 버튼 없음 — 로봇이 대신합니다.
  · 신뢰도 등급·전망 추정·컨센서스 없음 — 창작 금지 원칙.
"""

from __future__ import annotations

import json
import os
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import breadth
import config as cfg
import measurement as mm
import status

REQUIRED_CONFIG_VERSION = 27

st.set_page_config(page_title="실적 계기판", page_icon="📟", layout="centered")

if getattr(cfg, "CONFIG_VERSION", 0) < REQUIRED_CONFIG_VERSION:
    st.warning(
        "앱이 옛 설정을 붙들고 있습니다. 오른쪽 위 ⋮ → Reboot 을 눌러 주세요. "
        f"(현재 {getattr(cfg, 'CONFIG_VERSION', 0)} / 필요 {REQUIRED_CONFIG_VERSION})"
    )
    st.stop()


# ---------------------------------------------------------------------------
# 데이터 읽기 (로봇이 커밋한 파일 — 5분 캐시)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_snapshot():
    path = os.path.join(cfg.MEASURE_DIR, "snapshot.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=300, show_spinner=False)
def load_robot_log():
    path = os.path.join(cfg.MEASURE_DIR, "robot_log.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


st.markdown("### 📟 실적 계기판")
st.markdown(
    '<div style="color:#9aa0a6;font-size:0.9rem">조정 EPS 원문과 주가로 '
    "<b>상태만</b> 보여줍니다. 점수·매수 신호가 아닙니다 — 근거는 저장소의 "
    "측정결과.md 에 있습니다.</div>",
    unsafe_allow_html=True,
)

snapshot = load_snapshot()
if snapshot is None:
    st.info(
        "아직 데이터가 없습니다. 수집 로봇(깃허브 액션 `collect`)이 처음 돌고 나면 "
        "여기 계기판이 채워집니다 — 로봇은 매일 09:30 UTC 에 자동 실행됩니다."
    )
    st.stop()

saved_at = str(snapshot.get("saved_at", ""))[:16].replace("T", " ")
st.caption(f"🕐 데이터 수집 시각: **{saved_at} UTC** (로봇이 매일 자동 갱신)")


# ---------------------------------------------------------------------------
# ① 장세 폭 게이지 — 측정에서 유일하게 깨끗이 갈린 지표 (H5, 탐색치)
# ---------------------------------------------------------------------------
gauge = breadth.gauge_from_snapshot(snapshot)
if gauge["tickers"] >= 10:
    state_label = "🟢 사이클 ON" if gauge["on"] else "⚪ 사이클 OFF"
    st.info(
        f"**장세 게이지 — 실적 폭 {gauge['pct']:.0f}%** ({state_label}) · "
        f"1년치 이익 신기록을 새로 쓴 종목이 판정 가능 {gauge['tickers']}개 중 "
        f"{gauge['pct']:.0f}%"
    )
    st.caption(
        f"과거 이 상태에서 발표 뒤 60거래일간 시장 대비 +20%p 이상 오른 비율(탐색치): "
        f"ON {cfg.MEASURED_BREADTH_ON_SURGE_PCT}% / OFF {cfg.MEASURED_BREADTH_OFF_SURGE_PCT}% "
        f"(아무 발표나 {cfg.MEASURED_BASELINE_SURGE_PCT}%). ⚠️ 확정 검증 전 참고 지표."
    )
else:
    st.caption("장세 게이지: 판정 가능한 종목이 10개 미만이라 표시하지 않습니다.")


# ---------------------------------------------------------------------------
# ② 종목 상태표 — 사실만, 발표가 신선한 순서로
# ---------------------------------------------------------------------------
def build_status_rows(snap: dict) -> list[dict]:
    today = (snap["prices"].get(snap.get("benchmark", "SPY")) or {}).get("dates", [None])[-1]
    rows_out: list[dict] = []
    for ticker in snap["tickers"]:
        eps_rows = snap["eps"].get(ticker) or []
        prices = snap["prices"].get(ticker) or {}
        trend = (
            mm.trend_state(prices["dates"], prices["close"], today)
            if prices.get("dates") and today else None
        )

        # 잣대: 기본은 조정 EPS. 조정 EPS 를 발표하지 않는 회사(ZETA·APP 등)는
        # 회사가 발표한 조정 EBITDA(원문)로 대신 봅니다 — XBRL 근사치는 안 씀.
        metric_field, metric_label = "adj_eps", "조정EPS"
        runs, _breaks = mm.eps_runs(eps_rows)
        if not runs:
            runs, _breaks = mm.eps_runs(eps_rows, field="adjusted_ebitda")
            metric_field, metric_label = "adjusted_ebitda", "조정EBITDA"
        last_run = runs[-1] if runs else []
        if not last_run:
            rows_out.append({
                "종목": ticker, "델타 방향": "조정EPS·EBITDA 미발표", "신고점": "-",
                "주가 추세": trend or "-", "발표 후": "-", "최근 EPS": "-", "YoY%": "-",
                "_sort": 9_999,
            })
            continue

        scoring_rows = mm.to_scoring_rows(last_run, field=metric_field)
        judgment = status.judge_delta(scoring_rows)
        # 신고점은 끊긴 구간 이전의 정점까지 포함해 비교합니다 — 마지막 구간
        # 안에서만 보면 옛 정점보다 낮은데 '돌파'로 보입니다 (8차 감사).
        all_ttms = [
            t for r in runs
            for t in status.ttm_series(mm.to_scoring_rows(r, field=metric_field))
        ]
        last_ttms = status.ttm_series(scoring_rows)
        new_high = (
            all_ttms[-1] > max(all_ttms[:-1])
            if last_ttms and len(all_ttms) >= 2 else None
        )
        eps_values = [r[metric_field] for r in last_run]
        yoy = (
            round((eps_values[-1] - eps_values[-5]) / abs(eps_values[-5]) * 100.0, 1)
            if len(eps_values) >= 5 and eps_values[-5] else None
        )
        announce = next(
            (str(r["announced_date"])[:10] for r in reversed(last_run) if r.get("announced_date")),
            None,
        )
        weeks_ago = (
            (date.fromisoformat(today) - date.fromisoformat(announce)).days // 7
            if announce and today else None
        )
        rows_out.append({
            "종목": ticker,
            "델타 방향": judgment["direction"],
            "신고점": "🏔️ 돌파" if new_high else ("-" if new_high is None else "아님"),
            "주가 추세": trend or "-",
            "발표 후": f"{weeks_ago}주" if weeks_ago is not None else "-",
            # EBITDA 잣대 종목은 금액이 커서 백만 단위로 표시하고 잣대를 밝힙니다
            "최근 EPS": (
                "-" if eps_values[-1] is None
                else f"{eps_values[-1]/1e6:,.0f}M(EBITDA)" if metric_field == "adjusted_ebitda"
                else eps_values[-1]
            ),
            "YoY%": yoy if yoy is not None else "-",
            "_sort": weeks_ago if weeks_ago is not None else 9_998,
        })
    rows_out.sort(key=lambda r: (r["_sort"], r["종목"]))
    for row in rows_out:
        row.pop("_sort")
    return rows_out


st.markdown("#### 종목 상태표")
st.caption(
    "발표가 신선한 순서입니다 (순위가 아닙니다). "
    f"⚠️ '가속' 확인 후 매수는 3회 측정 모두 기준선 이하였습니다 "
    f"({cfg.MEASURED_ACCEL_SURGE_PCT}% vs {cfg.MEASURED_BASELINE_SURGE_PCT}%) — 상태 설명으로만 읽어 주세요."
)
status_rows = build_status_rows(snapshot)
st.dataframe(pd.DataFrame(status_rows), hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# ③ 종목 상세 — 분기 조정 EPS 막대 + 주가
# ---------------------------------------------------------------------------
st.markdown("#### 종목 상세")
picked = st.selectbox("종목을 고르세요", snapshot["tickers"])

picked_rows = sorted(
    (r for r in (snapshot["eps"].get(picked) or []) if r.get("filing_date")),
    key=lambda r: str(r["filing_date"]),
)
# 잣대: 조정 EPS → 없으면 회사 발표 조정 EBITDA (근사치는 안 씀 — 창작 금지)
metric_field, metric_title, metric_unit = "adj_eps", "분기 조정 EPS ($/주)", 1.0
runs, _ = mm.eps_runs(picked_rows)
if not runs:
    runs, _ = mm.eps_runs(picked_rows, field="adjusted_ebitda")
    metric_field, metric_title, metric_unit = (
        "adjusted_ebitda", "분기 조정 EBITDA ($M)", 1e6,
    )
if runs:
    # 전체 이력을 그립니다 — 마지막 연속 구간만 그리면 중간 분기 추출이
    # 실패한 종목(감사에서 23/80개)이 분기 1~2개짜리 차트가 됩니다.
    # 추출 실패 분기는 값 없이 비워 두어 구멍이 보이게 합니다 (창작 금지).
    labels = [r.get("period_label") or str(r["filing_date"])[:7] for r in picked_rows]
    values = [
        (r.get(metric_field) / metric_unit) if r.get(metric_field) is not None else None
        for r in picked_rows
    ]
    figure = go.Figure(go.Bar(x=labels, y=values, marker_color="#2e86ab", name="발표치"))

    # 회사가 준 다음 분기 가이던스(중간값) — 최신 발표분에 있을 때만 (EPS 잣대 전용)
    last_guided = None
    if metric_field == "adj_eps":
        last_guided = next(
            (r for r in reversed(picked_rows) if r.get("guid_eps_mid") is not None), None
        )
        if last_guided is not None and last_guided is not picked_rows[-1]:
            last_guided = None          # 오래된 가이던스는 그리지 않음
    if last_guided is not None:
        figure.add_bar(
            x=["다음분기 전망"], y=[last_guided["guid_eps_mid"]],
            marker_color="#f4a261", name="회사 가이던스",
        )
    figure.update_layout(
        height=260, margin=dict(l=10, r=10, t=30, b=10),
        title=f"{picked} — {metric_title}", yaxis_title=None, xaxis_title=None,
        showlegend=last_guided is not None,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
    )
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})

    if metric_field == "adjusted_ebitda":
        st.caption(
            "이 회사는 조정 EPS 를 발표하지 않아 **회사가 발표한 조정 EBITDA** "
            "로 이익의 방향을 봅니다 (근사치·추정 아님)."
        )
    missing = sum(1 for v in values if v is None)
    if missing:
        st.caption(
            f"빈 자리 {missing}개 = 원문에서 숫자 추출 실패 또는 미발표 분기 — "
            "지어내지 않고 비워 둡니다."
        )

    # 델타 판정은 등록된 규칙 그대로 '마지막 연속 구간'만 씁니다 (이어붙임 금지)
    last_run = runs[-1]
    judgment = status.judge_delta(mm.to_scoring_rows(last_run, field=metric_field))
    st.caption(f"델타 판정: **{judgment['direction']}** — {judgment['detail']}")
    if len(last_run) < 4 and sum(len(r) for r in runs) >= 4:
        st.caption(
            f"⚠️ 판정은 끊기지 않은 최근 {len(last_run)}개 분기만 씁니다 — 중간 분기 "
            "추출 실패로 구간이 끊겨 판정이 이릅니다. 이어붙이면 가짜 가속이 "
            "생기므로 붙이지 않습니다."
        )
else:
    st.caption(
        f"{picked}: 조정 EPS 도 조정 EBITDA 도 발표가 없는 회사입니다 "
        "(없음은 없음으로 둡니다 — 창작 금지)."
    )

picked_prices = snapshot["prices"].get(picked)
if picked_prices and picked_prices.get("dates"):
    price_fig = go.Figure(
        go.Scatter(x=picked_prices["dates"], y=picked_prices["close"],
                   mode="lines", line=dict(color="#e4572e", width=1.5))
    )
    price_fig.update_layout(
        height=220, margin=dict(l=10, r=10, t=30, b=10),
        title=f"{picked} — 종가 (수정주가)", yaxis_title=None, xaxis_title=None,
    )
    st.plotly_chart(price_fig, width="stretch", config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# ④ 측정 성적표 — 이 계기판이 정직한 이유
# ---------------------------------------------------------------------------
with st.expander("📊 측정 성적표 — 무엇이 기각됐고 무엇이 남았나"):
    st.markdown(
        f"""
발표 사건 380건 (29종목 · 약 5년 · 약세장 포함) 측정 결과입니다.
"폭등" = 발표 다음 거래일 진입 → 60거래일 → 시장(SPY) 대비 +20%p 이상.

| 발표 순간의 상태 | 이후 폭등률 | 판정 |
|---|---|---|
| 아무 발표나 (기준선) | {cfg.MEASURED_BASELINE_SURGE_PCT}% | — |
| '가속' 확인 후 | {cfg.MEASURED_ACCEL_SURGE_PCT}% | ❌ 기각 (3연속 기준선 이하) |
| TTM 신고점 돌파 후 | {cfg.MEASURED_NEWHIGH_SURGE_PCT}% | ⏳ 미채택 — 관찰 계속 |
| 실적 폭 게이지 ON | {cfg.MEASURED_BREADTH_ON_SURGE_PCT}% | ⏳ 탐색치 — 새 데이터로 판정 (H5) |
| 실적 폭 게이지 OFF | {cfg.MEASURED_BREADTH_OFF_SURGE_PCT}% | (위와 짝) |

전체 기록과 방법은 저장소의 **측정결과.md** · 원칙은 **전략.md** 에 있습니다.
        """
    )


# ---------------------------------------------------------------------------
# ④-b 자동 판정 — 로봇이 매일 가설을 재판정한 결과 (사람 개입 없음)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_verdict():
    path = os.path.join(cfg.MEASURE_DIR, "verdict.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


with st.expander("⚖️ 자동 판정 — 후보 신호들이 검증을 통과했나 (매일 갱신)"):
    st.caption(
        "판정 표본은 신호 발견에 쓰이지 않은 **신규 종목의 발표 사건**입니다 "
        "(측정결과.md 7차 사전 등록). 채택 = 신호 신뢰구간 하한이 기준선 상한을 "
        "넘을 때만. 같은 장세를 공유하는 한계가 있어 시간 방향 검증도 계속 쌓입니다."
    )
    verdict_data = load_verdict()
    if verdict_data is None:
        st.caption("아직 판정 기록이 없습니다 — 로봇의 다음 실행에서 생성됩니다.")
    elif verdict_data.get("error"):
        st.warning(f"최근 판정 실행이 실패했습니다: {verdict_data['error']}")
    else:
        rows = []
        for name, entry in verdict_data.get("가설", {}).items():
            judged = entry.get("신규(판정)") or {}
            signal = judged.get("신호") or {}
            base = judged.get("기준선") or {}
            rows.append({
                "가설": name.replace("_", " "),
                "판정": "✅ 채택" if entry.get("판정") == "채택" else "⏳ 미채택",
                "신호(%)": signal.get("rate"),
                "구간": str(signal.get("ci")),
                "기준선(%)": base.get("rate"),
                "n": signal.get("n"),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            f"판정 시각: {str(verdict_data.get('computed_at', ''))[:16].replace('T', ' ')} UTC · "
            f"표본 {verdict_data.get('표본', {})}"
        )


# ---------------------------------------------------------------------------
# ⑤ 수집 진단 — 로봇이 잘 돌고 있나
# ---------------------------------------------------------------------------
with st.expander("🔧 수집 진단 — 로봇이 잘 돌고 있나"):
    robot = load_robot_log()
    if robot is None:
        st.caption("로봇 실행 기록이 아직 없습니다.")
    else:
        st.caption(f"마지막 실행: {str(robot.get('ran_at', ''))[:16].replace('T', ' ')} UTC · {robot.get('summary', '')}")
        diagnostic = pd.DataFrame(
            [
                {
                    "종목": r.get("ticker"),
                    "조정EPS": r.get("adj_eps_ok", 0),
                    "직접공시": r.get("merged_direct", 0),
                    "소요(초)": r.get("seconds"),
                    "오류": (r.get("first_error") or "")[:60],
                }
                for r in robot.get("per_ticker", [])
            ]
        )
        st.dataframe(diagnostic, hide_index=True, width="stretch")

st.caption(
    "📟 v2 계기판 — 수집은 깃허브 로봇이, 측정은 개발 환경이, 이 화면은 보여주기만 합니다. "
    "v1 전체는 v1-final 브랜치에 봉인되어 있습니다."
)
