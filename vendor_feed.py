"""
vendor_feed.py — 두 번째 자: 데이터 회사(야후) 분기표 받아 오기 (75차)
=======================================================================

**왜 만드나** (주인 질문, 2026-08-16):
  "SEC 문서를 읽지 말고 야후 같은 데서 받아오면 더 정확하지 않을까?
   실적을 읽는 게 아니라 가져오기만 하면 되니까."

  반은 맞습니다. 74차에 고친 결함 **네 개가 전부 '읽기' 결함**이었습니다
  (공시 번호 99.1 을 이익으로, 주식 수 45.3 을 EPS 로, 연간값을 분기
  칸에…). 값을 **받아오기만** 하면 그런 사고는 아예 없습니다.

  그런데 우리 사다리 첫 칸은 **회사가 스스로 정의한 조정 EPS** 입니다.
  데이터 회사가 주는 "Reported EPS" 는 월가 기준으로 **그 회사가 통일
  시킨 값**이라 회사 발표와 미묘하게 다릅니다. 헌법 1조(창작 금지)가
  막는 것이 정확히 이런 것이고, "한 열 안에 정의가 섞여 델타가 뒤집힌
  사고"(주식보상비 사건)를 이미 겪었습니다.

**그래서 갈아타지 않고 두 번째 자로 붙입니다.**
  · 받아온 값은 `data/measure/vendor.json` 에 **따로** 저장합니다.
  · snapshot.json 과 **섞지 않습니다.** 측정·판정은 지금 그대로입니다.
  · 다음 세션이 두 자를 대조해 **불일치율**을 재고, 그 숫자를 보고
    본선을 바꿀지 정합니다 (원칙 6: 신호보다 장치를 먼저 의심).

**무엇을 받나** (전부 계산이 아니라 표에서 그대로 옮겨 적기):
  분기표(quarterly_income_stmt)에서
    매출 · 매출원가 · 매출총이익 · 영업이익 · 순이익 · 희석 EPS
  발표 기록(earnings_history)에서
    발표일 · 발표 EPS(월가 기준) · 추정치

**한계 (감추지 않습니다)**:
  · 개발 환경은 야후가 차단돼 있어 **여기서는 확인할 수 없습니다.**
    어떤 칸이 실제로 오는지는 로봇 실행 기록으로만 알 수 있습니다.
  · 데이터 회사는 과거 분기를 **말없이 고쳐 씁니다.** 우리 모델의 핵심은
    "**첫** 신기록 돌파"라서 과거 최고점이 바뀌면 과거 판정이 통째로
    달라집니다. 그래서 받은 날짜(as_of)를 함께 적어 두고, **덮어쓰기
    전에 달라진 칸을 세어** 기록합니다.
  · 회사 가이던스는 여기 없습니다 — 그건 계속 보도자료에서 읽습니다
    (헌법이 컨센서스를 전망 입력으로 금지합니다).
"""

from __future__ import annotations

import json

# 야후 분기표에서 옮겨 적을 줄 이름 → 우리 이름
# (표에 없는 줄은 "없음" 으로 둡니다 — 지어내지 않습니다)
ROW_NAMES = {
    "Total Revenue": "revenue",
    "Cost Of Revenue": "cost_of_revenue",
    "Gross Profit": "gross_profit",
    "Operating Income": "op_income",
    "Net Income": "net_income",
    "Diluted EPS": "gaap_eps",
    "Diluted Average Shares": "diluted_shares",
}


def _number(value):
    """숫자면 float, 아니면 None (NaN·None·문자 전부 '없음')."""
    try:
        if value is None:
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out        # NaN 걸러내기


