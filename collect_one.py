"""collect_one.py — 종목 **하나만** 지금 당장 수집합니다 (150차-O, 주인 요청)

주인 지시(2026-08-23): "화면에서 치면 로봇이 긁어오는 걸 기다리지 말고
바로 긁어오게 할 순 있어?"

**왜 따로 만드는가**: 매일 도는 로봇은 401종목을 훑어 약 226분 걸립니다.
궁금한 종목 하나 때문에 그걸 기다릴 수 없습니다. 한 종목이면 실측으로
10~25초(캐시 있을 때)이고, 캐시가 없는 새 종목이라도 몇 분입니다.

---------------------------------------------------------------------------
⚠️ **가장 중요한 규칙 — 판정 표본을 건드리지 않습니다**
---------------------------------------------------------------------------
주인이 궁금해서 친 종목을 가설 판정 표본에 넣으면 **"결과를 보고 종목을
고르는 것"** 이 됩니다. v1이 그렇게 무너졌고 헌법 2조(사전 등록)가
금지합니다.

그래서 이 수집물은:
  · `data/measure/snapshot.json` 에 **절대 쓰지 않습니다** (판정 표본)
  · `data/lookup/` 아래 따로 쌓습니다 (보기 전용)
  · 화면에도 **"요청 수집분 — 가설 판정에는 쓰이지 않습니다"** 라고 적습니다

유니버스 종목을 요청하면 그건 어차피 다음 정기 런에서 판정 표본에
들어가므로, 여기서는 **미리 보기**일 뿐입니다.

---------------------------------------------------------------------------
실행:  python3 collect_one.py NVDA
       (깃허브 액션 `collect-one` 워크플로가 이것을 부릅니다)
"""

from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

LOOKUP_DIR = os.path.join(ROOT, "data", "lookup")
WEB_TICKER_DIR = os.path.join(ROOT, "docs", "data", "t")

# 종목 코드로 인정할 모양 — 아무 글자나 받으면 엉뚱한 요청이 로봇을 돌립니다
_TICKER_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ.-")
MAX_TICKER_LEN = 8


def 종목코드_정리(raw: str) -> str | None:
    """입력을 대문자 종목 코드로. 모양이 아니면 **None** (지어내지 않음)."""
    t = (raw or "").strip().upper()
    if not t or len(t) > MAX_TICKER_LEN:
        return None
    if not set(t) <= _TICKER_OK:
        return None
    return t


