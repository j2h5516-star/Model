"""
dataset.py — 데이터 계층 (v3 4단계, 설계도.md ①)
==================================================

하는 일 (재료 손질):
  수집 로봇이 커밋한 data/measure/snapshot.json 을 읽어,
  ⑴ 검사를 통과한 값만 통행시키고
  ⑵ 시간순으로 정렬해
  ⑶ 측정 장치(5단계)가 쓰기 좋은 형태의 분기 표·가격 표로 돌려줍니다.

경계 (설계도.md 3장):
  · snapshot.json 은 **읽기만** 합니다. 파일을 쓰지 않습니다 —
    진실은 snapshot 하나여야 하고, 손질본 파일은 혼란의 씨앗입니다.
  · 값을 만들거나 고치지 않습니다 (창작 금지). 검사에서 탈락한 값은
    "없음"(None)으로 둡니다 — 없음이 틀림보다 안전합니다.
    ⚠️ 단 하나의 예외 (112차): **액면분할 단위 환산.** 회사는 EPS 를 발표
    당시 주식 수 기준으로 적고 주가·야후는 분할을 소급 반영하므로, 분할
    전후의 주당 값은 **단위가 다른 숫자**입니다 (실물 SMCI 22.09 = 소급
    2.20 × 10). 그대로 이으면 TTM 추세에 가짜 급락이 생깁니다. 공식 분할
    기록이 있을 때만, 주당 칸만, 현재 주식 수 기준으로 나눕니다 — 값을
    지어내는 것이 아니라 이미 쓰는 수정주가와 같은 단위 맞추기입니다.
  · TTM·신고점 같은 파생 계산은 하지 않습니다 (측정 장치의 일).
  · 인터넷에 접속하지 않습니다 (수집은 로봇만).

무엇을 검사하나:
  · 숫자 칸: 숫자가 아니거나 무한대(NaN/inf)면 "없음"으로
  · 주당 금액(조정 EPS·GAAP EPS·가이던스): 절대값이 상한을 넘으면 "없음"으로
    — 파서의 소수점 규칙이 1차 방어이고, 이것은 2차 방어입니다
  · 날짜 칸: 형식이 YYYY-MM-DD 가 아니면 "없음"으로,
    분기의 시간축(filing_date)이 아예 없으면 그 행을 버립니다
  · 가격: 날짜·종가 개수가 어긋난 종목은 가격 전체를 버리고,
    0 이하·숫자 아님·중복 날짜는 점 단위로 버립니다
  · 기준지수(SPY)가 없으면 측정 자체가 불가능하므로 즉시 오류를 냅니다

무엇을 했는지는 전부 notes 에 남깁니다 — 조용히 버리지 않습니다.
"""

from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime

import config as cfg

# 주당 금액의 절대 상한 (2차 방어).
#
# 근거 (2026-08-12 snapshot 실측 — 실행 출력에서 복사):
#   · 검증된 정상 최대값: SNDK 26 Q4 조정 EPS 39.25 (실물 시험 있음)
#   · 실제 오류값: 정수를 잘못 문 202.0 이 30건 — 전부 100 위
#   상한 100은 정상 최대의 2.5배 여유를 두면서 실측 오류를 전부 막습니다.
# 한계 (감추지 않습니다): 100 아래의 오류(예: MCHP 의 31.0)는 이 상한으로
#   못 잡습니다. 그건 파서의 소수점 규칙(1차 방어)이 막습니다.
PER_SHARE_ABS_LIMIT = 100.0

# 분기 행에서 통행시키는 칸 (measure_store.EPS_FIELDS + 가이던스 3칸)
_NUMBER_FIELDS = ("revenue", "op_income", "adj_eps", "adjusted_ebitda",
                  "gaap_eps", "gross_margin_pct",
                  "guid_eps_low", "guid_eps_high", "guid_eps_mid",
                  "guid_rev_low", "guid_rev_high", "guid_rev_mid",
                  "guid_ebitda_low", "guid_ebitda_high", "guid_ebitda_mid")
_PER_SHARE_FIELDS = ("adj_eps", "gaap_eps",
                     "guid_eps_low", "guid_eps_high", "guid_eps_mid")


