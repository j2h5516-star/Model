"""
app.py — 주식 추세추종 대시보드 (Streamlit 웹앱)
===============================================

터미널에서 아래 명령으로 실행합니다:

    streamlit run app.py

실행하면 웹 브라우저가 자동으로 열리고 대시보드가 표시됩니다.
데이터 계산 로직은 trend_analyzer.py 에 있습니다.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import trend_analyzer as ta

# ---------------------------------------------------------------------------
# 페이지 기본 설정 (제목, 아이콘, 넓은 화면 사용)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="주식 추세추종 대시보드",
    page_icon="📈",
    layout="wide",
)

# ---------------------------------------------------------------------------
# 추세 상태별 색상 (요약 테이블에 사용)
#   정배열 계열 = 초록, 역배열/훼손 = 빨강·주황, 중립 = 회색
# ---------------------------------------------------------------------------
STATE_COLORS = {
    ta.S_FULL_UP: "background-color: #1a7f37; color: white; font-weight: bold",   # 진초록
    ta.S_SEMI_UP: "background-color: #a6e3a1; color: #1a4a22; font-weight: bold", # 연초록
    ta.S_NEUTRAL: "background-color: #e5e7eb; color: #374151",                    # 회색
    ta.S_DAMAGED: "background-color: #ffb454; color: #7c2d12; font-weight: bold", # 주황
    ta.S_FULL_DOWN: "background-color: #d0342c; color: white; font-weight: bold", # 빨강
    ta.S_UNKNOWN: "background-color: #f3f4f6; color: #9ca3af",                    # 옅은 회색
}

# 이동평균선 색상 (차트에 사용)
MA_COLORS = {
    "MA4": "#ff9f1c",   # 주황 — 가장 빠르게 움직이는 단기선
    "MA13": "#8e44ad",  # 보라 — 분기(3개월) 추세선
    "MA26": "#2e86de",  # 파랑 — 반기(6개월) 추세선. 판정의 핵심 기준선
    "MA52": "#576574",  # 회색 — 1년 장기 추세선
}


# ---------------------------------------------------------------------------
# 데이터 내려받기 (1시간 동안 캐시 = 같은 데이터를 반복해서 받지 않아 빠름)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="야후 파이낸스에서 주가 데이터를 받아오는 중...")
def load_data():
    """13개 종목의 일봉 데이터를 내려받습니다. 결과는 1시간 동안 재사용됩니다."""
    return ta.fetch_daily_data(ta.TICKERS)


# ---------------------------------------------------------------------------
# 사이드바 (왼쪽 설정 패널)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 설정")

    # 진행 중인 주 포함 여부
    include_current = st.checkbox(
        "진행 중인 이번 주 포함",
        value=True,
        help=(
            "체크하면 아직 금요일 장이 끝나지 않은 이번 주 주봉도 포함해서 판정합니다. "
            "체크를 풀면 완성된 지난주 금요일 종가 기준으로 판정합니다."
        ),
    )

    # 새로고침 버튼: 캐시를 지우고 데이터를 다시 받아옵니다
    if st.button("🔄 데이터 새로고침", width="stretch"):
        load_data.clear()
        st.rerun()

    st.divider()

    # 판정 기준 설명 (비개발자용 도움말)
    with st.expander("📖 추세 판정 기준 보기"):
        st.markdown(
            """
| 상태 | 조건 |
|---|---|
| 🟢 **완전 정배열** | 주가 > 4주 > 13주 > 26주 > 52주 |
| 🟩 **준정배열** | 단기 조정 중이지만 주가 > 26주 > 52주 유지 |
| ⬜ **중립** | 주가가 26주선 ±3% 이내에서 공방 |
| 🟧 **추세 훼손** | 주가 < 26주선 또는 13주선이 26주선 하향돌파 |
| 🟥 **완전 역배열** | 4주 < 13주 < 26주 < 52주 |