def 한종목_수집(ticker: str, progress=print) -> dict:
    """SEC 실적 + 주가를 받아 보기 전용 수집물로 돌려줍니다.

    돌려주는 것: {"종목", "eps", "prices", "성공", "말"}
    실패해도 **예외를 던지지 않습니다** — 워크플로가 조용히 죽으면
    주인은 왜 안 나오는지 모릅니다. 실패 사실을 그대로 담아 돌려줍니다.

    ⚠️ **매일 도는 로봇과 똑같은 길로 만듭니다** (150차-S 에 크게 데었음).
    처음엔 `collect_job.collect_fundamentals()` 를 불렀는데, 그 함수는
    **진단 보고만** 돌려주고 분기 자료는 버립니다. 그래서 이 자리가
    언제나 "분기 0개"였고 **한 번도 성공한 적이 없었습니다.** 로봇은
    `sec_fundamentals.get_fundamentals()` → `measure_store.eps_rows()` 로
    갑니다. 여기도 같은 길을 씁니다 — 다른 길을 내면 또 갈라집니다.
    """
    import config as cfg
    import market_data as md
    import measure_store as ms
    import sec_fundamentals as sf

    t0 = time.time()
    sf._ensure_identity()          # SEC 는 신원 없는 요청을 막습니다(403)
    오류 = None
    quarters: list[dict] = []
    보고 = {}
    try:
        quarters, 보고 = sf.get_fundamentals(ticker, use_cache=False)
        오류 = (보고 or {}).get("first_error")
    except Exception as exc:       # 한 종목 때문에 워크플로가 죽지 않게
        오류 = f"{type(exc).__name__}: {str(exc)[:160]}"
    # 로봇이 판정 표본을 만들 때 쓰는 것과 **같은 변환**입니다
    eps = ms.eps_rows(quarters)

    daily, _실패 = md.fetch_daily_data([ticker, cfg.BENCHMARK])
    # 판정 표본과 **같은 모양**으로 옮겨 적습니다 — 날짜·종가 목록.
    # 받아 온 표를 그대로 넘기면 dataset.build 가 못 읽습니다(150차-S).
    prices = {t: ms.price_rows(daily.get(t))
              for t in (ticker, cfg.BENCHMARK)}
    걸린 = time.time() - t0

    주가있음 = bool(prices.get(ticker, {}).get("dates"))
    기준지수있음 = bool(prices.get(cfg.BENCHMARK, {}).get("dates"))
    성공 = bool(eps) and 주가있음 and 기준지수있음
    말 = []
    if not eps:
        말.append(f"실적을 못 읽었습니다{(' — ' + str(오류)[:80]) if 오류 else ''}")
    if not 주가있음:
        말.append("주가를 못 받았습니다 (상장 폐지·코드 오타일 수 있습니다)")
    if not 기준지수있음:
        말.append(f"기준지수({cfg.BENCHMARK}) 주가를 못 받아 견줄 수가 없습니다")
    progress(f"{ticker}: 분기 {len(eps)}개 · 주가 {'있음' if 주가있음 else '없음'}"
             f" · 기준지수 {'있음' if 기준지수있음 else '없음'} · {걸린:.1f}초")

    # 수집 진단 계기를 그대로 남깁니다 (150차-Z).
    # 왜: "분기가 왜 없나"의 답이 여기 들어 있습니다 — 8-K 를 몇 건
    # 찾았고(filings_found), 실적발표로 인정했고(gate_passed), 숫자를
    # 뽑았고(parsed_ok), **짝을 못 찾아 버려진 것이 몇 건인가**
    # (unpaired_press·pair_note). 짐작 대신 이 숫자를 봅니다.
    # ⚠️ `raw_texts` 는 원문 통째라 큽니다 — 뺍니다.
    계기 = {k: v for k, v in (보고 or {}).items() if k != "raw_texts"}
    progress(f"[수집계기] 8-K {계기.get('filings_found')}건 · 실적으로 인정 "
             f"{계기.get('gate_passed')}건 · 숫자 뽑음 {계기.get('parsed_ok')}건 · "
             f"짝 못 찾음 {계기.get('unpaired_press')}건")
    return {"종목": ticker, "eps": eps, "prices": prices, "계기": 계기,
            "성공": 성공, "말": " / ".join(말), "초": round(걸린, 1)}