def quarters_from_frame(frame) -> list[dict]:
    """야후 분기표(DataFrame)를 우리 형식의 분기 목록으로 옮겨 적습니다.

    표는 **열이 분기 종료일**, 행이 항목입니다. 계산은 하지 않고 그대로
    옮기되, 매출총이익률만은 표의 두 값(매출총이익 ÷ 매출)으로 냅니다 —
    이것은 창작이 아니라 **표에 있는 두 수의 나눗셈**입니다.
    """
    if frame is None or getattr(frame, "empty", True):
        return []
    out = []
    for column in frame.columns:
        # 표에 없는 줄도 **자리는 만들되 "없음"** 으로 둡니다. 칸이 아예
        # 없으면 읽는 쪽이 KeyError 로 넘어지거나, 더 나쁘게는 0으로
        # 채워 넣게 됩니다 (창작 금지).
        row = {"period_end": str(column)[:10]}
        for name, 우리이름 in ROW_NAMES.items():
            row[우리이름] = (_number(frame.loc[name, column])
                          if name in frame.index else None)
        매출, 총이익 = row.get("revenue"), row.get("gross_profit")
        row["gross_margin_pct"] = (
            round(총이익 / 매출 * 100.0, 4)
            if 매출 and 총이익 is not None and 매출 > 0 else None
        )
        out.append(row)
    out.sort(key=lambda r: r["period_end"])
    return out


def announcements_from_frame(frame, 날짜뜻: str = "분기끝") -> list[dict]:
    """발표 기록에서 날짜와 EPS 를 옮겨 적습니다.

    이 표의 EPS 는 **월가 기준(데이터 회사가 통일시킨 값)** 입니다.
    회사가 발표한 조정 EPS 와 다를 수 있으므로 이름을 `street_eps` 로
    구분해 둡니다 — 나중에 헷갈리지 않게.

    ⚠️ **날짜의 뜻이 창구마다 다릅니다 (105차 ⑥ — 실측으로 찾은 결함).**

        earnings_history      줄 이름 = **분기 종료일**
        get_earnings_dates    줄 이름 = **실제 발표일시**

    예전에는 둘을 가리지 않고 전부 `announced_date` 라고 이름 붙였습니다.
    실물 NVDA 로 맞대어 보니 우리 발표일은 05-28 · 08-27 · 11-19 · 02-25
    인데 야후 쪽은 07-31 · 10-31 · 01-31 · 04-30 — **분기 종료일**이라
    한 칸도 안 맞았습니다 (전 종목 **0건**). 심판이 있는 줄 알았는데
    쓸 수가 없는 상태였고, 아무도 이 칸을 안 써서 **조용히** 그랬습니다.

    그래서 부르는 쪽이 날짜의 뜻을 알려 주고, 그 뜻대로 이름을 붙입니다.
    모르면 지어내지 않고 "분기끝"(예전 창구의 뜻)으로 둡니다.
    """
    if frame is None or getattr(frame, "empty", True):
        return []
    열이름 = {c.lower(): c for c in frame.columns}
    # 창구가 둘이라 열 이름이 다릅니다 (105차)
    #   earnings_history      → epsActual · epsEstimate
    #   get_earnings_dates    → Reported EPS · EPS Estimate
    # 둘 다 받아들입니다 — 어느 창구를 쓰든 같은 모양으로 담기게.
    실제 = (열이름.get("epsactual") or 열이름.get("eps actual")
            or 열이름.get("reported eps"))
    추정 = (열이름.get("epsestimate") or 열이름.get("eps estimate"))
    out = []
    for index, row in frame.iterrows():
        날짜 = str(index)[:10]
        if len(날짜) != 10 or 날짜[4] != "-":
            continue
        값 = _number(row[실제]) if 실제 else None
        추정값 = _number(row[추정]) if 추정 else None
        # get_earnings_dates 는 **앞으로 있을 발표**도 함께 줍니다 (값이
        # 아직 없음). 둘 다 없는 줄은 심판으로 쓸 수 없으므로 담지
        # 않습니다 — 값을 지어내는 것이 아니라 빈 줄을 안 싣는 것입니다.
        if 값 is None and 추정값 is None:
            continue
        키 = "announced_date" if 날짜뜻 == "발표일" else "period_end"
        out.append({
            키: 날짜,
            "날짜뜻": 날짜뜻,
            "street_eps": 값,
            "street_estimate": 추정값,
        })
    out.sort(key=lambda r: r.get("announced_date") or r.get("period_end") or "")
    return out


