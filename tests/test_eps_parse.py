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