def 단위진단(ticker: str, progress=print) -> dict:
    """보도자료가 **단위 표기를 어디에 두었는지** 실물로 확인합니다 (150차-U).

    왜 필요한가 — 주인 질문("값이 왜 비지? 어떻게 가져오지?")의 답을 찾기
    위해서입니다. 실데이터에서 영업이익·EBITDA **1,800칸**이 "단위 착오
    의심"으로 버려지고 있습니다(138종목). 크리도는 영업이익 19칸 중 11칸.

        CRDO 26 Q4: 영업이익 216,722 ↔ 매출 437,003,000 → 마진 0.05%
        216,722 **천 달러** = 2억 1,672만 → 마진 49.6% 가 맞는 값

    매출은 단위를 제대로 읽는데 영업이익은 못 읽습니다. `_detect_table_unit`
    은 숫자 **앞쪽 3,000자**만 훑어 "(in thousands)" 를 찾고, **못 찾으면
    1(단위 없음)** 로 봅니다. 그 3,000자가 모자란 것인지, 표기가 아예
    없는 것인지 — **짐작하지 말고 원문에서 봅니다.**

    개발 환경은 SEC 가 막혀 원문을 못 봅니다. 이 함수는 **깃허브에서**
    돕니다(150차-S 에서 워크플로를 직접 띄울 수 있음을 확인). 수집이
    끝난 뒤 `data/raw8k/<종목>/` 에 남은 원문 캐시를 읽습니다.

    **읽기만 합니다** — 어떤 값도 고치지 않습니다.
    """
    import re
    import config as cfg
    import sec_fundamentals as sf

    폴더 = os.path.join(cfg.RAW8K_CACHE_DIR, ticker)
    if not os.path.isdir(폴더):
        return {"본 문서": 0, "말": "원문 캐시가 없습니다"}

    def 라벨위치(text, patterns):
        for p in patterns:
            m = re.search(p, text, re.I | re.M)
            if m:
                return m.start()
        return None

    def 가장가까운단위(text, pos):
        """숫자 앞쪽 **전체**에서 가장 가까운 단위 표기와 그 거리."""
        best = (None, None)
        앞 = text[:pos]
        for pattern, _ in sf._UNIT_PATTERNS:
            for m in pattern.finditer(앞):
                거리 = pos - m.start()
                if best[1] is None or 거리 < best[1]:
                    best = ((m.group(1) or "").lower(), 거리)
        return best

    본것 = 0
    사례: list[dict] = []
    요약 = {"매출만 단위 찾음": 0, "둘 다 찾음": 0, "둘 다 못 찾음": 0,
           "영업이익만 찾음": 0, "표기가 문서에 아예 없음": 0}
    for 이름 in sorted(os.listdir(폴더)):
        if not 이름.endswith(".json"):
            continue
        try:
            with open(os.path.join(폴더, 이름), encoding="utf-8") as f:
                text = (json.load(f) or {}).get("text") or ""
        except Exception:
            continue
        if not text:
            continue
        본것 += 1
        rev위치 = 라벨위치(text, sf.LABELS_REVENUE)
        op위치 = 라벨위치(text, sf.LABELS_NONGAAP_OP_INCOME)
        if op위치 is None:
            continue
        rev단위 = sf._detect_table_unit(text, rev위치) if rev위치 is not None else None
        op단위 = sf._detect_table_unit(text, op위치)
        가까운말, 거리 = 가장가까운단위(text, op위치)

        if 가까운말 is None:
            요약["표기가 문서에 아예 없음"] += 1
        elif op단위 > 1 and (rev단위 or 1) > 1:
            요약["둘 다 찾음"] += 1
        elif op단위 == 1 and (rev단위 or 1) > 1:
            요약["매출만 단위 찾음"] += 1
        elif op단위 > 1:
            요약["영업이익만 찾음"] += 1
        else:
            요약["둘 다 못 찾음"] += 1

        # 표기를 **못 찾았을 때**, 단위 낱말이 글로는 있는지 따로 봅니다.
        #  · 낱말이 있는데 못 찾았다 → 우리 패턴이 모자란 것 (고칠 수 있음)
        #  · 낱말이 아예 없다      → 원문에 단위가 안 적혔거나 뽑을 때 잃은 것
        낱말 = sorted({m.group(0).lower() for m in
                     re.finditer(r"thousands?|millions?|billions?", text, re.I)})
        if 가까운말 is None:
            요약["단위 낱말은 글에 있음" if 낱말 else "단위 낱말도 아예 없음"] = \
                요약.get("단위 낱말은 글에 있음" if 낱말 else "단위 낱말도 아예 없음", 0) + 1

        if len(사례) < 5 and op단위 == 1:
            낱말자리 = [m.start() for m in
                     re.finditer(r"thousands?|millions?|billions?", text, re.I)][:3]
            사례.append({
                "문서": 이름,
                "매출_단위배수": rev단위,
                "영업이익_단위배수": op단위,
                "가장가까운표기": 가까운말,
                "그 표기까지_글자수": 거리,
                "훑는_창": 3000,
                "창_밖인가": (거리 > 3000) if 거리 is not None else None,
                "단위낱말": 낱말[:5],
                "단위낱말_둘레": [text[max(0, p - 90):p + 40].replace("\n", " ")
                             for p in 낱말자리],
                "문서_머리": text[:260].replace("\n", " "),
                "영업이익_둘레": text[max(0, op위치 - 60):op위치 + 160]
                                .replace("\n", " ")[:220],
            })

    progress(f"[단위진단] 원문 {본것}건 — " +
             " · ".join(f"{k} {v}" for k, v in 요약.items() if v))
    맞댐 = xbrl_대_보도자료(ticker, progress=progress)
    return {"본 문서": 본것, "요약": 요약, "사례": 사례, "XBRL맞댐": 맞댐}


