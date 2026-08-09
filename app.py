"""
app.py — 추세추종 대시보드 v2 (Streamlit)
=========================================

실행 방법:
    streamlit run app.py

화면 구성:
  ① 종목 카드 그리드   — 최종점수와 판정을 한눈에
  ② 순위 표            — 점수·추세·마진 성격·상대강도 비교
  ③ 4사분면 매트릭스   — 펀더멘털 vs 기술 위치
  ④ 종목 상세          — 분기 이익 막대 + 증가율 선, 마진 추이
  ⑤ 지표 해석 가이드   — 비전문가용 용어 설명

계산 로직은 다른 파일에 나뉘어 있습니다:
  market_data.py(주가) · sec_fundamentals.py(실적) ·
  forward_estimates.py(전망) · scoring.py(점수) · pipeline.py(전체 실행)
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import config as cfg
import pipeline

# ---------------------------------------------------------------------------
# 페이지 기본 설정
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="추세추종 대시보드 v2",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",  # 모바일에서 화면을 넓게 쓰기 위해
)

# ---------------------------------------------------------------------------
# 커스텀 CSS — 다크 테마 + 모바일 가독성
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
  /* 모바일에서 좌우 여백을 줄여 화면을 넓게 씁니다 */
  .block-container { padding-top: 1.2rem; padding-bottom: 2rem;
                     padding-left: 1rem; padding-right: 1rem; max-width: 1400px; }

  /* 종목 카드 그리드 — 화면 폭에 맞춰 자동으로 줄바꿈됩니다 */
  .card-grid { display: grid; gap: 10px;
               grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
               margin: 10px 0 18px 0; }
  .card { background: #1a1f2b; border: 1px solid #2b3241; border-radius: 12px;
          padding: 12px 12px 10px 12px; border-left-width: 4px; }
  .card-top { display: flex; justify-content: space-between; align-items: baseline; }
  .card-ticker { font-size: 1.02rem; font-weight: 700; color: #f3f4f6; letter-spacing: .3px; }
  .card-score { font-size: 1.9rem; font-weight: 800; line-height: 1.1; margin: 2px 0 6px 0; }
  .card-sub { font-size: .74rem; color: #9ca3af; margin-top: 2px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px;
           font-size: .68rem; font-weight: 700; margin: 3px 3px 0 0; white-space: nowrap; }
  .badge-src { background: #263042; color: #93c5fd; border: 1px solid #33415a; }

  /* 지표 설명 툴팁 (ⓘ 아이콘에 마우스를 올리거나 탭하면 설명이 뜹니다) */
  .tip { border-bottom: 1px dotted #6b7280; cursor: help; }

  /* 표 글자 크기 (모바일 대응) */
  [data-testid="stDataFrame"] { font-size: 0.86rem; }

  /* 섹션 제목 */
  h2, h3 { color: #f3f4f6 !important; }
  .section-note { color: #9ca3af; font-size: .82rem; margin: -6px 0 10px 0; }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 지표 설명 (비전문가용) — 화면 곳곳의 ⓘ 툴팁과 하단 가이드에서 함께 씁니다
# ---------------------------------------------------------------------------
GLOSSARY = {
    "최종점수": "펀더멘털 점수(실적) 60% + 기술 점수(주가) 40%를 합친 종합 점수입니다. 100점 만점.",
    "펀더": "실적이 얼마나 좋아지고 있는지를 100점 만점으로 나타낸 점수입니다.",
    "기술": "주가 흐름이 얼마나 좋은지를 100점 만점으로 나타낸 점수입니다.",
    "논갭 영업이익": (
        "회계 규정(GAAP)대로 계산한 이익에서 주식보상비처럼 실제 현금이 나가지 않는 "
        "항목을 빼고 다시 계산한 이익입니다. 회사의 진짜 영업 체력을 보는 데 씁니다."
    ),
    "델타 가속": (
        "이익이 '얼마나 늘었나'가 아니라 '늘어나는 속도가 빨라지고 있나'를 봅니다. "
        "가속 ↗ 이면 성장에 탄력이 붙는 중, 감속 ↘ 이면 힘이 빠지는 중입니다."
    ),
    "GM% 드라이버": (
        "매출총이익률(마진)이 왜 변했는지 성격을 구분한 것입니다. "
        "물량형=제품이 잘 팔려 규모가 커진 것(가장 건강), "
        "믹스형=비싼 제품 비중이 늘어난 것, "
        "가격형=가격을 올려서 생긴 것(오래 못 갈 수 있음)."
    ),
    "추세 5단계": (
        "주봉 이동평균선의 배열 순서로 주가 흐름을 5단계로 나눈 것입니다. "
        "완전 정배열이 가장 강하고, 완전 역배열이 가장 약합니다."
    ),
    "26주선 기울기": "약 6개월 평균 주가선이 올라가는 중인지 내려가는 중인지 나타냅니다.",
    "이격도": (
        "현재 주가가 26주 평균선보다 몇 % 위/아래에 있는지입니다. "
        "너무 높으면(+40% 초과) 단기 과열일 수 있습니다."
    ),
    "RS": (
        "상대강도. 최근 13주 동안 이 종목이 시장 전체(SPY, S&P500 ETF)보다 "
        "몇 %p 더 올랐는지입니다. 양수면 시장을 이긴 것입니다."
    ),
    "포워드": (
        "다음 분기 예상 실적입니다. 회사가 직접 밝힌 전망이 있으면 '가이던스', "
        "없으면 애널리스트 매출 전망에 과거 평균 마진을 곱한 '추정'입니다."
    ),
    "판정": (
        "매수 후보=실적·주가 모두 좋음 / ⚠️경고=실적은 좋은데 주가가 꺾임(정점 가능성) / "
        "관찰=주가는 좋으나 실적 확신 부족 / 펀더 없는 모멘텀=실적 근거 없이 주가만 오름 / "
        "제외=조건 미달"
    ),
}


def tip(label: str, key: str | None = None) -> str:
    """지표 이름 옆에 ⓘ 아이콘을 붙여 설명이 뜨게 만듭니다."""
    text = GLOSSARY.get(key or label, "")
    if not text:
        return label
    safe = text.replace('"', "&quot;")
    return f'<span class="tip" title="{safe}">{label} ⓘ</span>'


# ---------------------------------------------------------------------------
# 데이터 실행 (캐시: 같은 조건이면 1시간 동안 재사용)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_all(include_current_week: bool, use_cache: bool):
    return pipeline.run_pipeline(
        include_current_week=include_current_week, use_cache=use_cache
    )


# ---------------------------------------------------------------------------
# 사이드바
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 설정")

    include_current = st.checkbox(
        "진행 중인 이번 주 포함",
        value=True,
        help=(
            "체크하면 아직 끝나지 않은 이번 주 주가까지 반영합니다. "
            "체크를 풀면 완성된 지난주 금요일 종가 기준으로 판정합니다."
        ),
    )

    if st.button("🔄 데이터 새로고침", width="stretch"):
        load_all.clear()
        st.rerun()

    st.caption(
        "새로고침을 누르면 저장된 데이터를 지우고 SEC·야후에서 다시 받아옵니다. "
        "종목이 많아 1~3분 걸릴 수 있습니다."
    )

    st.divider()
    st.caption(
        f"**대상 종목** {len(cfg.TICKERS)}개\n\n"
        f"{', '.join(cfg.TICKERS)}\n\n"
        f"**비교 지수** {cfg.BENCHMARK}"
    )


# ---------------------------------------------------------------------------
# 헤더
# ---------------------------------------------------------------------------
st.title("📊 추세추종 대시보드 v2")
st.markdown(
    '<div class="section-note">실적(SEC 8-K 논갭 영업이익) + 주가 추세를 합쳐 '
    "종목별 종합 점수를 매깁니다. 모든 데이터는 무료 공개 자료에서 자동으로 수집됩니다.</div>",
    unsafe_allow_html=True,
)

with st.spinner("SEC 공시와 주가 데이터를 모으는 중입니다... (첫 실행은 1~3분 걸릴 수 있습니다)"):
    try:
        result = load_all(include_current, True)
    except Exception as exc:  # 파이프라인 전체가 실패한 경우
        st.error(
            "데이터를 불러오지 못했습니다.\n\n"
            "- 인터넷 연결을 확인해 주세요\n"
            "- 잠시 후 왼쪽 사이드바의 **🔄 데이터 새로고침**을 눌러 다시 시도해 주세요\n\n"
            f"기술적 원인: `{type(exc).__name__}: {exc}`"
        )
        st.stop()

scores = result["scores"]
ranking = result["ranking"]

if not scores:
    st.error(
        "분석할 수 있는 종목이 없습니다. 주가 데이터를 받지 못했을 가능성이 큽니다.\n\n"
        "왼쪽 사이드바의 **🔄 데이터 새로고침**을 눌러 다시 시도해 주세요."
    )
    st.stop()

# 업데이트 시각 + 데이터 수집 실패 안내
st.caption(f"🕐 마지막 업데이트: **{result['updated_at'].strftime('%Y-%m-%d %H:%M')}**")

if result["failed"]:
    st.warning(f"주가 데이터를 받지 못한 종목: {', '.join(result['failed'])}")
if result["no_fundamentals"]:
    st.info(
        f"실적 데이터를 찾지 못해 주가 지표만 반영된 종목: {', '.join(result['no_fundamentals'])} "
        "(펀더멘털 점수는 기본값으로 계산됩니다)"
    )


# ---------------------------------------------------------------------------
# ① 종목 카드 그리드
# ---------------------------------------------------------------------------
st.subheader("종목별 종합 점수")

# 순위 표와 같은 순서로 카드를 배치합니다
ordered = [scores[t] for t in ranking["종목"]] if not ranking.empty else list(scores.values())

cards_html = ['<div class="card-grid">']
for score in ordered:
    color = cfg.VERDICT_COLORS.get(score["verdict"], "#6b7280")

    badges = [
        f'<span class="badge" style="background:{color}22;color:{color};'
        f'border:1px solid {color}55;">{score["verdict"]}</span>'
    ]
    if score["data_source"]:
        badges.append(f'<span class="badge badge-src">{score["data_source"]}</span>')
    if score["forward_basis"]:
        badges.append(f'<span class="badge badge-src">{score["forward_basis"]}</span>')

    rs_text = f"RS {score['rs']:+.0f}" if score["rs"] is not None else "RS -"
    cards_html.append(
        f"""<div class="card" style="border-left-color:{color};">
  <div class="card-top">
    <span class="card-ticker">{score['ticker']}</span>
    <span class="card-sub">{rs_text}</span>
  </div>
  <div class="card-score" style="color:{color};">{score['final_score']:.0f}</div>
  <div>{''.join(badges)}</div>
  <div class="card-sub">펀더 {score['fund_score']:.0f} · 기술 {score['tech_score']:.0f}</div>
  <div class="card-sub">{score['trend_state']}</div>
