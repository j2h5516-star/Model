"""
measure_store.py — 측정용 실데이터를 저장소로 보내는 연료 파이프라인
====================================================================

왜 필요한가 (전략.md 8장 1단계):
  개발 환경(코드를 고치는 곳)은 SEC·주가 접속이 차단되어 실물 데이터를
  볼 수 없습니다. 배포된 앱만 ① SEC 접속 ② 주가 수신 ③ 저장소 쓰기가
  전부 가능합니다. 그래서 앱이 방금 수집한 실데이터를 저장소에 커밋해
  두면, 다음 개발 세션이 그 실물로 측정(전략.md 7장)과 파서 수정을 합니다.

무엇을 저장하나:
  data/measure/snapshot.json          조정 EPS 시계열(발표일 포함) + 일봉 종가
  data/measure/raw/{종목}_{날짜}.txt   조정 EPS 를 못 읽은 보도자료 원문

창작 금지 원칙 (전략.md 1장):
  이 파일은 어떤 값도 계산하거나 고치지 않습니다. 수집된 그대로 옮겨
  적기만 합니다. 없는 값은 없는 채로(null) 남깁니다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import config as cfg
import forward_estimates as fe
import sec_fundamentals as sf

# 분기에서 스냅샷으로 옮겨 적는 항목 (큰 설명 텍스트는 제외 — 파일 크기)
#   filing_date    XBRL 뼈대 행은 분기 종료일, 8-K 단독 행은 발표일
#   announced_date 8-K 발표일(제출일) — 측정의 기준 시점 (전략.md 7장:
#                  회계연도가 제각각이라 발표일로만 전 종목을 정렬합니다)
#   op_income      논갭 영업이익 — 조정 EPS 와의 방향 일치(85.7%) 재측정용
EPS_FIELDS = (
    "filing_date",
    "announced_date",
    "period_label",
    "revenue",
    "op_income",
    "adj_eps",
    "adjusted_ebitda",   # 조정 EPS 미발표 회사(ZETA·APP 등)의 대체 잣대 — 회사 발표 원문
    "gaap_eps",
    # 매출총이익률(%) — 69차 추가. 수집기는 예전부터 보도자료와 XBRL 에서
    # 이 값을 읽고 있었는데 **스냅샷에 담지 않아** 개발 환경에서 쓸 수가
    # 없었습니다. 저장만 안 했을 뿐이라 수집 비용은 늘지 않습니다.
    # 쓰임: 매출이 늘 때 마진이 함께 오르는가(판가 쪽) 아니면 그대로인가
    #       (물량 쪽)를 가르는 재료 (주인 제안, 아직 등록 전 탐색용).
    "gross_margin_pct",
    # 보도자료가 덮어쓰기 **전**의 XBRL 값 (90차 — 재고 뒤집기의 1단계).
    #
    # 값은 하나도 안 바뀝니다. 나란히 적어 두기만 합니다. 다음 세션이
    # 야후를 심판으로 "XBRL 이 맞은 횟수 vs 보도자료가 맞은 횟수"를 칸별로
    # 세어, 어느 쪽을 본선으로 삼을지 **짐작이 아니라 숫자로** 정합니다.
    #
    # 왜 이 셋인가: XBRL 은 값마다 기간(3개월)·단위·부문이 태그로 붙어
    # 있어, 89차까지 실측된 오류 네 종류(전년 열·9개월 누적·연간값·부문)가
    # 구조적으로 생길 수 없습니다. 조정 EPS·EBITDA 는 XBRL 에 없으므로
    # 여기 없습니다 — 그건 계속 보도자료 파서의 몫입니다.
    "gaap_eps_xbrl",
    "revenue_xbrl",
    "gross_margin_pct_xbrl",
    # 150차-V — 보도자료 표의 단위를 XBRL 을 자로 삼아 맞춘 자취.
    #   op_income_xbrl   덮어쓰기 전의 XBRL 영업이익 (자로 쓴 값)
    #   unit_scale_fixed 실제로 곱한 배수 (안 고쳤으면 없음)
    # 남기는 이유: 나중에 "이 값이 왜 이렇게 됐나"를 사람이 되짚을 수
    # 있어야 합니다. 고친 자취를 안 남기면 감사할 수가 없습니다.
    "op_income_xbrl",
    "unit_scale_fixed",
    # 150차-AL — 그 회계연도의 **연간** GAAP EPS (XBRL 12개월 값).
    #
    # 이 칸이 있는 행은 그 기간끝이 **결산일**이라는 뜻입니다 — 12개월
    # 값은 결산일에만 있기 때문입니다. 그래서 회계 달력을 짐작할 필요가
    # 없습니다.
    #
    # 쓰임: 보도자료가 **4분기 자리에 연간값을 넣는** 사고를 가립니다.
    # 150차-AK 에서 항등식(Q1+Q2+Q3+Q4=연간)만으로는 연간값과 진짜 성수기
    # 4분기를 원리적으로 못 가른다는 것을 실측으로 확인했습니다. 가르려면
    # **식 밖에서 온 값**이 있어야 하고, 그것이 이 칸입니다.
    "gaap_eps_annual_xbrl",
    "source",
    # 이 분기에 보도자료가 실제로 붙었는가 (98차 계기).
    #
    # 조정 EPS 가 빈 칸일 때 "수집을 못 한 것"인지 "회사가 안 준 것"인지
    # 가르는 유일한 표시입니다. 대응이 정반대인데(고칠 수 있음 vs 없음이
    # 정답) 지금까지는 짐작밖에 할 수 없었습니다.
    #
    # ⚠️ `source` 로는 못 가릅니다 — 그 칸은 **논갭 영업이익의 출처**만
    #    적어서, 보도자료가 붙었어도 그 안에 논갭 영업이익이 없으면
    #    '근사치'로 남습니다 (실물 FN·QCOM·TER).
    "press_matched",
)


def eps_rows(quarters: list[dict] | None) -> list[dict]:
    """분기 목록에서 측정에 필요한 항목만 그대로 옮겨 적습니다.

    가이던스 EPS(H3 측정용)는 각 분기의 보도자료 뒷부분(guidance_text)에서
    원문 그대로 읽어 함께 담습니다 — 회사가 안 줬으면 없음(None)입니다.
    """
    if not quarters:
        return []
    rows = []
    for quarter in quarters:
        row = {key: quarter.get(key) for key in EPS_FIELDS}
        guidance_text = quarter.get("guidance_text") or ""
        guid = fe.parse_guidance_eps(guidance_text)
        row["guid_eps_low"] = guid["low"]
        row["guid_eps_high"] = guid["high"]
        row["guid_eps_mid"] = guid["mid"]
        # 매출·조정 EBITDA 가이던스 (대책 2) — EPS 가이던스를 안 주는
        # 회사가 대부분이라, 회사가 직접 준 다른 전망 숫자도 원문 그대로 담습니다
        rev = fe.parse_guidance_revenue(guidance_text)
        row["guid_rev_low"] = rev["low"]
        row["guid_rev_high"] = rev["high"]
        row["guid_rev_mid"] = rev["mid"]
        ebitda = fe.parse_guidance_ebitda(guidance_text)
        row["guid_ebitda_low"] = ebitda["low"]
        row["guid_ebitda_high"] = ebitda["high"]
        row["guid_ebitda_mid"] = ebitda["mid"]
        rows.append(row)
    return rows


def price_rows(daily_df) -> dict:
    """일봉 표에서 날짜와 종가만 그대로 옮겨 적습니다.

    측정(전략.md 7장)은 "발표 다음 거래일 종가부터 60거래일"을 재므로
    주봉이 아니라 **일봉**이 필요합니다. 종가는 수정주가(액면분할·배당
    반영)이고, 소수 4자리로 반올림합니다 (파일 크기 절약 — 1원 미만
    차이는 측정에 영향이 없습니다).
    """
    if daily_df is None or getattr(daily_df, "empty", True) or "Close" not in daily_df:
        return {"dates": [], "close": []}
    closes = daily_df["Close"].dropna()
    return {
        "dates": [str(ts)[:10] for ts in closes.index],
        "close": [round(float(value), 4) for value in closes],
    }


def build_files(
    tickers: list[str],
    daily_map: dict,
    reports: list[dict],
    load_quarters=None,
    now=None,
) -> tuple[dict[str, str], str]:
    """저장소에 커밋할 파일들을 만듭니다.

    입력:
      tickers        현재 대시보드의 종목 목록
      daily_map      {티커: 일봉 표} — 앱의 주가 저장소 (기준지수 SPY 포함)
      reports        종목별 수집 진단 (raw_texts = 파싱 실패 원문)
      load_quarters  분기 데이터를 읽는 함수 (기본: SEC 디스크 캐시.
                     테스트에서 가짜로 갈아끼울 수 있습니다)

    반환: ({저장 경로: 내용}, 요약 문장)

    분기 데이터를 화면용 점수가 아니라 **디스크 캐시(수집 원본)** 에서
    읽는 이유: 화면 경로는 기준자 교체와 값 검사가 분기를 바꾸거나
    버립니다. 측정은 수집된 원본에서 직접 해야 그 판단들도 검증할 수
    있습니다.
    """
    if load_quarters is None:
        load_quarters = sf.load_cache
    if now is None:
        now = datetime.now(timezone.utc)

    wanted = list(dict.fromkeys(list(tickers) + [cfg.BENCHMARK]))

    eps: dict[str, list[dict]] = {}
    missing_eps: list[str] = []
    for ticker in tickers:
        try:
            quarters = load_quarters(ticker)
        except Exception:
            quarters = None
        rows = eps_rows(quarters)
        eps[ticker] = rows          # 없으면 빈 목록 그대로 — 창작 금지
        if not rows:
            missing_eps.append(ticker)

    prices: dict[str, dict] = {}
    missing_price: list[str] = []
    for ticker in wanted:
        rows = price_rows(daily_map.get(ticker))
        if rows["dates"]:
            prices[ticker] = rows
        else:
            missing_price.append(ticker)

    snapshot = {
        "saved_at": now.isoformat(),
        "설명": (
            "측정용 실데이터 (전략.md 8장 1단계). 배포된 앱이 수집한 그대로이며 "
            "어떤 값도 계산하거나 고치지 않았습니다. announced_date(발표일)가 "
            "측정의 기준 시점입니다."
        ),
        "tickers": list(tickers),
        "benchmark": cfg.BENCHMARK,
        "eps": eps,
        "prices": prices,
    }

    files: dict[str, str] = {
        f"{cfg.MEASURE_DIR}/snapshot.json": json.dumps(
            snapshot, ensure_ascii=False, separators=(",", ":")
        )
    }

    # 원문 보관 — 다음 세션이 파서를 고칠 실물 자료
    #
    # 두 종류가 섞여 들어옵니다. 이름표(filing_date 앞에 붙는 말머리)로
    # 구분하고, **파일 이름과 머리말에 그 뜻을 그대로 적습니다.**
    #   (없음)   잣대값을 하나도 못 읽은 공시 — "못 읽음"
    #   의심정수_ 정수 EPS 가 남은 공시 — "값은 있는데 수상함"
    #   부탁_    조사가 콕 집어 부탁한 공시 (73차) — "잘못 읽은 것 같음"
    #
    # ⚠️ 예전에는 날짜를 앞 10글자로 잘라 "부탁_2025-02" 처럼 **달까지만**
    #    남아, 같은 달에 두 건이면 파일이 서로 덮어썼습니다(실측 3건).
    #    말머리와 날짜를 따로 다루어 날짜를 온전히 남깁니다.
    raw_count = 0
    이름표 = {"의심정수_": "의심정수", "부탁_": "부탁"}
    설명 = {
        "": "잣대값 파싱 실패 원문",
        "의심정수": "정수 EPS 의심 원문 (값은 그대로 두고 감사용으로 보관)",
        "부탁": "조사가 부탁한 원문 (73차 — 잘못 읽은 값의 원인 확인용)",
    }
    for report in reports:
        ticker = report.get("ticker", "종목미상")
        for raw in report.get("raw_texts") or []:
            원본 = str(raw.get("filing_date", ""))
            종류 = ""
            for 말머리, 이름 in 이름표.items():
                if 원본.startswith(말머리):
                    종류, 원본 = 이름, 원본[len(말머리):]
                    break
            date_text = 원본[:10] or "날짜미상"
            꼬리 = f"_{종류}" if 종류 else ""
            path = f"{cfg.MEASURE_DIR}/raw/{ticker}_{date_text}{꼬리}.txt"
            header = (
                f"# {설명[종류]} — {ticker} {date_text}\n"
                f"# 출처: {raw.get('url', '')}\n\n"
            )
            files[path] = header + str(raw.get("text", ""))
            raw_count += 1

    summary_parts = [
        f"조정 EPS 시계열 {len(tickers) - len(missing_eps)}종목",
        f"일봉 {len(prices)}종목(기준지수 포함)",
        f"파싱 실패 원문 {raw_count}건",
    ]
    if missing_eps:
        summary_parts.append(f"실적 캐시 없음: {', '.join(missing_eps)}")
    if missing_price:
        summary_parts.append(f"주가 없음: {', '.join(missing_price)}")
    return files, " · ".join(summary_parts)