def xbrl_대_보도자료(ticker: str, progress=print) -> dict:
    """XBRL 영업이익(달러)과 합쳐진 값(보도자료)을 **크기로** 맞댑니다.

    보도자료에 단위 표기가 아예 없으면 원문만 봐서는 배수를 알 수 없습니다.
    그런데 **XBRL 은 언제나 절대 달러**입니다. 같은 분기의 GAAP 영업이익과
    논갭 영업이익이 1,000배 차이 나는 일은 없습니다 — 그래서 XBRL 값이
    **독립된 자**가 됩니다. 지어내는 것이 아니라 **다른 자로 재는 것**입니다.

    읽기만 합니다. 어떤 값도 고치지 않습니다.
    """
    import sec_fundamentals as sf

    try:
        보고 = sf.new_report(ticker)
        xb = sf.fetch_xbrl_approximation(ticker, None, 보고)
    except Exception as exc:
        return {"말": f"XBRL 을 못 받았습니다: {type(exc).__name__}: {str(exc)[:100]}"}

    x맵 = {str(q.get("period_end") or q.get("filing_date") or "")[:10]:
           q.get("op_income") for q in (xb or [])}
    합친, _ = sf.get_fundamentals(ticker, use_cache=True)
    줄 = []
    배수셈: dict[str, int] = {}
    for q in (합친 or []):
        키 = str(q.get("period_end") or q.get("filing_date") or "")[:10]
        p, x = q.get("op_income"), x맵.get(키)
        if p is None or x is None or p == 0 or x == 0:
            continue
        비 = abs(x / p)
        표 = ("같은 크기(1배)" if 0.2 <= 비 <= 5 else
              "약 1,000배" if 200 <= 비 <= 5000 else
              "약 100만배" if 2e5 <= 비 <= 5e6 else f"그 밖({비:,.0f}배)")
        배수셈[표] = 배수셈.get(표, 0) + 1
        if len(줄) < 8:
            줄.append({"분기끝": 키, "합쳐진값": p, "XBRL값": x,
                       "XBRL÷합쳐진": round(비, 1), "판정": 표})
    progress("[XBRL맞댐] " + (" · ".join(f"{k} {v}" for k, v in 배수셈.items())
                            or "맞댈 짝이 없습니다"))
    return {"짝 수": sum(배수셈.values()), "배수분포": 배수셈, "보기": 줄}


