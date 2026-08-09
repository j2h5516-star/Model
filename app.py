"""
app.py — 추세추종 대시보드 v2 (Streamlit)
=========================================

실행 방법:
    streamlit run app.py

화면 구성:
  ① 종목 카드 그리드   — 최종점수와 판정을 한눈에
  ② 순위 표            — 행을 누르면 아래 상세로 이동
  ③ 4사분면 매트릭스   — 펀더멘털 vs 기술 위치
  ④ 종목 상세          — 항목을 누르면 계산 과정·출처·이력·예측을 볼 수 있음
  ⑤ 지표 해석 가이드   — 비전문가용 용어 설명

계산 로직은 다른 파일에 나뉘어 있습니다:
  market_data.py(주가) · sec_fundamentals.py(실적) · forward_estimates.py(전망) ·
  scoring.py(점수) · explain.py(설명문) · pipeline.py(전체 실행)
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import config as cfg
import explain
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

# 그래프 공통 설정 — 툴바를 숨겨야 제목과 겹치지 않습니다
PLOTLY_CONFIG = {
    "displayModeBar": False,   # 카메라·돋보기 등 툴바 제거 (모바일에서 제목을 가림)
    "scrollZoom": False,       # 페이지 스크롤 중 실수로 확대되는 것 방지
    "staticPlot": False,
}

# ---------------------------------------------------------------------------
# 커스텀 CSS — 다크 테마 + 모바일 가독성
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
  .block-container { padding-top: 1.2rem; padding-bottom: 2rem;
                     padding-left: 0.9rem; padding-right: 0.9rem; max-width: 1400px; }

  /* 종목 카드 그리드 — 화면 폭에 맞춰 자동 줄바꿈 */
  .card-grid { display: grid; gap: 10px;
               grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
               margin: 10px 0 16px 0; }
  .card { background: #1a1f2b; border: 1px solid #2b3241; border-radius: 12px;
          padding: 11px 12px 10px 12px; border-left-width: 4px;
          display: flex; flex-direction: column; min-height: 152px; }
  .card-top { display: flex; justify-content: space-between; align-items: baseline; gap: 6px; }
  .card-ticker { font-size: 1rem; font-weight: 700; color: #f3f4f6; letter-spacing: .3px; }
  .card-score { font-size: 1.85rem; font-weight: 800; line-height: 1.05; margin: 3px 0 7px 0; }
  .card-sub { font-size: .73rem; color: #9ca3af; margin-top: 2px; line-height: 1.35; }
  .card-badges { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 999px;
           font-size: .66rem; font-weight: 700; white-space: nowrap; }
  .badge-src { background: #263042; color: #93c5fd; border: 1px solid #33415a; }

  /* 차트 위 제목 — plotly 안에 넣지 않고 밖에 두어 겹침을 막습니다 */
  .chart-title { color: #f3f4f6; font-size: 1rem; font-weight: 700;
                 margin: 14px 0 2px 0; }
  .chart-note { color: #9ca3af; font-size: .8rem; margin: 0 0 6px 0; line-height: 1.5; }

  .section-note { color: #9ca3af; font-size: .82rem; margin: -6px 0 10px 0; line-height: 1.6; }
  /* 표 바로 위 문구는 여백을 더 줍니다 — 표 오른쪽 위 툴바가 문구를 덮지 않도록 */
  .note-above-table { margin-bottom: 26px; }
  .tip { border-bottom: 1px dotted #6b7280; cursor: help; }
  h2, h3 { color: #f3f4f6 !important; }
  [data-testid="stDataFrame"] { font-size: 0.86rem; }

  /* 상세 설명 버튼들을 한 줄에 촘촘히 */
  div[data-testid="stPopover"] button { width: 100%; }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 지표 설명 (툴팁 + 하단 가이드 공용)
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
        "믹스형=비싼 제품 비중이 늘어난 것, 가격형=가격을 올려서 생긴 것(오래 못 갈 수 있음)."
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
    """지표 이름 옆에 ⓘ 를 붙여 설명이 뜨게 만듭니다."""
    text = GLOSSARY.get(key or label, "")
    if not text:
        return label
    return f'<span class="tip" title="{text}">{label} ⓘ</span>'


# ---------------------------------------------------------------------------
# 상세 설명 그리기 도우미 — 눌렀을 때 나오는 내용 공통 서식
# ---------------------------------------------------------------------------
def render_explanation(data: dict) -> None:
    """설명 딕셔너리를 화면에 보기 좋게 그립니다."""
    if data.get("meaning"):
        st.markdown(f"**이게 무슨 뜻인가요?**\n\n{data['meaning']}")

    if data.get("formula"):
        st.markdown(f"**계산 방법**\n\n{data['formula']}")

    lines = [line for line in data.get("calc_lines", []) if line]
    if lines:
        st.markdown("**이 종목의 실제 숫자**\n\n" + "\n".join(lines))

    if data.get("detail"):
        st.info(data["detail"])

    if data.get("bands"):
        st.markdown("**구간별 점수표**")
        st.dataframe(
            pd.DataFrame(
                [{"차이(%p)": b[0], "성격": b[1], "점수": b[2]} for b in data["bands"]]
            ),
            hide_index=True,
            width="stretch",
        )

    if data.get("rules"):
        st.markdown("**판정 규칙 전체**")
        rules_df = pd.DataFrame(
            [{"판정": r[0], "조건": r[1], "뜻": r[2]} for r in data["rules"]]
        )
        current = data.get("current")
        st.dataframe(
            rules_df.style.apply(
                lambda row: [
                    "background-color:#22c55e22; font-weight:700" if row["판정"] == current else ""
                    for _ in row
                ],
                axis=1,
            ),
            hide_index=True,
            width="stretch",
        )

    if data.get("history"):
        st.markdown("**지금까지의 변화 (2025년 초부터)**")
        st.dataframe(pd.DataFrame(data["history"]), hide_index=True, width="stretch")

    if data.get("forecast_lines"):
        st.markdown("**앞으로의 예측**\n\n" + "\n".join(data["forecast_lines"]))

    if data.get("revision_text"):
        st.caption(f"애널리스트 전망 변화: {data['revision_text']}")

    if data.get("source_text"):
        st.caption(f"📚 자료 출처: {data['source_text']}")

    if data.get("url"):
        st.markdown(f"[🔗 SEC 원문 공시 열어보기]({data['url']})")


# ---------------------------------------------------------------------------
# 데이터 실행 (캐시: 같은 조건이면 1시간 재사용)
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
        f"**대상 종목** {len(cfg.TICKERS)}개\n\n{', '.join(cfg.TICKERS)}\n\n"
        f"**비교 지수** {cfg.BENCHMARK}"
    )


# ---------------------------------------------------------------------------
# 헤더 + 데이터 로드
# ---------------------------------------------------------------------------
st.title("📊 추세추종 대시보드 v2")
st.markdown(
    '<div class="section-note">실적(SEC 8-K 논갭 영업이익) + 주가 추세를 합쳐 종목별 종합 점수를 매깁니다. '
    "모든 데이터는 무료 공개 자료에서 자동 수집됩니다. "
    "<b>화면의 배지나 항목을 누르면 어떻게 계산했는지 볼 수 있습니다.</b></div>",
    unsafe_allow_html=True,
)

with st.spinner("SEC 공시와 주가 데이터를 모으는 중입니다... (첫 실행은 1~3분 걸릴 수 있습니다)"):
    try:
        result = load_all(include_current, True)
    except Exception as exc:
        st.error(
            "데이터를 불러오지 못했습니다.\n\n"
            "- 인터넷 연결을 확인해 주세요\n"
            "- 잠시 후 왼쪽 사이드바의 **🔄 데이터 새로고침**을 눌러 주세요\n\n"
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

st.caption(f"🕐 마지막 업데이트: **{result['updated_at'].strftime('%Y-%m-%d %H:%M')}**")

if result["failed"]:
    st.warning(f"주가 데이터를 받지 못한 종목: {', '.join(result['failed'])}")
if result["no_fundamentals"]:
    st.info(
        f"실적 데이터를 찾지 못해 주가 지표만 반영된 종목: {', '.join(result['no_fundamentals'])}"
    )


# ---------------------------------------------------------------------------
# ① 종목 카드 그리드
# ---------------------------------------------------------------------------
st.subheader("종목별 종합 점수")

ordered_tickers = ranking["종목"].tolist() if not ranking.empty else list(scores.keys())

cards_html = ['<div class="card-grid">']
for ticker in ordered_tickers:
    score = scores[ticker]
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
    <span class="card-ticker">{ticker}</span>
    <span class="card-sub">{rs_text}</span>
  </div>
  <div class="card-score" style="color:{color};">{score['final_score']:.0f}</div>
  <div class="card-badges">{''.join(badges)}</div>
  <div class="card-sub">펀더 {score['fund_score']:.0f} · 기술 {score['tech_score']:.0f}</div>
  <div class="card-sub">{score['trend_state']}</div>
</div>"""
    )