def _finite_number(value) -> bool:
    """진짜 숫자인가 (NaN·무한대·불리언·문자 제외)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _valid_date(value) -> bool:
    """YYYY-MM-DD 형식의 실제 날짜인가."""
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value[:10], "%Y-%m-%d")
        return True
    except ValueError:
        return False


def load(path: str | None = None) -> dict:
    """snapshot.json 을 읽습니다 (읽기 전용)."""
    if path is None:
        path = os.path.join(cfg.MEASURE_DIR, "snapshot.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _clean_quarters(eps_map: dict, notes: list[str]) -> dict:
    """종목별 분기 행을 검사·정렬합니다. 원본은 바꾸지 않습니다."""
    cleaned: dict[str, list[dict]] = {}
    for ticker, rows in (eps_map or {}).items():
        kept: list[dict] = []
        for row in rows or []:
            # 시간축이 없는 행은 어디에도 놓을 수 없으므로 버립니다
            if not _valid_date(row.get("filing_date")):
                notes.append(
                    f"{ticker} {row.get('period_label', '?')}: "
                    "filing_date 가 없거나 형식이 이상해 행을 버렸습니다"
                )
                continue
            clean = dict(row)   # 사본 — 원본 불변

            # 발표일: 형식이 이상하면 "없음"으로 (사건 후보에서 빠질 뿐,
            # 분기 이력으로는 남습니다)
            if clean.get("announced_date") is not None and not _valid_date(
                clean["announced_date"]
            ):
                notes.append(
                    f"{ticker} {clean.get('period_label', '?')}: "
                    f"발표일 {clean['announced_date']!r} 형식이 이상해 없음 처리"
                )
                clean["announced_date"] = None

            # 숫자 칸: 숫자가 아니면 "없음"으로 (고치지 않고 버림)
            for field in _NUMBER_FIELDS:
                value = clean.get(field)
                if value is not None and not _finite_number(value):
                    notes.append(
                        f"{ticker} {clean.get('period_label', '?')}: "
                        f"{field}={value!r} 는 숫자가 아니어서 없음 처리"
                    )
                    clean[field] = None

            # 매출 최저 하한: 자릿수가 무너진 매출(실물: APP 21Q4 3.55달러 —
            # 주당·백만 단위 착오 3건 실측)은 없음으로. 이 유니버스에 분기
            # 매출 1만 달러 미만 회사는 없습니다.
            if clean.get("revenue") is not None and clean["revenue"] < 10_000:
                # 잔해 판정은 "형제 행보다 매출이 100배 작은가"를 보는데,
                # 여기서 먼저 지워 버리면 비교할 값이 사라집니다. 기억해 둡니다.
                clean["_raw_revenue"] = clean["revenue"]
                notes.append(
                    f"{ticker} {clean.get('period_label', '?')}: "
                    f"매출 {clean['revenue']} 은 자릿수가 무너진 값이라 없음 처리"
                )
                clean["revenue"] = None

            # 영업이익 단위 검사 (25차 감사): 보도자료 표는 '천/백만 달러'
            # 단위인데 병합 때 매출만 XBRL 달러값으로 바뀌면 단위가 어긋난다
            # (실물: CRDO 216,722 ↔ 매출 4.37억 = 마진 0.05%). 매출이 달러
            # 단위로 확실할 때(1천만 이상) 마진이 상식 밖이면 없음 처리.
            #   · |영업이익| < 매출의 0.1% → 단위 미환산·주당값 오인 의심
            #   · 영업이익 > 매출의 90% → 표의 다른 숫자를 집었을 가능성
            #   · 마진 < MARGIN_MIN_PCT(-500%) → 같은 이유
            # 진짜 0.1% 미만 마진 분기를 잃을 수 있으나, "없음"은 안전하고
            # "1000배 틀림"은 위험하다 (창작 금지 원칙의 버림 쪽).
            rev, op = clean.get("revenue"), clean.get("op_income")
            if rev is not None and op is not None and rev >= 10_000_000:
                margin = op / rev
                if (abs(op) < rev * 0.001 or op > rev * 0.9
                        or margin * 100.0 < cfg.MARGIN_MIN_PCT):
                    notes.append(
                        f"{ticker} {clean.get('period_label', '?')}: "
                        f"영업이익 {op} 은 매출 {rev} 대비 마진 "
                        f"{margin * 100.0:.3f}% — 단위 착오/오인 의심이라 없음 처리"
                    )
                    clean["op_income"] = None

            # 조정 EBITDA 단위 검사 (46차 감사) — 위 영업이익 가드와 같은 사고가
            # EBITDA 칸에서도 일어나고 있었습니다. 실물 85건 · 10종목
            # (BE CIEN CMCSA ENTG ETSY GNRC PINS PWR SNAP TTMI).
            # 가장 아픈 예: CIEN 은 25 Q3 157,962,000 → 25 Q4 **205,536** 으로
            # 저장돼 있어 **99.9% 급감**으로 읽혔습니다. 실제로는 205,536천 달러
            # (2.06억)로 **증가**입니다. 이 오염이 광통신 묶음의 "이익 델타"를
            # 통째로 뒤집어, 주도섹터 판정을 틀리게 만들고 있었습니다.
            # EBITDA 는 이익이므로 매출을 넘을 수 없고, 매출의 0.1% 미만이면
            # 천/백만 단위 미환산입니다. 고치지 않고 버립니다 (창작 금지).
            ebitda = clean.get("adjusted_ebitda")
            if rev is not None and ebitda is not None and rev >= 10_000_000:
                ratio = ebitda / rev
                if (abs(ebitda) < rev * 0.001 or ebitda > rev
                        or ratio * 100.0 < cfg.MARGIN_MIN_PCT):
                    notes.append(
                        f"{ticker} {clean.get('period_label', '?')}: "
                        f"조정 EBITDA {ebitda} 은 매출 {rev} 대비 "
                        f"{ratio * 100.0:.3f}% — 단위 착오 의심이라 없음 처리"
                    )
                    clean["adjusted_ebitda"] = None

            # 매출총이익률 범위 검사 (69차). 이익률은 100%를 넘을 수 없고
            # (매출총이익 ≤ 매출), -100% 아래면 원가가 매출의 2배를 넘는
            # 것이라 표의 다른 숫자를 집었을 가능성이 큽니다. 고치지 않고
            # 버립니다 (창작 금지).
            gm = clean.get("gross_margin_pct")
            if gm is not None and not (-100.0 <= gm <= 100.0):
                notes.append(
                    f"{ticker} {clean.get('period_label', '?')}: "
                    f"매출총이익률 {gm}% 는 -100~100 범위 밖이라 없음 처리"
                )
                clean["gross_margin_pct"] = None

            # 주당 금액 상한 (2차 방어 — 위 PER_SHARE_ABS_LIMIT 주석 참조)
            for field in _PER_SHARE_FIELDS:
                value = clean.get(field)
                if value is not None and abs(value) > PER_SHARE_ABS_LIMIT:
                    notes.append(
                        f"{ticker} {clean.get('period_label', '?')}: "
                        f"{field}={value} 는 주당 금액 상한({PER_SHARE_ABS_LIMIT})을 "
                        "넘어 없음 처리 (표의 다른 숫자를 잘못 집었을 가능성)"
                    )
                    clean[field] = None

            kept.append(clean)

        kept.sort(key=lambda r: r["filing_date"])
        # 정렬 뒤에야 "직전 4분기"를 말할 수 있으므로 여기서 누적값을 거릅니다
        _drop_repeated_revenue(ticker, kept, notes)
        _drop_cumulative_values(ticker, kept, notes)
        _drop_parse_debris(ticker, kept, notes)
        _drop_same_day_siblings(ticker, kept, notes)
        for row in kept:
            row.pop("_raw_revenue", None)      # 내부용 표시는 밖으로 내보내지 않습니다
        cleaned[ticker] = kept
    return cleaned



# 누적(YTD·연간)값이 분기 칸에 들어온 것 (52차 감사 — 산술로 확증)
# ---------------------------------------------------------------------------
# 보도자료에는 "이번 분기"와 "누적 6개월/9개월/연간"이 나란히 실립니다.
# 파서가 누적 쪽을 물면 그 분기만 몇 배로 튀고, **다음 분기에 정상으로
# 돌아오면서 가짜 급등·급락을 한 쌍 만듭니다.** 이익 델타가 통째로 뒤집힙니다.
#
# 산술 지문: 누적값은 **직전 4분기의 합과 거의 같습니다**
#   (사업이 평평하거나 완만히 자라면 연간 ≈ 최근 4분기 합)
#   실측: QCOM 10.22 vs 9.77 · GS 30.06 vs 29.24 · MU 12.20 vs 11.28
#
# 판정에 쓰는 값은 **그 행보다 앞선 값들뿐**입니다 — 미래를 보지 않습니다.
# 부호가 다르면(적자 분기) 누적이 아니므로 건드리지 않습니다.
# 고치지 않고 버립니다 (창작 금지).
_CUMULATIVE_FIELDS = ("adj_eps", "adjusted_ebitda", "gaap_eps")
_CUMULATIVE_MULTIPLE = 2.5      # 지난 값들의 중앙값 대비 이 배수를 넘고
_CUMULATIVE_TOLERANCE = 0.20    # 직전 4분기 합과 이 비율 안으로 같으면
_CUMULATIVE_MIN_HISTORY = 4     # 비교할 지난 분기가 이만큼은 있어야 한다


def _one_cumulative_pass(ticker: str, rows: list[dict], notes: list[str]) -> bool:
    """누적값을 **한 칸만** 찾아 없음 처리하고, 지웠으면 True 를 돌려줍니다."""
    for field in _CUMULATIVE_FIELDS:
        seen = [(index, row[field]) for index, row in enumerate(rows)
                if row.get(field) is not None]
        for position in range(_CUMULATIVE_MIN_HISTORY, len(seen)):
            index, value = seen[position]
            # 아래 `value > middle * 배수` 가 음수·0 을 이미 배제합니다.
            # 적자 분기는 누적값일 수 없으므로 건드리지 않게 됩니다.
            past = [v for _, v in seen[:position]]
            middle = statistics.median(abs(v) for v in past)
            total = sum(v for _, v in seen[position - 4:position])
            if middle <= 0 or total <= 0:
                continue
            if (value > middle * _CUMULATIVE_MULTIPLE
                    and abs(value - total) / total < _CUMULATIVE_TOLERANCE):
                notes.append(
                    f"{ticker} {rows[index].get('period_label', '?')}: "
                    f"{field}={value} 는 직전 4분기 합 {total:.2f} 과 거의 같고 "
                    f"지난 중앙값 {middle:.2f} 의 {value / middle:.1f}배 — "
                    "누적(YTD·연간)값이 분기 칸에 들어온 것으로 보아 없음 처리"
                )
                rows[index][field] = None
                return True
    return False


def _drop_cumulative_values(ticker: str, rows: list[dict], notes: list[str]) -> None:
    """분기 칸에 들어온 누적(YTD·연간)값을 없음 처리합니다 (제자리 수정).

    **왜 한 번이 아니라 변화가 없을 때까지 반복하는가** (73차, 실물 VZ·GS):
      이 검사는 "직전 4분기 합과 거의 같은가"를 봅니다. 그런데 그 직전
      4분기 안에 **이미 오염된 값이 끼어 있으면 합이 부풀어** 진짜 연간값이
      통과해 버립니다. 오염이 오염을 가려 주는 것입니다.

      실물 VZ(조정 EPS): 1월 발표 행마다 연간값이 들어와 있습니다 —
        5.18 · 4.71 · 4.59 · 4.71 (분기 실제는 1.2 안팎)
      두 번째 4.71 을 잴 때 직전 4분기 합이 5.18+1.2+1.21+1.22 = 8.81 로
      부풀어 "합과 다르다"고 판정돼 살아남았습니다. 앞의 5.18 을 먼저
      지우고 다시 재면 합이 4.95 가 되어 4.71 이 제대로 걸립니다.

      그래서 **앞에서부터 한 칸씩 지우고 매번 다시 재기**를, 더 지울 것이
      없을 때까지 반복합니다. 문턱은 하나도 바꾸지 않았습니다 — 같은 자를
      끝까지 대 보는 것뿐입니다 (원칙 6: 신호보다 장치를 먼저 의심).

      실측(73차): 한 번만 재면 38칸, 반복하면 57칸 (10종목 19칸 추가 —
      GS 연간 EPS 22.87/40.54/51.32, DELL 7.99/7.98/8.38 등).

    한 번에 한 칸씩만 지우므로 칸 수만큼 돌면 반드시 멈춥니다. 그래도
    무한 반복을 원천 봉쇄하려고 상한을 걸어 둡니다.
    """
    limit = len(rows) * len(_CUMULATIVE_FIELDS) + 1
    for _ in range(limit):
        if not _one_cumulative_pass(ticker, rows, notes):
            return



# 같은 발표일에 두 행이 있고 한쪽 매출만 무너진 경우 (52차 감사 — 원문 확증)
# ---------------------------------------------------------------------------
# 실적 보도자료 한 장에서 두 행이 만들어질 때가 있습니다. 한쪽은 진짜 실적이고,
# 다른 한쪽은 파서가 **배당금·주식수·수익률 표**를 실적으로 오인한 잔해입니다.
# 잔해 쪽은 매출이 함께 무너져 있어 구별할 수 있습니다.
#
# 실물 (JPM, 원문 확증): 같은 발표일 2024-04-12 에
#   · 매출 17,653 · gaap_eps 4.45   ← 진짜 분기 실적
#   · 매출     19.3 · gaap_eps 1.15   ← **분기 배당금**이 EPS 칸에 들어옴
# 이 잔해가 남아 있으면 JPM 델타 9쌍이 전부 "하락"으로 읽힙니다.
#
# ⚠️ "매출이 작다"만으로 자르면 안 됩니다. 매출을 **백만 달러 단위**로 적는
#    회사(AMD 4,313 · MCHP 1,649)의 정상 행까지 지웁니다 — 실측 172칸.
#    그래서 **같은 발표일에 100배 이상 큰 매출을 가진 형제 행이 있을 때만**
#    자릅니다. 고치지 않고 버립니다 (창작 금지).
_SIBLING_REVENUE_RATIO = 100.0    # 형제 행 매출이 이 배수 이상 크면 잔해로 본다


def _revenue_before_guard(row: dict) -> float:
    """매출 하한 가드가 지우기 **전**의 값 (없으면 지금 값)."""
    return row.get("_raw_revenue") or row.get("revenue") or 0.0


_SAME_DAY_NOTE = (
    "같은 발표일에 두 행이 있어 어느 쪽이 그 분기 실적인지 가릴 수 없습니다"
)


_REPEATED_REVENUE_MIN = 4


def _drop_repeated_revenue(ticker: str, rows: list[dict],
                           notes: list[str]) -> None:
    """한 종목 안에서 **똑같은 매출값**이 여러 분기에 반복되면 자리채움입니다.

    진짜 매출은 분기마다 다릅니다. 소수점까지 똑같은 값이 4개 분기 이상
    나온다면 그것은 측정값이 아니라 채워 넣은 값입니다.

    실물 (68차 실측):
      OXY  매출 1           이 23분기 중 20개
      BAC  매출 2,000,000   이 24분기 중 17개  (실제 매출은 250억 달러대)
      COF  매출 -5,000,000  이 24분기 중  8개  (**음수 매출**)
      PG   매출 2           이 20분기 중  6개
      BE   매출 1,000 · MRK 매출 -9 · ETN 매출 232억(연간값으로 보임)

    왜 고쳐야 하나: 매출은 잣대가 아니지만 **다른 가드들의 잣대**입니다.
    형제 행 잔해 판정(100배 비율)·영업이익 마진 검사·EBITDA 단위 검사가
    모두 매출을 기준으로 삼습니다. 가짜 매출이 통행하면 그 가드들이
    **틀린 기준으로 판단**합니다. 실제로 BAC 은 형제 행 매출이 둘 다
    2,000,000 으로 같아 비율 규칙이 아무것도 못 걸렀습니다.

    값을 고치지 않고 **없음**으로 둡니다 (창작 금지). 매출이 없으면 그
    가드들은 그냥 넘어갑니다 — 틀린 기준으로 판단하는 것보다 낫습니다.
    """
    from collections import Counter
    values = [row.get("revenue") for row in rows if row.get("revenue") is not None]
    if len(values) < _REPEATED_REVENUE_MIN:
        return
    counts = Counter(values)
    fake = {v for v, n in counts.items() if n >= _REPEATED_REVENUE_MIN}
    if not fake:
        return
    for row in rows:
        if row.get("revenue") in fake:
            notes.append(
                f"{ticker} {row.get('period_label', '?')}: "
                f"매출 {row['revenue']:,.0f} 이 이 종목의 "
                f"{counts[row['revenue']]}개 분기에 똑같이 나와 자리채움 값으로 "
                "보고 없음 처리 (진짜 매출은 분기마다 다릅니다)"
            )
            row["revenue"] = None


def _drop_same_day_siblings(ticker: str, rows: list[dict],
                            notes: list[str]) -> None:
    """같은 발표일 형제 행인데 **어느 쪽도 매출로 가릴 수 없으면** 둘 다 버립니다.

    이 저장소는 이미 형제 행을 매출 비율(100배)로 가려냅니다
    (`_drop_parse_debris`) — 배당금 행은 매출이 19.3 처럼 무너져 있고
    진짜 실적 행은 17,653 이라 잘 갈립니다.

    67차에 그 규칙이 **못 잡는 경우**를 찾았습니다: **양쪽 매출이 다
    무너진 날**입니다.
      JPM 2025-01-15 → 매출 20.3 / gaap 1.4  (배당금)
                       매출 13.0 / gaap 4.82 (진짜 실적)
    비율이 1.56배뿐이라 100배 규칙이 안 걸리고, 게다가 **가짜 쪽 매출이
    더 큽니다.** 그래서 두 행이 다 남아 JPM 이익 시계열이
    1.4 → 5.08 → 1.4 → 5.25 로 톱니가 됐습니다(TTM·신기록·델타가 무의미).

    1.4·1.5 가 배당금인 근거: JPM 의 분기 배당 인상 일정
    (1.15 → 1.25 → 1.40 → 1.50) 과 값·시점이 정확히 일치합니다.

    **왜 골라서 남기지 않는가**: "큰 쪽이 진짜"는 크기 규칙이고, 54차에
    크기 규칙으로 **진짜 실적 하락 71건을 지운** 사고가 있었습니다.
    매출로도 못 가리는 것이 이 경우의 정의입니다. 가릴 방법이 없으면
    **없음**으로 둡니다 — 없음은 안전하고 틀림은 위험합니다 (원칙 1).

    ⚠️ 매출이 하나라도 멀쩡한 날은 건드리지 않습니다. 그 경우는 기존
    100배 규칙의 영역이고, 그 규칙이 "비슷한 크기면 둘 다 남긴다"고
    이미 정해 두었습니다(54차 등록).
    """
    by_day: dict[str, list[dict]] = {}
    for row in rows:
        day = row.get("announced_date")
        if day:
            by_day.setdefault(day, []).append(row)
    for day, group in by_day.items():
        if len(group) < 2:
            continue
        # 매출이 하한 검사를 통과한 행이 하나라도 있으면 가릴 수 있다 → 넘어감
        if any(row.get("revenue") is not None for row in group):
            continue
        for row in group:
            dropped = [f for f in _CUMULATIVE_FIELDS if row.get(f) is not None]
            if not dropped:
                continue
            notes.append(
                f"{ticker} {row.get('period_label', '?')} ({day}): "
                "같은 발표일 형제 행인데 양쪽 매출이 다 무너져 어느 쪽이 그 "
                f"분기 실적인지 가릴 수 없어 {'·'.join(dropped)} 없음 처리"
            )
            for field in dropped:
                row[field] = None


def _drop_parse_debris(ticker: str, rows: list[dict], notes: list[str]) -> None:
    """같은 발표일의 형제 행보다 매출이 100배 이상 작은 행의 값을 버립니다."""
    by_day: dict[str, list[dict]] = {}
    for row in rows:
        day = row.get("announced_date")
        if day:
            by_day.setdefault(day, []).append(row)
    for day, group in by_day.items():
        if len(group) < 2:
            continue
        revenues = [_revenue_before_guard(r) for r in group]
        biggest = max(revenues)
        if biggest <= 0:
            continue
        for row in group:
            revenue = _revenue_before_guard(row)
            if not (0 < revenue < biggest / _SIBLING_REVENUE_RATIO):
                continue
            dropped = [f for f in _CUMULATIVE_FIELDS if row.get(f) is not None]
            if not dropped:
                continue
            notes.append(
                f"{ticker} {row.get('period_label', '?')} ({day}): "
                f"같은 발표일 형제 행의 매출 {biggest:,.0f} 에 견줘 이 행 매출은 "
                f"{revenue} — 배당금·주식수 표를 실적으로 오인한 잔해로 보아 "
                f"{'·'.join(dropped)} 없음 처리"
            )
            for field in dropped:
                row[field] = None


def _clean_prices(price_map: dict, notes: list[str]) -> dict:
    """종목별 일봉을 검사·정렬합니다. 원본은 바꾸지 않습니다."""
    cleaned: dict[str, dict] = {}
    for ticker, series in (price_map or {}).items():
        dates = (series or {}).get("dates") or []
        closes = (series or {}).get("close") or []
        if len(dates) != len(closes):
            # 날짜와 종가의 짝이 어긋난 표는 어느 쪽도 믿을 수 없습니다
            notes.append(
                f"{ticker}: 가격 표의 날짜({len(dates)})와 종가({len(closes)}) "
                "개수가 달라 이 종목 가격을 통째로 버렸습니다"
            )
            continue

        by_date: dict[str, float] = {}
        dropped = 0
        duplicated = 0
        for date, close in zip(dates, closes):
            if not _valid_date(date) or not _finite_number(close) or close <= 0:
                dropped += 1
                continue
            if date in by_date:
                duplicated += 1   # 같은 날짜가 두 번이면 뒤의 값을 씁니다
            by_date[date] = float(close)
        if dropped:
            notes.append(f"{ticker}: 이상한 가격 점 {dropped}개를 버렸습니다")
        if duplicated:
            notes.append(f"{ticker}: 중복 날짜 {duplicated}개 — 뒤의 값만 남겼습니다")

        ordered = sorted(by_date)
        cleaned[ticker] = {
            "dates": ordered,
            "close": [by_date[d] for d in ordered],
        }
    return cleaned



# 발표일 되찾기 (137차) — 값은 있는데 **날짜가 없어** 통째로 빠지던 분기
# ---------------------------------------------------------------------------
# 실측으로 드러난 손실: 전체 9,466행 중 **2,336행(24.7%)에 발표일이 없고**,
# 그중 **785행은 잣대 값이 멀쩡히 들어 있습니다.** 측정은 발표일로 창을
# 잡으므로, 이 785분기는 실적이 있는데도 한 번도 세어지지 않았습니다.
#
# ⚠️ **분기끝(filing_date)을 발표일로 쓰면 안 됩니다.** 이름이 헷갈리지만
#    이 칸은 접수일이 아니라 **분기 종료일**입니다(vendor_compare 머리말도
#    같은 말을 합니다). 둘 다 있는 7,130행으로 재 보니 발표는 분기끝보다
#    **중앙 30일** 뒤였습니다(90% 39일 · 99% 58일 · 최대 117일 · 앞선 건 0건).
#    분기끝을 발표일로 쓰면 **뉴스가 나오기 한 달 전에 사는 셈**이 되어
#    모든 가설이 부풀려집니다. 이것이 108차에 적힌 "값은 멀쩡한데 **뜻**이
#    틀렸다"의 정확한 재현입니다.
#
# 그래서 **바깥 자(야후)의 실제 발표일**로 채웁니다. 근거는 지어낸 것이
# 아니라 잰 것입니다 — 우리 발표일이 **있는** 7,130행을 야후와 맞대 보니
# **96.4%가 ±3일 안에서 일치**했습니다(6,874 대 256). 야후 발표일은
# 시간축으로 쓸 만합니다.
#
# 규칙은 좁게 잡습니다:
#   · 분기끝 뒤 **0~75일** 안에 야후 발표일이 **정확히 하나**일 때만 채운다
#   · 우리 발표일이 이미 있으면 **절대 덮어쓰지 않는다** (대조용으로 남김)
#   · 채운 행에는 `_발표일출처="야후"` 를 남긴다 — 숨기지 않는다
#
# 창 75일은 위 실측 분포에서 골랐습니다. 실행 출력(창별 성적):
#     0~60일 → 딱 1개 2,226 · 애매 0      0~90일  → 2,229 · 애매 1
#     0~75일 → 딱 1개 2,229 · 애매 0      0~120일 → 1,008 · 애매 1,222
# 90일을 넘기면 **다음 분기 발표가 창 안으로 들어와** 애매해집니다.
# 75일은 실제 지연의 99.5%를 덮으면서 애매한 건이 0인 자리입니다.
ANNOUNCE_WINDOW_DAYS = 75


def load_announcements(path: str | None = None) -> dict:
    """vendor.json 에서 **발표일 기록만** 읽습니다 (137차).

    반환: {종목: [발표일(YYYY-MM-DD), ...]} — 없으면 빈 dict.
    `날짜뜻` 이 "발표일" 인 기록만 씁니다. 뜻이 다른 날짜를 발표일로
    쓰는 것이 바로 108차에 겪은 사고이므로, 뜻을 확인하지 않은 날짜는
    한 건도 담지 않습니다.
    """
    if path is None:
        path = f"{cfg.MEASURE_DIR}/vendor.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out: dict = {}
    for ticker, d in (data.get("tickers") or {}).items():
        날들 = sorted({
            str(a["announced_date"])[:10]
            for a in (d or {}).get("announcements") or []
            if a.get("날짜뜻") == "발표일" and a.get("announced_date")
        })
        if 날들:
            out[ticker] = 날들
    return out


def _recover_announced(quarters: dict, 발표: dict, notes: list[str]) -> int:
    """발표일이 빈 행을 바깥 자의 발표일로 채웁니다. 덮어쓰지 않습니다."""
    from datetime import date as _date

    채움 = 0
    for ticker, rows in (quarters or {}).items():
        날들 = 발표.get(ticker) or []
        if not 날들:
            continue
        # 이미 쓰인 발표일은 다시 쓰지 않습니다 — 회사는 한 날에 두 분기를
        # 발표하지 않으므로, 같은 날이 두 행에 붙으면 **둘 중 하나는
        # 틀린 것**입니다. 어느 쪽이 틀렸는지 모르므로 새로 채우지 않고
        # 비워 둡니다(없음은 안전하고 틀림은 위험 — 헌법 1조).
        # 이 가드가 없으면 실제로 16건이 겹쳤습니다(137차 실측).
        쓰인날 = {str(r["announced_date"])[:10] for r in rows
                if r.get("announced_date")}
        for row in rows:
            if row.get("announced_date") or not _valid_date(row.get("filing_date")):
                continue
            try:
                끝 = _date.fromisoformat(str(row["filing_date"])[:10])
            except ValueError:
                continue
            후보 = []
            for 날 in 날들:
                try:
                    사이 = (_date.fromisoformat(날) - 끝).days
                except ValueError:
                    continue
                if 0 <= 사이 <= ANNOUNCE_WINDOW_DAYS and 날 not in 쓰인날:
                    후보.append(날)
            # 딱 하나일 때만 — 여럿이면 어느 것인지 모르므로 비워 둡니다
            if len(후보) == 1:
                row["announced_date"] = 후보[0]
                row["_발표일출처"] = "야후"
                쓰인날.add(후보[0])
                채움 += 1
    if 채움:
        notes.append(
            f"발표일이 비어 있던 {채움}행을 바깥 자(야후)의 발표일로 "
            f"채웠습니다 — 분기끝 뒤 0~{ANNOUNCE_WINDOW_DAYS}일 안에 "
            "발표 기록이 정확히 하나일 때만. 우리 값은 덮어쓰지 않습니다."
        )
    return 채움


def load_splits(path: str | None = None) -> dict:
    """vendor.json 에서 공식 액면분할 기록을 읽습니다 (112차).

    반환: {종목: [(분할일, 비율), ...]} — 기록이 없거나 파일이 없으면 빈 dict.
    없는 것을 지어내지 않습니다. 로봇이 아직 splits 를 수집하지 않았다면
    환산도 일어나지 않습니다 (예전과 완전히 같은 동작).
    """
    if path is None:
        path = f"{cfg.MEASURE_DIR}/vendor.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out: dict = {}
    for ticker, d in (data.get("tickers") or {}).items():
        rows = []
        for item in (d or {}).get("splits") or []:
            date, ratio = item.get("date"), item.get("ratio")
            if (_valid_date(date) and _finite_number(ratio)
                    and ratio > 0 and abs(ratio - 1) > 1e-9):
                rows.append((str(date)[:10], float(ratio)))
        if rows:
            out[ticker] = sorted(rows)
    return out


def _apply_splits(ticker: str, rows: list[dict], splits: list, notes: list[str]) -> None:
    """주당 칸을 현재 주식 수 기준으로 환산합니다 (112차 — 단위 맞추기).

    발표일이 분할일보다 앞이면 그 뒤의 모든 분할 비율을 곱한 값으로
    나눕니다. 정분할(비율>1)은 값이 작아지고, 역분할(비율<1)은 커집니다 —
    나누기 하나로 양쪽이 다 맞습니다.
    주당 칸(_PER_SHARE_FIELDS)만 건드립니다. 매출·영업이익은 주식 수와
    무관하므로 그대로 둡니다.
    """
    보정 = 0
    for row in rows:
        day = str(row.get("announced_date") or row.get("filing_date") or "")[:10]
        if len(day) != 10:
            continue
        factor = 1.0
        for split_day, ratio in splits:
            if day < split_day:
                factor *= ratio
        if abs(factor - 1.0) <= 1e-9:
            continue
        touched = False
        for field in _PER_SHARE_FIELDS:
            value = row.get(field)
            if isinstance(value, (int, float)):
                row[field] = value / factor
                touched = True
        if touched:
            보정 += 1
    if 보정:
        notes.append(
            f"{ticker}: 액면분할 환산 — 공식 분할 {len(splits)}건, "
            f"분기 {보정}개의 주당 칸을 현재 주식 수 기준으로 나눔"
        )

def build(snapshot: dict, splits: dict | None = None,
          announcements: dict | None = None) -> dict:
    """snapshot 을 검사·정렬해 측정용 밥상을 차립니다.

    반환:
      {
        "saved_at":  snapshot 이 만들어진 시각 (사실 그대로),
        "benchmark": 기준지수 티커,
        "tickers":   종목 목록,
        "quarters":  {종목: [검사·정렬된 분기 행]},
        "prices":    {종목: {"dates": [...], "close": [...]}},
        "notes":     [무엇을 버렸는지 전부],
      }

    기준지수 가격이 없으면 ValueError — SPY 대비 측정 자체가 불가능한
    재료이므로, 조용히 넘어가지 않고 즉시 실패를 드러냅니다.
    """
    notes: list[str] = []
    benchmark = snapshot.get("benchmark") or cfg.BENCHMARK

    quarters = _clean_quarters(snapshot.get("eps") or {}, notes)
    # 발표일 되찾기 (137차) — 분할 환산보다 **먼저** 합니다. 환산이
    # 발표일을 보고 분할 시점을 가리기 때문입니다(_apply_splits).
    #
    # 기본값 None 은 "파일에서 읽어라"는 뜻입니다. 부르는 곳이 9군데라
    # 인자를 넘겨야만 돌게 하면 화면과 로봇이 서로 다른 표본을 보게
    # 됩니다 — 이 저장소가 실제로 겪은 종류의 사고입니다. 시험에서
    # 끄고 싶으면 `announcements={}` 를 넘기면 됩니다.
    if announcements is None:
        announcements = load_announcements()
    if announcements:
        _recover_announced(quarters, announcements, notes)
    # 액면분할 단위 환산 (112차) — 공식 기록이 있는 종목만.
    # 검사(_clean_quarters) 뒤에 하는 이유: 상한 검사 등은 발표 당시
    # 원문 값 기준이어야 하고, 환산은 그다음의 단위 맞추기이기 때문입니다.
    if splits:
        for ticker, rows in quarters.items():
            if ticker in splits:
                _apply_splits(ticker, rows, splits[ticker], notes)
    prices = _clean_prices(snapshot.get("prices") or {}, notes)

    if not prices.get(benchmark, {}).get("dates"):
        raise ValueError(
            f"기준지수({benchmark}) 가격이 없습니다 — SPY 대비 측정이 "
            "불가능한 재료입니다. 로봇 수집 기록을 확인하세요."
        )

    return {
        "saved_at": snapshot.get("saved_at"),
        "benchmark": benchmark,
        "tickers": list(snapshot.get("tickers") or []),
        "quarters": quarters,
        "prices": prices,
        "notes": notes,
    }