def 빠진분기_진단(ticker: str, progress=print) -> dict:
    """**분기가 통째로 안 잡히는** 이유를 XBRL 원자료에서 봅니다 (150차-X).

    실측: 발표일이 130일 넘게 벌어진 곳 170건 · 76종목. 그중
    **61.7%(92건)가 Q4** 다. 골드만삭스는 12월 결산 분기(1월 발표)가
    **아홉 해 내리** 통째로 없다.

        GS 분기 이름: 22Q2 22Q3 23Q1 23Q2 23Q3 24Q1 … — **Q4 가 하나도 없음**

    회사는 1~3분기를 10-Q 에 "3개월"로 신고하지만 4분기는 10-K 에
    "연간(12개월)"으로만 신고하는 일이 많습니다. 그래서 코드에는
    `연간 − (1+2+3분기)` 로 채우는 길이 있습니다(`_fill_missing_q4`).
    **그 길이 왜 GS 에서 안 통하는지**를 짐작하지 말고 원자료에서 봅니다.

    무엇을 적나 — 개념 묶음별로:
      · 3개월 값이 몇 개인가 · 12개월(연간) 값이 몇 개인가
      · 채우기 뒤 12월 31일(또는 회계연도 끝) 키가 생겼는가
      · 못 채웠다면 앞선 세 분기를 못 찾은 것인가

    **읽기만 합니다** — 어떤 값도 고치지 않습니다.
    """
    import sec_fundamentals as sf

    try:
        sf._ensure_identity()
        from edgar import Company
        facts = Company(ticker).get_facts()
    except Exception as exc:
        return {"말": f"XBRL 을 못 받았습니다: {type(exc).__name__}: {str(exc)[:100]}"}
    if facts is None:
        return {"말": "이 종목의 XBRL 자료가 없습니다"}

    묶음 = {}
    for key in ("revenue", "op_income", "gaap_eps"):
        if key not in sf._XBRL_CONCEPTS:
            continue
        unit = sf._XBRL_UNITS.get(key, "USD")
        삼개월, 연간 = {}, {}
        for concept in sf._XBRL_CONCEPTS[key]:
            try:
                삼개월.update(sf._quarterly_series(facts, concept, None, unit=unit))
                연간.update(sf._annual_series(facts, concept, None, unit=unit))
            except Exception:
                continue
        # ⚠️ **실제 수집이 쓰는 함수를 그대로 부릅니다** (150차-X).
        #    처음엔 여기서 `_fill_missing_q4` 를 직접 불렀는데, 그러면
        #    수집 경로가 채우기를 그만두어도 진단은 "채워졌다"고 말합니다.
        #    돌연변이(`_series_for_key` 의 채우기 끄기)가 통과해서 알았습니다.
        #    진단이 **딴 길로 가면 거짓말을 합니다** — 150차-S 와 같은 부류.
        채운 = sf._series_for_key(key, facts)
        새로생긴 = sorted(set(채운) - set(삼개월))
        못채운 = []
        for fy in sorted(연간):
            if fy in 삼개월 or fy in 새로생긴:
                continue
            앞셋 = sf._find_prior_three_quarters(삼개월, fy)
            못채운.append({"회계연도끝": fy,
                         "앞선세분기": "못 찾음" if 앞셋 is None else "찾음"})
        묶음[key] = {
            "3개월 값": len(삼개월),
            "연간 값": len(연간),
            "채워서 새로 생긴 분기": len(새로생긴),
            "새로 생긴 것 보기": 새로생긴[-4:],
            "연간은 있는데 못 채운 것": 못채운[-6:],
            "gaap_eps 는 일부러 안 채움": key == "gaap_eps",
        }

    # 뼈대가 실제로 만들어 낸 분기끝 — 위 시계열이 행으로 살아남았는가
    try:
        보고 = sf.new_report(ticker)
        뼈대 = sf.fetch_xbrl_approximation(ticker, None, 보고)
        분기끝 = sorted(str(q.get("filing_date"))[:10] for q in (뼈대 or []))
    except Exception as exc:
        분기끝, 보고 = [], {"first_error": f"{type(exc).__name__}: {str(exc)[:80]}"}

    달별 = {}
    for d in 분기끝:
        달별[d[5:7]] = 달별.get(d[5:7], 0) + 1
    progress(f"[빠진분기진단] 뼈대 분기 {len(분기끝)}개 · 끝나는 달 분포 {달별}")
    return {"묶음": 묶음, "뼈대 분기 수": len(분기끝),
            "끝나는 달 분포": 달별, "최근 분기끝": 분기끝[-8:],
            "애매하다고 뺀 것": (보고 or {}).get("xbrl_ambiguous", [])[-6:]}