**보조 신호**
- **26주선 기울기**: 4주 전과 비교해 상승/하락/횡보
- **이격도**: 주가가 26주선보다 몇 % 위(+)/아래(−)에 있는지
            """
        )


# ---------------------------------------------------------------------------
# 메인 화면
# ---------------------------------------------------------------------------
st.title("📈 주식 추세추종 대시보드")
st.caption("주봉 기준 4·13·26·52주 이동평균선으로 추세를 5단계 판정합니다.")

# 1) 데이터 내려받기
daily_map, failed = load_data()

# 데이터를 하나도 받지 못한 경우: 안내 메시지를 보여주고 종료
if not daily_map:
    st.error(
        "⚠️ 주가 데이터를 받아오지 못했습니다.\n\n"
        "- 인터넷 연결을 확인해 주세요.\n"
        "- 잠시 후 왼쪽의 **데이터 새로고침** 버튼을 눌러 다시 시도해 주세요.\n"
        "- 회사/학교 네트워크라면 야후 파이낸스 접속이 차단되어 있을 수 있습니다."
    )
    st.stop()

# 일부 종목만 실패한 경우: 어떤 종목이 빠졌는지 알려주기
if failed:
    st.warning(f"다음 종목은 데이터를 받지 못해 제외되었습니다: {', '.join(failed)}")

# 2) 추세 분석 실행
summary, chart_map = ta.analyze_all(daily_map, include_current_week=include_current)

# 데이터 기준일 표시 (가장 최근 거래일)
latest_dates = [df.index[-1] for df in daily_map.values()]
data_date = max(latest_dates).strftime("%Y-%m-%d")
st.caption(f"데이터 기준일: **{data_date}** (마지막 거래일)")

# ---------------------------------------------------------------------------
# 3) 추세 상태 요약 테이블
# ---------------------------------------------------------------------------
st.subheader("종목별 추세 상태 요약")

# 상태별 종목 수를 위쪽에 큼직하게 표시
state_counts = summary["추세 판정"].value_counts()
cols = st.columns(6)
metric_defs = [
    ("🟢 완전 정배열", ta.S_FULL_UP),
    ("🟩 준정배열", ta.S_SEMI_UP),
    ("⬜ 중립", ta.S_NEUTRAL),
    ("🟧 추세 훼손", ta.S_DAMAGED),
    ("🟥 완전 역배열", ta.S_FULL_DOWN),
    ("❔ 판정 불가", ta.S_UNKNOWN),
]
for col, (label, state) in zip(cols, metric_defs):
    col.metric(label, f"{int(state_counts.get(state, 0))}종목")


def style_state_cell(value):
    """'추세 판정' 셀에 상태별 배경색을 입힙니다."""
    return STATE_COLORS.get(value, "")


def style_change_cell(value):
    """등락률/이격도 셀: 플러스는 빨강, 마이너스는 파랑 (한국식 표기)"""
    if pd.isna(value):
        return ""
    return "color: #d0342c" if value > 0 else "color: #1e6ff5"


# 표에 색상과 숫자 형식을 입힙니다
styled = (
    summary.style
    .map(style_state_cell, subset=["추세 판정"])                 # 상태 색상
    .map(style_change_cell, subset=["주간 등락(%)", "이격도(%)"])  # 등락 색상
    .format(
        {
            "현재가($)": "{:,.2f}",
            "주간 등락(%)": "{:+.1f}%",
            "이격도(%)": "{:+.1f}%",
        },
        na_rep="-",  # 값이 없으면 - 로 표시
    )
)

st.dataframe(
    styled,
    width="stretch",
    height=min(38 * (len(summary) + 1) + 12, 560),  # 종목 수에 맞춰 높이 조절
    hide_index=True,
)

# ---------------------------------------------------------------------------
# 4) 종목별 상세 차트
# ---------------------------------------------------------------------------
st.subheader("종목별 주봉 차트")

# 요약 테이블과 같은 순서(좋은 추세부터)로 선택 목록 구성
ticker_options = summary["종목"].tolist()
selected = st.selectbox(
    "차트를 볼 종목을 선택하세요",
    options=ticker_options,
    format_func=lambda t: f"{t}  —  {summary.loc[summary['종목'] == t, '추세 판정'].iloc[0]}",
)

if selected and selected in chart_map:
    weekly_ma = chart_map[selected]
    info = summary.loc[summary["종목"] == selected].iloc[0]

    # --- 선택 종목 핵심 지표 카드 ---
    c1, c2, c3, c4 = st.columns(4)
    week_chg = info["주간 등락(%)"]
    c1.metric(
        "현재가",
        f"${info['현재가($)']:,.2f}",
        delta=None if pd.isna(week_chg) else f"{week_chg:+.1f}% (주간)",
    )
    c2.metric("추세 판정", info["추세 판정"])
    slope_txt = info["26주선 기울기"]
    slope_icon = {"상승": "↗", "하락": "↘", "횡보": "→"}.get(slope_txt, "")
    c3.metric("26주선 기울기", f"{slope_txt} {slope_icon}")
    disparity = info["이격도(%)"]
    c4.metric("26주선 이격도", "-" if pd.isna(disparity) else f"{disparity:+.1f}%")

    st.info(f"💡 **판정 근거**: {info['판정 근거']}")

    # --- 주봉 캔들차트 + 이동평균선 4개 + 거래량 ---
    # 최근 2년(104주)만 표시해 차트를 보기 좋게 만듭니다
    plot_df = weekly_ma.tail(104)

    # 위(가격 차트 75%) / 아래(거래량 25%) 두 단으로 나눈 차트
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,       # 위아래 차트의 날짜축을 함께 움직이기
        vertical_spacing=0.03,
        row_heights=[0.75, 0.25],
    )

    # 캔들차트 (한국식: 상승 주 = 빨강, 하락 주 = 파랑)
    fig.add_trace(
        go.Candlestick(
            x=plot_df.index,
            open=plot_df["Open"],
            high=plot_df["High"],
            low=plot_df["Low"],
            close=plot_df["Close"],
            name="주봉",
            increasing_line_color="#d0342c",
            increasing_fillcolor="#d0342c",
            decreasing_line_color="#1e6ff5",
            decreasing_fillcolor="#1e6ff5",
        ),
        row=1,
        col=1,
    )

    # 이동평균선 4개 그리기 (26주선은 판정 기준선이므로 더 굵게)
    for ma_name, color in MA_COLORS.items():
        weeks = ma_name.replace("MA", "")
        fig.add_trace(
            go.Scatter(
                x=plot_df.index,
                y=plot_df[ma_name],
                mode="lines",
                name=f"{weeks}주선",
                line=dict(color=color, width=2.5 if ma_name == "MA26" else 1.5),
            ),
            row=1,
            col=1,
        )

    # 거래량 막대 (그 주가 올랐으면 빨강, 내렸으면 파랑)
    volume_colors = [
        "#d0342c" if c >= o else "#1e6ff5"
        for o, c in zip(plot_df["Open"], plot_df["Close"])
    ]
    fig.add_trace(
        go.Bar(
            x=plot_df.index,
            y=plot_df["Volume"],
            name="거래량",
            marker_color=volume_colors,
            opacity=0.6,
        ),
        row=2,
        col=1,
    )

    # 차트 모양 다듬기
    fig.update_layout(
        title=f"{selected} — 주봉 차트 (최근 2년)",
        height=650,
        xaxis_rangeslider_visible=False,  # 아래쪽 미니 슬라이더 숨기기
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=40, r=40, t=80, b=40),
        hovermode="x unified",  # 마우스를 올리면 그 주의 모든 값을 한 번에 표시
    )
    fig.update_yaxes(title_text="주가 ($)", row=1, col=1)
    fig.update_yaxes(title_text="거래량", row=2, col=1)

    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# 하단 안내 문구
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    "⚠️ 본 대시보드는 참고용 정보이며 투자 권유가 아닙니다. "
    "데이터 출처: Yahoo Finance (yfinance). 데이터는 15분 이상 지연될 수 있습니다."
)
