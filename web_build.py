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
# 매일 유니버스 전체(지금 401종목)를 커밋하므로 저장소가 커지지 않게
# 최소한만 싣습니다.
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
    # 표본이 0 인 가설은 "표본 없음"만으로는 **아무것도 알려 주지 못합니다**
    # (150차-J). 11개가 전부 그렇게 나오면 주인은 시스템이 멈춘 줄 압니다.
    # 실제 이유는 구조적입니다 — 표적 창이 60거래일이라 08-15 에 등록한
    # 가설은 11-07 이전에 판정이 나올 수가 없습니다. 그 날짜를 적습니다.
    바닥 = {d["가설"]: d for d in judge.first_verdict_floor(verdict)}
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
            # 값을 만들지 않습니다 — 등록일 상수를 못 찾은 가설은 없음.
            "가장이른날": (바닥.get(name) or {}).get("가장이른날"),
            "창_거래일": (바닥.get(name) or {}).get("창_거래일"),
        })
    순서 = {"채택": 0, "미채택": 2, "판정 불가": 1}
    rows.sort(key=lambda r: (순서.get(r["판정"], 3), r["이름"]))
    return rows


def _세분만(확정: list[dict], 세분: list[dict]) -> list[dict]:
    """세분 묶음표에서 확정 묶음표와 **다른 줄만** 고릅니다 (164차).

    세분 분류는 두 묶음(기기 OEM 반도체 · 좌석·계약 정액 구독)만 가르므로
    나머지 줄은 확정표와 똑같습니다 — 같은 줄을 두 번 그리지 않습니다.
    """
    같은 = {(g["묶음"], g["가속"], g["감속"], g["판단불가"]) for g in 확정}
    return [g for g in 세분
            if (g["묶음"], g["가속"], g["감속"], g["판단불가"]) not in 같은]


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
        # 154차 (주인 지시) — 지금 H29 조합(정배열∧델타↑∧이격30%+∧이격상승)
        # 상태인 종목. 과거 실측과 판정 상태를 함께 싣는다(정직화 원칙).
        "조합신호": {
            "종목들": app.combo_now_rows(ds),
            "실측": {"폭등률": 24.4, "폭락률": 12.0, "기준선": 9.0,
                     "출처": "152차 백테스트 (완성 4,971건 · 60거래일 · "
                            "SPY+20%p) — 탐색 표본, 채택 아님"},
            "판정상태": "H29 판정 불가 — 등록(08-27) 뒤 새 표본 수집 중, "
                      "첫 표본은 빨라야 2026-11-19",
        },
        "완성전체": 종목판,
        # 162차 (주인 지시 "저런 표를 앞으로의 기준으로 삼아") — 성장 속도표.
        #   종목마다 TTM 증가율과 그 직전 증가율을 나란히 두어 "가속/감속"을
        #   **사실로만** 적습니다. 판정·점수 아님. 가속이 이후 수익을
        #   예측하는지는 H31 로 사전 등록했고(164차, 2026-09-02) 판정은 새
        #   표본이 쌓인 뒤입니다. 화면이 그 말을 함께 합니다.
        #   세분 묶음(45차 ④ 감도 분석 — 데이터센터 실리콘 5 · 사용량 과금
        #   SW 5 를 갈라 본 판)은 확정 묶음과 다른 줄만 따로 싣습니다.
        "성장속도": {
            "묶음별": app.growth_sector_rows(ds),
            "묶음별_세분": _세분만(app.growth_sector_rows(ds),
                                 app.growth_sector_rows(ds, fine=True)),
            "종목들": app.growth_table_rows(ds),
            "기준": (f"마지막 연속 {app.GROWTH_MIN_QUARTERS}분기 이상 · 최근 발표가 "
                    f"기준일에서 {sm.DELTA_FRESH_DAYS}일 안 · 가속 = 이번 TTM 증가율 "
                    "> 직전 TTM 증가율 · 묶음 비율은 가릴 수 있는 종목 5개 이상일 때만"),
            "정직화": "사실의 나열입니다 — 가속이 이후 수익을 예측하는지는 H31 로 "
                     "사전 등록(2026-09-02)해 새 표본으로 재는 중이며 아직 판정이 "
                     "없습니다. 판정도 점수도 아니며 채택 근거로 쓰지 않습니다.",
        },
        "정배열유지": app.aligned_now_rows(ds),
        # 150차-O — 검색이 **자료가 있는 종목 전부**를 훑게 하려면
        # 목록이 필요합니다. 정배열도 아니고 최근 완성도 없는 종목은
        # 그동안 화면 목록에 아예 없어서 검색해도 안 나왔습니다.
        "종목목록": sorted(ds["quarters"]),
        "묶음표": {t: cfg.GROUPS.get(t, "미분류") for t in sorted(ds["quarters"])},
        "섹터폭": 섹터폭,
        # 같은 앱 안의 두 "정배열"이 갈리는 종목 (150차-D) — 화면이
        # 서로 다른 말을 하는 것처럼 보이던 것을 설명하기 위한 사실.
        "잣대차이": app.gauge_gap_rows(ds),
        "가설": hypothesis_rows(verdict),
        # 160차 — 채택이 생기면 **얼마나 아슬아슬한지**를 함께 싣습니다.
        #   판정 파일에 이미 있는 수를 옮겨 적을 뿐, 만들지 않습니다.
        #   "채택"이라는 글자만 띄우면 매수 근거로 읽힙니다(헌법 3·4원칙).
        "채택주의": app.adoption_caveats(verdict),
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

    # ⚠️ 신고점폭이 **끊긴 구간을 건너뛰어** 계산됐는지 표시합니다(150차-P).
    #
    # 주인이 CRDO 에서 "폭 3700%"를 봤습니다. 직전 정점 0.09(2024-05-29)와
    # 지금 3.42(2026-06-01) 사이 **2년의 TTM 이 전부 없어서**, 점진적으로
    # 오른 것이 한 번에 뛴 것처럼 계산된 값입니다. 산수는 맞지만 **한
    # 분기에 그만큼 뛴 것으로 읽히면 거짓**입니다.
    #
    # 인수인계 4장 7번이 금지한 "끊긴 이력을 이어붙이기"와 같은 병입니다.
    # 전 유니버스 실측: 폭이 계산된 2,124칸 중 **89칸**이 이 모양입니다.
    #
    # 계산은 **고치지 않습니다** — `신고점폭`은 H22·H22b 의 사전 등록된
    # 신호값입니다. 표시만 답니다(헌법 8조: 결과를 보고 규칙을 고치지 말 것).
    실적 = []
    앞ttm = None
    for s in states[-24:]:
        폭 = s["신고점폭"]
        실적.append({
            "발표일": s["announced"],
            "ttm": _round(s["ttm"], 3),
            "신고점": bool(s["new_high"]),
            "첫돌파": s["newhigh_streak"] == 1,
            "신고점폭": _round(폭, 1),
            # 직전 발표에 TTM 이 없었으면 = 끊긴 구간을 건너뛴 폭
            "끊김너머": bool(폭 is not None and 앞ttm is None),
        })
        앞ttm = s["ttm"]

    # 분기가 **통째로 빠진** 자리 — 화면에는 그냥 없어서 안 보입니다(150차-P).
    # 발표일 간격이 정상(약 91일)의 1.4배를 넘으면 사이에 분기가 빠진 것.
    빠진구간 = []
    _날 = [str(r["announced_date"])[:10] for r in quarters
          if r.get("announced_date")]
    for i in range(1, len(_날)):
        _간격 = (date.fromisoformat(_날[i]) - date.fromisoformat(_날[i - 1])).days
        if _간격 > 130:
            빠진구간.append({"앞": _날[i - 1], "뒤": _날[i], "일수": _간격,
                          "빠진분기": max(1, round(_간격 / 91) - 1)})

    # 델타 흐름 (150차-L, 주인 요청) — 잣대 분기값이 직전 분기보다 올랐나.
    # **TTM 과 다른 것을 잰다**: 델타는 분기 대 분기, TTM 은 네 분기 합이라
    # 한 분기가 빠지면 TTM 만 못 만듭니다(CRDO 에서 주인이 본 것).
    # 계기판과 **같은 함수**를 씁니다 — 스스로 세면 두 화면이 갈립니다.
    델타흐름 = [{"발표일": d, "상승": bool(up)}
              for d, up in sm._delta_series(ds, ticker)]
    # 그 발표일의 잣대 분기값도 같이 실어 흐름을 눈으로 보게 합니다
    분기값 = {str(r.get("announced_date"))[:10]: r.get(잣대)
            for r in quarters if r.get("announced_date")} if 잣대 else {}
    # 주인 지적(150차-M): "1-10-20 이면 올라갔다 내려가야지 — 10배에서
    # 2배가 된 거니까." 값의 **방향**만 보면 1→10→20 이 계속 "상승"이지만
    # 성장 속도는 **+900% → +100%** 로 꺾였습니다. 그래서 성장률과
    # 그 성장률의 방향(가속/둔화)을 같이 싣습니다.
    #
    # ⚠️ `상승`(방향)은 사전 등록된 델타 정의라 **건드리지 않습니다**
    #    (H19·H21 등이 씁니다). 아래 성장률은 **화면 표시**일 뿐입니다.
    #
    # 값이 0 이하를 지나면 비율의 뜻이 뒤집히므로 **없음**으로 둡니다
    # (적자에서 적자로 옮겨간 것을 "몇 % 성장"이라 부를 수 없습니다).
    # ⚠️ 앞값은 **델타흐름이 아니라 분기 목록**에서 찾아야 합니다.
    #    `_delta_series` 는 쌍을 만들므로 **둘째 분기부터** 시작합니다 —
    #    델타흐름 안에서 앞값을 찾으면 첫 항목의 성장률이 통째로 없어집니다
    #    (150차-M 에 실제로 그렇게 만들었다 시험이 잡았습니다).
    순서 = sorted((str(r["announced_date"])[:10], r.get(잣대))
                 for r in quarters
                 if r.get("announced_date")) if 잣대 else []
    앞의값 = {}
    for i in range(1, len(순서)):
        앞의값[순서[i][0]] = 순서[i - 1][1]

    앞성장 = None
    for x in 델타흐름:
        v = 분기값.get(x["발표일"])
        앞 = 앞의값.get(x["발표일"])
        x["값"] = _round(v, 3)
        성장 = None
        if v is not None and 앞 is not None and 앞 > 0 and v > 0:
            성장 = (v - 앞) / 앞 * 100.0
        x["성장률"] = _round(성장, 1)
        # 가속 = 성장률이 직전 성장률보다 커짐 · 둔화 = 작아짐
        x["가속"] = (None if (성장 is None or 앞성장 is None)
                   else bool(성장 > 앞성장))
        if 성장 is not None:
            앞성장 = 성장

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
        "델타흐름": 델타흐름,
        "실적": 실적,
        "빠진구간": 빠진구간,
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
