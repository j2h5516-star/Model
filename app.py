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

import os
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import backtest
import config as cfg
import explain
import pipeline
import storage

# ---------------------------------------------------------------------------
# 설정 파일이 최신인지 먼저 확인합니다
# ---------------------------------------------------------------------------
# 코드를 새로 올렸는데 서버가 옛 설정 파일을 메모리에 붙들고 있으면,
# 새 app.py가 찾는 항목이 없어서 빨간 오류 화면(AttributeError)이 뜹니다.
# 그런 경우 사용자가 무엇을 해야 하는지 알 수 있도록 안내로 바꿔 줍니다.
REQUIRED_CONFIG_VERSION = 14

if getattr(cfg, "CONFIG_VERSION", 0) < REQUIRED_CONFIG_VERSION:
    st.error(
        "### 🔄 앱을 다시 시작해 주세요\n\n"
        "코드는 새 버전으로 올라갔는데, 서버가 **옛 설정 파일**을 아직 붙들고 있습니다.\n\n"
        "**해결 방법 (30초)**\n\n"
        "1. 화면 **오른쪽 아래 `Manage app`** 을 누릅니다\n"
        "2. 오른쪽 위 **⋮ 메뉴** → **`Reboot app`** 을 누릅니다\n"
        "3. 1~2분 기다렸다가 새로고침하면 정상 동작합니다\n\n"
        "---\n"
        f"*기술 정보: 설정 파일 판 번호 {getattr(cfg, 'CONFIG_VERSION', 0)} "
        f"(필요: {REQUIRED_CONFIG_VERSION})*"
    )
    st.stop()

# SEC에 보낼 요청자 신원을 Streamlit Secrets에서 읽어옵니다.
# Secrets는 저장소에 올라가지 않아 개인 이메일을 비공개로 유지할 수 있습니다.
# (설정하지 않아도 기본값으로 동작합니다)
try:
    if "SEC_IDENTITY" in st.secrets:
        os.environ["SEC_IDENTITY"] = str(st.secrets["SEC_IDENTITY"])