cards_html.append("</div>")
st.markdown("".join(cards_html), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# ② 순위 표 (행을 누르면 아래 상세로 연결)
# ---------------------------------------------------------------------------
st.subheader("순위 표")
# 표 위 안내는 짧게 유지합니다 — 표 오른쪽 위의 작은 툴바가 긴 문구를 가리기 때문입니다
st.markdown(
    f'<div class="section-note note-above-table">{tip("최종점수")} = {tip("펀더")} × 0.6 + '
    f'{tip("기술")} × 0.4</div>',
    unsafe_allow_html=True,
)


def _style_verdict(value):
    color = cfg.VERDICT_COLORS.get(value)
    return f"background-color:{color}22; color:{color}; font-weight:700" if color else ""


def _style_score(value):
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
    if pd.isna(value):
        return ""
    return "color:#22c55e" if value > 0 else "color:#ef4444"


styled = (
    ranking.style.map(_style_verdict, subset=["판정"])
    .map(_style_score, subset=["최종점수", "펀더", "기술"])
    .map(_style_rs, subset=["RS"])
    .format(
        {"최종점수": "{:.0f}", "펀더": "{:.0f}", "기술": "{:.0f}", "RS": "{:+.1f}%p"},
        na_rep="-",
    )
)

table_event = st.dataframe(
    styled,
    width="stretch",
    hide_index=True,
    height=min(36 * (len(ranking) + 1) + 8, 560),
    on_select="rerun",
    selection_mode="single-row",
    key="ranking_table",
)

# 표에서 고른 행이 있으면 그 종목을 상세 화면의 기본값으로 삼습니다
picked_from_table = None
try:
    rows = table_event.selection.rows
    if rows:
        picked_from_table = ranking.iloc[rows[0]]["종목"]
except (AttributeError, IndexError, KeyError):
    picked_from_table = None

st.markdown(
    '<div class="section-note">👆 <b>표의 행을 누르면</b> 아래 \'종목 상세\'가 그 종목으로 바뀝니다 · '
    "매수 후보는 상대강도(RS)가 높은 순으로 위에 표시됩니다<br>"
    "📱 모바일에서는 표를 좌우로 밀어서 나머지 열을 볼 수 있습니다.</div>",
    unsafe_allow_html=True,
)


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

# 사분면 배경 (x=기술 50 기준, y=펀더 70 기준)
quadrants = [
    (50, 100, cfg.FUND_STRONG, 100, "#22c55e", "매수 후보권", 99, 99, "right", "top"),
    (0, 50, cfg.FUND_STRONG, 100, "#f59e0b", "⚠️ 실적 좋음·추세 꺾임", 1, 99, "left", "top"),
    (50, 100, 0, cfg.FUND_STRONG, "#3b82f6", "관찰 / 모멘텀", 99, 1, "right", "bottom"),
    (0, 50, 0, cfg.FUND_STRONG, "#6b7280", "제외권", 1, 1, "left", "bottom"),
]
for x0, x1, y0, y1, color, label, lx, ly, xanchor, yanchor in quadrants:
    matrix.add_shape(
        type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
        fillcolor=color, opacity=0.10, line_width=0, layer="below",
    )
    # 라벨을 사분면 "모서리"에 붙여 종목 점과 겹치지 않게 합니다
    matrix.add_annotation(
        x=lx, y=ly, text=label, showarrow=False,
        xanchor=xanchor, yanchor=yanchor,
        font=dict(color=color, size=10), opacity=0.9,
    )

# 종목명이 차트 오른쪽 끝에서 잘리지 않도록, 오른쪽에 있는 점은 글자를 왼쪽에 붙입니다
_text_positions = [
    "middle left" if scores[t]["tech_score"] >= 82
    else "middle right" if scores[t]["tech_score"] <= 12
    else "top center"
    for t in ordered_tickers
]

matrix.add_trace(
    go.Scatter(
        x=[scores[t]["tech_score"] for t in ordered_tickers],
        y=[scores[t]["fund_score"] for t in ordered_tickers],
        mode="markers+text",
        text=ordered_tickers,
        textposition=_text_positions,
        textfont=dict(size=9, color="#d1d5db"),
        marker=dict(
            size=13,
            color=[cfg.VERDICT_COLORS.get(scores[t]["verdict"], "#6b7280") for t in ordered_tickers],
            line=dict(width=1, color="#0e1117"),
        ),
        customdata=[[scores[t]["verdict"], scores[t]["final_score"]] for t in ordered_tickers],
        hovertemplate=(
            "<b>%{text}</b><br>기술 %{x:.0f} · 펀더 %{y:.0f}"
            "<br>최종 %{customdata[1]:.0f} · %{customdata[0]}<extra></extra>"
        ),
        showlegend=False,
        cliponaxis=False,
    )
)
matrix.update_layout(
    template="plotly_dark",
    height=470,
    margin=dict(l=52, r=42, t=16, b=48),   # 오른쪽 여백은 종목명이 잘리지 않도록
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
)
# 축 범위를 0~100으로 고정 — 이렇게 하지 않으면 글자 때문에 축이 -20~120까지 늘어납니다
matrix.update_xaxes(
    title_text="기술 점수 (주가 흐름) →",
    range=[0, 100], autorange=False, fixedrange=True,
    dtick=25, gridcolor="#2b3241", zeroline=False,
)
matrix.update_yaxes(
    title_text="펀더멘털 점수 (실적) →",
    range=[0, 100], autorange=False, fixedrange=True,
    dtick=25, gridcolor="#2b3241", zeroline=False,
)
st.plotly_chart(matrix, width="stretch", config=PLOTLY_CONFIG)


# ---------------------------------------------------------------------------
# ④ 종목 상세
# ---------------------------------------------------------------------------
st.subheader("종목 상세")

ticker_list = ordered_tickers
default_index = 0
if picked_from_table in ticker_list:
    default_index = ticker_list.index(picked_from_table)

selected = st.selectbox(
    "종목을 선택하세요 (위 순위 표의 행을 눌러도 됩니다)",
    options=ticker_list,
    index=default_index,
    format_func=lambda t: f"{t} — {scores[t]['verdict']} (최종 {scores[t]['final_score']:.0f}점)",
)

detail = scores[selected]

# --- 핵심 지표 카드 ---
c1, c2, c3, c4 = st.columns(4)
c1.metric("최종점수", f"{detail['final_score']:.0f}점", detail["verdict"], delta_color="off")
c2.metric("펀더멘털", f"{detail['fund_score']:.0f}점", detail["delta_direction"], delta_color="off")
c3.metric("기술", f"{detail['tech_score']:.0f}점", detail["trend_state"], delta_color="off")
c4.metric(
    "상대강도(RS)",
    "-" if detail["rs"] is None else f"{detail['rs']:+.1f}%p",
    "시장 대비 13주",
    delta_color="off",
)

# --- 눌러서 보는 상세 설명 버튼들 ---
st.markdown(
    '<div class="section-note">🔎 아래 버튼을 누르면 <b>그 항목이 어떻게 계산됐는지</b> '
    "계산식·실제 숫자·자료 출처·지난 이력·앞으로의 예측을 볼 수 있습니다.</div>",
    unsafe_allow_html=True,
)

row1 = st.columns(3)
with row1[0]:
    with st.popover(f"📄 출처: {detail['data_source'] or '없음'}", width="stretch"):
        render_explanation(explain.explain_data_source(detail))
with row1[1]:
    with st.popover(f"🔮 전망: {detail['forward_basis'] or '없음'}", width="stretch"):
        render_explanation(explain.explain_forward(detail))
with row1[2]:
    with st.popover(f"🏷️ 판정: {detail['verdict']}", width="stretch"):
        render_explanation(explain.explain_verdict_full(detail))

row2 = st.columns(3)
with row2[0]:
    with st.popover(f"📊 GM% 드라이버: {detail['gm_type']}", width="stretch"):
        render_explanation(explain.explain_gm_driver(detail))
with row2[1]:
    with st.popover(f"⚡ 델타 방향: {detail['delta_direction']}", width="stretch"):
        render_explanation(explain.explain_delta(detail))
with row2[2]:
    with st.popover(f"📈 기술 점수: {detail['tech_score']:.0f}점", width="stretch"):
        render_explanation(explain.explain_technical(detail))

# --- 점수 구성 요약 ---
fundamental = detail["fundamental"]
technical = detail["technical"]

with st.expander("🔍 점수 전체 구성 한눈에 보기"):
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

    forward_op = detail["forward_op_income"]
    if forward_op is not None:
        labels = labels + ["다음(전망)"]
        forward_bar = [None] * len(op_values) + [forward_op]
        op_bar = op_values + [None]
    else:
        forward_bar = None
        op_bar = op_values

    # 제목을 plotly 밖에 두어 툴바·범례와 겹치지 않게 합니다
    st.markdown(
        f'<div class="chart-title">{selected} — 분기별 논갭 영업이익과 증가 속도</div>',
        unsafe_allow_html=True,
    )

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=labels,
            y=[v / 1e6 if v is not None else None for v in op_bar],
            name="논갭 영업이익",
            marker_color="#22c55e",
            opacity=0.9,
            hovertemplate="%{x}<br>영업이익 $%{y:,.0f}M<extra></extra>",
        ),
        secondary_y=False,
    )
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
                connectgaps=True,
                hovertemplate="%{x}<br>QoQ %{y:+.1f}%<extra></extra>",
            ),
            secondary_y=True,
        )

    fig.update_layout(
        template="plotly_dark",
        height=390,
        margin=dict(l=60, r=58, t=42, b=44),   # 위 여백은 범례, 좌우는 축 제목 자리
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, font=dict(size=10)),
        hovermode="x unified",
        barmode="overlay",
        bargap=0.28,
    )
    # 가로축 라벨을 눕히지 않고 짧게 유지해 글자 겹침을 막습니다
    fig.update_xaxes(tickangle=0, tickfont=dict(size=11), fixedrange=True)
    fig.update_yaxes(
        title_text="영업이익($M)", title_font=dict(size=11),
        secondary_y=False, gridcolor="#2b3241", fixedrange=True,
    )
    fig.update_yaxes(
        title_text="QoQ 증가율(%)", title_font=dict(size=11),
        secondary_y=True, showgrid=False, fixedrange=True,
    )
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)

    st.markdown(
        '<div class="chart-note">초록 막대가 이익 크기, 주황 선이 "전분기 대비 얼마나 빨리 늘고 있는가"입니다. '
        "주황 선이 우상향이면 가속(성장에 탄력), 우하향이면 감속입니다. "
        "빗금 무늬 막대는 아직 발표되지 않은 <b>추정치</b>입니다.</div>",
        unsafe_allow_html=True,
    )

    # --- 매출총이익률(GM%) 추이 ---
    gm_series = quarters_df.dropna(subset=["gross_margin_pct"])
    if len(gm_series) >= 2:
        st.markdown(
            f'<div class="chart-title">{selected} — 매출총이익률(GM%) 추이 '
            f"<span style='color:#9ca3af;font-weight:400;font-size:.85rem;'>"
            f"· 현재 성격: {detail['gm_type']}</span></div>",
            unsafe_allow_html=True,
        )
        gm_fig = go.Figure()
        gm_fig.add_trace(
            go.Scatter(
                x=gm_series["period_label"],
                y=gm_series["gross_margin_pct"],
                mode="lines+markers",
                line=dict(color="#60a5fa", width=2.5),
                marker=dict(size=8),
                hovertemplate="%{x}<br>GM %{y:.1f}%<extra></extra>",
            )
        )
        gm_fig.update_layout(
            template="plotly_dark",
            height=270,
            margin=dict(l=60, r=22, t=14, b=44),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
        )
        gm_fig.update_xaxes(tickangle=0, tickfont=dict(size=11), fixedrange=True)
        gm_fig.update_yaxes(
            title_text="GM (%)", title_font=dict(size=11),
            gridcolor="#2b3241", fixedrange=True,
        )
        st.plotly_chart(gm_fig, width="stretch", config=PLOTLY_CONFIG)

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
        if detail.get("filing_url"):
            st.markdown(f"[🔗 가장 최근 SEC 공시 원문 열어보기]({detail['filing_url']})")