</div>"""
    )
cards_html.append("</div>")
st.markdown("".join(cards_html), unsafe_allow_html=True)

st.markdown(
    f'<div class="section-note">배지 설명: 판정({tip("무엇인지", "판정")}) · '
    "데이터 출처(직접공시=보도자료에 그대로 있음, 역산=매출×마진으로 계산, "
    "근사치=XBRL 회계데이터로 추정) · 전망 근거(가이던스=회사 발표, 추정=애널리스트 전망 기반)</div>",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# ② 순위 표
# ---------------------------------------------------------------------------
st.subheader("순위 표")
st.markdown(
    f'<div class="section-note">{tip("최종점수")} = {tip("펀더")} × 0.6 + {tip("기술")} × 0.4 · '
    f'매수 후보는 {tip("RS")} 높은 순으로 위에 표시됩니다</div>',
    unsafe_allow_html=True,
)


def _style_verdict(value):
    """판정 칸에 색을 입힙니다."""
    color = cfg.VERDICT_COLORS.get(value)
    return f"background-color:{color}22; color:{color}; font-weight:700" if color else ""


def _style_score(value):
    """점수가 높을수록 초록, 낮을수록 회색으로 표시합니다."""
    if pd.isna(value):
        return ""
    if value >= 70:
        return "color:#22c55e; font-weight:700"
    if value >= 50:
        return "color:#a3e635"
    if value >= 40:
        return "color:#e5e7eb"
    return "color:#9ca3af"


def _style_rs(value):
    """상대강도: 양수는 초록, 음수는 빨강."""
    if pd.isna(value):
        return ""
    return "color:#22c55e" if value > 0 else "color:#ef4444"


styled = (
    ranking.style.map(_style_verdict, subset=["판정"])
    .map(_style_score, subset=["최종점수", "펀더", "기술"])
    .map(_style_rs, subset=["RS"])
    .format({"최종점수": "{:.0f}", "펀더": "{:.0f}", "기술": "{:.0f}", "RS": "{:+.1f}%p"}, na_rep="-")
)
st.dataframe(
    styled,
    width="stretch",
    hide_index=True,
    height=min(36 * (len(ranking) + 1) + 8, 560),
)
st.caption("📱 모바일에서는 표를 좌우로 밀어서 나머지 열을 볼 수 있습니다.")


# ---------------------------------------------------------------------------
# ③ 4사분면 매트릭스
# ---------------------------------------------------------------------------
st.subheader("펀더멘털 × 기술 매트릭스")
st.markdown(
    '<div class="section-note">오른쪽 위로 갈수록 실적과 주가가 모두 좋은 종목입니다. '
    "왼쪽 위(주황)는 실적은 좋은데 주가가 꺾인 구간으로, 정점을 지났을 가능성을 살펴야 합니다.</div>",
    unsafe_allow_html=True,
)

matrix = go.Figure()

# 사분면 배경색 (x=기술점수 50 기준, y=펀더 70 기준)
quadrants = [
    # (x0, x1, y0, y1, 색, 라벨, 라벨 x, 라벨 y)
    (50, 100, cfg.FUND_STRONG, 100, "#22c55e", "매수 후보권", 75, 88),
    (0, 50, cfg.FUND_STRONG, 100, "#f59e0b", "⚠️ 실적 좋음 · 추세 꺾임", 25, 88),
    (50, 100, 0, cfg.FUND_STRONG, "#3b82f6", "관찰 / 모멘텀", 75, 30),
    (0, 50, 0, cfg.FUND_STRONG, "#6b7280", "제외권", 25, 30),
]
for x0, x1, y0, y1, color, label, lx, ly in quadrants:
    matrix.add_shape(
        type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
        fillcolor=color, opacity=0.09, line_width=0, layer="below",
    )
    matrix.add_annotation(
        x=lx, y=ly, text=label, showarrow=False,
        font=dict(color=color, size=11), opacity=0.85,
    )

# 종목 점 찍기
matrix.add_trace(
    go.Scatter(
        x=[s["tech_score"] for s in scores.values()],
        y=[s["fund_score"] for s in scores.values()],
        mode="markers+text",
        text=[s["ticker"] for s in scores.values()],
        textposition="top center",
        textfont=dict(size=10, color="#e5e7eb"),
        marker=dict(
            size=14,
            color=[cfg.VERDICT_COLORS.get(s["verdict"], "#6b7280") for s in scores.values()],
            line=dict(width=1, color="#0e1117"),
        ),
        customdata=[[s["verdict"], s["final_score"]] for s in scores.values()],
        hovertemplate=(
            "<b>%{text}</b><br>기술 %{x:.0f} · 펀더 %{y:.0f}"
            "<br>최종 %{customdata[1]:.0f} · %{customdata[0]}<extra></extra>"
        ),
        showlegend=False,
    )
)
matrix.update_layout(
    template="plotly_dark",
    height=460,
    margin=dict(l=45, r=25, t=20, b=45),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    xaxis=dict(title="기술 점수 (주가 흐름) →", range=[0, 100], gridcolor="#2b3241"),
    yaxis=dict(title="펀더멘털 점수 (실적) →", range=[0, 100], gridcolor="#2b3241"),
)
st.plotly_chart(matrix, width="stretch")


# ---------------------------------------------------------------------------
# ④ 종목 상세
# ---------------------------------------------------------------------------
st.subheader("종목 상세")

ticker_list = ranking["종목"].tolist() if not ranking.empty else list(scores.keys())
selected = st.selectbox(
    "종목을 선택하세요",
    options=ticker_list,
    format_func=lambda t: f"{t} — {scores[t]['verdict']} (최종 {scores[t]['final_score']:.0f}점)",
)

detail = scores[selected]

# --- 핵심 지표 카드 ---
c1, c2, c3, c4 = st.columns(4)
c1.metric("최종점수", f"{detail['final_score']:.0f}점", detail["verdict"])
c2.metric("펀더멘털", f"{detail['fund_score']:.0f}점", detail["delta_direction"])
c3.metric("기술", f"{detail['tech_score']:.0f}점", detail["trend_state"])
c4.metric(
    "상대강도(RS)",
    "-" if detail["rs"] is None else f"{detail['rs']:+.1f}%p",
    "시장 대비 13주",
    delta_color="off",
)

# --- 점수 상세 설명 ---
fundamental = detail["fundamental"]
technical = detail["technical"]

with st.expander("🔍 이 점수가 나온 이유 (자세히 보기)", expanded=False):
    st.markdown(f"**펀더멘털 {detail['fund_score']:.0f}점**")
    st.markdown(
        f"- **델타 가속** {fundamental['delta']['score']:.0f}/{cfg.W_DELTA_ACCEL}점 — "
        f"{fundamental['delta']['detail']}\n"
        f"- **GM% 드라이버** {fundamental['gm']['score']:.0f}/{cfg.W_GM_DRIVER}점 — "
        f"{fundamental['gm']['detail']}\n"
        f"- **매출 성장의 질** {fundamental['revenue']['score']:.0f}/{cfg.W_REVENUE_QUALITY}점 — "
        f"{fundamental['revenue']['detail']}\n"
        f"- **포워드 신호** {fundamental['forward']['score']:.0f}/{cfg.W_FORWARD}점 — "
        f"{fundamental['forward']['detail']}"
    )
    st.markdown(f"**기술 {detail['tech_score']:.0f}점**")
    st.markdown(
        f"- **추세** {technical['trend_score']:.0f}/{cfg.W_TREND}점 — {detail['trend_state']}\n"
        f"- **26주선 기울기** {technical['slope_score']:.0f}/{cfg.W_SLOPE}점 — {detail['slope']}\n"
        f"- **이격도** {technical['disparity_score']:.0f}/{cfg.W_DISPARITY}점 — "
        f"{technical['disparity_text']}\n"
        f"- **상대강도** {technical['rs_score']:.0f}/{cfg.W_RS}점 — {technical['rs_text']}"
    )
    if detail["forward_detail"]:
        st.caption(f"전망 산출 근거: {detail['forward_detail']}")

# --- 분기별 실적 차트 ---
quarters_df = pipeline.quarters_to_frame(detail["quarters"])

if quarters_df.empty:
    st.info(
        f"{selected}의 분기 실적 데이터를 SEC에서 찾지 못했습니다. "
        "주가 지표(기술 점수)만 반영된 상태입니다."
    )
else:
    labels = quarters_df["period_label"].tolist()
    op_values = quarters_df["op_income"].tolist()

    # 포워드(다음 분기 전망)를 마지막에 점선/반투명으로 덧붙입니다
    forward_op = detail["forward_op_income"]
    if forward_op is not None:
        labels = labels + ["다음 분기(전망)"]
        forward_bar = [None] * len(op_values) + [forward_op]
        op_bar = op_values + [None]
    else:
        forward_bar = None
        op_bar = op_values

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # 논갭 영업이익 막대 (단위: 백만 달러)
    fig.add_trace(
        go.Bar(
            x=labels,
            y=[v / 1e6 if v is not None else None for v in op_bar],
            name="논갭 영업이익",
            marker_color="#22c55e",
            opacity=0.85,
            hovertemplate="%{x}<br>영업이익 $%{y:,.0f}M<extra></extra>",
        ),
        secondary_y=False,
    )

    # 전망치 막대 — 빗금 무늬 + 반투명으로 "아직 발표 전 추정치"임을 구분합니다
    if forward_bar is not None:
        fig.add_trace(
            go.Bar(
                x=labels,
                y=[v / 1e6 if v is not None else None for v in forward_bar],
                name=f"다음 분기 전망({detail['forward_basis'] or '추정'})",
                marker=dict(
                    color="rgba(34,197,94,0.25)",
                    line=dict(color="#22c55e", width=2),
                    pattern=dict(shape="/", fgcolor="#22c55e", size=6, solidity=0.25),
                ),
                hovertemplate="%{x}<br>전망 $%{y:,.0f}M (추정치)<extra></extra>",
            ),
            secondary_y=False,
        )

    # QoQ 증가율 선 — 가속/감속이 한눈에 보이도록
    if "qoq_pct" in quarters_df.columns:
        qoq_values = quarters_df["qoq_pct"].tolist()
        if forward_bar is not None:
            qoq_values = qoq_values + [None]
        fig.add_trace(
            go.Scatter(
                x=labels,
                y=qoq_values,
                name="전분기 대비 증가율(QoQ)",
                mode="lines+markers",
                line=dict(color="#f59e0b", width=2.5),
                marker=dict(size=7),
                hovertemplate="%{x}<br>QoQ %{y:+.1f}%<extra></extra>",
            ),
            secondary_y=True,
        )

    fig.update_layout(
        template="plotly_dark",
        height=420,
        title=f"{selected} — 분기별 논갭 영업이익과 증가 속도",
        margin=dict(l=45, r=45, t=60, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        hovermode="x unified",
        barmode="overlay",
    )
    fig.update_yaxes(title_text="논갭 영업이익 (백만 달러)", secondary_y=False, gridcolor="#2b3241")
    fig.update_yaxes(title_text="QoQ 증가율 (%)", secondary_y=True, showgrid=False)
    st.plotly_chart(fig, width="stretch")

    st.markdown(
        '<div class="section-note">초록 막대가 이익 크기, 주황 선이 '
        "'전분기 대비 얼마나 빨리 늘고 있는가'입니다. "
        "주황 선이 우상향이면 가속(성장에 탄력), 우하향이면 감속입니다. "
        "빗금 무늬 막대는 아직 발표되지 않은 <b>추정치</b>입니다.</div>",
        unsafe_allow_html=True,
    )

    # --- 매출총이익률(GM%) 추이 ---
    gm_series = quarters_df.dropna(subset=["gross_margin_pct"])
    if len(gm_series) >= 2:
        gm_fig = go.Figure()
        gm_fig.add_trace(
            go.Scatter(
                x=gm_series["period_label"],
                y=gm_series["gross_margin_pct"],
                mode="lines+markers",
                name="매출총이익률",
                line=dict(color="#60a5fa", width=2.5),
                marker=dict(size=8),
                hovertemplate="%{x}<br>GM %{y:.1f}%<extra></extra>",
            )
        )
        gm_fig.update_layout(
            template="plotly_dark",
            height=300,
            title=f"{selected} — 매출총이익률(GM%) 추이 · 현재 성격: {detail['gm_type']}",
            margin=dict(l=45, r=25, t=55, b=40),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
        )
        gm_fig.update_yaxes(title_text="GM (%)", gridcolor="#2b3241")
        st.plotly_chart(gm_fig, width="stretch")

    # --- 원본 숫자 표 ---
    with st.expander("📋 분기별 원본 숫자 보기"):
        display = quarters_df.copy()
        display["매출($M)"] = display["revenue"].apply(
            lambda v: f"{v/1e6:,.0f}" if pd.notna(v) else "-"
        )
        display["논갭 영업이익($M)"] = display["op_income"].apply(
            lambda v: f"{v/1e6:,.0f}" if pd.notna(v) else "-"
        )
        display["GM(%)"] = display["gross_margin_pct"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else "-"
        )
        display["QoQ(%)"] = display["qoq_pct"].apply(
            lambda v: f"{v:+.1f}" if pd.notna(v) else "-"
        )
        display = display.rename(
            columns={"period_label": "분기", "filing_date": "발표일(8-K)", "source": "출처"}
        )
        st.dataframe(
            display[["분기", "발표일(8-K)", "매출($M)", "논갭 영업이익($M)", "GM(%)", "QoQ(%)", "출처"]],
            width="stretch",
            hide_index=True,
        )


# ---------------------------------------------------------------------------
# ⑤ 지표 해석 가이드
# ---------------------------------------------------------------------------
st.divider()
with st.expander("📖 지표 해석 가이드 (처음 보신다면 여기부터)", expanded=False):
    st.markdown("### 이 대시보드는 무엇을 보나요?")
    st.markdown(
        "회사의 **실적이 좋아지는 속도**와 **주가 흐름**을 각각 점수로 매기고, "
        "둘을 합쳐 종목을 비교합니다. 실적만 좋아도, 주가만 좋아도 부족하다는 관점입니다."
    )

    st.markdown("### 용어 설명")
    for term, description in GLOSSARY.items():
        st.markdown(f"- **{term}** — {description}")

    st.markdown("### 점수는 이렇게 계산됩니다")
    st.markdown(
        f"""