def fetch(ticker: str) -> dict:
    """한 종목의 분기표와 발표 기록을 받아 옵니다 (로봇 환경에서만 동작)."""
    import yfinance as yf

    import config as cfg

    handle = yf.Ticker(ticker)
    # 발표 기록은 **깊은 창구**로 받습니다 (105차 — 자세한 이유는
    # config.VENDOR_EARNINGS_LIMIT 주석에). 옛 판(또는 야후 변경)으로
    # 그 창구가 없으면 예전 창구로 돌아갑니다 — 없는 것보다 낫습니다.
    try:
        frame, 날짜뜻 = handle.get_earnings_dates(
            limit=cfg.VENDOR_EARNINGS_LIMIT), "발표일"
    except Exception:
        frame, 날짜뜻 = handle.earnings_history, "분기끝"
    return {
        "quarters": quarters_from_frame(handle.quarterly_income_stmt),
        "announcements": announcements_from_frame(frame, 날짜뜻),
    }


def changed_cells(before: dict, after: dict) -> int:
    """전에 받아 둔 값과 **달라진 칸**의 수 (소급 수정 감시).

    데이터 회사가 과거를 말없이 고치는지 보려는 것입니다. 세기만 하고
    막지는 않습니다 — 무엇이 바뀌었는지는 로봇 기록에 남습니다.
    """
    옛분기 = {q["period_end"]: q for q in (before or {}).get("quarters") or []}
    센다 = 0
    for q in (after or {}).get("quarters") or []:
        옛 = 옛분기.get(q["period_end"])
        if 옛 is None:
            continue                     # 새 분기는 '바뀜'이 아닙니다
        for key, value in q.items():
            if key == "period_end":
                continue
            if 옛.get(key) is not None and value != 옛.get(key):
                센다 += 1
    return 센다


def load(path: str) -> dict:
    """전에 저장해 둔 파일 (없으면 빈 것)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def collect(tickers: list[str], previous: dict, as_of: str,
            progress=print) -> tuple[dict, str]:
    """전 종목을 받아 새 보관본과 한 줄 요약을 돌려줍니다.

    한 종목이 실패해도 나머지는 계속합니다 — 이 축은 **관찰 전용**이라
    그날의 데이터 커밋을 막으면 안 됩니다.
    """
    옛티커 = (previous or {}).get("tickers") or {}
    새것: dict[str, dict] = {}
    받음 = 실패 = 0
    바뀐칸 = 0
    for ticker in tickers:
        try:
            got = fetch(ticker)
        except Exception as exc:                      # noqa: BLE001
            실패 += 1
            progress(f"  {ticker} 두 번째 자 실패: {type(exc).__name__}")
            옛 = 옛티커.get(ticker)
            if 옛:
                새것[ticker] = 옛                      # 옛 기록은 지키지 않고 그대로 둡니다
            continue
        바뀐칸 += changed_cells(옛티커.get(ticker), got)
        got["as_of"] = as_of
        새것[ticker] = got
        받음 += 1

    분기수 = sum(len(v.get("quarters") or []) for v in 새것.values())
    발표수 = sum(len(v.get("announcements") or []) for v in 새것.values())
    요약 = (f"두 번째 자(야후) — {받음}종목 성공 · {실패}종목 실패 · "
          f"분기 {분기수}개 · 발표기록 {발표수}개 · "
          f"과거 소급 변경 {바뀐칸}칸")
    return {"as_of": as_of,
            "설명": ("데이터 회사(야후)에서 받은 분기표입니다. **snapshot.json 과 "
                   "섞지 않습니다** — 우리 파서 값과 대조해 불일치를 재기 위한 "
                   "두 번째 자입니다. street_eps 는 회사가 발표한 조정 EPS 가 "
                   "아니라 월가 기준값이므로 이름을 구분해 두었습니다."),
            "tickers": 새것}, 요약


def to_json(archive: dict) -> str:
    return json.dumps(archive, ensure_ascii=False, separators=(",", ":"))
