"""
web_build.py — 웹앱(GitHub Pages)용 데이터 빌더 (132차)
========================================================

로봇이 커밋한 측정 데이터를 **웹앱이 바로 읽을 수 있는 작은 JSON**으로
바꿔 docs/data/ 아래에 씁니다. 웹앱은 서버 없이 이 파일만 읽습니다.

경계 (설계도.md 3장을 그대로 따릅니다):
  · 여기서 **새로 계산하지 않습니다** — 계기판(app.py)이 쓰는 것과
    **똑같은 함수**를 불러 씁니다. 두 화면의 숫자가 어긋날 수 없습니다.
  · 없는 값은 null 로 둡니다 (창작 금지). 화면이 "없음"이라 적습니다.
  · 판정·문턱을 만들지 않습니다 — 판정 파일(verdict.json)을 옮겨 적습니다.

내보내는 파일:
  docs/data/app.json           — 첫 화면에 필요한 전부 (수백 KB 이하)
  docs/data/t/<종목>.json      — 종목 상세 (주봉 주가·52주선·완성·실적)

실행: python3 web_build.py            (docs/data 아래로 씁니다)
      python3 web_build.py --check    (쓰지 않고 크기만 알려 줍니다)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

import app                      # 계기판의 순수 함수를 그대로 재사용
import config as cfg
import dataset
import judge
import measure_engine as me
import sector_model as sm
import vendor_compare as vc

WEB_DIR = "docs/data"
TICKER_DIR = f"{WEB_DIR}/t"

# 종목 상세에 실을 주봉 개수 (약 3년). 파일을 작게 유지하려는 **표시용**
# 자르기이며, **측정에는 영향이 없습니다** (측정은 언제나 전체 이력).
# 매일 250종목치를 커밋하므로 저장소가 커지지 않게 최소한만 싣습니다.
DETAIL_WEEKS = 156


def _round(value, digits: int = 2):
    """숫자면 반올림, 아니면 그대로 (None 은 None)."""
    return None if value is None else round(float(value), digits)


def _weekly(prices: dict, weeks: int = DETAIL_WEEKS) -> list[list]:
    """일봉을 주봉(주 마지막 거래일)으로 줄입니다 — [[날짜, 종가], ...]."""
    if not prices or not prices.get("dates"):
        return []
    idx = sm.weekly_indices(prices["dates"])
    rows = [[prices["dates"][i], _round(prices["close"][i], 2)] for i in idx]
    return rows[-weeks:]


def _ma(values: list[float], window: int) -> list[float | None]:
    """단순 이동평균 — 앞쪽 모자란 자리는 None (없는 값은 만들지 않음)."""
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        out.append(_round(total / window, 2) if i >= window - 1 else None)
    return out


def hypothesis_rows(verdict: dict | None) -> list[dict]:
    """판정 파일을 화면용 줄로 — 판정을 만들지 않고 옮겨 적기만 합니다."""
    if not verdict or "가설" not in verdict:
        return []
    # 채택까지 남은 거리 (139·140차). 계기판에만 있고 **웹앱에는 없었습니다**
    # — 주인은 휴대폰만 쓰므로 이 정직화 정보가 정작 보는 화면에 안 닿고
    #   있었습니다(150차-C 실측). 계기판과 같은 함수를 씁니다.
    거리 = {d["가설"]: d for d in judge.adoption_distance(verdict)}
    rows = []
    for name, entry in (verdict.get("가설") or {}).items():
        judged = entry.get("신규(판정)") or {}
        signal = judged.get("신호") or {}
        base = judged.get("기준선") or {}
        탐색 = (entry.get("탐색표본(참고)") or {}).get("신호") or {}
        d = 거리.get(name) or {}
        rows.append({
            "이름": name,
            "라벨": app.HYPOTHESIS_LABELS.get(name, name),
            "설명": app.HYPOTHESIS_DETAILS.get(name),
            "판정": entry.get("판정"),
            "등록일": entry.get("등록일"),
            "신호n": signal.get("n"),
            "신호율": signal.get("rate"),
            "기준선율": base.get("rate"),
            "탐색n": 탐색.get("n"),
            "탐색율": 탐색.get("rate"),
            # 판정을 만들지 않습니다 — 이미 계산된 거리를 옮겨 적을 뿐입니다.
            "채택거리": d.get("상태"),
            "필요표본": d.get("필요표본"),
        })
    순서 = {"채택": 0, "미채택": 2, "판정 불가": 1}
    rows.sort(key=lambda r: (순서.get(r["판정"], 3), r["이름"]))
    return rows


def build_payload(ds: dict, verdict: dict | None, log: dict | None) -> dict:
    """첫 화면에 필요한 모든 값 (app.json 의 내용).

    계기판과 **같은 함수**로 만들기 때문에 두 화면이 다른 숫자를 말할 수
    없습니다. 못 재는 값은 None 으로 두고, 화면이 "없음"이라고 적습니다.
    """
    오늘 = ds["prices"][ds["benchmark"]]["dates"][-1]

    시장 = sm.market_breadth_series(ds)
    폭 = 시장[-1][1] if 시장 else None
    직전폭 = 시장[-2][1] if len(시장) >= 2 else None

    완성 = sm.completion_events(ds)
    종목판 = app.recent_completion_rows(완성, 오늘)
    묶음판 = app.leader_watch_rows(완성, 오늘)
    확인 = app.dedupe_confirmations(
        [r for r in sm.confirmation_rows(ds) if r["확인"]])
    한조건 = app.dedupe_confirmations(
        [r for r in sm.confirmation_rows(ds)
         if not r["확인"] and r["전제"] and (r["정배열확인"] or r["델타확인"])])

    def 구성(묶음: str) -> list[dict]:
        return [
            {"종목": m["종목"], "완성": m["완성"], "이격도": m["이격도"],
             "신호": m["신호"], "델타": m["델타"]}
            for m in app.group_member_rows(묶음, 완성, 오늘)
        ]

    섹터폭 = []
    for row in sm.current_breadth(ds):
        구간 = app.breadth_zone(row["폭"])
        섹터폭.append({
            "섹터": row["섹터"], "폭": _round(row["폭"], 1),
            "직전폭": _round(row["직전폭"], 1), "종목수": row["종목수"],
            "상태": row["상태"], "구간": 구간["zone"],
            "구간실측": 구간["rate"], "구간설명": 구간["note"],
        })

    return {
        "생성": date.today().isoformat(),
        "수집": (log or {}).get("ran_at"),
        "코드": (verdict or {}).get("code_rev"),
        "기준일": 오늘,
        "종목수": len(ds["tickers"]),
        "기간": {"시작": ds["prices"][ds["benchmark"]]["dates"][0], "끝": 오늘},
        "장세": {
            "폭": _round(폭, 1),
            "직전폭": _round(직전폭, 1),
            "시계열": [[d, _round(v, 1)] for d, v in 시장[-104:]],
            "띠": list(judge.H24_BAND),
        },
        "확인신호": [{
            "묶음": r["묶음"],
            "3개월상대": _round(r["3개월상대"], 1),
            "정배열폭": _round(r["정배열폭"], 0),
            "직전정배열폭": _round(r["직전정배열폭"], 0),
            "델타폭": _round(r["델타폭"], 0),
            "직전델타폭": _round(r["직전델타폭"], 0),
            "구성": 구성(r["묶음"]),
        } for r in 확인],
        "한조건": [{
            "묶음": r["묶음"],
            "모자란것": "정배열 미달" if not r["정배열확인"] else "델타 미달",
            "정배열폭": _round(r["정배열폭"], 0),
            "델타폭": _round(r["델타폭"], 0),
            "3개월상대": _round(r["3개월상대"], 0),
        } for r in 한조건],
        "관찰판": [{
            "묶음": g["묶음"], "완성": g["완성"], "신호": g["신호"],
            "델타상승": g["델타상승"], "마지막완성": g["마지막완성"],
            "구성": 구성(g["묶음"]),
        } for g in 묶음판 if g["완성"] >= 2],
        "신호종목": [r for r in 종목판 if r["신호"]],
        "완성전체": 종목판,
        "정배열유지": app.aligned_now_rows(ds),
        "섹터폭": 섹터폭,
        "가설": hypothesis_rows(verdict),
        "건강": (log or {}).get("건강검진"),
        "실측": {
            "완성이격도30_1년": 46.6, "완성기준선_1년": 27.5,
            "완성이격도30_60일": 27.8, "완성기준선_60일": 13.1,
            "이긴비율_1년": 59.0,
            "출처": "126차 백테스트 (창 완료 1,785건) — 채택 전 참고",
        },
    }


def build_ticker(ds: dict, ticker: str, 완성사건: list[dict]) -> dict:
    """종목 상세 — 주봉 주가·52주선·완성 이력·실적 이력."""
    prices = ds["prices"].get(ticker) or {}
    주봉 = _weekly(prices)
    종가 = [row[1] for row in 주봉]
    quarters = ds["quarters"].get(ticker) or []
    잣대 = me.yardstick_of(quarters)
    states = me.earnings_states(quarters, field=잣대) if 잣대 else []
    최근 = {r["종목"]: r for r in app.recent_completion_rows(
        완성사건, ds["prices"][ds["benchmark"]]["dates"][-1])}

    실적 = []
    for s in states[-24:]:
        실적.append({
            "발표일": s["announced"],
            "ttm": _round(s["ttm"], 3),
            "신고점": bool(s["new_high"]),
            "첫돌파": s["newhigh_streak"] == 1,
            "신고점폭": _round(s["신고점폭"], 1),
        })

    이력 = [{
        "완성일": e["day"], "이격도": _round(e["이격도"], 1),
        "델타": e["델타"], "초과60": _round(e["초과60"], 1),
        "초과250": _round(e["초과250"], 1),
    } for e in 완성사건 if e["ticker"] == ticker][-12:]

    return {
        "종목": ticker,
        "섹터": cfg.SECTORS.get(ticker, "미분류"),
        "묶음": cfg.GROUPS.get(ticker, "미분류"),
        "잣대": {"adj_eps": "조정 EPS", "adjusted_ebitda": "조정 EBITDA",
               "gaap_eps": "GAAP EPS"}.get(잣대),
        "주봉": 주봉,
        "ma52": _ma(종가, 52) if len(종가) >= 52 else [],
        "지금정배열": bool(sm.aligned_flags_chart(prices).get(max(sm.aligned_flags_chart(prices)))) if prices and sm.aligned_flags_chart(prices) else None,
        "이격도": _round(sm.gap_over_52w(
            prices, ds["prices"][ds["benchmark"]]["dates"][-1]), 1) if prices else None,
        "신호": bool(최근.get(ticker, {}).get("신호")),
        "완성이력": 이력,
        "실적": 실적,
    }


def write_all(payload: dict, tickers: dict[str, dict],
              root: str = ".", progress=print) -> dict:
    """파일로 씁니다. 되돌려 주는 값은 {경로: 바이트수} (검산용)."""
    written: dict[str, int] = {}
    web = os.path.join(root, WEB_DIR)
    tdir = os.path.join(root, TICKER_DIR)
    os.makedirs(tdir, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path = os.path.join(web, "app.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    written[path] = len(body.encode())
    for name, data in tickers.items():
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        p = os.path.join(tdir, f"{name}.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        written[p] = len(body.encode())
    progress(f"웹앱 데이터: app.json {written[os.path.join(web,'app.json')]:,}바이트 "
             f"· 종목 {len(tickers)}개 "
             f"(합계 {sum(written.values())/1024/1024:.1f}MB)")
    return written


def main(argv: list[str]) -> int:
    snap = dataset.load()
    ds = dataset.build(snap, splits=dataset.load_splits())
    verdict = None
    path = os.path.join(cfg.MEASURE_DIR, "verdict.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            verdict = json.load(f)
    log = None
    lpath = os.path.join(cfg.MEASURE_DIR, "robot_log.json")
    if os.path.exists(lpath):
        with open(lpath, encoding="utf-8") as f:
            log = json.load(f)

    payload = build_payload(ds, verdict, log)
    완성 = sm.completion_events(ds)
    tickers = {t: build_ticker(ds, t, 완성) for t in ds["tickers"]}

    if "--check" in argv:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        print(f"app.json {len(body.encode()):,}바이트 · 종목 {len(tickers)}개")
        return 0
    write_all(payload, tickers)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