def 화면자료_만들기(수집: dict, progress=print) -> dict | None:
    """수집물 하나로 종목 상세 화면 자료를 만듭니다.

    **판정 표본을 안 씁니다** — 이 종목만으로 만든 작은 데이터에서
    정배열·이격도·실적 이력을 계산합니다. 섹터 폭·주도섹터처럼 다른
    종목이 있어야 하는 값은 **아예 만들지 않습니다**(없는 값 금지).
    """
    import dataset
    import sector_model as sm
    import web_build as wb
    import config as cfg

    ticker = 수집["종목"]
    if not 수집["성공"]:
        return None
    snap = {
        "benchmark": cfg.BENCHMARK,
        "tickers": [ticker],
        "eps": {ticker: 수집["eps"]},
        "prices": 수집["prices"],
    }
    try:
        ds = dataset.build(snap)
        완성 = sm.completion_events(ds)
        d = wb.build_ticker(ds, ticker, 완성)
    except Exception as exc:                      # 화면 하나 때문에 죽지 않음
        progress(f"⚠️ 화면 자료 실패: {type(exc).__name__}: {str(exc)[:120]}")
        return None

    # 이 종목이 사전 등록 유니버스 안인지 — 화면이 정직하게 말해야 합니다
    d["요청수집"] = True
    d["유니버스"] = ticker in cfg.TICKERS
    d["수집시각"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    d["안내"] = (
        "요청 수집분입니다 — **가설 판정 표본에는 쓰이지 않습니다.** "
        "궁금해서 부른 종목을 표본에 넣으면 '결과를 보고 종목을 고르는 것'이 "
        "되어 사전 등록이 무너집니다(헌법 2조)."
        + ("" if d["유니버스"] else
           " 이 종목은 사전 등록 유니버스 밖이라, 정기 수집에도 들어오지 "
           "않습니다.")
        + " 섹터 정배열 폭·주도섹터는 다른 종목이 있어야 재므로 **여기서는 "
          "재지 않습니다** — 없는 값은 만들지 않습니다."
    )
    return d


def 저장(수집: dict, 화면: dict | None, progress=print,
       진단: dict | None = None, 구멍진단: dict | None = None) -> list[str]:
    """보기 전용 수집물과 화면 자료를 파일로. 판정 표본은 안 건드립니다."""
    쓴것 = []
    os.makedirs(LOOKUP_DIR, exist_ok=True)
    p = os.path.join(LOOKUP_DIR, f"{수집['종목']}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"종목": 수집["종목"], "수집시각": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "성공": 수집["성공"], "말": 수집["말"],
            "수집계기": 수집.get("계기"),  # 8-K 를 몇 건 찾고 몇 건 버렸나 (150차-Z)
            "단위진단": 진단,          # 원문이 단위를 어디에 뒀나 (150차-U)
            "빠진분기진단": 구멍진단,   # 분기가 왜 통째로 없나 (150차-X)
            "eps": 수집["eps"]}, f, ensure_ascii=False)
    쓴것.append(p)
    if 화면 is not None:
        os.makedirs(WEB_TICKER_DIR, exist_ok=True)
        p2 = os.path.join(WEB_TICKER_DIR, f"{수집['종목']}.json")
        with open(p2, "w", encoding="utf-8") as f:
            json.dump(화면, f, ensure_ascii=False)
        쓴것.append(p2)
    progress("적은 파일: " + ", ".join(os.path.relpath(x, ROOT) for x in 쓴것))
    return 쓴것


def main(argv: list[str]) -> int:
    if not argv:
        print("쓰는 법: python3 collect_one.py NVDA")
        return 2
    ticker = 종목코드_정리(argv[0])
    if ticker is None:
        print(f"⛔ '{argv[0]}' 는 종목 코드 모양이 아닙니다 "
              f"(영문 대문자 {MAX_TICKER_LEN}자 이내).")
        return 2
    print(f"=== {ticker} 지금 수집 ===")
    수집 = 한종목_수집(ticker)
    # 원문이 단위를 어디에 뒀는지 — 수집 뒤 캐시가 있을 때만 볼 수 있습니다
    try:
        진단 = 단위진단(ticker)
    except Exception as exc:                  # 진단 때문에 수집을 잃지 않음
        print(f"⚠️ 단위진단 실패: {type(exc).__name__}: {str(exc)[:120]}")
        진단 = None
    try:
        구멍 = 빠진분기_진단(ticker)
    except Exception as exc:
        print(f"⚠️ 빠진분기 진단 실패: {type(exc).__name__}: {str(exc)[:120]}")
        구멍 = None
    화면 = 화면자료_만들기(수집)
    저장(수집, 화면, 진단=진단, 구멍진단=구멍)
    if not 수집["성공"]:
        print(f"⛔ {ticker}: {수집['말']}")
        return 1
    if 화면 is None:
        print(f"⚠️ {ticker}: 자료는 받았으나 화면 자료를 못 만들었습니다 "
              "(분기가 너무 적거나 주가 이력이 짧을 수 있습니다).")
        return 1
    print(f"✅ {ticker} — 화면에서 바로 볼 수 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