except Exception:
    pass  # Secrets 파일이 없어도 앱은 정상 동작해야 합니다

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

  /* 데이터 신뢰도 게이지 (카드 맨 아래) */
  .conf-row { display: flex; align-items: center; gap: 6px; margin-top: 7px; }
  .conf-bar { flex: 1; height: 5px; border-radius: 3px; background: #2b3241; overflow: hidden; }
  .conf-fill { height: 100%; border-radius: 3px; }
  .conf-text { font-size: .66rem; font-weight: 700; white-space: nowrap; }

  /* 매트릭스 아래 번호↔종목명 범례 */
  .legend-grid { display: grid; gap: 4px 10px;
                 grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
                 margin: 2px 0 10px 0; }
  .legend-item { font-size: .76rem; color: #cbd5e1; white-space: nowrap; }
  .legend-no { display: inline-block; min-width: 17px; height: 17px; line-height: 17px;
               text-align: center; border-radius: 50%; font-size: .64rem;
               font-weight: 700; color: #0e1117; margin-right: 5px; }

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
# 데이터 실행 — 종목 단위로 따로 캐시합니다
# ---------------------------------------------------------------------------
# 종목을 하나 추가했을 때 나머지까지 전부 다시 받으면 1~3분이 걸립니다.
# 종목마다 따로 캐시해 두면 새로 추가한 종목만 받으면 되어 몇 초면 끝납니다.
@st.cache_data(ttl=3600, show_spinner=False)
def load_one_ticker(ticker: str, use_cache: bool):
    """종목 하나의 실적·전망 (1시간 캐시)"""
    return pipeline.collect_one_ticker(ticker, use_cache)


@st.cache_resource
def _price_store() -> dict:
    """종목별 주가 저장소 — 종목을 추가해도 새 종목만 받아옵니다.

    여러 사용자 세션이 같은 저장소를 공유하므로, 동시에 갱신해도
    중복 다운로드·데이터 역행이 없도록 락을 함께 둡니다.
    """
    import threading

    return {"data": {}, "ts": {}, "lock": threading.Lock()}


def load_all(tickers: list[str], include_current_week: bool, use_cache: bool = True):
    """종목별 캐시를 모아 최종 결과를 만듭니다."""
    # 주가: 저장소에 없는 종목만 받아옵니다 (야후 거부 시 자동 재시도 포함)
    store = _price_store()
    with st.spinner("주가 데이터를 확인하는 중..."):
        failed, stale = pipeline.refresh_price_store(store, tickers)

    # 기준 지수(SPY)는 종목 목록이 아니므로 실패 경고에서 분리해 별도 안내
    if cfg.BENCHMARK in failed or cfg.BENCHMARK in stale:
        st.info(
            f"기준 지수({cfg.BENCHMARK}) 갱신에 실패해 상대강도(RS)가 "
            "이전 데이터 기준이거나 비어 있을 수 있습니다."
        )
    failed = [t for t in failed if t != cfg.BENCHMARK]
    stale = [t for t in stale if t != cfg.BENCHMARK]
    if stale:
        st.info(
            f"다음 종목은 갱신에 실패해 **이전 시점의 주가**로 표시 중입니다: "
            f"{', '.join(stale)} (5분 후 자동 재시도)"
        )

    price_map, weekly_map = pipeline.prices_from_store(store, tickers, include_current_week)

    # 실적: 한 종목씩 차례로 받습니다.
    #
    # ⚠️ 4개를 동시에 받도록 바꾼 직후 전 종목 실적 수집이 실패했습니다.
    #    확인된 기전: SEC 신원을 설정하는 edgartools의 set_identity()가 여러 종목이
    #    함께 쓰는 접속 창구를 닫고 새로 엽니다. 동시에 받는 도중 다른 종목이 이
    #    함수를 또 부르면, 요청을 보내던 쪽의 창구가 닫혀 실패합니다.
    #    → sec_fundamentals._ensure_identity 에 자물쇠를 넣어 막았지만,
    #      실적 정확도가 속도보다 중요하므로 화면 수집은 순차로 둡니다.
    #    종목별로 하루치 캐시가 있어 처음 한 번만 느리고 이후는 즉시 표시됩니다.
    bundles: dict[str, dict] = {}
    todo = list(tickers)
    progress = st.progress(0.0, text="실적 자료를 모으는 중...")
    for done_count, ticker in enumerate(todo, start=1):
        progress.progress(
            (done_count - 1) / max(len(todo), 1),
            text=f"실적 자료를 모으는 중... ({done_count}/{len(todo)}) {ticker}",
        )
        started = time.monotonic()
        try:
            bundles[ticker] = load_one_ticker(ticker, use_cache)
        except Exception as exc:
            bundles[ticker] = pipeline.empty_bundle(
                ticker, f"{type(exc).__name__}: {str(exc)[:120]}"
            )
        # 진단용: 종목별 수집에 걸린 시간을 기록합니다 (어디서 막히는지 추적)
        report = (bundles.get(ticker) or {}).get("report")
        if isinstance(report, dict):
            report["seconds"] = round(time.monotonic() - started, 1)
    progress.empty()

    return pipeline.assemble(tickers, price_map, weekly_map, bundles, failed)


def clear_all_caches():
    """저장된 자료를 모두 비웁니다 (새로고침 버튼용)."""
    load_one_ticker.clear()
    _price_store.clear()


# ---------------------------------------------------------------------------
# 종목 목록 관리 — 주소(URL)에 저장해 다음에 열어도 유지됩니다
# ---------------------------------------------------------------------------
import re as _re

# 티커로 인정하는 형식: 영문 대문자·숫자·점·하이픈 1~10자 (예: BRK.B, MOG-A)
# 주소(URL)는 누구나 편집할 수 있으므로 이상한 값이 목록에 들어오지 않게 거릅니다.
_TICKER_RE = _re.compile(r"^[A-Z0-9.\-]{1,10}$")


def read_tickers_from_url() -> list[str]:
    """종목 목록을 정합니다: 주소(URL) → 저장 파일 → 기본 목록 순서."""
    raw = st.query_params.get("tickers", "")
    if raw:
        tickers = []
        for piece in str(raw).replace(" ", "").split(","):
            symbol = piece.strip().upper()
            if symbol and _TICKER_RE.match(symbol) and symbol not in tickers:
                tickers.append(symbol)
            if len(tickers) >= cfg.MAX_TICKERS:   # 주소로 상한을 우회하지 못하게
                break
        if tickers:
            return tickers

    # 주소에 없으면 저장 버튼으로 남긴 목록을 찾습니다
    saved = storage.load_saved_tickers()
    if saved:
        return saved[: cfg.MAX_TICKERS]

    return list(cfg.TICKERS)


def write_tickers_to_url(tickers: list[str]) -> None:
    """현재 종목 목록을 주소에 저장합니다 (홈 화면에 추가해두면 유지됩니다)."""
    if list(tickers) == list(cfg.TICKERS):
        st.query_params.pop("tickers", None)   # 기본 목록이면 주소를 깔끔하게 유지
    else:
        st.query_params["tickers"] = ",".join(tickers)


@st.cache_data(ttl=86400, show_spinner=False)
def ticker_exists(symbol: str) -> bool:
    """야후 파이낸스에 실제로 있는 티커인지 확인합니다 (오타 방지)."""
    try:
        import yfinance as yf

        history = yf.Ticker(symbol).history(period="5d")
        return history is not None and not history.empty
    except Exception:
        return False


def render_explain_buttons(detail: dict, key_prefix: str = "") -> None:
    """그 종목의 계산 근거 버튼 묶음을 그립니다 (누르면 계산 과정이 펼쳐집니다).

    순위 표 아래와 종목 상세, 두 곳에서 같은 버튼을 씁니다.
    표에서 행을 눌렀을 때 한참 아래까지 내려가지 않아도 근거를 볼 수 있게 하기 위함입니다.
    """
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
        with st.popover("⚡ 델타 계산·예측 근거", width="stretch"):
            render_explanation(explain.explain_delta(detail))
    with row2[2]:
        with st.popover(f"📈 기술 점수: {detail['tech_score']:.0f}점", width="stretch"):
            render_explanation(explain.explain_technical(detail))

    row3 = st.columns(3)
    with row3[0]:
        with st.popover(f"🎯 신뢰도: {detail['confidence']:.0f}%", width="stretch"):
            render_explanation(explain.explain_confidence(detail))
    with row3[1]:
        with st.popover(f"🔄 국면: {detail.get('phase', '-')}", width="stretch"):
            render_explanation(explain.explain_phase(detail))


# ---------------------------------------------------------------------------
# 종목 관리 (추가 · 삭제 · 저장)
# ---------------------------------------------------------------------------
# 현재 종목 목록 (주소에서 읽어옵니다)
if "tickers" not in st.session_state:
    st.session_state.tickers = read_tickers_from_url()


def render_ticker_manager() -> None:
    """메인 화면 맨 위에 종목 관리 칸을 그립니다.

    사이드바(서브 메뉴) 안에 있으면 모바일에서 한 번 더 들어가야 해서,
    순위 표 바로 위 메인 화면에 그대로 펼쳐 둡니다.
    """
    st.markdown("##### 📌 종목 관리")

    # 추가 처리는 반드시 '콜백'에서 해야 합니다.
    # 본문에서 입력칸을 비우면 Streamlit이 "위젯을 만든 뒤에는 못 바꾼다"며 앱을 죽입니다.
    # 콜백은 위젯을 그리기 전에 실행되므로 여기서만 입력칸을 비울 수 있습니다.
    def _do_add() -> None:
        symbol = str(st.session_state.get("new_ticker_input", "")).strip().upper()
        st.session_state.new_ticker_input = ""     # 추가하면 입력칸을 비웁니다
        if not symbol:
            return
        if symbol in st.session_state.tickers:
            st.session_state.add_message = ("warning", f"{symbol}은(는) 이미 목록에 있습니다")
        elif len(st.session_state.tickers) >= cfg.MAX_TICKERS:
            st.session_state.add_message = (
                "error",
                f"종목은 최대 {cfg.MAX_TICKERS}개까지입니다. "
                "수집 시간이 길어지고 야후가 요청을 거부할 수 있어 제한을 둡니다.",
            )
        elif not ticker_exists(symbol):
            st.session_state.add_message = (
                "error", f"{symbol}을(를) 찾을 수 없습니다. 티커를 다시 확인해 주세요"
            )
        else:
            st.session_state.tickers.append(symbol)
            st.session_state.last_added = symbol   # 추가 직후 수집 결과를 보여주기 위해
            write_tickers_to_url(st.session_state.tickers)

    # --- 종목 추가 ---
    add_cols = st.columns([3, 1])
    with add_cols[0]:
        new_symbol = st.text_input(
            "티커 입력",
            placeholder="예: NVDA",
            key="new_ticker_input",
            label_visibility="collapsed",
            help="미국 주식 티커를 넣으면 기존 종목과 완전히 같은 기준으로 비교됩니다.",
        ).strip().upper()
    with add_cols[1]:
        st.button("➕ 추가", width="stretch", disabled=not new_symbol, on_click=_do_add)

    message = st.session_state.pop("add_message", None)
    if message:
        {"warning": st.warning, "error": st.error}.get(message[0], st.info)(message[1])

    # --- 현재 목록 + 삭제 ---
    # 모바일에서 세로로 길게 늘어지지 않도록 가로로 감기는 알약(pills) 모양으로 그립니다.
    # (버튼 13개를 세로로 쌓으면 순위 표까지 한참 내려야 합니다)
    only_one = len(st.session_state.tickers) <= 1
    st.caption(
        f"현재 {len(st.session_state.tickers)}개 — "
        + ("종목이 하나뿐이라 삭제할 수 없습니다" if only_one else "종목을 눌러 고른 뒤 삭제 버튼을 누르세요")
    )
    # 누르는 즉시 지워지면 실수로 종목을 잃습니다. 여기서는 '고르기'만 하고,
    # 실제 삭제는 아래 삭제 버튼을 눌러야 일어납니다.
    picked = st.pills(
        "종목 고르기",
        options=list(st.session_state.tickers),
        selection_mode="single",
        default=None,
        disabled=only_one,
        # 목록이 바뀌면 키도 바뀌게 해서 지운 종목이 선택된 채로 남지 않게 합니다
        key="del_pills_" + "_".join(st.session_state.tickers),
        label_visibility="collapsed",
    )

    if st.button(
        f"🗑️ {picked} 삭제" if picked else "🗑️ 선택 종목 삭제",
        width="stretch",
        disabled=only_one or not picked,
    ):
        st.session_state.tickers.remove(picked)
        write_tickers_to_url(st.session_state.tickers)
        st.rerun()

    # --- 저장 / 되돌리기 ---
    is_custom = st.session_state.tickers != list(cfg.TICKERS)
    save_cols = st.columns([1, 1]) if is_custom else st.columns([1])
    with save_cols[0]:
        save_clicked = st.button("💾 이 목록 저장", width="stretch")
    reset_clicked = False
    if is_custom:
        with save_cols[1]:
            reset_clicked = st.button("↩️ 기본 목록", width="stretch")

    if reset_clicked:
        st.session_state.tickers = list(cfg.TICKERS)
        write_tickers_to_url(st.session_state.tickers)
        # 저장 파일도 기본 목록으로 덮어써야 다음에 켰을 때 진짜로 되돌아갑니다
        storage.save_tickers_local(cfg.TICKERS)
        st.rerun()

    if save_clicked:
        write_tickers_to_url(st.session_state.tickers)
        local_ok = storage.save_tickers_local(st.session_state.tickers)

        # GitHub 토큰이 Secrets에 있으면 저장소에도 커밋 (완전 영구)
        try:
            token = str(st.secrets.get("GITHUB_TOKEN", "")).strip()
            repo = str(st.secrets.get("GITHUB_REPO", "j2h5516-star/Model")).strip()
        except Exception:
            token, repo = "", ""
        if token:
            ok, github_message = storage.commit_tickers_github(
                st.session_state.tickers, token, repo
            )
            (st.success if ok else st.warning)(github_message)
            if ok:
                st.caption(
                    "ℹ️ 저장소에 커밋되면 앱이 1~2분간 자동 재시작될 수 있습니다. "
                    "재시작 후에도 목록은 그대로 유지됩니다."
                )

        if local_ok:
            st.success("저장했습니다. 앱을 다시 켜도 이 목록으로 시작합니다.")
        else:
            st.warning("서버 파일 저장에 실패했습니다. 주소(URL) 저장은 유지됩니다.")
        if not token:
            st.caption(
                "🔒 코드를 새로 올려도 유지되는 **완전 영구 저장**을 원하시면 "
                "Settings → Secrets 에 `GITHUB_TOKEN` 을 넣어 주세요 (README 참고)."
            )

    # 안내 문구는 짧게. 모바일에서 이 칸이 길어지면 순위·점수가 첫 화면에서 밀려납니다.
    st.caption("💡 주소를 홈 화면에 추가해두면 이 목록이 유지됩니다.")
    st.divider()


# ---------------------------------------------------------------------------
# 사이드바 — 설정만 (종목 관리는 메인 화면으로 옮겼습니다)
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

    show_full_history = st.checkbox(
        "전체 이력 보기 (3년)",
        value=False,
        help=(
            f"기본은 {cfg.DISPLAY_START_DATE[:4]}년부터 보여줍니다. "
            "체크하면 수집한 3년치를 모두 표시합니다."
        ),
    )

    if st.button("🔄 데이터 새로고침", width="stretch"):
        clear_all_caches()
        st.rerun()

    st.caption(
        "새로고침을 누르면 저장된 데이터를 지우고 SEC·야후에서 다시 받아옵니다. "
        "종목이 많아 1~3분 걸릴 수 있습니다."
    )
    st.caption(f"비교 지수: **{cfg.BENCHMARK}**")


# ---------------------------------------------------------------------------
# 헤더 + 데이터 로드
# ---------------------------------------------------------------------------
# 제목과 설명은 짧게 둡니다.
# 모바일(폭 412px)에서 제목이 두 줄로 넘어가고 설명이 세 줄이면,
# 그 아래 종목 관리 칸까지 더해져 점수·순위가 첫 화면에서 통째로 밀려납니다.
st.markdown("### 📊 추세추종 대시보드")
st.markdown(
    '<div class="section-note">실적(논갭 영업이익) + 주가 추세를 합쳐 종합 점수를 냅니다. '
    "<b>항목을 누르면 계산 과정을 볼 수 있습니다.</b></div>",
    unsafe_allow_html=True,
)

# 종목 추가/삭제 칸 — 순위 표 위 메인 화면에 바로 보이게 둡니다
render_ticker_manager()

with st.spinner("SEC 공시와 주가 데이터를 모으는 중입니다... (첫 실행은 1~3분 걸릴 수 있습니다)"):
    try:
        result = load_all(st.session_state.tickers, include_current, True)
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
    st.warning(
        f"주가 데이터를 받지 못한 종목: {', '.join(result['failed'])} — "
        "야후가 일시적으로 거부했을 수 있습니다. 잠시 후 🔄 새로고침을 눌러 주세요."
    )
if result["no_fundamentals"]:
    missing = result["no_fundamentals"]
    # 주가를 못 받아 아예 분석에서 빠진 종목이 있을 수 있으므로,
    # '전체'의 기준은 화면에 점수가 나온 종목(scores) 입니다.
    #
    # 13종목 중 12종목이 비어도 파란 안내만 뜨면 사고를 놓칩니다.
    # 대부분(70% 이상, 최소 2종목)이 비면 SEC 수집이 망가진 것으로 보고 크게 알립니다.
    mostly_missing = len(missing) >= max(2, len(scores) * 0.7)

    if mostly_missing:
        # 전 종목 실적이 비면 대시보드의 절반(펀더멘털)이 통째로 죽은 상태입니다.
        # 접힌 진단 패널에 묻히지 않도록 맨 위에 크게 알리고 원인을 바로 보여줍니다.
        first_errors = [
            f"**{r.get('ticker')}**: {r.get('first_error')}"
            for r in (result.get("reports") or [])
            if r.get("first_error")
        ]
        scope = (
            "모든 종목의"
            if len(missing) >= len(scores)
            else f"대부분의 종목({len(missing)}/{len(scores)})에서"
        )
        st.error(
            f"🚨 **{scope} 실적 자료를 가져오지 못했습니다.**\n\n"
            "이 종목들의 점수는 **주가 추세만 반영된 값**이라 원래 모델과 다르며, "
            f"판정은 `{cfg.V_NO_DATA}`으로 표시되고 순위표 맨 뒤로 밀려 있습니다.\n\n"
            "가장 흔한 원인은 SEC가 요청을 거부한 경우입니다. "
            "아래 **🔧 데이터 수집 진단**을 펼쳐 `첫 오류` 열을 확인해 주세요."
            + ("\n\n첫 오류:\n\n- " + "\n- ".join(first_errors[:3]) if first_errors else "")
        )
    else:
        st.info(
            f"실적 데이터를 찾지 못해 주가 지표만 반영된 종목: {', '.join(missing)}"
        )

# 방금 추가한 종목의 수집 결과를 바로 보여줍니다 (데이터 없이 등록만 되는 것 방지)
last_added = st.session_state.pop("last_added", None)
if last_added:
    added_report = next(
        (r for r in (result.get("reports") or []) if r.get("ticker") == last_added), None
    )
    if last_added in result["failed"]:
        st.error(f"➕ {last_added}: 주가를 받지 못했습니다. 🔄 새로고침으로 다시 시도해 주세요.")
    elif added_report is None or last_added not in scores:
        st.warning(f"➕ {last_added}: 수집 결과를 확인하지 못했습니다. 아래 진단 패널을 봐 주세요.")
    else:
        quarters_count = len(scores[last_added]["quarters"])
        direct_count = added_report.get("merged_direct", 0)
        if quarters_count > 0:
            st.success(
                f"➕ {last_added} 추가 완료 — 분기 {quarters_count}개 수집 "
                f"(공시 기반 {direct_count}개), 신뢰도 {scores[last_added]['confidence']:.0f}%. "
                "다른 종목과 완전히 같은 조건으로 비교됩니다."
            )
        else:
            st.warning(
                f"➕ {last_added}: 주가는 받았지만 SEC 실적을 찾지 못했습니다 "
                "(신규 상장이거나 공시 형식이 특이할 수 있습니다). "
                "기술 점수만 반영되며, 아래 🔧 진단 패널에서 원인을 볼 수 있습니다."
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

    # 데이터 신뢰도 게이지 (점수와는 별개인 참고 지표)
    conf = score["confidence"]
    conf_color = (
        "#22c55e" if conf >= cfg.CONF_HIGH else "#eab308" if conf >= cfg.CONF_MID else "#6b7280"
    )

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
  <div class="conf-row">
    <div class="conf-bar"><div class="conf-fill" style="width:{conf:.0f}%;background:{conf_color};"></div></div>
    <span class="conf-text" style="color:{conf_color};">신뢰도 {conf:.0f}%</span>
  </div>
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


def _style_confidence(value):
    """신뢰도: 80%+ 초록 / 55~80% 노랑 / 그 미만 회색"""
    if pd.isna(value):
        return ""
    if value >= cfg.CONF_HIGH:
        return "color:#22c55e; font-weight:700"
    if value >= cfg.CONF_MID:
        return "color:#eab308"
    return "color:#9ca3af"


def _style_delta(value):
    """델타 방향·예측: 가속 계열 초록, 감속 계열 빨강, 혼조 노랑"""
    text = str(value)
    # '약한' 판정은 흐리게 — 근거가 절반이면 화면에서도 절반으로 보여야 합니다
    if text.startswith("약한 가속"):
        return "color:#86efac"
    if text.startswith("약한 감속"):
        return "color:#fca5a5"
    if "가속" in text and "둔화" not in text:
        return "color:#22c55e; font-weight:700"
    if "반등" in text:
        return "color:#22c55e"
    if "감속" in text or "둔화" in text:
        return "color:#ef4444"
    if "혼조" in text:
        return "color:#eab308"
    return "color:#9ca3af"


def _style_phase(value):
    """국면: 바닥 통과·신고점은 초록, 고점 이탈은 빨강, 중간 자리는 회색"""
    text = str(value)
    if "턴어라운드" in text or "최고" in text:
        return "color:#22c55e; font-weight:700"
    if "고점 이탈" in text or "적자 지속" in text:
        return "color:#ef4444"
    return "color:#9ca3af"


styled = (
    ranking.style.map(_style_verdict, subset=["판정"])
    .map(_style_score, subset=["최종점수", "펀더", "기술"])
    .map(_style_confidence, subset=["신뢰도"])
    .map(_style_phase, subset=["국면"])
    .map(_style_delta, subset=["델타방향", "델타예측(1분기후)", "델타예측(2분기후)"])
    .map(_style_rs, subset=["RS"])
    .format(
        {
            "주가($)": "{:,.2f}",
            "최종점수": "{:.0f}", "펀더": "{:.0f}", "기술": "{:.0f}",
            "신뢰도": "{:.0f}%", "RS": "{:+.1f}%p",
        },
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

if picked_from_table and picked_from_table in scores:
    st.markdown(
        f'<div class="section-note">👇 <b>{picked_from_table}</b> 의 계산 근거 — '
        "아래 버튼을 누르면 그 항목을 어떻게 계산했는지 바로 볼 수 있습니다.</div>",
        unsafe_allow_html=True,
    )
    render_explain_buttons(scores[picked_from_table], key_prefix="table")

st.markdown(
    '<div class="section-note">👆 <b>표의 행을 누르면</b> 계산 근거 버튼이 바로 아래에 나오고, \'종목 상세\'도 그 종목으로 바뀝니다 · '
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

# 종목명을 점 옆에 쓰면 종목이 많을 때 글자끼리 겹칩니다.
# 그래서 점 안에는 "순위 번호"만 넣고, 종목명은 그래프 아래 범례로 뺍니다.
matrix.add_trace(
    go.Scatter(
        x=[scores[t]["tech_score"] for t in ordered_tickers],
        y=[scores[t]["fund_score"] for t in ordered_tickers],
        mode="markers+text",
        text=[str(i) for i in range(1, len(ordered_tickers) + 1)],
        textposition="middle center",
        textfont=dict(size=8, color="#0e1117", family="Arial Black"),
        marker=dict(
            # 점수가 비슷한 종목끼리 점이 겹칠 수 있어, 크기를 작게 하고
            # 진한 테두리를 둘러 서로 구분되게 합니다
            size=18,
            color=[cfg.VERDICT_COLORS.get(scores[t]["verdict"], "#6b7280") for t in ordered_tickers],
            line=dict(width=1.5, color="#0e1117"),
            opacity=0.94,
        ),
        customdata=[
            [t, scores[t]["verdict"], scores[t]["final_score"]] for t in ordered_tickers
        ],
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>기술 %{x:.0f} · 펀더 %{y:.0f}"
            "<br>최종 %{customdata[2]:.0f} · %{customdata[1]}<extra></extra>"
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

# 번호 ↔ 종목명 범례 (순위 표와 같은 순서라 눈으로 연결하기 쉽습니다)
legend_items = []
for number, ticker in enumerate(ordered_tickers, start=1):
    color = cfg.VERDICT_COLORS.get(scores[ticker]["verdict"], "#6b7280")
    legend_items.append(
        f'<span class="legend-item">'
        f'<span class="legend-no" style="background:{color};">{number}</span>{ticker}</span>'
    )
st.markdown(
    '<div class="legend-grid">' + "".join(legend_items) + "</div>",
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="section-note">번호는 순위 표와 같은 순서입니다 · 점을 누르면 종목명이 나옵니다</div>',
    unsafe_allow_html=True,
)


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

# --- 델타(이익 증가 속도) 한 줄 요약: 현재 방향 · 다음 분기 · 2분기 경로 ---
# 순위 표의 '델타방향 / 델타예측(1분기후) / 델타예측(2분기후)' 세 열과 같은 값입니다.
forecast_detail = detail.get("forecast_detail") or {}
next_qoq = forecast_detail.get("next_qoq")
d1, d2, d3 = st.columns(3)
d1.metric("델타 방향 (현재)", detail["delta_direction"], "실제 실적 기준", delta_color="off")
d2.metric(
    "델타예측 (1분기 후)",
    detail.get("delta_forecast") or cfg.F_NONE,
    "예상 증가율 -" if next_qoq is None else f"예상 증가율 {next_qoq:+.1f}%",
    delta_color="off",
)
next2_qoq = forecast_detail.get("next2_qoq")
if next2_qoq is None:
    # 다다음 분기 자료가 없으면 2분기 경로를 판정하지 않습니다 (없는 근거를 있는 척하지 않기)
    accel_note = "다다음 분기 자료 없음"
else:
    # 근거는 '다음 분기 근거 + 다다음 분기 근거'입니다.
    # (예전에는 다음 분기 근거만 적어 신뢰도를 실제보다 높게 보이게 했습니다)
    accel_note = (
        f"{detail.get('forward_basis') or '-'} + {forecast_detail.get('basis_2') or '-'}"
    )
d3.metric(
    "델타예측 (2분기 후)",
    detail.get("accel_forecast") or cfg.F2_NONE,
    accel_note,
    delta_color="off",
)

# 판정 기준이 특별한 경우 그 사실을 먼저 알립니다
_delta_trace = (detail["fundamental"]["delta"].get("trace") or {})
if _delta_trace.get("seasonal"):
    st.info(
        "🗓️ 이 종목은 **분기마다 실적이 오르내리는 계절 사업**으로 판정됐습니다. "
        "전분기와 비교하면 방향이 계속 뒤집히므로, **작년 같은 분기와 비교**해 판정했습니다.",
        icon="🗓️",
    )
if _delta_trace.get("anomaly"):
    st.warning(
        "⚠️ 최근 분기 중 이웃 분기보다 유별나게 튄 곳이 있습니다 "
        "(일회성 이익이나 인수합병일 수 있습니다). "
        "그 숫자로 방향을 말하면 다음 분기에 뒤집히므로 **판정을 미뤘습니다.**"
    )

# --- 눌러서 보는 상세 설명 버튼들 ---
st.markdown(
    '<div class="section-note">🔎 아래 버튼을 누르면 <b>그 항목이 어떻게 계산됐는지</b> '
    "계산식·실제 숫자·자료 출처·지난 이력·앞으로의 예측을 볼 수 있습니다.</div>",
    unsafe_allow_html=True,
)

render_explain_buttons(detail, key_prefix="detail")

# --- 점수 구성 요약 ---
fundamental = detail["fundamental"]
technical = detail["technical"]

with st.expander("🔍 점수 전체 구성 한눈에 보기"):
    st.markdown(f"**펀더멘털 {detail['fund_score']:.0f}점**")
    st.markdown(
        f"- **델타 가속** {fundamental['delta']['score']:.0f}/{cfg.W_DELTA_ACCEL}점 — "
        f"{fundamental['delta']['detail']}\n"
        f"- **국면** {fundamental['phase']['score']:.0f}/{cfg.W_PHASE}점 — "
        f"{fundamental['phase']['phase']} · {fundamental['phase']['detail']}\n"
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
# 기본은 2025년부터, 사이드바에서 '전체 이력 보기'를 켜면 수집한 3년치 전부
quarters_df = pipeline.quarters_to_frame(
    detail["quarters"],
    start_date=None if show_full_history else cfg.DISPLAY_START_DATE,
)

if quarters_df.empty:
    st.info(
        f"{selected}의 분기 실적 데이터를 SEC에서 찾지 못했습니다. "
        "주가 지표(기술 점수)만 반영된 상태입니다."
    )
else:
    labels = quarters_df["period_label"].tolist()
    op_values = quarters_df["op_income"].tolist()

    # 미래 구간: 다음 분기(가이던스/추정) + 다다음 분기(컨센서스)
    forward_op = detail["forward_op_income"]
    forward_op_2 = detail.get("forward_op_income_2")
    future_labels = []
    future_values = []
    if forward_op is not None:
        future_labels.append("다음(전망)")
        future_values.append(forward_op)
        if forward_op_2 is not None:
            future_labels.append("다다음(전망)")
            future_values.append(forward_op_2)

    # 전망 막대가 실적 막대를 짓눌러 화면에서 지워버리지 않게 합니다.
    # (전망이 실적의 4억 배로 계산돼 y축이 늘어나는 바람에 실제 실적 막대가
    #  픽셀 0이 되어 "그래프가 통째로 없다"는 문제가 있었습니다)
    # 기준은 '가장 최근 분기 영업이익' — 전망 검사(check_forward)와 같은 기준선입니다.
    latest_op = next((v for v in reversed(op_values) if v is not None and v > 0), None)
    dropped_future = 0
    if latest_op and future_labels:
        # ⚠️ 개별로 버리면 안 됩니다. 다음 분기를 버리고 다다음 분기만 남기면
        #    같은 x칸에 다른 분기의 값이 그려져 점선과 막대가 어긋납니다.
        #    다음 분기가 탈락하면 그것을 기준으로 만든 다다음 분기도 함께 버립니다.
        cut = len(future_values)
        for index, value in enumerate(future_values):
            if value is not None and abs(value) > latest_op * cfg.CHART_FORWARD_MAX_RATIO:
                cut = index
                break
        dropped_future = len(future_values) - cut
        future_labels, future_values = future_labels[:cut], future_values[:cut]

    if future_labels:
        labels = labels + future_labels
        forward_bar = [None] * len(op_values) + future_values
        op_bar = op_values + [None] * len(future_labels)
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
            qoq_values = qoq_values + [None] * len(future_labels)
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

        # 미래 증가율 경로를 점선으로 이어 그립니다 (델타가속예측의 시각화)
        forecast_info = detail.get("forecast_detail") or {}
        next_qoq = forecast_info.get("next_qoq")
        next2_qoq = forecast_info.get("next2_qoq")
        # None뿐 아니라 NaN(pandas 빈 값)도 걸러야 점선이 정상적으로 그려집니다
        last_actual_qoq = next(
            (v for v in reversed(qoq_values) if v is not None and pd.notna(v)), None
        )
        if next_qoq is not None and last_actual_qoq is not None and future_labels:
            last_actual_label = quarters_df["period_label"].tolist()[-1]
            # 막대에서 살아남은 분기만큼만 점선을 그립니다 (막대와 칸이 어긋나지 않게)
            future_x = [last_actual_label, future_labels[0]]
            future_y = [last_actual_qoq, next_qoq]
            if next2_qoq is not None and len(future_labels) >= 2:
                future_x.append(future_labels[1])
                future_y.append(next2_qoq)
            fig.add_trace(
                go.Scatter(
                    x=future_x,
                    y=future_y,
                    name="증가율 예측 경로",
                    mode="lines+markers",
                    line=dict(color="#f59e0b", width=2, dash="dot"),
                    marker=dict(size=7, symbol="diamond-open"),
                    hovertemplate="%{x}<br>예상 QoQ %{y:+.1f}%<extra></extra>",
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

    if dropped_future:
        st.caption(
            f"⚠️ 다음 분기부터 전망 {dropped_future}개는 실적보다 지나치게 커서 그리지 않았습니다 "
            "(계산이 깨진 값일 가능성이 큽니다 — 아래 진단을 확인해 주세요)"
        )

    st.markdown(
        '<div class="chart-note">초록 막대가 이익 크기, 주황 실선이 "전분기 대비 얼마나 빨리 늘고 있는가"입니다. '
        "주황 <b>점선</b>은 다음·다다음 분기의 <b>예상 경로</b>(가이던스·월가 컨센서스 기반 추정)입니다. "
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
# ⑤ 데이터 신뢰도 등급표
# ---------------------------------------------------------------------------
st.divider()
with st.expander("📚 데이터 신뢰도 등급표 — 어떤 종류가 있고 얼마나 믿을 만한가"):
    st.markdown(
        "숫자를 **어떻게 구했는지**에 따라 신뢰도가 다릅니다. "
        "회사가 직접 발표한 값일수록 높고, 계산으로 만든 근사치일수록 낮습니다."
    )
    st.dataframe(
        pd.DataFrame(explain.confidence_tier_table()),
        hide_index=True,
        width="stretch",
    )
    st.markdown(
        f"""
**종합 신뢰도 계산**

`실적 신뢰도 × {cfg.CONF_WEIGHT_ACTUAL} + 전망 신뢰도 × {cfg.CONF_WEIGHT_FORWARD}`

- 실적 신뢰도는 분기별 출처의 **가중평균**입니다. 최근 분기일수록 큰 가중치를 줍니다
  (한 분기 과거로 갈 때마다 × {cfg.CONF_RECENCY_DECAY}).
- 신뢰도는 **점수에 반영하지 않습니다.** 순위는 그대로 두고 참고용으로만 표시합니다.

> ⚠️ 이 퍼센티지는 자료의 성격을 고려해 정한 값이며 **통계적으로 검증된 수치가 아닙니다.**
> `config.py`의 `CONFIDENCE_TIERS`에서 조정할 수 있습니다.
        """
    )

# ---------------------------------------------------------------------------
# ⑥ 데이터 수집 진단 (8-K 파싱이 어디서 막히는지 확인)
# ---------------------------------------------------------------------------
with st.expander("🔧 데이터 수집 진단 — 실적 자료를 어디까지 가져왔나"):
    st.markdown(
        "SEC에서 자료를 모으는 각 단계에서 몇 건이 통과했는지 보여줍니다. "
        "`직접공시`가 나오지 않을 때 **어느 단계에서 막히는지** 확인할 수 있습니다."
    )

    # SEC는 요청자 신원에 이메일이 없으면 접속을 막습니다. 지금 무엇을 쓰고 있는지 표시합니다.
    _identity = cfg.get_sec_identity()
    _typed = os.environ.get("SEC_IDENTITY", "").strip()   # Secrets에 직접 넣은 값
    _local, _, _domain = _identity.rpartition("@")
    _masked = f"{_local[:3]}***@{_domain}" if _domain else _identity
    _source = "Secrets에 넣으신 값" if _typed and cfg._has_email(_typed) else "기본값"
    st.caption(
        f"SEC 요청자 신원: `{_masked}` ({_source}) — "
        "신원을 바꾸면 이미 열려 있는 접속에는 앱이 다시 시작된 뒤부터 적용됩니다."
    )

    if _typed and not cfg._has_email(_typed):
        # 조용히 기본값으로 바꿔치기하면, 설정을 고쳤는데도 안 된다고 오해하게 됩니다
        st.warning(
            f"Secrets의 `SEC_IDENTITY` 값(`{_typed}`)에 **이메일 주소가 없어** 기본값으로 대신했습니다. "
            "SEC는 이메일이 없는 요청을 차단하기 때문입니다. "
            '`SEC_IDENTITY = "홍길동 mymail@example.com"` 처럼 실제 이메일을 함께 넣어 주세요.'
        )

    reports = result.get("reports") or []
    if not reports:
        st.caption("진단 기록이 없습니다.")
    else:
        # 표에는 '숫자'만 둡니다.
        # 긴 문장을 표 칸에 넣으면 모바일에서 양쪽이 잘려 읽을 수가 없습니다.
        # 문장은 아래 종목별 '자세히'를 눌러 펼쳐 보게 합니다.
        diag = pd.DataFrame(
            [
                {
                    "종목": r.get("ticker", "-"),
                    "XBRL분기": r.get("xbrl_quarters", 0),
                    "8-K찾음": r.get("filings_found", 0),
                    "본문확보": r.get("text_ok", 0),
                    "실적판별": r.get("gate_passed", 0),
                    "숫자추출": r.get("parsed_ok", 0),
                    "공시반영": r.get("merged_direct", 0),
                    "짝못찾음": r.get("unpaired_press", 0),
                    "소요(초)": r.get("seconds", "-"),
                    "문제": "있음" if (r.get("first_error") or r.get("pair_note")) else "-",
                }
                for r in reports
            ]
        )
        st.dataframe(diag, hide_index=True, width="stretch")

        st.caption("👇 문장으로 된 기록은 아래에서 종목을 눌러 펼쳐 보세요 (표에서는 글자가 잘립니다)")
        for r in reports:
            ticker = r.get("ticker", "-")
            has_problem = bool(r.get("first_error") or r.get("pair_note"))
            with st.expander(f"{'⚠️ ' if has_problem else ''}{ticker} 자세히"):
                lines = [
                    f"- **텍스트 출처**: {r.get('text_source') or '-'}",
                    f"- **전망 수집**: {r.get('forward_note') or '정상'}",
                    f"- **수집 소요**: {r.get('seconds', '-')}초",
                ]
                if r.get("first_error"):
                    lines.append(f"- **첫 오류**: `{r['first_error']}`")
                if r.get("pair_note"):
                    lines.append(f"- **짝짓기 기록**: {r['pair_note']}")
                if r.get("note"):
                    lines.append(f"- **비고**: {r['note']}")
                # 숫자 검사 단계에서 고치거나 뺀 내역
                notes = r.get("quality_notes") or []
                if notes:
                    lines.append(
                        f"- **숫자 검사**: 보정 {r.get('quality_fixed', 0)}건 · "
                        f"제외 {r.get('quality_dropped', 0)}건"
                    )
                    lines.extend(f"    - {note}" for note in notes[:8])
                st.markdown("\n".join(lines))

        st.markdown(
            """
**읽는 법**

| 어디서 0이 되는가 | 무슨 뜻인가 |
|---|---|
| `8-K찾음`이 0 | SEC에서 8-K 공시 자체를 못 찾음 (접속 문제 또는 티커 문제) |
| `본문확보`가 0 | 공시는 찾았지만 보도자료 텍스트를 못 읽음 (첨부 형식 문제) |
| `실적판별`이 0 | 텍스트는 읽었지만 실적발표로 인식하지 못함 |
| `숫자추출`이 0 | 실적발표는 맞지만 숫자 표기를 못 읽음 (파싱 규칙 보강 필요) |
| `공시반영`이 0 | 숫자는 뽑았지만 분기 짝짓기에 실패 |

`첫 오류` 열에 내용이 있으면 그 메시지를 알려주시면 정확히 조준해서 고칠 수 있습니다.
            """
        )

# ---------------------------------------------------------------------------
# ⑥-2 백테스트 — 이 판정이 과거에는 얼마나 맞았나
# ---------------------------------------------------------------------------
with st.expander("🎯 백테스트 — 이 판정이 과거에는 얼마나 맞았나"):
    st.markdown(
        "각 시점에서 **그때까지의 자료만** 가지고 판정을 내린 뒤, 실제 다음 분기와 맞춰 봅니다. "
        "미래를 미리 보지 않도록 코드로 막아 두었습니다."
    )
    st.markdown(
        "**두 가지 잣대로 잽니다.**\n\n"
        f"- **엄격**: 모델이 스스로 정한 문턱({cfg.DELTA_THRESHOLD_PP:.0f}%p)만큼 실제로 움직였는가\n"
        "- **방향**: 문턱까지는 아니어도 주장한 방향으로 움직였는가"
    )

    def _render_backtest(outcome: dict, caption: str) -> None:
        total = outcome["전체"]
        if not total["판정한 시점"]:
            st.caption("되짚어 볼 자료가 부족합니다.")
            return

        low, high = total["적중률 95%구간"]
        cols = st.columns(3)
        cols[0].metric("엄격 적중", total["적중(원분수)"], f"95% {low:.0f}~{high:.0f}%",
                       delta_color="off")
        cols[1].metric("방향만 맞춤", f"{total['방향만 맞춘 비율(%)']:.0f}%",
                       "문턱 미만 포함", delta_color="off")
        cols[2].metric("판단 유보", f"{total['유보(혼조·판단불가)']}회",
                       "혼조·판단불가", delta_color="off")

        # 기준선 — 아무 계산도 안 하는 답안보다 나은지 보여줍니다
        base_rows = [
            {"답안": name, "적중률(%)": info["적중률(%)"]}
            for name, info in total["기준선(무계산 답안)"].items()
        ]
        best_base = max((r["적중률(%)"] or 0) for r in base_rows)
        model_rate = total["적중률(%)"] or 0
        st.dataframe(pd.DataFrame(base_rows), hide_index=True, width="stretch")
        if model_rate < best_base:
            st.warning(
                f"⚠️ 엄격 잣대에서는 모델({model_rate:.0f}%)이 "
                f"아무 계산도 하지 않는 답안({best_base:.0f}%)보다 낮습니다. "
                "이 모델은 방향을 **너무 자주 단정하는 경향**이 있다는 뜻입니다 — "
                "실제로는 '유지'인 분기가 많습니다. 판정을 그대로 믿지 마시고, "
                "위 '방향만 맞춤'과 함께 보세요."
            )
        st.caption(caption)

    # 지금 화면에 있는 종목들의 실제 이력으로 되짚어 봅니다
    real_history = {
        ticker: score["quarters"]
        for ticker, score in scores.items()
        if len(score.get("quarters") or []) >= 6
    }

    if real_history:
        st.markdown("#### 지금 이 종목들의 실제 이력")
        outcome = backtest.run(real_history)
        _render_backtest(
            outcome,
            f"표본이 {outcome['전체']['판정한 시점']}회뿐이라 참고용입니다 "
            f"(종목평균 {outcome['전체']['종목평균 적중률(%)']}% · "
            f"독립 종목 {outcome['전체']['독립 종목 수']}개). 분기 6개 이상인 종목만 계산합니다.",
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {"종목": t, "적중": v["적중(원분수)"], "적중률(%)": v["적중률(%)"],
                     "유보": v["유보(혼조·판단불가)"]}
                    for t, v in outcome["종목별"].items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("되짚어 볼 만큼 분기가 모인 종목이 아직 없습니다 (종목당 6분기 이상 필요).")

    st.markdown("#### 설계 점검용 가상 시나리오")
    standard = backtest.run(backtest.standard_scenarios())
    st.dataframe(
        pd.DataFrame(
            [
                {"시나리오": name, "적중": v["적중(원분수)"], "적중률(%)": v["적중률(%)"],
                 "유보": v["유보(혼조·판단불가)"]}
                for name, v in standard["종목별"].items()
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "가상 데이터로 재는 것은 '시장에서 얼마나 맞나'가 아니라 "
        "**'설계한 대로 동작하나'** 입니다. 둘은 다릅니다."
    )


# ---------------------------------------------------------------------------
# ⑦ 지표 해석 가이드
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