| 구분 | 항목 | 배점 | 무엇을 보나 |
|---|---|---|---|
| 펀더멘털 | 델타 가속 | {cfg.W_DELTA_ACCEL} | 이익 증가 **속도**가 빨라지는가 |
| 펀더멘털 | GM% 드라이버 | {cfg.W_GM_DRIVER} | 마진 개선이 물량형인가 가격형인가 |
| 펀더멘털 | 매출 성장의 질 | {cfg.W_REVENUE_QUALITY} | 매출이 크게, 그리고 가속하며 크는가 |
| 펀더멘털 | 포워드 신호 | {cfg.W_FORWARD} | 다음 분기 전망이 더 좋은가 |
| 기술 | 추세 5단계 | {cfg.W_TREND} | 이동평균선 배열 상태 |
| 기술 | 26주선 기울기 | {cfg.W_SLOPE} | 중기 추세 방향 |
| 기술 | 이격도 | {cfg.W_DISPARITY} | 과열 여부 |
| 기술 | 상대강도(RS) | {cfg.W_RS} | 시장보다 잘 갔는가 |

**최종점수 = 펀더멘털 × 0.6 + 기술 × 0.4**

> 💡 **저기저 함정 방지**: 최근 분기 논갭 영업이익이 $50M 미만이면
> 델타 가속 점수를 절반까지만 인정합니다. 기저(비교 대상)가 작으면
> 증가율이 실제보다 커 보이는 착시가 생기기 때문입니다.
        """
    )

    st.markdown("### 판정 5종")
    st.markdown(
        f"""
