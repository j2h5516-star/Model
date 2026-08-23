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
    """
    import collect_job as cj
    import config as cfg
    import market_data as md

    t0 = time.time()
    보고 = cj.collect_fundamentals([ticker], progress=progress)
    eps = (보고[0] or {}).get("quarters") or [] if 보고 else []
    오류 = (보고[0] or {}).get("first_error") if 보고 else "수집 실패"

    daily, _실패 = md.fetch_daily_data([ticker, cfg.BENCHMARK])
    걸린 = time.time() - t0

    성공 = bool(eps) and ticker in daily
    말 = []
    if not eps:
        말.append(f"실적을 못 읽었습니다{(' — ' + str(오류)[:80]) if 오류 else ''}")
    if ticker not in daily:
        말.append("주가를 못 받았습니다 (상장 폐지·코드 오타일 수 있습니다)")
    progress(f"{ticker}: 분기 {len(eps)}개 · 주가 {'있음' if ticker in daily else '없음'}"
             f" · {걸린:.1f}초")
    return {"종목": ticker, "eps": eps, "prices": daily,
            "성공": 성공, "말": " / ".join(말), "초": round(걸린, 1)}


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


def 저장(수집: dict, 화면: dict | None, progress=print) -> list[str]:
    """보기 전용 수집물과 화면 자료를 파일로. 판정 표본은 안 건드립니다."""
    쓴것 = []
    os.makedirs(LOOKUP_DIR, exist_ok=True)
    p = os.path.join(LOOKUP_DIR, f"{수집['종목']}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"종목": 수집["종목"], "수집시각": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "성공": 수집["성공"], "말": 수집["말"],
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
    화면 = 화면자료_만들기(수집)
    저장(수집, 화면)
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
