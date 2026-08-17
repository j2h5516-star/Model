"""
test_eps_parse.py — 보도자료에서 주당순이익(EPS) 읽어 오기 · 1단계 검증
========================================================================

왜 EPS 를 따로 읽는가
---------------------
미국 증권규정(Reg S-K 10(e))은 회사가 '조정 EPS' 를 발표하면 **반드시** GAAP EPS 와
나란히 대조표를 싣도록 정해 두었습니다. 그래서 조정 EPS 는 보도자료 안에서
**존재가 규정으로 보장된 유일한 논갭 숫자**입니다. 논갭 영업이익은 그런 의무가
없어 아예 안 싣는 회사가 많습니다(ZETA 는 조정 EBITDA 만 발표).

여기서 확인하는 것
------------------
  ① 회사마다 다른 표기 형식을 모두 읽는가
  ② **단위 배수를 적용하지 않는가** — 이게 가장 중요합니다.
     "(in thousands)" 표에서 $1.00 에 1,000 을 곱하면 $1,000 이 되어 버립니다.
  ③ 적자를 음수로 만드는가 (이름의 loss / 표의 괄호 두 가지 경로)
  ④ 논갭 값을 GAAP 자리에 잘못 넣지 않는가
  ⑤ 근사치만으로 '적자 지속'을 선언하지 않는가 (코히런트 사태)
  ⑥ 이익의 질(격차 추세) 판정이 맞는가

⚠️ 이 단계에서 EPS 는 **점수에 쓰지 않습니다.** 실제 배포 환경에서 얼마나 잘
   읽히는지 재는 것이 목적입니다. 기준자를 EPS 로 바꿀지는 그 성공률을 보고
   결정합니다.

실행: python3 tests/test_eps_parse.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import sec_fundamentals as sf  # noqa: E402

M = 1_000_000.0


# ---------------------------------------------------------------------------
# ① 회사마다 다른 표기 읽기
# ---------------------------------------------------------------------------
def test_narrative_form_reads_both_eps():
    """서술형 — 코히런트 실제 형식 (GAAP 적자 · 논갭 흑자)"""
    text = (
        "Coherent Corp. Reports Fourth Quarter Fiscal 2025 Results\n"
        "Revenue was a record $1,527.5 million. GAAP gross margin was 35.7%.\n"
        "GAAP net loss per diluted share was $0.83, while on a non-GAAP basis,\n"
        "gross margin was 38.1% and non-GAAP earnings per diluted share was $1.00."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.00, result["adj_eps"]
    assert result["gaap_eps"] == -0.83, result["gaap_eps"]


def test_nvidia_style_reads_both_eps():
    """서술형 — 둘 다 흑자이고 GAAP 이 먼저 나오는 형식"""
    text = (
        "For the quarter, GAAP earnings per diluted share was $0.78, up 12%.\n"
        "Non-GAAP earnings per diluted share was $0.89, up 19% from a year ago."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 0.89
    assert result["gaap_eps"] == 0.78


def test_adjusted_eps_wording_is_recognised():
    """'Adjusted EPS' 라는 짧은 표기와 'Diluted earnings per share' 어순"""
    text = (
        "Adjusted EPS of $2.45 compared to $1.90 in the prior year period.\n"
        "Diluted earnings per share was $1.12."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 2.45
    assert result["gaap_eps"] == 1.12


def test_reversed_word_order_is_recognised():
    """'Non-GAAP diluted earnings per share' 처럼 diluted 가 앞에 오는 형식"""
    text = (
        "Non-GAAP diluted earnings per share    $ 3.53\n"
        "Diluted loss per share                 $ (0.52)"
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 3.53
    assert result["gaap_eps"] == -0.52


def test_missing_eps_returns_none():
    """EPS 가 없는 보도자료는 조용히 None — 억지로 다른 숫자를 집지 않습니다"""
    text = "Revenue was $500 million. Non-GAAP operating income was $100 million."
    result = sf.parse_press_release(text)
    assert result["adj_eps"] is None
    assert result["gaap_eps"] is None


# ---------------------------------------------------------------------------
# ② 단위 배수를 적용하지 않는가 — 가장 중요한 검사
# ---------------------------------------------------------------------------
def test_table_unit_is_never_applied_to_eps():
    """'(in thousands)' 표 안에서도 $1.00 은 $1.00 이어야 합니다.

    금액용 함수(find_labeled_value)는 표 제목의 단위를 곱합니다. 주당 금액에
    그 규칙을 그대로 쓰면 $1.00 이 $1,000 이 되어 모든 계산이 무너집니다.
    """
    text = (
        "CONDENSED RESULTS (in thousands, except per share data)\n"
        "Total revenue                                  $ 1,527,500\n"
        "GAAP net income (loss) per diluted share       $   (0.83)\n"
        "Non-GAAP net income per diluted share          $    1.00"
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.00, f"단위 배수가 잘못 적용됨: {result['adj_eps']}"
    assert result["gaap_eps"] == -0.83, result["gaap_eps"]
    # 매출은 반대로 단위 배수가 적용되어야 합니다 (천 단위 → 15억 달러)
    assert result["revenue"] == 1_527_500 * 1_000


def test_absurd_per_share_value_is_rejected():
    """주당 금액이 비현실적으로 크면 표의 다른 숫자를 잘못 집은 것입니다"""
    text = f"Non-GAAP EPS {cfg.EPS_MAX_ABS * 10:,.0f}"
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


# ---------------------------------------------------------------------------
# ③ 적자를 음수로 — 두 가지 경로
# ---------------------------------------------------------------------------
def test_loss_in_label_flips_sign():
    """이름이 'loss' 라고 말하면 양수로 적혀 있어도 적자입니다"""
    text = "The company reported a net loss per diluted share of $1.25 for the quarter."
    result = sf.parse_press_release(text)
    assert result["gaap_eps"] == -1.25


def test_income_loss_header_does_not_flip_sign():
    """'net income (loss) per share' 는 표의 항목 이름일 뿐 적자 선언이 아닙니다.

    이 표기를 적자로 오해해 부호를 뒤집으면, 괄호로 이미 음수가 된 값이
    다시 양수가 되어 흑자로 둔갑합니다.
    """
    text = "GAAP net income (loss) per diluted share       $ 2.10"
    result = sf.parse_press_release(text)
    assert result["gaap_eps"] == 2.10, result["gaap_eps"]


# ---------------------------------------------------------------------------
# ④ 논갭 값을 GAAP 자리에 넣지 않는가
# ---------------------------------------------------------------------------
def test_nongaap_line_is_not_read_as_gaap():
    """'Non-GAAP' 안에도 'GAAP' 글자가 있습니다. 여기에 속으면 안 됩니다."""
    text = "Non-GAAP net income per diluted share    $ 1.00"
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.00
    assert result["gaap_eps"] is None, f"논갭 값을 GAAP 으로 읽음: {result['gaap_eps']}"


def test_adjusted_prefix_is_not_read_as_gaap():
    """'Adjusted diluted earnings per share' 도 GAAP 이 아닙니다"""
    text = "Adjusted diluted earnings per share    $ 4.20"
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 4.20
    assert result["gaap_eps"] is None


# ---------------------------------------------------------------------------
# ⑤ 근사치만으로 '적자 지속'을 선언하지 않는가 — 코히런트 사태
# ---------------------------------------------------------------------------
def test_report_counts_eps_separately():
    """논갭 영업이익과 조정 EPS 중 어느 쪽이 잘 읽히는지 따로 세어야 합니다.

    이 두 숫자를 배포 환경에서 비교해 보고 모델의 기준자를 바꿀지 결정합니다.
    """
    report = sf.new_report("TEST")
    for key in ("op_income_ok", "adj_eps_ok", "gaap_eps_ok"):
        assert key in report, f"진단 기록에 {key} 가 없습니다"
        assert report[key] == 0


def test_eps_survives_the_merge_into_quarter_rows():
    """보도자료에서 읽은 EPS 가 최종 분기 자료까지 살아서 도착하는가"""
    row = {"op_income": 100 * M, "source": cfg.SRC_APPROX}
    press = {"adj_eps": 1.00, "gaap_eps": -0.83, "op_income": None}
    sf._apply_press_to_row(row, press)
    assert row["adj_eps"] == 1.00
    assert row["gaap_eps"] == -0.83


def test_eps_only_release_is_still_accepted():
    """매출·영업이익을 못 읽어도 조정 EPS 가 있으면 실적발표로 인정합니다.

    규정상 존재가 보장된 값이므로, 이것만 잡히는 보도자료를 버리면
    수집 성공률을 재는 것 자체가 불가능해집니다.
    """
    text = "Non-GAAP earnings per diluted share was $1.00 for the quarter."
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.00
    assert result["revenue"] is None and result["op_income"] is None


# ---------------------------------------------------------------------------
# ⑧ 기준자(어느 숫자로 재는가)에 따라 크기 검사가 달라져야 한다
# ---------------------------------------------------------------------------
def _basis_quarters(values, basis):
    """같은 '성장 모양'을 서로 다른 단위로 만듭니다"""
    return [
        {
            "period_label": f"Q{i + 1}",
            "fiscal_quarter": i % 4 + 1,
            "period_end": f"202{3 + i // 4}-{(i % 4) * 3 + 3:02d}-28",
            "op_income": v,
            "revenue": abs(v) * 5 + 1e8,
            "source": cfg.SRC_DIRECT,
            "basis": basis,
        }
        for i, v in enumerate(values)
    ]


def test_amount_is_written_in_the_right_unit():
    """조정 EPS 를 백만 단위로 나눠 '$0M' 으로 쓰면 안 됩니다"""
    assert cfg.fmt_amount(1_352_000_000, cfg.BASIS_OP_INCOME) == "$1,352.0M"
    assert cfg.fmt_amount(8.53, cfg.BASIS_ADJ_EPS) == "$8.53/주"
    assert cfg.fmt_amount(None, cfg.BASIS_ADJ_EPS) == "-"


def test_basis_defaults_to_operating_income():
    """표시가 없는 옛 자료는 지금까지처럼 논갭 영업이익으로 봅니다"""
    assert cfg.quarters_basis([]) == cfg.BASIS_OP_INCOME
    assert cfg.quarters_basis([{"op_income": 1.0}]) == cfg.BASIS_OP_INCOME


# ---------------------------------------------------------------------------
# ⑨ 구조대 경로 — 논갭 영업이익이 모자랄 때만 조정 EPS 로 판정
# ---------------------------------------------------------------------------
def _mixed(op_values, eps_values):
    """영업이익과 EPS 를 각각 원하는 만큼 채운 분기 목록"""
    rows = []
    for i in range(max(len(op_values), len(eps_values))):
        rows.append({
            "period_label": f"Q{i + 1}",
            "fiscal_quarter": i % 4 + 1,
            "period_end": f"202{3 + i // 4}-{(i % 4) * 3 + 3:02d}-28",
            "op_income": op_values[i] if i < len(op_values) else None,
            "adj_eps": eps_values[i] if i < len(eps_values) else None,
            "revenue": 1e9,
            "source": cfg.SRC_DIRECT,
        })
    return rows


def test_measured_numbers_are_recorded_in_config():
    """검증 결과를 코드에 남겨 둡니다 — 나중에 근거를 찾을 수 있도록"""
    assert cfg.EPS_BASIS_DIRECTION_MATCH == 0.857     # 30/35 (오염 분기 제외 후)
    assert cfg.EPS_BASIS_MATCH_NO_BUYBACK == 0.96     # 48/50 (자사주 없는 종목)
    assert cfg.EPS_BASIS_BUYBACK_CORR < 0             # 자사주 종목 표본에서는 음의 상관
    # ⚠️ 그런데 다른 표본에서는 관계가 사라집니다 — 그래서 점수에 반영하지 않습니다
    assert abs(cfg.EPS_BASIS_BUYBACK_CORR_OTHER) < 0.1
    # 조정 EPS 의 신뢰도는 직접공시(95%)보다 낮고 근사치(55%)보다 높아야 합니다
    assert cfg.CONFIDENCE_PCT[cfg.SRC_APPROX] < cfg.CONFIDENCE_PCT[cfg.SRC_ADJ_EPS]
    assert cfg.CONFIDENCE_PCT[cfg.SRC_ADJ_EPS] < cfg.CONFIDENCE_PCT[cfg.SRC_DIRECT]


# ---------------------------------------------------------------------------
# 실물 보도자료 검증 — 연료 파이프라인(data/measure/raw/)이 가져온 진짜 문장
# ---------------------------------------------------------------------------
# 지금까지 파서가 계속 어긋난 이유는 "지어낸 예제로만 테스트해서"였습니다.
# 아래 문장들은 배포된 앱이 저장소로 커밋해 준 **실제 보도자료**에서 그대로
# 따온 것이고, 기대값은 그 문서에 인쇄된 실제 숫자입니다.
def test_real_crdo_diluted_before_net():
    """실물 CRDO 2026-06-01 — 'non-GAAP **diluted** net income per share' 어순"""
    text = (
        "GAAP diluted net income per share of $0.88 and "
        "non-GAAP diluted net income per share of $1.16"
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.16, result["adj_eps"]
    assert result["gaap_eps"] == 0.88, result["gaap_eps"]


def test_real_sndk_paren_value_before_label():
    """실물 샌디스크 2026-08-05 — 값이 이름 **앞 괄호 안**에 있는 형식.

    고치기 전에는 이름 뒤쪽을 훑다가 뒤 문장의 숫자를 물어
    GAAP EPS 가 202.0 으로 나왔습니다.
    """
    text = (
        "Fiscal fourth quarter revenue was $8.97 billion, up 51% sequentially, "
        "with GAAP net income reported at $6.90 billion ($43.97 diluted net income "
        "per share). Sequential revenue growth came approximately one-third from "
        "higher volumes and two-thirds from higher pricing. Fourth quarter "
        "Non-GAAP diluted net income per share was $39.25."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 39.25, result["adj_eps"]      # EPS 상한(1000) 안의 큰 EPS
    assert result["gaap_eps"] == 43.97, result["gaap_eps"]


def test_real_amd_combo_reconciliation_row():
    """실물 AMD 2026-08-04 — '순이익 / EPS' 묶음 대조표.

    한 줄에 [순이익 $2,760] [EPS $1.66] 이 짝으로 반복되므로,
    소수점 없는 순이익 칸을 건너뛰고 EPS 칸을 집어야 합니다.
    """
    text = (
        "GAAP net income / earnings per share $2,297 $1.38 $1,383 $0.84 $872 $0.54\n"
        "Non-GAAP net income / earnings per share $2,760 $1.66 $2,265 $1.37 $781 $0.48"
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.66, result["adj_eps"]
    assert result["gaap_eps"] == 1.38, result["gaap_eps"]


def test_amd_combo_small_net_income_is_not_eaten():
    """순이익이 크기 상한(1000) **아래**인 분기 — 소수점 규칙이 지켜 줍니다.

    $781 은 상한 아래라 크기 검사로는 못 거르지만, 소수점이 없어 EPS 칸이
    아닙니다. 이 규칙이 없으면 EPS 가 781.0 이 됩니다.
    """
    text = "Non-GAAP net income / earnings per share $781 $0.48"
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.48


def test_date_number_after_label_is_skipped():
    """이름 뒤에 날짜·연도 같은 정수가 먼저 오면 건너뛰고 진짜 값을 집는다.

    실제 사고 (2026-08-12 snapshot 실측): 분기 종료일 "March 31" 의 31을
    EPS 로 물어 MCHP 조정 EPS 가 세 해 연속 31.0, 그 밖에 정수를 문
    202.0 이 30건 (FN 15개 분기 전부 · TER · CLS · FORM 등).
    실제 EPS 는 보도자료에서 항상 소수점 표기($1.66, $39.25)이므로,
    소수점 없는 숫자는 EPS 후보가 아닙니다.
    """
    text = (
        "Non-GAAP diluted earnings per share for the three months ended "
        "March 31, 2025 was $1.60."
    )
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.60


def test_integer_only_sentence_returns_none():
    """소수점 있는 값이 끝내 없으면 "없음"이 답입니다 — 없음이 틀림보다 안전."""
    text = "Adjusted EPS discussion in Item 202 of the annual report"
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


def test_real_zeta_has_no_adjusted_eps():
    """실물 ZETA 2026-08-04 — 조정 EPS 를 발표하지 않는 회사.

    조정 EBITDA 만 발표하므로 adj_eps 는 '없음'이 정답입니다.
    없는 숫자를 만들어 내면 안 됩니다 (창작 금지).
    """
    text = (
        "Achieved positive GAAP net income of $8 million, and GAAP earnings per "
        "share of $0.03. Generated $92 million of adjusted EBITDA and expanded "
        "adjusted EBITDA margin by 170 bps Y/Y to 20.7%."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] is None, result["adj_eps"]
    assert result["gaap_eps"] == 0.03, result["gaap_eps"]


def test_real_zeta_ebitda_is_extracted():
    """실물 ZETA 2026-08-04 — 조정 EPS 미발표 회사의 대체 잣대(조정 EBITDA).

    이 값이 스냅샷까지 흘러가야 계기판이 ZETA 의 이익 방향을 볼 수 있습니다
    (9차 감사: 값은 뽑히는데 분기 행으로 복사되지 않아 버려지고 있었음).
    """
    text = (
        "(In thousands, except percentages)\n"
        "Three months ended June 30,\n"
        "Net income / (loss)                           $8,173\n"
        "Add back:\n"
        "Depreciation and amortization                 22,658\n"
        "Stock-based compensation                      52,115\n"
        "Adjusted EBITDA                              $91,697\n"
        "Adjusted EBITDA margin                          20.7      %\n"
    )
    result = sf.parse_press_release(text)
    assert result["adjusted_ebitda"] == 91_697_000.0, result["adjusted_ebitda"]


def test_real_crdo_q3_fy26_eps():
    """실물 CRDO 2026-03-02 — 짝짓기 사고로 통째로 버려졌던 발표의 원문.

    'non-GAAP diluted net income per share of $1.07' 어순이 계속 읽히는지
    고정합니다 (원문 보관 파일은 순환 삭제되므로 여기 박제).
    """
    text = (
        "Credo Technology Group Holding Ltd Reports Third Quarter of Fiscal "
        "Year 2026 Financial Results. Revenue of $407.0 million, grew by 51.9% "
        "quarter over quarter and 201.5% year over year. GAAP net income of "
        "$157.1 million and non-GAAP net income of $208.8 million. GAAP diluted "
        "net income per share of $0.82 and non-GAAP diluted net income per "
        "share of $1.07"
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.07, result["adj_eps"]
    assert result["gaap_eps"] == 0.82, result["gaap_eps"]
    assert result["revenue"] == 407_000_000.0, result["revenue"]


def test_real_sedg_loss_word_order():
    """실물 SEDG 2023-11-01 — 'net diluted loss' 어순의 적자는 음수여야 함."""
    text = "Non-GAAP net diluted loss per share* of $0.55"
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == -0.55


def test_real_stx_paren_loss():
    """실물 STX 2023-10-26 — '(loss)' 괄호 표기 + 괄호 음수. 이중 반전 금지."""
    text = "GAAP (loss) per share of $(0.88); non-GAAP (loss) per share of $(0.22)"
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == -0.22
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS, exclude_nongaap=True) == -0.88


def test_real_amba_ordinary_share_current_quarter():
    """실물 AMBA 2026-02-26 — 이번 분기(0.13)를 집어야지 1년 전(0.11)이면 안 됨.

    문장 중간에 여백 패딩(공백 68자+줄바꿈)이 끼는 실물 구조 그대로.
    """
    text = (
        "Non-GAAP net profit for the fourth quarter of fiscal 2026 was"
        + " " * 68 + "\n"
        + "    $5.5 million, or earnings per diluted ordinary share of $0.13. "
        "This compares with non-GAAP net profit of $4.8 million, or earnings "
        "per diluted ordinary share of $0.11, for the same period in fiscal 2025."
    )
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.13


def test_real_mpwr_table_picks_diluted_row():
    """실물 MPWR 2026-07-30 — 표제목 아래 Basic/Diluted 줄에서 Diluted 를 집기.

    실물과 같은 기하(222자 줄, 값은 78열)로 재현 — 제목과 값 사이가 멀어
    일반 패턴은 못 읽고, Diluted 로 건너뛰는 표 패턴이 6.50 을 집어야 함.
    """
    text = (
        "Non-GAAP net income per share:".ljust(222) + "\n"
        + ("Basic".ljust(78) + "$6.51").ljust(222) + "\n"
        + ("Diluted".ljust(78) + "$6.50").ljust(222) + "\n"
    )
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 6.50


def test_partnership_8k_is_not_kept_as_raw():
    """실물 LITE·COHR 2026-03-02 — 파트너십 발표는 실적이 아닙니다.

    실적으로 판별되지 않아야 원문 보관 상한(종목당 2건)을 차지하지 않습니다.
    """
    text = (
        "NVIDIA Announces Strategic Partnership with Lumentum to Develop "
        "State-of-the-Art Optics Technology. NVIDIA to invest $2B in Lumentum "
        "to grow capacity and deepen R&D collaboration in data center optics."
    )
    assert not sf._looks_like_earnings(text)


def test_real_cgnx_column_table():
    """실물 CGNX 2023-02-16 — 열 제목이 여러 줄에 걸친 위치 정렬 표.

    "Non-GAAP Net Income per Diluted Share" 열 제목이 4줄에 쌓여 있고
    값은 "Current quarter: Q4-22" 행의 네 번째 칸($0.27)에 있습니다.
    문장형 파서는 각주(*Non-GAAP ...)만 물고 값을 못 뽑던 실물입니다.
    """
    text = '  Table 1  (Dollars in thousands, except per share amounts) \n \n                                                       Revenue                                Net Income                              Net Income                               Non-GAAP              \n                                                                                                                                     per Diluted                              Net Income             \n                                                                                                                                        Share                                per Diluted             \n                                                                                                                                                                                Share*               \n-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------\nQuarterly Comparisons                                                                                                                                                                                \nCurrent quarter: Q4-22                                               $239,433                                 $55,311                                   $0.32                                   $0.27\nPrior year’s quarter: Q4-21                                          $244,065                                 $53,535                                   $0.30                                   $0.30\nChange: Q4-21 to Q4-22                                                   (2)%                                      3%                                      7%                                   (10)%\nPrior quarter: Q3-22                                                 $209,622                                 $33,980                                   $0.19                                   $0.21\nChange: Q3-22 to Q4-22                                                    14%                                     63%                                     68%                                     29%\nYearly Comparisons                                                                                                                                                                                   \nYear ended December 31, 2022                                       $1,006,090                                $215,525                                   $1.23                                   $1.31\nYear ended December 31, 2021                                       $1,037,098                                $279,881                                   $1.56                                   $1.49\nChange from 2021 to 2022                                                 (3)%                                   (23)%                                   (21)%                                   (12)%\n*Non-GAAP net income per diluted share excludes a loss from fire and restructuring charges, both net of tax benefit, and discrete tax adjustments. A reconciliation from GAAP to Non-GAAP is         \nshown in Exhibit 2 of this news release.                                                                                                                                                             '
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 0.27, result["adj_eps"]
    assert result["gaap_eps"] == 0.32, result["gaap_eps"]


def test_column_table_without_nongaap_column_returns_none():
    """논갭 열이 없는 표에서는 값을 지어내면 안 됩니다 — 없음이 정답."""
    text = (
        "                Revenue        Net Income\n"
        "--------------------------------------------------\n"
        "Current quarter: Q4-22      $1,000      $100\n"
    )
    assert sf.find_eps_in_column_table(text)["adj_eps"] is None


def test_real_tsla_date_column_row():
    """실물 TSLA 2026-07-22 — 열 제목이 날짜(Q2-2025 … Q2-2026)인 한 줄 표.

    열 순서가 과거→현재라 "첫 숫자" 규칙은 1년 전 값(0.40)을 뭅니다.
    날짜를 읽어 **가장 최신 분기 열**(Q2-2026 = 0.33)을 골라야 합니다.
    이 형식 때문에 TSLA 자동 추출이 차단되어 있었습니다 (사고 백서 9번).
    """
    text = '($ in millions, except percentages and per share data) Q2-2025 Q3-2025 Q4-2025 Q1-2026 Q2-2026 YoY Total automotive revenues 16,661 21,205 17,693 16,234 20,516 23% Energy generation and storage revenue 2,789 3,415 3,837 2,408 3,139 13% Services and other revenue 3,046 3,475 3,371 3,745 4,581 50% Total revenues 22,496 28,095 24,901 22,387 28,236 26% Total gross profit 3,878 5,054 5,009 4,720 4,751 23% Total GAAP gross margin 17.2% 18.0% 20.1% 21.1% 16.8% -41 bp Operating expenses 2,955 3,430 3,600 3,779 4,353 47% Income from operations 923 1,624 1,409 941 398 -57% Operating margin 4.1% 5.8% 5.7% 4.2% 1.4% -269 bp Adjusted EBITDA 3,401 4,227 4,154 3,668 3,273 -4% Adjusted EBITDA margin 15.1% 15.0% 16.7% 16.4% 11.6% -353 bp Net income attributable to common stockholders (GAAP) 1,172 1,373 840 477 1,114 -5% Net income attributable to common stockholders (non-GAAP) 1,393 1,770 1,761 1,453 1,153 -17% EPS attributable to common stockholders, diluted (GAAP) 0.33 0.39 0.24 0.13 0.32 -3% EPS attributable to common stockholders, diluted (non-GAAP) 0.40 0.50 0.50 0.41 0.33 -18% '
    result = sf.find_eps_in_date_column_table(text)
    assert result["adj_eps"] == 0.33, result
    assert result["gaap_eps"] == 0.32, result


def test_date_column_needs_enough_period_headers():
    """날짜 열이 3개 미만이면 확신이 없으므로 없음이 정답입니다."""
    text = ("Q2-2026 EPS attributable to common stockholders, diluted "
            "(non-GAAP) 0.40 0.50")
    assert sf.find_eps_in_date_column_table(text)["adj_eps"] is None


def test_money_amount_is_not_eps():
    """주당 금액에는 million 같은 단위 낱말이 붙지 않습니다.

    실제 사고 (2026-08-13 잔여 5건): 인수·공지 문서의 "accretive to
    non-GAAP EPS ... $5.0 million" 류에서 금액을 EPS 로 물어
    TTMI 5.0 · QRVO 7.0 · TER 8.0 · MKSI 8.0 이 만들어졌고,
    TTMI 는 이 가짜 행이 이력의 연속성까지 끊었습니다.
    """
    text = ("The $5.0 million transaction is expected to be immediately "
            "accretive to non-GAAP EPS, with revenue of $8.0 million.")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None
    # 단위 낱말이 없는 진짜 EPS 는 그대로 읽혀야 합니다
    assert sf.find_eps_value("Non-GAAP EPS of $5.05 for the quarter.",
                             sf.LABELS_ADJUSTED_EPS) == 5.05


def test_real_merger_announcement_is_not_earnings():
    """실물 SWKS·QRVO 2025-10-28 합병 발표 — 실적발표로 오인하면 안 됩니다.

    오인하면 ① 원문 보관함(종목당 2건)을 합병 문서가 차지해 진짜 실패
    원문이 못 담기고 ② "accretive to EPS" 문구가 가짜 행을 만듭니다.
    """
    text = 'Skyworks and Qorvo to Combine to Create $22 Billion U.S.-Based Leader in High-Performance RF, Analog and Mixed-Signal Solutions ·   Immediately and meaningfully accretive to non-GAAP EPS post-close, with $500 million or more of annual cost synergies within 24-36 ·   Phil Brace will serve as chief executive officer of the combined company; Bob Bruggeworth will join the Board of Directors of the combined company IRVINE, CA and GREENSBORO, NC – Oct. 28, 2025 – Skyworks (Nasdaq: SWKS), a global leader in high-performance analog and mixed-signal semiconductors, and Qorvo (Nasdaq: QRVO), a leading global provider of connectivity and “This combination marks an important milestone for our industry and for Skyworks,” said Phil Brace, chief executive officer and president of Skyworks. “Combining Skyworks’ and Qorvo’s complementary portfolios and world-c'
    assert not sf._looks_like_earnings(text)


def test_forecast_sentence_is_not_quarterly_eps():
    """전망·연간 목표 문장의 EPS 는 분기 실적이 아닙니다 (사고 16).

    실물 (2026-08-13 의심 정수 원문 감사에서 확보):
      · QRVO: "we continue to expect non-GAAP diluted EPS approaching $7.00"
      · TER : "estimates in our 2024 earnings model to $4.9 billion and $8.00"
      · TTMI: "non-GAAP net income per share to approach $5.00" — 그리고
        같은 문서의 표에 진짜 분기 값 $0.99 가 있음 → 전망 문장을
        건너뛰면 값이 지워지는 게 아니라 **회수**됩니다.
    """
    qrvo = ("For full-year fiscal 2027, we continue to expect non-GAAP gross "
            "margin above 50% and non-GAAP diluted earnings per share "
            "approaching $7.00.")
    assert sf.find_eps_value(qrvo, sf.LABELS_ADJUSTED_EPS) is None

    ter = ("We increased the mid-point of the revenue and non-GAAP earnings "
           "per share estimates in our 2024 earnings model to $4.9 billion "
           "and $8.00 respectively.")
    assert sf.find_eps_value(ter, sf.LABELS_ADJUSTED_EPS) is None

    ttmi = ("We expect non-GAAP net income per share to approach $5.00. "
            "Our third quarter estimate and full year outlook do not include "
            "pending acquisitions.\n"
            "Non-GAAP earnings per diluted share       $0.99      $0.58\n")
    assert sf.find_eps_value(ttmi, sf.LABELS_ADJUSTED_EPS) == 0.99


def test_slide_image_line_is_not_sentence_parsed():
    """이미지 슬라이드가 눌린 줄(<img ...)의 숫자 나열은 문장이 아닙니다.

    실물 MKSI: 차트 축·값 텍스트 "Non-GAAP Earnings per Diluted Share ...
    3,900 8.00 550" 에서 8.00(연간 차트 값)을 분기 EPS 로 물었습니다.
    슬라이드 줄은 날짜 열 파서(분기 제목 필요)만 다루게 합니다.
    """
    text = (' <img height="768" src="slide14.jpg"/> 2025 YoY Growth '
            "Revenue Non-GAAP Earnings per Diluted Share Free Cash Flow "
            "3,900 8.00 550 35% 23%")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None



# ---------------------------------------------------------------------------
# 수식어 없는 "earnings per share" (47차 감사)
# ---------------------------------------------------------------------------
def test_bare_earnings_per_share_is_read():
    """실물 TXN 문장: "earnings per share of $2.14".

    GAAP·net·diluted 중 어느 수식어도 없어서 예전 라벨 다섯 개가 전부
    놓쳤고, 그 결과 잣대 사다리를 못 넘어 측정에서 통째로 빠진 종목이
    24개였습니다.
    """
    text = (
        "TI reports second quarter 2026 financial results\n"
        "DALLAS (July 22, 2026) - Texas Instruments Incorporated today "
        "reported second quarter revenue of $5.46 billion, net income of "
        "$1.98 billion and earnings per share of $2.14."
    )
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS, exclude_nongaap=True) == 2.14


def test_bare_pattern_does_not_steal_nongaap():
    """"adjusted earnings per share" 는 GAAP 값으로 잡히면 안 됩니다."""
    text = "The company reported adjusted earnings per share of $3.10 for the quarter."
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS, exclude_nongaap=True) is None
    text2 = "Non-GAAP earnings per share was $1.55 in the fourth quarter."
    assert sf.find_eps_value(text2, sf.LABELS_GAAP_EPS, exclude_nongaap=True) is None


def test_specific_labels_still_win_first():
    """수식어 없는 라벨은 **맨 뒤**에 있어야 합니다 — 실물 HD 2023-11-14.

    실적표에는 Basic 과 Diluted 가 나란히 있고 Basic 이 **위**에 옵니다.
    수식어 없는 라벨이 앞에 오면 Basic($3.83)을 먼저 물고, 정답인
    Diluted($3.81)를 놓칩니다. 전수 비교로 확인한 실제 차이입니다
    (원문 120건 중 3건에서 순서가 값을 바꿨습니다).
    """
    text = (
        "in millions, except per share data\n"
        "Basic earnings per share       $3.83     $4.25\n"
        "Diluted earnings per share     $3.81     $4.24\n"
    )
    got = sf.find_eps_value(text, sf.LABELS_GAAP_EPS, exclude_nongaap=True)
    assert got == 3.81, f"Basic 을 집었습니다: {got}"



# ---------------------------------------------------------------------------
# 은행·금융 표기 (50차) — "per common share" 와 "숫자가 이름 앞"
# ---------------------------------------------------------------------------
def test_per_common_share_is_read():
    """실물: GS·WFC 는 "per **common** share" 로 씁니다.

    이 한 낱말이 없어서 대형 은행 5곳이 통째로 측정에서 빠져 있었습니다.
    """
    gs = "Diluted earnings per common share (EPS)1 was $20.98 for the second quarter"
    assert sf.find_eps_value(gs, sf.LABELS_GAAP_EPS, exclude_nongaap=True) == 20.98
    wfc = "Diluted earnings per common share                 2.00    1.60"
    assert sf.find_eps_value(wfc, sf.LABELS_GAAP_EPS, exclude_nongaap=True) == 2.00


def test_bare_eps_of_is_read_but_footnotes_are_not():
    """실물 BLK "EPS of $12.19" 는 읽고, GS 각주 "EPS1"·"EPS Impact" 는 안 읽습니다."""
    blk = "EPS of $12.19, or $13.91 as adjusted"
    assert sf.find_eps_value(blk, sf.LABELS_GAAP_EPS, exclude_nongaap=True) == 12.19
    noise = "basic EPS1 and diluted EPS1 are shown in Note 21. EPS Impact was 3.00"
    assert sf.find_eps_value(noise, sf.LABELS_GAAP_EPS, exclude_nongaap=True) is None


def test_eps_before_per_share_phrase():
    """실물 COF: "…or $4.73 per diluted common share" — 숫자가 이름 **앞**."""
    cof = ("net income for the second quarter of 2026 of $3.0 billion, "
           "or $4.73 per diluted common share, compared")
    assert sf.find_eps_before_per_share(cof) == 4.73
    loss = "net loss of $4.3 billion, or $(8.58) per diluted common share"
    assert sf.find_eps_before_per_share(loss) == -8.58


def test_eps_before_prefers_diluted_and_skips_basic():
    """희석을 먼저 쓰고, basic 만 있으면 쓰지 않습니다."""
    both = "or $3.83 per basic share and $3.81 per diluted share"
    assert sf.find_eps_before_per_share(both) == 3.81
    only_basic = "or $3.83 per basic share for the quarter"
    assert sf.find_eps_before_per_share(only_basic) is None


def test_eps_before_blocks_nongaap_and_forecast():
    """논갭·전망 문맥은 GAAP 실적이 아닙니다 (사고 16 규칙 그대로)."""
    assert sf.find_eps_before_per_share(
        "adjusted net income, or $5.20 per diluted share, excluded charges") is None
    assert sf.find_eps_before_per_share(
        "The company expects full year results of $9.00 per diluted share.") is None

# ---------------------------------------------------------------------------
# 74차 — 원문 부탁 목록으로 확보한 실물 4건에서 나온 결함들
# ---------------------------------------------------------------------------
# 아래 문장은 전부 data/measure/raw/ 에 담겨 온 **실제 보도자료**에서
# 그대로 옮긴 것입니다 (지어낸 예제가 아닙니다).

def test_share_count_row_is_not_read_as_eps():
    """주식 수 행을 EPS 로 읽으면 안 됩니다 (실물 UCTT 2026-04-28).

    손익계산서 맨 아래에는 EPS 표 바로 다음에 **주식 수** 표가 붙는데,
    그 제목도 "…per share" 로 끝납니다. 파서가 그 아래 45.3 을 물고
    이름에 loss 가 있다고 부호까지 뒤집어 **−45.30** 으로 읽었습니다.
    UCTT 는 세 해 연속 같은 사고를 냈습니다(−45.30 · −45.10 · −44.60).
    """
    # 실물 UCTT 원문의 두 대목을 그대로 붙였습니다: 앞은 진짜 값이 있는
    # 문장, 뒤는 파서를 넘어뜨린 주식 수 표입니다.
    text = (
        "Total revenue was $533.7 million. Total gross margin was 15.8%, "
        "operating margin was 2.1%, and net loss was $(17.9) million or "
        "$(0.40) per diluted share.\n"
        "\n"
        "Net loss per share attributable to UCT common stockholders:\n"
        "Basic                        $(0.40)      $(0.11)\n"
        "Diluted                      $(0.40)      $(0.11)\n"
        "Shares used in computing net loss per share:\n"
        "Basic                          45.3         45.1\n"
        "Diluted                        45.3         45.1\n"
    )
    got = sf.parse_press_release(text)["gaap_eps"]
    assert got == -0.40, got
    assert got != -45.3, "주식 수를 EPS 로 읽었습니다"

    # ⚠️ 정직하게 적어 둡니다: **이 실물에서는** 줄 넘기 금지만으로도
    # 막힙니다(45.3 이 이름과 다른 줄에 있으므로). 아래는 표가 한 줄로
    # 눌린 경우로, 이때는 주식 수 가드가 있어야만 막힙니다. 슬라이드·PDF
    # 추출에서 표가 한 줄로 눌리는 일은 실제로 있습니다(TSLA 날짜 열 표).
    # 다만 **주식 수 표가 눌린 실물은 아직 못 봤습니다** — 예방입니다.
    한줄 = "Shares used in computing net loss per share: Basic 45.3 Diluted 45.1\n"
    assert sf.find_eps_value(한줄, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) is None


def test_annual_value_after_the_number_is_skipped():
    """값 바로 뒤가 "for the year ended" 면 그 숫자는 연간값입니다.

    실물 GS 2022-01-18 — 한 문장에 연간·전년·분기가 줄줄이 있습니다.
    파서는 맨 앞의 **연간 59.45** 를 물고 있었습니다. 진짜 분기값은
    같은 문장 뒤쪽의 **10.81** 입니다.
    """
    text = (
        "Diluted earnings per common share (EPS) was $59.45 for the year "
        "ended December 31, 2021 compared with $24.74 for the year ended "
        "December 31, 2020, and was $10.81 for the fourth quarter of 2021 "
        "compared with $12.08 for the fourth quarter of 2020.\n"
    )
    got = sf.find_eps_value(text, sf.LABELS_GAAP_EPS, exclude_nongaap=True)
    assert got == 10.81, got


def test_headline_for_year_is_skipped():
    """헤드라인의 "of $59.45 for 2021" 도 연간값입니다 (실물 GS)."""
    text = ("Goldman Sachs Reports Record Earnings Per Common Share of "
            "$59.45 for 2021\n")
    got = sf.find_eps_value(text, sf.LABELS_GAAP_EPS, exclude_nongaap=True)
    assert got is None, got


def test_full_year_bullet_line_is_skipped():
    """줄이 "Full-year …" 로 시작하면 그 줄의 값은 전부 연간입니다.

    실물 VZ 2023-01-24 — 이 한 줄 때문에 조정 EPS 가 5.18(연간)로
    읽혔습니다. 실제 4분기 조정 EPS 는 1.19 입니다.
    """
    line = ("\u2022Full-year 2022 earnings per share (EPS) of $5.06, compared "
            "with $5.32 in 2021; adjusted EPS1, excluding special items, of "
            "$5.18, compared with 2021 adjusted EPS1 2 of $5.50.\n")
    assert sf.find_eps_value(line, sf.LABELS_ADJUSTED_EPS) is None, "연간 줄"

    # 같은 형식의 **분기** 줄은 그대로 읽어야 합니다 (반대쪽도 막습니다)
    분기줄 = ("\u2022Fourth-quarter 2022 adjusted EPS1, excluding special "
           "items, of $1.19.\n")
    assert sf.find_eps_value(분기줄, sf.LABELS_ADJUSTED_EPS) == 1.19


def test_eps_search_never_crosses_a_line():
    """이름 뒤 숫자 탐색이 줄을 넘으면 안 됩니다 (실물 IPGP 2025-02-11).

    각주 문장에 "adjusted EPS" 가 있고, 그 뒤로 줄과 문단을 넘어
    300자 떨어진 **"Exhibit 99.1"** 을 물어 조정 EPS 가 99.10 이
    됐습니다. 진짜 EPS 는 그 자리에 없습니다 — 답은 "없음"입니다.
    """
    text = (
        "the amortization of acquired intangible assets of $2.5 million "
        "excluded from the calculation of adjusted EPS, stock based "
        "compensation of $11.0 million excluded from adjusted EBITDA.\n"
        "3\n\n\nExhibit 99.1\n\nIPG PHOTONICS CORPORATION\n"
    )
    got = sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS)
    assert got is None, got


def test_label_on_a_heading_line_still_finds_the_next_line_value():
    """이름이 **제목 줄**이고 값이 다음 줄에 오는 형식도 읽어야 합니다.

    실물 HPE 2026-03-09 — 74차 전수 비교에서 이 형식 2건을 잃는 것을
    보고 탐색 범위를 "같은 줄"에서 "같은 문단"으로 넓혔습니다.
    한쪽만 막으면 반대로 넘어집니다.
    """
    text = ("Diluted net earnings per share (\u201cEPS\u201d): \n"
            "\u25e6GAAP of $0.31, down $0.13 from the prior-year period\n")
    got = sf.find_eps_value(text, sf.LABELS_GAAP_EPS, exclude_nongaap=True)
    assert got == 0.31, got


def test_comparison_to_a_prior_fiscal_year_is_not_annual_context():
    """"in the fourth quarter of fiscal year 2022" 는 **비교 대상**입니다.

    실물 NTAP — 이것까지 "연간"으로 보면 바로 뒤의 진짜 분기값 1.54 를
    버리고, 같은 줄 끝의 환율 영향 0.08 을 뭅니다. 74차 전수 비교가
    잡아낸 제 실수입니다.
    """
    line = ("\u2022Earnings per share: GAAP net income per share6 of $1.13 "
            "compared to $1.14 in the fourth quarter of fiscal year 2022; "
            "non-GAAP net income per share of $1.54 compared to $1.42 in the "
            "fourth quarter of fiscal year 2022. The year-over-year "
            "fluctuations include an unfavorable impact of approximately "
            "$0.08 from foreign currency exchange rate changes.\n")
    assert sf.find_eps_value(line, sf.LABELS_ADJUSTED_EPS) == 1.54

    # 반대쪽: 줄이 "Fiscal year 2023 …" 으로 **시작**하면 그 줄은 연간입니다
    연간줄 = ("\u2022Fiscal year 2023 GAAP net income per share of $5.79; "
           "fiscal year 2023 non-GAAP net income per share of $5.59\n")
    assert sf.find_eps_value(연간줄, sf.LABELS_ADJUSTED_EPS) is None


def test_section_title_separates_annual_from_quarterly():
    """구역 제목이 연간/분기를 가릅니다 (76차 — 실물 HPE 2023-11-28).

    값이 있는 줄만 보면 둘을 가를 수 없습니다 — 글자가 똑같습니다.
    가르는 정보는 **몇 줄 위의 구역 제목**에 있습니다. 이 실물에서
    파서는 연간 1.54 를 물고 있었고, 진짜 분기값은 0.49 입니다.
    """
    text = (
        "Fiscal 2023 Full-Year Financial Results\n"
        "\u2022Revenue: $29.1 billion, up 2%\n"
        "\u2022Diluted net earnings per share (\u201cEPS\u201d): \n"
        "\u25e6GAAP of $1.54, up 133% from the prior-year period\n"
        "Fourth Quarter Fiscal 2023 Financial Results  \n"
        "\u2022Revenue: $7.4 billion, down 7%\n"
        "\u2022Diluted net EPS: \n"
        "\u25e6GAAP of $0.49, up 313% from the prior-year period\n"
    )
    got = sf.find_eps_value(text, sf.LABELS_GAAP_EPS, exclude_nongaap=True)
    assert got == 0.49, got


def test_document_title_is_not_mistaken_for_a_section_title():
    """문서 제목을 구역 제목으로 오인하면 **문서 전체**를 버립니다.

    문서 제목은 길고 회사 이름·Reports 가 들어갑니다. 이것을 연간
    구역이라 보면 그 아래 진짜 분기값까지 전부 사라집니다.
    """
    본문 = "\u2022Diluted net EPS: \n\u25e6GAAP of $1.30 for the quarter\n"

    # ⑴ 분기가 함께 적힌 문서 제목 (실물 Western Digital)
    제목1 = ("Western Digital Reports Fiscal Fourth Quarter and Fiscal Year "
           "2022 Financial Results\n")
    # ⑵ 연간만 적힌 문서 제목 — "Reports" 로 알아봅니다
    제목2 = "Acme Corporation Reports Full Year 2025 Financial Results\n"
    # ⑶ Reports 도 없는 긴 제목 — **길이**로 알아봅니다
    제목3 = ("Acme Corporation Global Holdings Limited Full Year 2025 "
           "Financial Results\n")

    for 제목 in (제목1, 제목2, 제목3):
        got = sf.find_eps_value(제목 + 본문, sf.LABELS_GAAP_EPS,
                                exclude_nongaap=True)
        assert got == 1.30, (제목.strip(), got)


def test_section_title_naming_both_periods_is_not_annual_only():
    """제목에 분기가 함께 있으면 분기값도 그 아래 있습니다.

    실물: "Fourth Quarter and Full Year 2025 Financial Results".
    """
    text = ("Fourth Quarter and Full Year 2025 Financial Results\n"
            "\u2022Diluted net EPS: \n\u25e6GAAP of $0.77\n")
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) == 0.77


def test_dividend_per_share_is_never_eps():
    """배당금은 EPS 가 아닙니다 (76차 — 실물 HPE).

    67차에도 JPM 배당금이 EPS 자리에 들어와 이익 시계열을 톱니로
    만든 적이 있습니다. 같은 사고가 다른 경로로 또 났습니다.
    """
    text = ("The Board of Directors declared a regular cash dividend of "
            "$0.13 per share on the company\u2019s common stock.\n")
    assert sf.find_eps_before_per_share(text) is None

    # 반대쪽: 진짜 EPS 문장은 그대로 읽어야 합니다
    진짜 = "net loss was $(17.9) million or $(0.40) per diluted share.\n"
    assert sf.find_eps_before_per_share(진짜) == -0.40


def test_slide_guard_measures_distance_not_the_whole_line():
    """슬라이드 규칙은 **라벨 앞 거리**로 재야 합니다 (83차 — 실물 BAC).

    눌린 문서는 한 줄이 수천 자입니다. "줄 안에 <img 가 있기만 하면"
    으로 재면 이미지 뒤에 이어지는 **멀쩡한 문장까지** 버립니다.
    BAC 는 그래서 **전 분기가 "없음"** 이었습니다 — 진짜 값 1.06 이
    원문에 그대로 있는데도.
    실측 거리: BAC 진짜 문장 1,094~4,751자 · PG 슬라이드 잡음 104자.
    """
    # 이미지가 **멀리** 있으면 그 뒤 문장은 읽어야 합니다
    멀리 = ('<img src="a.jpg"/> ' + "x" * 400 +
          " Diluted earnings per share of $1.06 compared to $0.81.\n")
    assert sf.find_eps_value(멀리, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) == 1.06

    # 이미지가 **바로 앞**이면 차트 숫자 나열이므로 건너뜁니다
    가까이 = ('<img src="b.jpg"/> \u2022 Core gross margin \u2022 diluted '
           'earnings per share 5 2 1 6 3\n')
    assert sf.find_eps_value(가까이, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) is None


def test_core_eps_is_non_gaap():
    """"Core EPS" 는 논갭입니다 (83차 — 실물 PG).

    P&G 는 자기네 논갭 지표를 "Core" 라고 부릅니다:
    "Diluted EPS $1.63 … **Core EPS $1.59**".
    이 말을 모르면 논갭 값이 GAAP 칸에 들어갑니다.
    """
    text = "Core earnings per share were $1.59, +3% vs the prior year.\n"
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) is None

    # 진짜 GAAP 줄은 그대로 읽어야 합니다
    진짜 = "Diluted net earnings per share were $1.63 for the quarter.\n"
    assert sf.find_eps_value(진짜, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) == 1.63


def test_normal_quarterly_sentence_still_reads(): 
    """멀쩡한 분기 문장은 그대로 읽혀야 합니다 (한쪽만 막으면 안 됨)."""
    for 문장, 답 in (
        ("Fourth quarter non-GAAP earnings per diluted share of $1.66\n", 1.66),
        ("Adjusted EPS of $0.83 for the third quarter\n", 0.83),
    ):
        got = sf.find_eps_value(문장, sf.LABELS_ADJUSTED_EPS)
        assert got == 답, (문장, got)

    # GAAP 쪽 이름도 마찬가지입니다 (적자 부호 뒤집기 포함)
    문장 = "net loss per diluted share of $0.42 for the quarter\n"
    got = sf.find_eps_value(문장, sf.LABELS_GAAP_EPS, exclude_nongaap=True)
    assert got == -0.42, got


if __name__ == "__main__":
    tests = [
        (n, f) for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
    ]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {name} — {type(e).__name__}: {e}")
            failed += 1
    print(f"\nEPS 읽기·이익의 질 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