| 판정 | 조건 | 뜻 |
|---|---|---|
| 🟢 {cfg.V_BUY} | 펀더 {cfg.FUND_STRONG:.0f}+ & 추세 1~2단계 | 실적·주가 모두 양호 (RS 높은 순 정렬) |
| 🟧 {cfg.V_WARN} | 펀더 {cfg.FUND_STRONG:.0f}+ & 추세 4~5단계 | 실적은 좋은데 주가가 꺾임 — 정점 가능성 |
| 🔵 {cfg.V_WATCH} | 펀더 {cfg.FUND_WEAK:.0f}~{cfg.FUND_STRONG:.0f} & 추세 1~2단계 | 추세는 좋으나 실적 확신 부족 |
| 🟣 {cfg.V_MOMENTUM} | 펀더 {cfg.FUND_WEAK:.0f} 미만 & 추세 양호 | 실적 근거 없이 주가만 오르는 중 |
| ⬜ {cfg.V_EXCLUDE} | 그 외 | 조건 미달 |
        """
    )

    st.markdown("### 데이터 출처와 한계")
    st.markdown(
        """
- **실적**: 미국 증권거래위원회(SEC) 전자공시의 8-K(Item 2.02 실적발표) 보도자료를 자동으로 읽어 사용합니다.
- **주가**: 야후 파이낸스(무료)의 종가 데이터를 사용합니다. 15분 이상 지연될 수 있습니다.
- **⚠️ 포워드(다음 분기 전망)의 한계**: 무료 데이터로는 진짜 월가 컨센서스를 구할 수 없습니다.
  회사가 발표한 가이던스가 있으면 그것을(배지: 가이던스), 없으면 애널리스트 매출 전망에
  과거 평균 마진을 곱해 **추정**합니다(배지: 추정). **실제 결과와 다를 수 있습니다.**
- **⚠️ 근사치 배지**: 보도자료 파싱에 실패한 종목은 XBRL 회계데이터로
  `GAAP 영업이익 + 주식보상비 + 무형자산상각`을 계산해 논갭을 근사합니다. 회사가 발표하는
  공식 논갭 수치와 차이가 날 수 있습니다.
        """
    )

# ---------------------------------------------------------------------------
# 면책 문구
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    "⚠️ **면책**: 본 대시보드는 공개 데이터를 자동 집계한 참고 자료이며 **투자 조언이 아닙니다**. "
    "포워드(다음 분기) 수치는 회사 가이던스 또는 통계적 추정치이며 실제 결과와 다를 수 있습니다. "
    "'근사치' 배지가 붙은 실적은 회사 공식 논갭 수치와 차이가 날 수 있습니다. "
    "데이터 출처: SEC EDGAR, Yahoo Finance. 투자의 최종 판단과 책임은 이용자 본인에게 있습니다."
)
