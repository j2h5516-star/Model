"""
fixtures.py — 테스트용 가상 데이터
==================================

실제 SEC나 야후에 접속하지 않고도 로직을 검증할 수 있도록,
진짜 실적 보도자료와 같은 형식의 가상 텍스트와 주가 데이터를 만들어 둡니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# ① 표 형식 보도자료 (단위: 천 달러) — 반도체 회사에서 흔한 형태
# ---------------------------------------------------------------------------
PR_TABLE_THOUSANDS = """
CREDO TECHNOLOGY GROUP HOLDING LTD
Reports Third Quarter Fiscal 2025 Financial Results

SAN JOSE, Calif. - Credo Technology Group Holding Ltd today announced financial
results for the third quarter of fiscal 2025 ended January 31, 2025.

CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS
(In thousands, except per share amounts)
(Unaudited)

                                              Three Months Ended
                                              January 31, 2025
Revenue                                       $      135,000
Cost of revenue                                       47,250
Gross profit                                          87,750
Operating expenses:
   Research and development                           38,000
   Selling, general and administrative                24,000
Total operating expenses                              62,000
Income from operations                                25,750

RECONCILIATION OF GAAP TO NON-GAAP FINANCIAL MEASURES
(In thousands)
(Unaudited)

GAAP gross margin                                      65.0 %
Stock-based compensation                              1,350
Non-GAAP gross margin                                  66.0 %

GAAP income from operations                    $      25,750
Stock-based compensation                              18,500
Amortization of acquired intangibles                   1,200
Non-GAAP income from operations                $      45,450

Business Outlook
For the fourth quarter of fiscal 2025, we expect revenue of $155 million to
$165 million. We expect non-GAAP gross margin to be in the range of 64% to 66%,
and non-GAAP operating expenses of $46 million to $48 million.
"""

# ---------------------------------------------------------------------------
# ② 서술형 보도자료 (단위: 백만 달러가 문장에 직접 표기)
# ---------------------------------------------------------------------------
PR_NARRATIVE_MILLIONS = """
Celestica Announces First Quarter 2025 Financial Results

TORONTO - Celestica Inc. today announced financial results for the first
quarter ended March 31, 2025.

First Quarter 2025 Highlights:
- Revenue of $2,650 million, up 20% year-over-year
- Non-GAAP gross margin of 10.8%, compared to 10.2% in the prior year period
- Non-GAAP operating income of $185.5 million
- Non-GAAP operating margin of 7.0%

Second Quarter 2025 Guidance
The Company expects revenue of $2,600 million to $2,750 million and non-GAAP
operating margin of approximately 7.2%.
"""

# ---------------------------------------------------------------------------
# ③ 논갭 영업이익이 없어 "역산"이 필요한 보도자료
# ---------------------------------------------------------------------------
PR_NEEDS_DERIVATION = """
MaxLinear, Inc. Announces Second Quarter 2025 Financial Results

(In thousands)

Net revenue                                    $      120,000
Non-GAAP gross margin                                  58.0 %
Non-GAAP operating expenses                    $       60,000

The company did not disclose a consolidated non-GAAP operating profit figure.
"""

# ---------------------------------------------------------------------------
# ④ 적자(괄호 = 음수) 보도자료
# ---------------------------------------------------------------------------
PR_LOSS_PARENTHESES = """
Ichor Holdings Reports First Quarter 2025 Financial Results

(In thousands)

Revenue                                        $      220,000
Non-GAAP gross margin                                  13.5 %
Non-GAAP operating income                      $      (4,500)
"""

# ---------------------------------------------------------------------------
# ⑤ 가이던스가 전혀 없는 보도자료
# ---------------------------------------------------------------------------
PR_NO_GUIDANCE = """
TTM Technologies Reports Fourth Quarter 2025 Results

(In thousands)

Net sales                                      $      650,000
Non-GAAP gross margin                                  22.0 %
Non-GAAP operating income                      $       58,000

TTM will host a conference call to discuss these results.
"""


# ---------------------------------------------------------------------------
# ⑥ 퍼센트가 금액보다 먼저 나오는 함정 보도자료
#    ("Revenue grew 20% ... to $135.0 million" → 20을 매출로 오인하면 안 됨)
# ---------------------------------------------------------------------------
PR_PERCENT_TRAP = """
Sterling Infrastructure Reports Second Quarter 2025 Results

Revenue grew 20% year-over-year to $135.0 million in the second quarter.
Non-GAAP gross margin improved to 24.5% from 22.0% a year ago.
Non-GAAP operating income increased 35% to $28.5 million.
"""


# ---------------------------------------------------------------------------
# 가상 분기 실적 만들기 (스코어링 테스트용)
# ---------------------------------------------------------------------------
def make_quarters(
    op_incomes: list[float],
    revenues: list[float] | None = None,
    margins: list[float] | None = None,
    source: str = "직접공시",
) -> list[dict]:
    """분기 실적 목록을 손쉽게 만들어 주는 도우미.

    op_incomes: 분기별 논갭 영업이익 (오래된 것부터)
    """
    n = len(op_incomes)
    if revenues is None:
        revenues = [op * 5 for op in op_incomes]  # 영업마진 20% 가정
    if margins is None:
        margins = [50.0] * n

    quarters = []
    for i in range(n):
        month = 3 * (i % 4) + 1
        year = 2025 + i // 4
        quarters.append(
            {
                "ticker": "TEST",
                "filing_date": f"{year}-{month:02d}-15",
                "period_label": f"FY{year} Q{i % 4 + 1}",
                "revenue": revenues[i],
                "op_income": op_incomes[i],
                "gross_margin_pct": margins[i],
                "source": source,
                "gm_is_gaap": False,
                "guidance_text": "",
            }
        )
    return quarters


# ---------------------------------------------------------------------------
# 가상 주가 데이터 만들기
# ---------------------------------------------------------------------------
def make_daily(closes, start: str = "2021-01-04") -> pd.DataFrame:
    """종가 목록으로 가상의 일봉 표를 만듭니다 (평일만 사용)."""
    closes = np.asarray(closes, dtype=float)
    dates = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame(
        {
            "Open": closes * 0.99,
            "High": closes * 1.01,
            "Low": closes * 0.98,
            "Close": closes,
            "Volume": np.full(len(closes), 1000.0),
        },
        index=dates,
    )


def trending_daily(n_days: int = 900, daily_rate: float = 0.003, start_price: float = 10.0):
    """꾸준히 오르거나(양수) 내리는(음수) 가상 주가를 만듭니다."""
    return make_daily(start_price * ((1 + daily_rate) ** np.arange(n_days)))


def flat_daily(n_days: int = 900, price: float = 100.0):
    """거의 움직이지 않는 가상 주가 (중립 추세 테스트용)."""
    wobble = 1 + 0.001 * np.sin(np.arange(n_days) / 7.0)
    return make_daily(price * wobble)