# ---------------------------------------------------------------------------
# ⑤ 지표 해석 가이드
# ---------------------------------------------------------------------------
st.divider()
with st.expander("📖 지표 해석 가이드 (처음 보신다면 여기부터)"):
    st.markdown("### 이 대시보드는 무엇을 보나요?")
    st.markdown(
        "회사의 **실적이 좋아지는 속도**와 **주가 흐름**을 각각 점수로 매기고, "
        "둘을 합쳐 종목을 비교합니다. 실적만 좋아도, 주가만 좋아도 부족하다는 관점입니다."
    )

    st.markdown("### 용어 설명")
    for term, description in GLOSSARY.items():
        st.markdown(f"- **{term}** — {description}")

    st.markdown("### 점수 계산")
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

> 💡 **저기저 함정 방지**: 최근 분기 논갭 영업이익이 $50M 미만이면 델타 가속 점수를
> 절반까지만 인정합니다. 기저가 작으면 증가율이 커 보이는 착시가 생기기 때문입니다.
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
- **실적**: SEC 전자공시의 8-K(Item 2.02 실적발표) 보도자료를 자동으로 읽습니다.
- **주가**: 야후 파이낸스(무료) 종가. 15분 이상 지연될 수 있습니다.
- **⚠️ 포워드의 한계**: 무료 데이터로는 진짜 월가 컨센서스를 구할 수 없습니다.
  `가이던스`는 회사 발표에 근거하지만 `추정`은 통계적 추정치이며 실제와 다를 수 있습니다.
- **⚠️ 근사치 배지**: 보도자료 파싱에 실패한 종목은 XBRL로
  `GAAP 영업이익 + 주식보상비 + 무형자산상각`을 계산해 논갭을 근사합니다.
  회사 공식 논갭 수치와 차이가 날 수 있습니다.
        """
    )

st.divider()
st.caption(
    "⚠️ **면책**: 본 대시보드는 공개 데이터를 자동 집계한 참고 자료이며 **투자 조언이 아닙니다**. "
    "포워드 수치는 회사 가이던스 또는 통계적 추정치이며 실제 결과와 다를 수 있습니다. "
    "'근사치' 배지가 붙은 실적은 회사 공식 논갭 수치와 차이가 날 수 있습니다. "
    "데이터 출처: SEC EDGAR, Yahoo Finance. 투자의 최종 판단과 책임은 이용자 본인에게 있습니다."
)
