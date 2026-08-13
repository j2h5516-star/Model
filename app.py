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
HYPOTHESIS_LABELS = {
    "H2_신고점": "H2 · TTM 신고점",
    "H2b_신고점_첫돌파": "H2b · 신고점 첫 돌파",
    "H5_실적폭_고정20": "H5 · 실적 폭 ≥20% (고정)",
    "H5b_실적폭_중앙값": "H5b · 실적 폭 > 자기 이력 중앙값",
    "H6_결합_H5bxH2b": "H6 · 결합 (H5b ON × 첫 돌파)",
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
    rows = []
    for ticker in ds["tickers"]:
        states = me.earnings_states(ds["quarters"].get(ticker) or [])
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
def main():
    import streamlit as st

    st.set_page_config(page_title="측정 계기판", layout="centered")
    st.title("측정 계기판")

    verdict = load_json("verdict.json")
    log = load_json("robot_log.json")
    is_v3 = verdict_is_v3(verdict)

    # --- 로봇·데이터 상태 ---
    if log:
        st.caption(f"로봇 마지막 수집: {str(log.get('ran_at', '?'))[:16]} UTC · "
                   f"{log.get('summary', '')}")

    # --- 판정 현황 (가장 위 — 이 프로젝트의 답) ---
    st.subheader("판정 현황")
    adopted = adopted_names(verdict) if is_v3 else []
    if not is_v3:
        st.warning(
            "지금 판정 파일은 v2 유물입니다. 새 판정(11차 등록)은 다음 "
            "로봇 수집 때 자동 계산됩니다 — 그때까지 아래 신호 상태는 "
            "판단 근거가 아닙니다."
        )
    elif adopted:
        st.success("채택된 신호: " + " · ".join(adopted))
    else:
        st.info("채택된 신호 없음 — 어떤 상태도 매수 판단의 근거가 아닙니다.")
    if verdict:
        st.caption(f"판정 계산 시각: {str(verdict.get('computed_at', '?'))[:16]} UTC · "
                   "채택 = 신호 윌슨 하한 > 기준선 상한 (신규 표본, n≥10)")
        for row in verdict_rows(verdict):
            st.markdown(
                f"**{row['가설']}** — {row['판정']}  \n"
                f"신호 {row['신호']} · 기준선 {row['기준선']}"
            )

    # --- 실물 데이터 (사실 표시) ---
    try:
        ds = dataset.build(dataset.load())
    except (FileNotFoundError, ValueError) as exc:
        st.error(f"데이터를 읽지 못했습니다: {exc}")
        return

    st.subheader("위층 — 장세 (실적 폭 게이지)")
    gauge = gauge_now(ds)
    if gauge["value"] is None:
        st.write("게이지 값 없음 (신선한 발표 부족)")
    else:
        h5b_text = {True: "자기 이력 중앙값 위", False: "자기 이력 중앙값 아래",
                    None: "판단 불가 (이력 부족)"}[gauge["h5b"]]
        st.metric("신고점 종목 비율", f"{gauge['value']}%", help="최근 140일 내 "
                  "발표가 신선한 종목 중 TTM 조정 EPS 신고점 상태인 종목의 비율")
        st.write(f"상태: **{h5b_text}** ({gauge['asof']} 기준)")
    st.caption("· H5b — " + hypothesis_note(verdict if is_v3 else None,
                                            "H5b_실적폭_중앙값"))

    st.subheader(f"아래층 — 최근 {RECENT_DAYS}일 발표 종목")
    recent = recent_ticker_rows(ds)
    if not recent:
        st.write("최근 발표 없음")
    else:
        for row in recent:
            ttm = row["TTM 조정EPS"]
            st.markdown(
                f"**{row['종목']}** · {row['발표일']}  \n"
                f"{row['상태']}"
                + (f" · TTM ${ttm}" if ttm is not None else "")
            )
    st.caption("· 첫 돌파(H2b) — " + hypothesis_note(verdict if is_v3 else None,
                                                     "H2b_신고점_첫돌파"))

    # --- 한계 명시 (감추지 않는다) ---
    st.subheader("한계")
    st.caption(
        "· 위 상태들은 사실의 표시일 뿐, 채택되지 않은 신호는 판단·점수·"
        "추천에 쓰지 않습니다.\n"
        "· 조정 EPS 를 발표하지 않는 종목(TXN·TSLA·FSLR 등)은 이 화면에서 "
        "빠지거나 '판단 불가'로 나옵니다 — 없는 값은 없음으로 둡니다.\n"
        "· 같은 시기의 발표들은 같은 장세를 공유하므로 통계 구간은 실제보다 "
        "좁게 나올 수 있습니다."
    )


if __name__ == "__main__":
    main()
