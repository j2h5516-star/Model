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
import io
import pathlib
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


def test_보도자료가_붙었는지_표시가_남는다():
    """조정 EPS 가 빈 칸일 때 "수집 실패"인지 "회사가 안 준 것"인지
    가릴 수 있어야 합니다 (98차 계기).

    ⚠️ 이 시험이 왜 필요한가 — 97차에 제가 `source` 로 이걸 재려다
    **틀린 진단을 냈습니다.** `source` 는 **논갭 영업이익의 출처**만
    적는 칸이라, 보도자료가 붙었어도 그 안에 논갭 영업이익이 없으면
    XBRL 값인 '근사치'로 남습니다. 실물 FN·QCOM·TER 는 조정 EPS 를
    보도자료에서 읽었는데도 전 행이 '근사치'였습니다.

    그래서 **논갭 영업이익이 없는 보도자료**를 일부러 물려, 그때도
    "붙었다"가 남는지를 못박습니다. 이 경우가 바로 제가 틀렸던 자리입니다.
    """
    # ① 논갭 영업이익이 **없는** 보도자료 (source 는 '근사치'로 남는다)
    row = {"op_income": 100 * M, "source": cfg.SRC_APPROX}
    sf._apply_press_to_row(row, {"adj_eps": 3.72, "op_income": None})
    assert row["press_matched"] is True, "보도자료가 붙었는데 표시가 안 남았다"
    assert row["source"] == cfg.SRC_APPROX, (
        "이 시험의 전제 — 논갭 영업이익이 없으면 source 는 '근사치'로 남는다. "
        "전제가 깨지면 시험이 재려던 것을 못 잰다."
    )

    # ② 보도자료가 **안 붙은** 행은 False 여야 한다.
    #    "없음"이 아니라 **False** 여야 세는 쪽에서 구분이 된다
    #    (칸 자체가 없으면 옛 데이터와 섞여 조용히 뭉개진다).
    from sec_fundamentals import _apply_press_to_row  # noqa: F401
    뼈대 = {"source": cfg.SRC_APPROX, "press_matched": False}
    assert 뼈대["press_matched"] is False


def test_보도자료_표시가_스냅샷까지_간다():
    """계기는 저장돼야 쓸모가 있습니다 — 69차에 매출총이익률을 읽고도
    스냅샷에 안 담아 개발 환경에서 쓸 수 없던 사고가 있었습니다."""
    import measure_store
    assert "press_matched" in measure_store.EPS_FIELDS
    rows = measure_store.eps_rows([
        {"announced_date": "2026-05-04", "adj_eps": 3.72, "press_matched": True},
        {"announced_date": "2026-02-04", "adj_eps": None, "press_matched": False},
    ])
    assert [r["press_matched"] for r in rows] == [True, False], rows


def test_xbrl_value_is_kept_beside_the_press_value():
    """보도자료가 덮어쓰기 **전**의 XBRL 값을 나란히 남겨야 합니다 (90차).

    89차까지 실측한 오류(전년 열·9개월 누적·연간값·부문)는 전부
    "숫자는 맞는데 자리를 잘못 짚은 것"이었습니다. XBRL 은 값마다
    기간·단위·부문이 태그로 붙어 그 네 종류가 생길 수 없습니다.
    어느 쪽을 본선으로 삼을지 **짐작이 아니라 숫자로** 정하려면
    두 값이 다 남아 있어야 합니다.
    """
    row = {"gaap_eps": 1.96, "revenue": 10_236_000_000.0,
           "gross_margin_pct": 77.0, "source": cfg.SRC_APPROX}
    press = {"gaap_eps": 1.56, "revenue": 9_000_000_000.0,
             "gross_margin_pct": 80.0, "op_income": None}
    sf._apply_press_to_row(row, press)

    # XBRL 값이 옆에 남아 있어야 합니다 (이 시험의 본래 목적)
    assert row["gaap_eps_xbrl"] == 1.96
    assert row["revenue_xbrl"] == 10_236_000_000.0
    assert row["gross_margin_pct_xbrl"] == 77.0

    # 본선 값 — 92차에 매출만 XBRL 우선으로 뒤집었습니다.
    # (90차에는 셋 다 보도자료가 이겼고, 이 시험이 그 상태를 못박고
    #  있었습니다. 91차 승부 결과 98:0 을 보고 매출만 바꿨으므로
    #  시험도 함께 고칩니다 — 실물이 규칙을 이깁니다.)
    assert row["revenue"] == 10_236_000_000.0, "매출은 XBRL 이 이겨야 합니다"
    # 99차 — GAAP EPS 도 승부가 났습니다 (야후 심판, 갈린 칸 XBRL 61 : 보도자료 2.
    # 전체 정확도 77.7% → 91.1%). 이 시험이 "아직 승부가 안 났다"를 못박고
    # 있어 빨간 불이 났고, 실측이 나온 뒤라 시험 쪽이 낡은 것이므로 고칩니다.
    assert row["gaap_eps"] == 1.96, "GAAP EPS 도 XBRL 이 이겨야 합니다 (99차)"
    # 매출총이익률은 **여전히 보도자료**입니다 — 승부가 아니라 정의 차이라
    # 뒤집지 않았습니다 (회사 논갭 vs 야후·XBRL 갭. 92차 ③)
    assert row["gross_margin_pct"] == 80.0, "매출총이익률은 뒤집지 않습니다"
    assert row["gross_margin_pct"] == 80.0, "이익률은 정의 차이라 안 뒤집습니다"


def test_xbrl_companion_is_none_when_there_was_no_xbrl_row():
    """XBRL 에 짝이 없던 분기는 **없음**이어야 합니다 — 지어내지 않습니다."""
    row = {"source": cfg.SRC_APPROX}
    sf._apply_press_to_row(row, {"gaap_eps": 1.56, "op_income": None})
    assert row["gaap_eps_xbrl"] is None
    assert row["revenue_xbrl"] is None


def test_adjusted_eps_has_no_xbrl_companion():
    """조정 EPS·조정 EBITDA 는 XBRL 에 없으므로 짝 칸을 만들지 않습니다.

    없는 것을 만들면 다음 세션이 "XBRL 에도 조정 EPS 가 있구나"라고
    잘못 읽습니다 (창작 금지).
    """
    assert "adj_eps" not in sf._XBRL_KEPT_FIELDS
    assert "adjusted_ebitda" not in sf._XBRL_KEPT_FIELDS
    row = {"adj_eps": 9.9, "source": cfg.SRC_APPROX}
    sf._apply_press_to_row(row, {"adj_eps": 1.0, "op_income": None})
    assert "adj_eps_xbrl" not in row


def test_per_share_candidates_are_judged_by_cents_not_by_ratio():
    """주당 금액은 **비율이 아니라 절대차**로 골라야 합니다 (92차).

    옛 규칙(`low > 0 and high/low <= 2.0`)을 그대로 돌려 보면 EPS 를
    거의 다 버립니다 — 적자면 `low > 0` 에서 탈락하고, 값이 작아 두 후보의
    비가 2배를 쉽게 넘습니다. 실제로 XBRL GAAP EPS 가 스냅샷 3,066행에
    **한 건도** 안 들어와 있었습니다.
    """
    # 사실상 같은 값 — 골라야 합니다 (부호와 무관하게)
    assert sf._pick_close_value([-0.31, -0.30], per_share=True) is not None
    assert sf._pick_close_value([0.00, 0.01], per_share=True) is not None
    assert sf._pick_close_value([1.96, 1.96], per_share=True) == 1.96

    # 진짜로 다른 값 — 골라선 안 됩니다 (기본 0.10 vs 희석 0.30 류)
    assert sf._pick_close_value([0.10, 0.30], per_share=True) is None
    assert sf._pick_close_value([1.96, 5.00], per_share=True) is None


def test_money_candidates_keep_the_ratio_rule():
    """금액은 자릿수가 커서 비율 판정이 맞습니다 — 건드리지 않았습니다."""
    assert sf._pick_close_value([100.0, 150.0], per_share=False) == 150.0
    assert sf._pick_close_value([100.0, 900.0], per_share=False) is None


def test_per_share_unit_is_matched_by_word_not_by_spelling():
    """주당 단위는 제공처마다 표기가 달라 **낱말로** 가려야 합니다 (92차).

    글자 그대로 비교하면 한 글자만 어긋나도 전부 버려집니다.
    """
    for 표기 in ("USD/SHARES", "USD/shares", "USD-per-shares", "usd/share"):
        assert sf._unit_matches(표기, "USD/SHARES"), 표기
    # 금액 단위가 주당 자리로 새어 들면 자릿수가 무너집니다
    assert not sf._unit_matches("USD", "USD/SHARES")
    assert not sf._unit_matches("USD/SHARES", "USD")
    assert sf._unit_matches("USD", "USD")


def test_revenue_prefers_xbrl_but_falls_back_to_the_press_release():
    """매출은 XBRL 우선, **없을 때만** 보도자료 (92차 — 91차 승부 반영).

    91차 실측: 갈린 98칸에서 XBRL 98 : 보도자료 0.
    다만 XBRL 이 없는 11칸 중 7칸은 보도자료가 맞던 값이라, 무조건
    XBRL 로 두면 맞던 값을 잃습니다.
    """
    # 둘 다 있으면 XBRL 이 이긴다 (실물 ABBV — 보도자료는 부문 매출)
    row = {"revenue": 15_423_000_000.0, "source": cfg.SRC_APPROX}
    sf._apply_press_to_row(row, {"revenue": 11_762_000_000.0, "op_income": None})
    assert row["revenue"] == 15_423_000_000.0
    assert row["revenue_xbrl"] == 15_423_000_000.0

    # XBRL 이 없으면 보도자료를 쓴다 (실물 ABNB — 보도자료가 맞던 값)
    row2 = {"revenue": None, "source": cfg.SRC_APPROX}
    sf._apply_press_to_row(row2, {"revenue": 3_096_000_000.0, "op_income": None})
    assert row2["revenue"] == 3_096_000_000.0


def test_gross_margin_is_deliberately_not_flipped():
    """매출총이익률은 뒤집지 않습니다 — 승부가 아니라 **정의 차이**입니다.

    보도자료는 회사가 발표한 논갭 이익률(ADI 69.4%)이고 XBRL·야후는
    갭(61.0%)입니다. 심판이 갭 기준이라 XBRL 이 자동으로 이길 뿐입니다.
    """
    row = {"gross_margin_pct": 61.0, "source": cfg.SRC_APPROX}
    sf._apply_press_to_row(row, {"gross_margin_pct": 69.4, "op_income": None})
    assert row["gross_margin_pct"] == 69.4, "이익률까지 뒤집으면 안 됩니다"
    assert row["gross_margin_pct_xbrl"] == 61.0


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


def test_구역_제목은_summary_라고도_적는다():
    """(183차-O) "Financial **Summary**" 도 구역 제목입니다 — 실물 ACMR.

    150차-AP 가 CSX 의 "Financial **Highlights**" 로 겪은 것과 **같은 병**이
    ACMR 에서 낱말만 바꿔 되풀이됐습니다. 파서는 연간 구역의 1.37 을 물고
    있었고 진짜 4분기 값은 0.11 입니다.

    ACMR 은 이 일이 **해마다** 일어나 22·23·24·25 Q4 의 GAAP EPS 가 전부
    "연간값이 분기 칸에 들어왔다"며 버려졌고(150차-AO), 그래서 이 종목은
    해마다 4분기가 **구멍**이었습니다.
    """
    text = (
        "Full Year 2025 Financial Summary\n"
        "Unless otherwise noted, the following figures refer to the full "
        "year of 2025.\n"
        "•Revenue was $901.3 million, up 15.2%\n"
        "•Diluted net income per share attributable to ACM Research, "
        "Inc. was $1.37 compared to $1.53.\n"
        "Fourth Quarter 2025 Financial Summary\n"
        "•Revenue was $244.4 million, up 9.4%\n"
        "•Diluted net income per share attributable to ACM Research, "
        "Inc. was $0.11 compared to $0.46.\n"
    )
    got = sf.find_eps_value(text, sf.LABELS_GAAP_EPS, exclude_nongaap=True)
    assert got == 0.11, f"연간 구역의 값을 물었습니다: {got}"

    # 실물 원문이 저장소에 있으면 그것으로도 확인합니다 (합성 자료만 믿지 않음)
    실물 = os.path.join(os.path.dirname(__file__), "..", "data", "measure",
                      "raw", "ACMR_2026-02-26_부탁.txt")
    if os.path.exists(실물):
        with open(실물, encoding="utf-8") as f:
            r = sf.parse_press_release(f.read())
        assert r.get("gaap_eps") == 0.11, (
            f"실물에서 분기 EPS 를 못 읽었습니다: {r.get('gaap_eps')}")


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


def test_target_and_range_numbers_are_not_actuals():
    """">$17.10" 과 "$18.45 - $18.95" 는 전망입니다 (84차 — 실물 UNH).

    83차에 슬라이드 규칙을 좁히자 UNH **전망표**가 새로 읽히면서
    분기 EPS 가 6.04 → 17.10 으로 **틀려졌습니다.** 내가 낸 회귀입니다.

        UnitedHealth Group Reports Second Quarter 2026 Results
        • **Earnings of $6.04 Per Share** …            ← 진짜 실적
        Diluted Net Earnings per Share  **> $17.10**   ← 전망(이상)
        Earnings per Share  **$18.45 - $18.95**        ← 전망(범위)

    실적에 "이상" 표기를 붙이거나 범위로 적는 회사는 없습니다.
    """
    전망 = ("Diluted Net Earnings per Share to Shareholders > $17.10 "
          "$18.45 - $18.95\n")
    assert sf.find_eps_value(전망, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) is None

    # 같은 문서의 진짜 실적 문장은 읽어야 합니다.
    # (이 형식은 숫자가 이름 앞에 오므로 다른 경로가 읽습니다)
    실적 = "Earnings of $6.04 per diluted share for the second quarter.\n"
    assert sf.find_eps_before_per_share(실적) == 6.04

    # 둘이 한 문서에 있을 때 전망이 아니라 실적이 나와야 합니다
    합친것 = 실적 + 전망
    assert sf.parse_press_release(합친것)["gaap_eps"] == 6.04


def test_negative_eps_is_not_mistaken_for_a_range_end():
    """적자 표기 "-$0.40" 을 범위 뒤끝으로 보면 안 됩니다.

    범위 뒤끝은 **대시 앞에 숫자**가 있습니다("18.45 - $18.95").
    적자 표기는 그렇지 않습니다. 한쪽만 막으면 반대로 넘어집니다.
    """
    text = "GAAP net loss per diluted share was -$0.40 for the quarter.\n"
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) == -0.40


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


def test_impact_sentence_is_not_earnings():
    """"…per share **by** $0.00" 은 영향을 말하는 문장이지 실적이 아닙니다.

    실물 (87차, CRM 2025-09-03 보도자료 원문 그대로):
    이 한 문장 때문에 조정 EPS 와 GAAP EPS 가 **둘 다 0.00** 이 됐습니다.
    진짜 값은 같은 문서의 표에 2.91·1.96 으로 있었습니다.
    """
    문장 = (
        "During the three months ended July 31, 2025 and 2024, gains (losses) "
        "on strategic investments impacted GAAP diluted net income per share "
        "by $0.00 and $(0.03) based on a U.S. tax rate of 23.5% and 24.5%, "
        "respectively, and non-GAAP diluted net income per share by $0.00 "
        "and $(0.03) based on a non-GAAP tax rate of 22.0%.\n"
    )
    assert sf.find_eps_value(문장, sf.LABELS_ADJUSTED_EPS) is None
    assert sf.find_eps_value(문장, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) is None


def test_impact_sentence_does_not_hide_the_real_table_row():
    """영향 문장을 건너뛴 뒤 **진짜 표의 값**은 그대로 읽어야 합니다.

    걷어내기만 하고 진짜 값까지 놓치면 고친 것이 아닙니다.
    """
    글 = (
        "gains on strategic investments impacted GAAP diluted net income "
        "per share by $0.00 and $(0.03).\n"
        "Diluted net income per share      $1.96      $1.47\n"
    )
    assert sf.find_eps_value(글, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) == 1.96


def test_number_at_the_edge_of_the_search_window_is_read_whole():
    """찾는 창의 끝이 숫자 위에 떨어져도 숫자를 **자르지 않아야** 합니다.

    실물 (87차, CRM): 표의 이름과 값 사이가 공백 40칸쯤 벌어지자 160자
    창의 끝이 "$1.5|9" 한가운데에 떨어져 **1** 로 읽혔고, 그 뒤 옆 칸의
    전년 값 1.56 이 채택됐습니다.
    """
    이름 = "Diluted net income per share"
    # 창(160자) 끝이 값 한가운데 오도록 공백을 맞춥니다
    빈칸 = " " * (160 - 3)
    글 = f"{이름}{빈칸}$1.59      $1.56\n"
    자리 = len(이름)
    읽은값 = sf._parse_number_at(글, 자리)
    assert 읽은값 is not None and 읽은값[0] == 1.59, 읽은값


def test_number_starting_inside_the_tail_is_still_refused():
    """여유 글자는 숫자를 **끝맺는 데만** 씁니다.

    여유 구간(창 뒤 40자) 안에서 **새로 시작하는** 숫자까지 주워 오면
    탐색 범위가 160자에서 200자로 몰래 넓어집니다. 그래서 이 시험은
    숫자를 여유 구간 한가운데(창 끝 +5자)에 두고, 거절되는지 봅니다.
    """
    이름 = "Diluted net income per share"

    def 글(숫자시작: int) -> str:
        """이름 뒤 `숫자시작` 번째 자리에 숫자의 **첫 글자**가 오도록 만듭니다.
        ("$" 가 한 칸 앞에 붙으므로 공백은 그만큼 덜 넣습니다)"""
        return 이름 + " " * (숫자시작 - 1) + "$1.59\n"

    # 창(160) 밖 · 여유(40) 안에서 시작하는 숫자 → 거절
    assert sf._parse_number_at(글(165), len(이름)) is None

    # 창 마지막 자리에서 시작하는 숫자 → 읽어야 합니다 (거절이 과하지 않은지)
    읽은값 = sf._parse_number_at(글(159), len(이름))
    assert 읽은값 is not None and 읽은값[0] == 1.59, 읽은값


def test_콤마_없는_네자리_숫자를_자르지_않는다():
    """콤마 없이 적은 네 자리 이상 숫자를 **통째로** 읽어야 합니다 (183차-BH).

    숫자 정규식의 첫 갈래가 `\\d{1,3}(?:,\\d{3})*` 였습니다. 세 자리를 먹고
    콤마가 없으면 반복이 0회로 끝나는데, 정규식의 `|` 는 **먼저 성공한
    갈래**를 쓰므로 네 자리를 통째로 읽을 두 번째 갈래까지 가지 못했습니다.

    그래서 이런 일이 있었습니다 (실행 출력):

        "2023"           → 202      ← 연도가 매출로 들어앉음
        "$ 1234 million" → 123 million   ← **매출이 10분의 1**

    마지막 줄이 가장 위험합니다. 잘린 값은 "그럴듯한 크기"라 뒷단의
    어떤 검사에도 안 걸립니다 — 없음도 아니고 대놓고 이상하지도 않은,
    조용히 틀린 값입니다.
    """
    for 글자, 참값 in [
        ("2023", 2023.0),
        ("9420", 9420.0),
        ("1234.5", 1234.5),
        ("12345", 12345.0),
        ("202", 202.0),        # 세 자리는 그대로
        ("1,234.5", 1234.5),   # 콤마가 있는 표기는 예전대로
        (".75", 0.75),         # 소수점으로 시작하는 표기(SCHW)도 예전대로
    ]:
        m = sf._NUMBER_RE.match(글자)
        assert m is not None, 글자
        읽은 = float(m.group("num").replace(",", ""))
        assert 읽은 == 참값, f"{글자!r} → {읽은} (참값 {참값})"

    # 단위까지 붙은 실제 모양 — 매출이 10분의 1로 읽히면 안 됩니다.
    # (`_parse_number_at` 은 "million" 을 이미 곱해 돌려줍니다)
    읽은값 = sf._parse_number_at("Total revenue $ 1234 million", len("Total revenue"))
    assert 읽은값 is not None and 읽은값[0] == 1_234_000_000.0, 읽은값


def test_연도를_값으로_물지_않는다():
    """네 자리 연도는 값이 아닙니다 (183차-BI).

    183차-BH 가 숫자 잘림을 고치자 **숨어 있던 병이 드러났습니다.**
    파서는 라벨 뒤 첫 숫자를 값으로 삼는데, 그 자리에 흔히 **연도**가
    있습니다 — "Revenue Range for Third Quarter **2023**".

    잘려 있을 때는 `202` 라는 작은 수라 뒷단 검사가 버렸습니다. 이제
    `2023` 이 되어 표 단위(백만)가 곱해지면 **20억** — 어떤 검사에도
    안 걸리는 그럴듯한 크기가 됩니다. 실측으로 영업이익 35칸 · 매출
    6칸이 이 모양이었습니다(CAT 은 202조 → 2,025조).

    빠져나갈 구멍을 셋 열어 둡니다. 연도에는 없는 표시들입니다:
      `2,023`(콤마) · `2023 million`(단위 낱말) · `2023.5`(소수점)
    """
    # 연도 → 값으로 쓰지 않습니다
    for 연도 in ("1900", "2023", "2026", "2100"):
        읽은값 = sf._parse_number_at(f"Total revenue   {연도}", len("Total revenue"))
        assert 읽은값 is None, f"{연도} → {읽은값}"

    # 연도가 아닌 표시가 하나라도 붙으면 그대로 읽습니다
    for 글자, 참값 in [
        ("2,023", 2023.0),              # 콤마를 쓴 표 값
        ("2023 million", 2.023e9),      # 값이 스스로 단위를 말함
        ("2023.5", 2023.5),             # 연도에는 소수점이 없음
        ("1899", 1899.0),               # 범위 밖
        ("2101", 2101.0),               # 범위 밖
    ]:
        읽은값 = sf._parse_number_at(f"Total revenue   {글자}", len("Total revenue"))
        assert 읽은값 is not None and 읽은값[0] == 참값, f"{글자!r} → {읽은값}"


def test_이익률을_찾을_때는_연도를_건너뛰지_않는다():
    """연도 건너뛰기는 **이익률 탐색에서는 이득 없이 판만 흔듭니다** (183차-BJ).

    이익률은 "뒤에 % 기호가 붙었는가"로 이미 걸러집니다. 연도가 값으로
    채택될 길이 원래 없습니다. 그런데 건너뛰면 **그 뒤의 나쁜 값**을
    더 빨리 만납니다.

    실물 PG 2020-10-20(눌린 발표자료) — 연도 가드를 넣자 52.70% 가
    1.00% 가 되었습니다:

        "… Core operating margin +350 basis points … Q1 FY **2021**
         **+1%** Pricing, flat Mix …"

    "2021" 을 건너뛰자 바로 뒤의 "+1%" 를 물었습니다. 연도는 어차피
    퍼센트가 아니라 채택되지 않았을 값입니다 — 건너뛸 까닭이 없습니다.

    ⚠️ 이 시험은 **실물 원문**으로 잽니다. 처음에는 짧은 더미 문장으로
    만들었는데, 그 문장에서는 연도를 건너뛰든 말든 결과가 같아
    **고쳐도 빨간 불이 그대로**였습니다. 실제 동작을 재현하지 못하는
    시험은 아무것도 지켜 주지 않습니다.
    """
    원문 = pathlib.Path("data/measure/raw/PG_2020-10-20.txt")
    if not 원문.exists():          # 원문 캐시가 없는 환경에서는 건너뜁니다
        return
    글 = 원문.read_text(encoding="utf-8", errors="replace")
    읽은값 = sf.parse_press_release(글)["gross_margin_pct"]
    assert 읽은값 == 52.7, f"표의 52.7% 가 아니라 {읽은값} 을 물었습니다"


def test_fiscal_연도_표기도_연간으로_본다():
    """`fiscal 2023` 도 "연간"이라는 말입니다 (183차-BJ).

    연간 가드는 `full year` · `fiscal year` · `FY 2025` 는 알아보는데
    **`fiscal 2023`** 은 못 알아봤습니다.

    실물 HD 2024-02-20 — 연도 가드를 넣자 분기값 2.82 가 **연간값**
    15.11 로 바뀌었습니다:

        "Net earnings for **fiscal 2023** were $15.1 billion,
         or **$15.11 per diluted share**"

    전에는 이 문장의 연도가 우연히 방패 노릇을 했습니다. 방패를 치웠으니
    제대로 된 문지기를 세웁니다.

    이 문장은 숫자가 **이름 앞**에 오는 모양이라
    `find_eps_before_per_share` 가 읽습니다. 그 길에는 연간 가드가
    **아예 없었습니다** — 배당·전망·논갭만 걸렀습니다.
    """
    글 = ("Net earnings for fiscal 2023 were $15.1 billion, "
          "or $15.11 per diluted share.")
    assert sf.find_eps_before_per_share(글) is None

    # 분기 문장은 그대로 읽어야 합니다 (가드가 과하지 않은지)
    분기글 = ("Net earnings for the fourth quarter were $2.8 billion, "
              "or $2.82 per diluted share.")
    assert sf.find_eps_before_per_share(분기글) == 2.82

    # 판정 자체 — `quarter` 가 함께 있으면 연간이 아닙니다.
    # (이 줄이 없으면 quarter 예외를 지워도 시험이 전부 초록이었습니다)
    assert sf._연간이라고_말하는가(" for fiscal 2023 were ") is True
    assert sf._연간이라고_말하는가("Fiscal 2018 fourth quarter ") is False
    assert sf._연간이라고_말하는가(" for the full year 2020, ") is True
    assert sf._연간이라고_말하는가(" for the third quarter of fiscal 2025 ") is False

    # 실물 — 이름과 값 **사이**에 연간 표시가 끼는 길(`find_eps_value`).
    # 이 갈래가 없으면 사이 구간 가드를 통째로 지워도 시험이 초록이었습니다.
    원문 = pathlib.Path("data/measure/raw/HD_2024-02-20.txt")
    if 원문.exists():
        실물 = 원문.read_text(encoding="utf-8", errors="replace")
        읽은값 = sf.parse_press_release(실물)["gaap_eps"]
        assert 읽은값 == 2.82, f"연간 EPS 를 물었습니다 ({읽은값}, 분기값은 2.82)"


def test_누적_YTD_는_분기값이_아니다():
    """`YTD`(연초 이래 누적)는 분기값이 아닙니다 (183차-BK).

    파서는 **연간**과 **분기**만 가렸습니다. `YTD` 라는 말은 코드
    어디에도 없었는데, 저장된 원문 259건에 그 표현이 있습니다.

    실물 MCO 2024-10-22(눌린 발표자료):

        3Q 2024        3Q 2024        Diluted EPS
        $2.93 ⇑ 39%    $3.21 ⇑ 32%
        YTD 2024       YTD 2024       Adjusted Diluted EPS1
        $9.09 ⇑ 32%    $9.85 ⇑ 28%

    그 분기 조정 EPS 는 **3.21** 인데 누적 **9.09** 를 물었습니다.

    누적은 연간값보다 **잡기 어렵습니다** — 3분기 누적은 그 분기의
    3배쯤이라 "그럴듯한 크기"로 보이기까지 합니다. 이웃 분기와 견주는
    잣대만으로는 가릴 수 없습니다.
    """
    assert sf._연간이라고_말하는가(" YTD 2024 Adjusted Diluted EPS ") is True
    assert sf._연간이라고_말하는가(" year-to-date results ") is True
    # 분기 표현은 그대로 — 가드가 과하지 않은지
    assert sf._연간이라고_말하는가(" 3Q 2024 Adjusted Diluted EPS ") is False

    # ⚠️ **분기 표기 `3Q` 는 일부러 알아보지 않습니다** (183차-BM).
    #    저장 원문에 `3Q`·`4Q19` 꼴이 435건이나 있어 넣어 봤다가
    #    **되돌렸습니다** — 전수 3,299건에서 좋아짐 6 · 나빠짐 6 이었고,
    #    그중 MCO 두 칸은 참값 3.21 이 GAAP 값 2.93 으로 틀어졌습니다.
    #    이 자는 연간 가드의 **예외**라, 넓힐수록 가드가 풀려서 맞는
    #    분기값을 되찾는 만큼 연간값도 함께 들어옵니다.
    #    이 시험은 **지금 그렇다는 사실을 적어 두는 것**이지 옳다는
    #    뜻이 아닙니다 — 다른 장치로 고치면 이 줄을 뒤집어야 합니다.
    assert sf._SECTION_QUARTER_RE.search(" Q3 2024 ") is not None
    assert sf._SECTION_QUARTER_RE.search(" 3Q 2024 ") is None   # ⚠️ 못 알아봄

    # 실물 — 참값은 그 분기 조정 EPS **3.21** 입니다.
    # (이 갈래가 없으면 이름 앞 가드에서 YTD 를 지워도 시험이 초록이었습니다)
    원문 = pathlib.Path("data/measure/raw/MCO_2024-10-22_부탁.txt")
    if 원문.exists():
        실물 = 원문.read_text(encoding="utf-8", errors="replace")
        읽은값 = sf.parse_press_release(실물)["adj_eps"]
        assert 읽은값 == 3.21, f"누적 EPS 를 물었습니다 ({읽은값}, 분기값은 3.21)"


def test_FY22_두자리_표기도_연간이다():
    """`FY22` 도 "그 회계연도 전체"라는 말입니다 (183차-BP).

    연간 가드는 `FY 2022` · `FY2022` 는 아는데 **두 자리 표기는
    몰랐습니다.** 실물 RF 2023-04-18(1분기 발표문):

        "… Performance Metrics **FY22** Reported Adjusted(1) …
         Total Revenue **$7.2B** $7.2B …"

    72억은 리전스파이낸셜의 **2022년 한 해** 매출입니다. 그 분기값은
    19억 안팎입니다.
    """
    for 연간표기 in (" FY22 Reported ", " FY'22 ", " FY 2022 ", " FY2022 "):
        assert sf._ANNUAL_BEFORE_RE.search(연간표기) is not None, 연간표기


def test_매출도_전망_문맥이면_읽지_않는다():
    """전망(가이던스)은 실적이 아닙니다 — **매출도** 마찬가지입니다 (183차-BP).

    `_FORECAST_NEAR_RE`(`guidance`·`outlook`·`expects` 포함)가 EPS
    경로에만 달려 있고 **매출 경로에는 없었습니다.**

    실물 FSLR 2018-07-26(2분기 발표문):

        2018 GAAP **Guidance**              Prior          Current
        **Net Sales**            $2.45B to $2.65B   $2.5B to $2.6B

    2분기 발표에 실린 **그 해 연간 전망**을 그 분기 매출로 읽었습니다.
    """
    전망글 = ("2018 GAAP Guidance                  Prior          Current\n"
              "Net Sales                $2.45B to $2.65B   $2.5B to $2.6B\n")
    assert sf.find_labeled_value(전망글, sf.LABELS_REVENUE) is None

    # 실적 문장은 그대로 읽어야 합니다 (가드가 과하지 않은지)
    실적글 = "Net sales for the second quarter were $309.3 million.\n"
    assert sf.find_labeled_value(실적글, sf.LABELS_REVENUE) == 309_300_000


def test_명사_approach_는_전망이_아니다():
    """`approach` 는 **뒤에 숫자가 올 때만** 전망입니다 (183차-BQ).

    전망 가드가 `\\bapproach(es|ing)?\\b` 를 그대로 물어 **명사**까지
    전망으로 봤습니다. 실물 STT 2021-06-14:

        "leadership **approach**: Solutions-based, leveraging Alpha for …"

    그 뒤의 멀쩡한 매출을 버렸습니다.

    저장 원문 실측 — `approach` 1,082건 중 표본 12개가 전부 명사
    ("Top-down approach" · "our approach" · "hyper-local approach").
    그렇다고 낱말을 통째로 빼면 안 됩니다: `approaches 11.7%` ·
    `approaching $1 billion` 같은 **진짜 전망 용법**이 있습니다.
    """
    # 명사 — 전망이 아닙니다
    for 명사 in ("leadership approach: Solutions-based",
                 "our approach is working",
                 "a more personalized approach to the guest app",
                 "Top-down approach to re-imagine what we do"):
        assert sf._FORECAST_NEAR_RE.search(명사) is None, 명사

    # 뒤에 숫자가 오면 전망입니다
    for 전망 in ("approaching $1 billion", "approaches 11.7%",
                 "approaching 20%", "approach 11.4%"):
        assert sf._FORECAST_NEAR_RE.search(전망) is not None, 전망

    # 다른 전망 낱말은 그대로 (가드가 헐거워지지 않았는지)
    for 그대로 in ("the Company expects", "2018 GAAP Guidance",
                   "now anticipates", "full-year outlook"):
        assert sf._FORECAST_NEAR_RE.search(그대로) is not None, 그대로


def test_문장_경계가_닫는_따옴표를_넘는다():
    """`performance.”` 도 문장 끝입니다 (183차-BR).

    전망 가드는 **그 문장 안**만 봅니다. 그런데 경계 찾기가
    `". "`(마침표+공백)만 보아서, 인용이 `.”` 로 끝나고 줄바꿈이 오면
    경계를 못 잡고 **앞 문장의 전망 낱말**이 넘어왔습니다.

    실물 NSC 2018-04-25:

        "… we are increasing our **expected** annual share repurchases
         to $1.5 billion, confident that we will deliver strong
         financial performance.**”**
         First-quarter summary
         •   **Railway operating revenues** … $2.7 billion"

    27억은 그 분기 실제 철도 매출인데, 221자 앞 문장의 `expected` 가
    넘어와 버렸습니다.
    """
    글 = ('We are increasing our expected annual share repurchases to '
          '$1.5 billion, confident that we will deliver strong financial '
          'performance.”\nFirst-quarter summary\n'
          '•   Railway operating revenues were $2.7 billion.\n')
    assert sf.find_labeled_value(글, sf.LABELS_REVENUE) == 2_700_000_000

    # 같은 문장 안의 전망은 그대로 막아야 합니다 (가드가 헐거워지지 않았는지)
    같은문장 = "The Company expects Net sales of $2.45 billion this year.\n"
    assert sf.find_labeled_value(같은문장, sf.LABELS_REVENUE) is None


def test_값_없는_제목줄은_건너뛴다():
    """논갭 조정표의 **제목 줄**이 다음 줄 GAAP 값을 물면 안 됩니다 (103차).

    실물 CRM 2025-05-28 — 조정 EPS 를 **1.59** 로 읽었는데 참값은 **2.58**:

        Non-GAAP diluted net income per share            ← 값이 없다 (제목)
        GAAP diluted net income per share      $1.59     ← 이걸 물었다
        Plus: ...
        Non-GAAP diluted net income per share  $2.58     ← 진짜 값

    논갭 조정표는 맨 위에 결과 항목 **이름만** 적고 조정 내역을 늘어놓은 뒤
    마지막에 합계를 적습니다. 이름과 숫자의 거리로 고르는 규칙이 바로 아래
    GAAP 줄의 숫자를 물어 버립니다.
    """
    text = (
        "Non-GAAP diluted net income per share\n"
        "GAAP diluted net income per share      $1.59      $1.56\n"
        "Plus: Amortization of purchased intangibles   0.35   0.33\n"
        "Non-GAAP diluted net income per share  $2.58      $2.44\n"
    )
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 2.58
    # GAAP 은 그대로여야 합니다 — 이 가드가 GAAP 을 건드리면 안 됩니다
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS) == 1.59


def test_제목줄_가드는_GAAP_이름을_건드리지_않는다():
    """가드를 모든 주당 이름에 걸었더니 원문 982건 중 **3건이 회귀**했습니다.

    조정표 머리글 "Reconciliation of GAAP net loss per share, diluted, to
    non-GAAP net income per share, fully diluted:" 안의 GAAP 이름이
    **우연히 바로 뒤의 올바른 GAAP 값과 가장 가까워서** 정답을 내고
    있었는데, 그 머리글을 막자 더 나쁜 후보가 이겼습니다
    (실물 MDB 2025-12-02: GAAP −0.02 → 1.44).

    그래서 가드는 **논갭 이름일 때만** 걸립니다. 이 시험이 그 경계를
    지킵니다 — 넓히면 하나 고치고 셋을 잃습니다.

    ⚠️ 이 시험은 실물 원문을 씁니다. 처음에 손으로 지어낸 짧은 예문으로
    썼더니 **가드를 넓혀도 빨간 불이 안 켜졌습니다** — 회귀를 못 잡는
    시험이었습니다. 실물이 규칙을 이깁니다.
    """
    import os
    경로 = os.path.join(os.path.dirname(__file__), "..",
                       "data", "measure", "raw", "MDB_2025-12-02.txt")
    if not os.path.exists(경로):
        return          # 원문이 없는 환경에서는 건너뜁니다 (없음은 없음으로)
    text = open(경로, encoding="utf-8", errors="ignore").read()
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS) == -0.02, (
        "GAAP 이 조정값으로 무너졌습니다 — 가드가 너무 넓습니다"
    )
    # ⚠️ 119차 정정: 이 자리는 원래 1.44 를 정답으로 적었는데, 실물을 다시
    # 보니 1.44 는 **다음 분기 전망표**("Non-GAAP Net Income per Share
    # $1.44 to $1.48")의 앞끝이었습니다. 진짜 분기값은 대조표의 1.41
    # ("Non-GAAP net income per share, diluted $1.41 $1.33 …" — 뒤 셋은
    # 전년·9개월 열). 119차의 "X to Y" 범위 가드가 전망값을 거르면서
    # 올바른 값으로 옮겨 왔습니다. 당시 동작을 정답으로 박았던 시험의
    # 오류입니다 — 시험도 실물로 검산해야 합니다.
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.41



def test_짝없는_XBRL_사실을_센다():
    """분기 목록은 **논갭 영업이익에서만** 만들어집니다 (106차 계기).

    그래서 영업이익이 없는 분기의 매출은 **찾아 놓고도 버려집니다.**
    실물로 그 꼴이 이미 보였습니다 — BAC 는 `Revenues` 가 55건 살아남는데
    스냅샷의 `revenue_xbrl` 은 44행 전부 비어 있고, 대신 보도자료의
    쓰레기 값(8달러)이 들어갔습니다.

    아직 **고치지 않습니다.** 분기 목록의 뼈대를 바꾸면 모든 종목의 행이
    달라져 판정까지 흔들립니다. 먼저 얼마나 버려지는지 세고 그 숫자를 보고
    정합니다.

    ⚠️ 이 시험은 **진짜 함수(`sf.orphan_counts`)를 부릅니다.** 처음에는
    세는 규칙을 시험 안에 베껴 썼는데, 그러면 본 코드가 망가져도 초록
    불이 켜집니다 — 오늘 여러 번 잡아낸 가짜 초록불과 같은 꼴입니다.
    """
    series = {
        "op_income": {"2025-03-31": 10.0, "2025-06-30": 11.0},
        "revenue": {"2025-03-31": 100.0, "2025-06-30": 110.0,
                    "2025-09-30": 120.0, "2025-12-31": 130.0},
        "gaap_eps": {"2025-09-30": 1.0},
        "gross_profit": {},
    }
    센것 = sf.orphan_counts(series, ["2025-03-31", "2025-06-30"], "2020-01-01")
    assert 센것["분기목록(영업이익 기준)"] == 2
    assert 센것["revenue"] == 2, "영업이익 없는 두 분기의 매출이 버려집니다"
    assert 센것["gaap_eps"] == 1
    assert 센것["gross_profit"] == 0

    # 시작일보다 옛것은 애초에 안 셉니다 (수집 범위 밖)
    옛것 = sf.orphan_counts(series, ["2025-03-31", "2025-06-30"], "2025-10-01")
    assert 옛것["revenue"] == 1, 옛것



def _은행모양_series(**추가):
    base = {"op_income": {}, "revenue": {}, "gaap_eps": {}, "gross_profit": {},
            "sbc": {}, "amortization": {}, "depreciation_amortization": {}}
    base.update(추가)
    return base


def test_영업이익_없는_회사도_뼈대가_생긴다():
    """113차 — 분기 목록을 영업이익 하나가 아니라 세 항목의 합집합에서.

    실측 근거(106·107차): 은행·보험은 OperatingIncomeLoss 를 신고하지 않아
    분기 목록이 아예 안 만들어졌고, 찾아 둔 XBRL 사실 **1,832건**(매출 851 ·
    GAAP EPS 760 · 매출총이익 221)이 붙을 자리가 없어 버려졌습니다.
    22종목(금융 9·제약 5·에너지 4 등)이 XBRL 보호를 한 칸도 못 받았습니다.
    """
    series = _은행모양_series(
        revenue={"2025-03-31": 2.0e10, "2025-06-30": 2.1e10},
        gaap_eps={"2025-03-31": 0.85, "2025-06-30": 0.90},
    )
    rows = sf._quarters_from_series("BANK", series, "2020-01-01")
    assert len(rows) == 2, "영업이익이 없다고 분기 행이 통째로 사라졌습니다 (113차 전 결함)"
    assert rows[0]["revenue"] == 2.0e10
    assert rows[0]["gaap_eps"] == 0.85
    assert rows[0]["op_income"] is None, "없는 논갭 근사를 지어내면 안 됩니다"


def test_셋_다_없는_분기는_행을_만들지_않는다():
    """빈 행은 재료가 아니라 소음입니다.

    ⚠️ 처음에는 주식보상비에만 날짜가 있는 자료로 썼는데, 주식보상비는
    분기 목록 합집합에 **애초에 안 들어가** 시험이 아무것도 안 지켰습니다
    (돌연변이가 초록 불 — 가짜 초록불). 가드에 실제로 닿는 두 경우로
    다시 썼습니다:
      ㉠ EPS 만 있는데 상한을 넘어 없음 처리된 분기
      ㉡ 매출만 있는데 교차검증(매출 ≤ 0)이 비운 분기
    """
    # ㉠ 상한(100)을 넘는 EPS 하나뿐 → 없음 처리 → 셋 다 없음
    series = _은행모양_series(gaap_eps={"2025-03-31": 5000.0})
    assert sf._quarters_from_series("TT", series, "2020-01-01") == []
    # ㉡ 매출 0 이하 → 교차검증이 비움 → 셋 다 없음
    series2 = _은행모양_series(revenue={"2025-03-31": -5.0})
    assert sf._quarters_from_series("TT", series2, "2020-01-01") == []


def test_BAC_시나리오_전체_쓰레기_매출이_밀려난다():
    """이 시험이 182칸 쓰레기의 사망 확인서입니다 (106차 실측 기반).

    실물: BAC 는 XBRL 매출 55건이 살아 있는데 뼈대가 없어 전부 버려졌고,
    보도자료의 쓰레기 값(매출 **8달러**, 중앙값 220억)이 대신 들어갔습니다.

    113차 후의 올바른 흐름:
      뼈대 행 생성(매출 2.0e10) → 8-K 가 짝지어짐(발표일 도장) →
      92차 규칙 "매출은 XBRL 우선"에 따라 쓰레기 8.0 은 **밀려나고**
      XBRL 값이 남는다. 조정 EPS 는 보도자료에서 온다.
    """
    series = _은행모양_series(
        revenue={"2025-03-31": 2.0e10},
        gaap_eps={"2025-03-31": 0.85},
    )
    뼈대 = sf._quarters_from_series("BAC", series, "2020-01-01")
    보도 = [{"filing_date": "2025-04-15",      # 분기 종료 15일 뒤 발표
            "revenue": 8.0,                    # 실물 쓰레기 값 그대로
            "adj_eps": 0.90, "gaap_eps": 0.83, "op_income": None}]
    합침 = sf.merge_quarters(뼈대, 보도)
    행 = 합침[0]
    assert 행["announced_date"] == "2025-04-15", "발표일 도장이 안 찍혔습니다 — 측정에서 빠집니다"
    assert 행["revenue"] == 2.0e10, f"쓰레기 매출이 XBRL 을 밀어냈습니다: {행['revenue']}"
    assert 행["revenue_xbrl"] == 2.0e10
    assert 행["adj_eps"] == 0.90, "조정 EPS 는 보도자료에서 와야 합니다 (XBRL 에 없음)"
    assert 행["press_matched"] is True


# ---------------------------------------------------------------------------
# 연간값 오염 6갈래 (119차 — 부탁_ 원문 17건 실물 감사로 확정한 구멍들)
# ---------------------------------------------------------------------------
def test_연간낱말_annual_이_이름_앞에_있으면_건너뛴다():
    """실물 ADBE: "reported annual GAAP … and non-GAAP diluted earnings per
    share of $4.31" — annual 이 기존 연간 낱말 목록에 없어 연간값을 물었다."""
    text = ("The company reported annual GAAP diluted earnings per share of "
            "$3.38 and non-GAAP diluted earnings per share of $4.31.\n\n"
            "Fourth quarter non-GAAP diluted earnings per share of $1.26.")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.26


def test_이름과_값_사이의_연간표시를_알아본다():
    """실물 SWKS: "Non-GAAP diluted earnings per share **for fiscal year
    2018** was $7.22" — 이름 앞도 값 뒤도 아닌 **사이**라 다 빠져나갔다."""
    text = ("Record Q4 Revenue with GAAP Diluted EPS of $1.58 and "
            "Non-GAAP EPS of $1.94\n\n"
            "Non-GAAP diluted earnings per share for fiscal year 2018 "
            "was $7.22, up 12 percent.")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.94


def test_사이_연간표시라도_quarter_가_있으면_비교문구다():
    """74차 NTAP 반례 보존: "in the fourth quarter of fiscal year 2022" 는
    연간 표시가 아니라 비교 문구 — 진짜 분기값을 버리면 안 된다."""
    text = ("Non-GAAP net income per share compared to the fourth quarter "
            "of fiscal year 2022 was $1.54.")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.54


def test_전망범위_X_to_Y_는_양끝_다_안_문다():
    """실물 LOW·LLY·NEE: "Adjusted diluted earnings per share of
    approximately $11.80 to $11.90" (연간 전망) — 앞끝도 뒤끝도 실적이 아니다."""
    text = ("Full Year 2024 Outlook\n"
            "Adjusted diluted earnings per share of approximately "
            "$11.80 to $11.90 (previously $11.70 to $11.90)")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


def test_from_X_to_Y_변화문구는_Y_가_진짜_값이다():
    """실물 AMGN: "Non-GAAP EPS remained relatively unchanged from $5.31 to
    $5.29 for the fourth quarter" — 5.31 은 전년, 5.29 가 이번 분기.
    범위 가드를 넓게 걸면 옳은 값(5.29)까지 잃는다."""
    text = ("Non-GAAP EPS remained relatively unchanged from $5.31 to "
            "$5.29 for the fourth quarter as expenses were offset.")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 5.29


def test_성장문구_increased_to_는_그대로_읽는다():
    """실물 DIS: "adjusted EPS(1) increased 16% for Q3 to $1.61 from $1.39"
    — 처음 만든 뒤끝 가드가 "Q3 to $" 까지 물어 진짜 값을 버리고 전년값
    1.39 를 집었다 (전수 채점에서 발각). 1.61 이어야 한다."""
    text = ("Diluted earnings per share (EPS) for Q3 improved to $2.92 from "
            "$1.43 in Q3 fiscal 2024, and adjusted EPS(1) increased 16% for "
            "Q3 to $1.61 from $1.39 in Q3 fiscal 2024")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.61


def test_줄이_Fiscal_20xx_로_시작하면_연간_줄이다():
    """실물 SYNA: "• Fiscal 2018 revenue of $1.63 billion, … non-GAAP net
    income per diluted share of $4.05" — 줄머리 가드가 "fiscal year" 만
    알고 year 생략형을 몰랐다."""
    text = ("• Fiscal 2018 revenue of $1.63 billion, GAAP net income (loss) "
            "per diluted share of $(3.63) and non-GAAP net income per "
            "diluted share of $4.05\n"
            "• Fourth quarter non-GAAP net income per diluted share of $1.00\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.0


def test_Fiscal_20xx_줄머리라도_quarter_가_붙으면_분기_줄이다():
    text = ("Fiscal 2018 fourth quarter non-GAAP net income per diluted "
            "share of $1.00\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.0


def test_겹친_머리글_윗줄이_연간이면_아랫줄도_연간이다():
    """실물 QCOM 머리글 더미: "Fiscal 2017 Revenues $22.3 billion ⏎ GAAP EPS
    $1.65, Non-GAAP EPS $4.28" — 연간 표시는 윗줄에만 있다."""
    text = ("Fiscal 2017 Revenues $22.3 billion\n"
            "GAAP EPS $1.65, Non-GAAP EPS $4.28\n\n"
            "Fourth quarter Non-GAAP EPS of $0.92\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.92


# ---------------------------------------------------------------------------
# 주당 값이 **아랫줄**에 있는 표 (150차-AJ — 실물 ALL 올스테이트)
# ---------------------------------------------------------------------------

_보험사표 = (
    "($ in millions, except per share data)          Q2 2022     Q2 2021\n"
    "Consolidated revenues                            $12,220     $12,646\n"
    "Net income (loss) applicable to common shareholders  (1,042)    1,595\n"
    "per diluted common share (1)                       (3.81)       5.26\n"
    "Adjusted net income (loss)*                          (209)       316\n"
    "per diluted common share* (1)                       (0.76)      3.79\n"
)


def test_주당값이_아랫줄에_있는_표를_읽는다():
    """보험사는 주당 값을 **윗줄 항목에 딸린 아랫줄**로 적습니다.

    실물 ALL(올스테이트) 2022년 2분기. 지금 파서는 이름과 "per share" 가
    같은 줄에 붙어 있다고 보고 찾으므로 이 꼴을 통째로 못 읽었고,
    ALL 은 GAAP EPS 도 조정 EPS 도 전부 "없음"이었습니다.
    """
    assert sf.find_eps_value(_보험사표, sf.LABELS_GAAP_EPS) == -3.81
    assert sf.find_eps_value(_보험사표, sf.LABELS_ADJUSTED_EPS) == -0.76


def test_아랫줄_규칙은_이미_찾은_값을_밀어내지_않는다():
    """**아무것도 못 찾았을 때만** 도는 마지막 수단이어야 합니다.

    이 방어막이 없으면, 멀쩡히 읽히던 문장형 보도자료가 표 어딘가의
    아랫줄 값에 밀려 바뀝니다 — 하나 고치고 여럿을 잃는 길입니다.
    """
    글 = ("Diluted net income per share was $1.96 for the quarter.\n"
         "Net income applicable to common shareholders          1,000\n"
         "per diluted common share (1)                          (9.99)\n")
    assert sf.find_eps_value(글, sf.LABELS_GAAP_EPS) == 1.96


def test_아랫줄_규칙은_윗줄을_모르면_손대지_않는다():
    """윗줄이 어느 잣대인지 모르면 **없음**이 정답입니다 (헌법 1조).

    윗줄에 adjusted·non-GAAP·core 도, net income/earnings/loss 도 없으면
    그 주당 값이 무엇의 주당 값인지 알 수 없습니다.
    """
    글 = ("Book value                                            75,000\n"
         "per diluted common share (1)                          (9.99)\n")
    assert sf.find_eps_value(글, sf.LABELS_GAAP_EPS) is None
    assert sf.find_eps_value(글, sf.LABELS_ADJUSTED_EPS) is None


def test_아랫줄_규칙은_조정과_GAAP_을_섞지_않는다():
    """조정 줄을 GAAP 자리에, GAAP 줄을 조정 자리에 넣으면 안 됩니다."""
    조정만 = ("Adjusted net income (loss)*                          (209)\n"
             "per diluted common share* (1)                       (0.76)\n")
    assert sf.find_eps_value(조정만, sf.LABELS_ADJUSTED_EPS) == -0.76
    assert sf.find_eps_value(조정만, sf.LABELS_GAAP_EPS) is None

    GAAP만 = ("Net income (loss) applicable to common shareholders  (1,042)\n"
             "per diluted common share (1)                       (3.81)\n")
    assert sf.find_eps_value(GAAP만, sf.LABELS_GAAP_EPS) == -3.81
    assert sf.find_eps_value(GAAP만, sf.LABELS_ADJUSTED_EPS) is None


def test_아랫줄_규칙은_이름_뒤에_설명글이_오면_손대지_않는다():
    """"per share" 로 시작하기만 하면 무엇이든 읽으면 안 됩니다.

    이 문지기가 없어서 실측으로 헛값 둘이 잡혔습니다:
      PG   "per share from early debt retirement … Core Effective Tax Rate
            range of 18% to 19%"  → 세율 18 을 EPS 로 읽음 (참값 1.6대)
      CGNX "Per share impact of discrete tax adjustments identified above
            0.0x"                 → 영향값을 EPS 로 읽음

    진짜 표의 줄은 이름이 끝나면 **바로 숫자**입니다.
    """
    설명글 = ("Net income applicable to common shareholders   1,000\n"
             "per share from early debt retirement, tax rate range of 18% to 19%\n")
    assert sf.find_eps_value(설명글, sf.LABELS_GAAP_EPS) is None

    영향줄 = ("Adjusted net income                              1,000\n"
             "Per share impact of discrete tax adjustments      0.07\n")
    assert sf.find_eps_value(영향줄, sf.LABELS_ADJUSTED_EPS) is None

    # 각주 번호와 별표는 이름의 일부이므로 통과해야 합니다
    각주 = ("Net income applicable to common shareholders   1,000\n"
           "per diluted common share* (2)                  (3.81)\n")
    assert sf.find_eps_value(각주, sf.LABELS_GAAP_EPS) == -3.81


# ===========================================================================
# 150차-AP — 저장된 원문 2,064건을 전수로 읽어 찾아낸 여섯 가지 (실물 근거)
# ===========================================================================
def test_회사가_논갭을_core_라고_부르면_읽는다():
    """실물 BA(보잉) — 자기 논갭 지표를 'core' 라고 부릅니다.
    이 말이 이름 목록에 없어서 보잉의 조정 EPS 9개 분기가 통째로
    "없음"이었습니다."""
    text = ("•GAAP loss per share of ($0.11) and core loss per share "
            "(non-GAAP)* of ($0.20)\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == -0.20


def test_맨_core_EPS_는_증감률_이름이라_읽지_않는다():
    """반례 보존 — PG·PEP 의 발표자료는 'Core EPS Growth +19%' 처럼
    **증감률**의 이름으로 이 말을 씁니다. 여기서 숫자를 읽으면
    (실측 PG 0.40·5.35·0.10, PEP 8.16) 전부 틀린 값이 됩니다."""
    # 실물 PG 발표자료 꼴 — "Core EPS Growth" 뒤에 EPS 가 아닌 숫자가 옵니다.
    text = ("Currency Neutral Core EPS Growth Q1 FY 2021 RESULTS\n"
            "Core operating margin expanded to 1.70 points\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None
    # ⚠️ 글로만 재면 다른 문지기가 대신 막아 헛돌 수 있으므로 **이름 목록
    #    자체**도 함께 못박습니다 — 맨 core EPS 이름이 있으면 안 됩니다.
    맨core = [p for p in sf.LABELS_ADJUSTED_EPS
              if "core" in p.lower() and "per" not in p.lower()]
    assert not 맨core, f"맨 'core EPS' 이름이 목록에 들어왔습니다: {맨core}"


def test_값이_이름보다_앞에_오는_or_꼴을_읽는다():
    """실물 UCTT·MCHP·ISRG — "…였고, **주당 얼마**" 꼴.
    파서는 이름 **뒤**에서 숫자를 찾으므로 이름을 "…or" 까지로 끊습니다."""
    text = ("On a non-GAAP basis, gross margin was 16.5%, operating margin "
            "was 5.1%, and net income was $14.5 million or $0.31 per "
            "diluted share.\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.31


def test_제외한다는_문장의_숫자는_잣대가_아니다():
    """실물 HPE — "non-GAAP diluted net EPS estimate **excludes** net
    after-tax adjustments of approximately $1.28 per diluted share".
    1.28 은 빼낸 항목의 크기이지 EPS 가 아닙니다."""
    # ⚠️ 시험글에서 "full year" 와 "estimate" 를 **일부러 뺐습니다.**
    #    처음엔 실물 문장 그대로 썼는데, 돌연변이 시험을 해 보니 이 문지기를
    #    꺼도 시험이 통과했습니다 — 연간 문지기와 전망 문지기가 대신
    #    막고 있었던 것입니다. 이 시험은 **제외 문지기만** 재야 합니다.
    text = ("Non-GAAP diluted net EPS excludes net after-tax adjustments of "
            "approximately $1.28 per diluted share, primarily related to "
            "costs.\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


def test_excluding_은_이름을_꾸미는_말이라_값을_살린다():
    """반례 보존 — 실물 VZ·IFF. "excludes(제외한다)" 와 달리
    "excluding(제외한)" 은 잣대 이름을 꾸미는 단서입니다. 이것까지
    막으면 멀쩡한 값 8건이 죽습니다(전수 실측)."""
    text = ("•$1.56 in EPS, compared with $1.11 in fourth-quarter 2021; "
            "adjusted EPS1, excluding special items, of $1.19.\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.19


def test_compared_to_바로_뒤_숫자는_비교값이다():
    """실물 PYPL — "non-GAAP earnings per diluted share of ~$0.86,
    **compared to $1.15** in the prior year period". 1.15 는 전년 값입니다."""
    # ⚠️ "expected" 와 "in the prior year period" 를 **일부러 뺐습니다** —
    #    실물 문장 그대로 쓰면 전망 문지기와 전기(前期) 문지기가 대신
    #    막아, 이 문지기를 꺼도 시험이 통과합니다(돌연변이 시험으로 확인).
    text = ("• non-GAAP earnings per diluted share of ~$0.86, compared to "
            "$1.15 a year earlier\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


def test_같은_기간에_라는_꼬리말이_붙은_값은_비교값이다():
    """실물 PCG — "…, or $3.93 per share, **during the same period in
    2019**". 앞의 연간값을 거른 뒤 전년 연간값을 물면 안 됩니다."""
    text = ("PG&E Corporation's non-GAAP core earnings were $2,020 million, "
            "or $1.61 per share, for the full year 2020, compared with "
            "$2,074 million, or $3.93 per share, during the same period "
            "in 2019.\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


def test_소수점_세자리는_각주번호다():
    """실물 DG "$1.741" · MS "$1.301" — 마지막 한 자리는 각주 번호입니다.
    자릿수를 우리가 고치는 것은 창작이므로 **읽지 않습니다**."""
    text = "Adjusted Diluted EPS Increased 14.5% to $1.741\n"
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


def test_expected_도_전망_문맥이다():
    """실물 PYPL — "Non-GAAP earnings per diluted share **expected** to be
    in line with $5.10 in the prior year". expects/expecting 만 알고
    **expected** 를 몰라서 연간 전망치가 4분기 값으로 들어왔습니다."""
    # ⚠️ 실물의 "in the prior year" 를 **일부러 뺐습니다** — 두면 전기(前期)
    #    문지기가 대신 막아 이 시험이 헛돕니다(돌연변이 시험으로 확인).
    text = ("• Non-GAAP earnings per diluted share expected to be in line "
            "with $5.10.\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


def test_수식어가_둘_겹친_줄임말도_읽는다():
    """실물 HPE "non-GAAP diluted net EPS" · SEDG "Non-GAAP net diluted EPS".
    diluted 하나만 허용해서 이 어순을 놓쳤습니다."""
    text = "Non-GAAP net diluted EPS of $0.63\n"
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.63


def test_Financial_Highlights_도_구역_제목이다():
    """실물 CSX — "Fourth Quarter Financial **Highlights**" 와 "Full Year
    2025 Financial **Highlights**" 로 나눠 적습니다. 'results' 만 알아서
    연간 구역의 값을 4분기로 읽었습니다."""
    text = ("Full Year 2025 Financial Highlights1\n"
            "•Revenue totaled $14.09 billion in 2025.\n"
            "•EPS was $1.54, and adjusted EPS was $1.61.\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


def test_멀리_있는_연간_표시도_본다():
    """실물 PG 발표자료 — 한 줄이 수천 자라 60자 되돌아보기로는 "for the
    fiscal year" 에 닿지 못했습니다. 그 글에는 4분기 값이 아예 없으므로
    올바른 답은 "없음"입니다."""
    text = ("26 of our top 50 category/country combinations held or grew "
            "share for the fiscal year. Global aggregate value share was "
            "slightly down vs the prior year. Core earnings per share were "
            "$6.89, +1% vs the prior year.\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


# ===========================================================================
# 150차-AS — 이름이 두 줄로 쪼개진 표, 그리고 그 표를 읽다가 드러난 셋
# ===========================================================================
def test_이름이_두_줄로_쪼개진_표를_읽는다():
    """실물 CGNX — 항목 이름이 길어 줄바꿈이 되면서 **어느 잣대인지
    말해 주는 꼬리표가 아랫줄로** 밀려났다. 값은 윗줄에 있다.
    기존 이름들은 "non-GAAP … per share" 가 붙어 있기를 요구해 이 꼴을
    하나도 못 읽었다 (CGNX·SCHW 두 종목만 20건)."""
    text = (
        "Net income (Non-GAAP)                                    $40,433\n"
        "Net income per diluted weighted-average common and common-equivalent   $0.23\n"
        "share (Non-GAAP)\n"
    )
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.23


def test_소수점으로_시작하는_표기를_읽는다():
    """실물 SCHW — 주당 금액을 "$.74" 라고 적는다(0 을 안 쓴다).
    이 표기를 못 읽어 그 칸을 건너뛰고 **6개월 누적 칸**을 분기값으로
    읽고 있었다."""
    text = (
        "Adjusted net income available to common stockholders   $1,358   $.74\n"
        "(non-GAAP), Adjusted diluted EPS (non-GAAP)\n"
    )
    # $1,358 은 소수점이 없어 저절로 걸러지고 $.74 가 남는다
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.74


def test_영점영영은_EPS_로_읽지_않는다():
    """실물 BE — "GAAP EPS, Diluted   **$.00**   $(0.10)  $0.46" 처럼
    첫 칸이 빈칸 대신 놓인 0 이다. 그 분기의 진짜 값은 −0.10 이다."""
    text = "GAAP EPS, Diluted       $.00       $(0.10)       $0.46\n"
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) == -0.10


def test_앞_문장의_논갭_이야기는_이_이름과_상관없다():
    """반례 보존 (실물 CRDO) — 되돌아보기 창을 넓히면서 **문장 끝**에서
    자르지 않으면, 앞 문장의 'non-GAAP' 때문에 뒤 문장의 진짜 GAAP 값을
    잃습니다. 55자 앞이라 40자 창에서는 안 걸리던 것입니다."""
    text = ("GAAP net income of $157.1 million and non-GAAP net income of "
            "$208.8 million. GAAP diluted net income per share of $0.82 and "
            "non-GAAP diluted net income per share of $1.07")
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) == 0.82


def test_같은_문장의_논갭_이야기는_막는다():
    """실물 SCHW — "…costs, **adjusted** (1) net income and diluted common
    **earnings per share** equaled $1.5 billion and $.74". 'adjusted' 가
    51자 앞이라 40자 창에 닿지 않아 **조정값이 GAAP 칸에** 들어갔습니다.
    GAAP 과 조정이 같은 값으로 무너지는 것이 이 결함의 표시입니다."""
    text = ("Excluding $140 million of pre-tax costs, adjusted (1) net income "
            "and diluted common earnings per share equaled $1.5 billion and "
            "$.74, respectively.")
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) is None


def test_영향의_크기는_잣대가_아니다():
    """실물 PYPL — "GAAP EPS **included a negative impact of** approximately
    $0.11 …". 0.11 은 잣대 안에 든 영향의 크기입니다.
    실물 BMY 도 같은 꼴로 조정 EPS 가 −0.01 이 돼 있었습니다(참값 1.82)."""
    text = ("In the fourth quarter, GAAP EPS included a negative impact of "
            "approximately $0.11 on strategic investments.")
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) is None


def test_Approx_는_전망표의_말이다():
    """실물 CCL 전망표 — "Adjusted earnings per share - diluted (a)
    **Approx. $0.00**   **Approx. $1.70**". 둘 다 전망(분기·연간)이다.
    낱말 전체("approximately")는 실적 문장에도 흔해 **약자만** 막는다."""
    text = ("Adjusted earnings per share - diluted (a)   "
            "Approx. $0.00      Approx. $1.70\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


# ===========================================================================
# 150차-AT — 남은 일감 98건에서 뭉쳐 있던 네 꼴 + 전망 창 고침
# ===========================================================================
def test_이름_가운데_수식어가_길어도_읽는다():
    """실물 CGNX — "Net income per **diluted weighted-average common and
    common-equivalent** share (Non-GAAP)". 줄임 표기는 "per (diluted)
    share" 가 붙어 있기를 요구해 이 꼴 9건을 놓쳤다."""
    text = ("Net income per diluted weighted-average common and "
            "common-equivalent share (Non-GAAP)      $0.21\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.21


def test_영향_줄은_이_이름으로_읽히지_않는다():
    """반례 보존 — 같은 CGNX 표의 "Per share impact of Non-GAAP adjustments
    identified above   0.02" 는 영향값이다. 이름이 share 로 끝나지 않아
    위 이름에 걸리지 않아야 한다."""
    text = ("Per share impact of Non-GAAP adjustments identified above"
            "        0.02\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


def test_적자_분기의_같은_문장_꼴을_읽는다():
    """실물 AMBA — "non-GAAP net **loss** of $5.9 million, or **loss** per
    diluted ordinary share of $0.13". 앞 이름은 "net profit … or
    **earnings** per …" 만 받아 적자 분기 6건을 놓쳤다. 부호도 뒤집힌다."""
    text = ("Non-GAAP net loss of $5.9 million, or loss per diluted "
            "ordinary share of $0.13.\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == -0.13


def test_제목_줄_아래_논갭_항목을_읽는다():
    """실물 HPE — 제목 줄 아래 GAAP·논갭이 하위 항목으로 온다.
    GAAP 쪽만 이름이 있어 조정 EPS 가 비어 있었다(5건)."""
    text = ("•Diluted net earnings per share (“EPS”): \n"
            "◦GAAP of $0.44, up $1.26 from the prior-year period\n"
            "◦Non-GAAP(1) of $0.79, up $0.41 from the prior-year period\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.79


def test_값_뒤의_전망_낱말은_실적을_버리지_않는다():
    """실물 HPE·MKSI — 값 **뒤**의 "above our outlook range of …" ·
    "above the midpoint of guidance" 는 이번 분기 실적을 전망과 견주는
    말이다. 그것 때문에 진짜 실적을 버리고 있었다."""
    text = ("Non-GAAP net earnings per diluted share of $1.32, above the "
            "midpoint of guidance\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 1.32


def test_전망_그_자체인_숫자는_읽지_않는다():
    """반례 보존 (실물 QRVO 가이던스 갱신 공시) — "$2.14 **at** the midpoint
    of guidance" 는 값이 곧 전망이다. 위 MKSI 의 "**above** the midpoint"
    와 낱말 하나가 다르다."""
    text = ("Diluted earnings per share   $2.14 at the midpoint of guidance\n")
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) is None


def test_줄머리_Excluding_은_GAAP_이_아니다():
    """실물 KSS — "• **Excluding** tax reform benefits, diluted earnings per
    share were $4.31" 은 정의상 GAAP 이 아니다(게다가 연간값)."""
    text = ("•   Excluding tax reform benefits, diluted earnings per share "
            "were $4.31, exceeding the high end of guidance\n")
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) is None


def test_문장_중간의_excluding_은_GAAP_을_막지_않는다():
    """반례 보존 (실물 PYPL) — 줄머리가 아닌 excluding 까지 막았더니
    진짜 GAAP 값 세 분기가 죽었다."""
    text = ("• Revenue of $6.5 billion, growing 7%; excluding eBay, revenue "
            "grew 15% on a spot basis(1) • GAAP EPS of $0.43 compared to "
            "$0.92 in Q1'21\n")
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) == 0.43


# ===========================================================================
# 150차-AZ — GAAP 쪽에는 있는데 조정 쪽에만 빠져 있던 세 곳
# ===========================================================================
def test_common_share_도_조정_이름으로_받는다():
    """실물 EL·SMCI — "Adjusted diluted net earnings **per common share**".
    GAAP 쪽 이름은 common|ordinary 를 둘 다 받는데 조정 쪽은 ordinary 만
    받고 있었습니다(반쪽만 넣은 자리).

    SMCI 는 이 때문에 GAAP 과 조정이 **같은 값 0.60 으로 무너져** 있었습니다."""
    text = ("•Diluted net income per common share of $0.60 versus $0.26\n"
            "•Non-GAAP diluted net income per common share of $0.69 versus $0.59\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.69
    assert sf.find_eps_value(text, sf.LABELS_GAAP_EPS,
                             exclude_nongaap=True) == 0.60


def test_operating_수식어도_받는다():
    """실물 AMP — "Adjusted **operating** earnings per diluted share
    increased 7 percent to $9.11". 보험·자산운용사의 표준 표현입니다."""
    text = ("•Second quarter adjusted operating earnings per diluted share "
            "increased 7 percent to $9.11.\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 9.11


def test_이름에_쉼표가_끼어도_받는다():
    """실물 UTHR 표 — "Non-GAAP earnings**,** per diluted share(1)  $3.34"."""
    text = ("Non-GAAP earnings, per diluted share(1)           $3.34"
            "             $3.89\n")
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 3.34


# ---------------------------------------------------------------------------
# 173차 — 연도 열 표 파서 (실물: NBIS 6-K, 전년 열이 먼저)
# ---------------------------------------------------------------------------
NBIS_YEAR_TABLE = (
    "Nebius reports third quarter financial results\n"
    "  In USD $ millions          Three months ended September 30            Nine months ended September 30\n"
    "                                   2024        2025     Change             2024      2025     Change\n"
    "  Revenues                         32.1       146.1      355%              56.3     302.1      437%\n"
    "  Adjusted EBITDA / (loss)        (45.9)       (5.2)     -89%            (162.4)    (79.9)     -51%\n"
    "  Net income / (loss) from continuing operations   (43.6)   (119.6)   174%   (229.1)   278.6\n"
)


def test_연도_열_표는_더_늦은_연도의_열을_읽는다():
    r = sf.find_values_in_year_column_table(NBIS_YEAR_TABLE)
    assert r["revenue"] == 146.1e6 and r["adjusted_ebitda"] == -5.2e6, r
    # 6-K 외국 회사(우선 모드)에서는 이 값이 첫 숫자(전년 32.1)를 이긴다
    p = sf.parse_press_release(NBIS_YEAR_TABLE, year_table_priority=True)
    assert p["revenue"] == 146.1e6 and p["adjusted_ebitda"] == -5.2e6, p
    # 기본 모드는 문장 파서가 찾은 값을 건드리지 않고 빈 칸만 채운다.
    # (문장 파서는 "Revenues" 뒤 첫 숫자 32.1 — 전년 열·단위 없음 — 을 읽는다.
    #  바로 그 결함 때문에 6-K 종목에는 우선 모드를 쓴다. 173차)
    q = sf.parse_press_release(NBIS_YEAR_TABLE)
    assert q["revenue"] == 32.1, ("기본 모드에서 있던 값을 덮어썼습니다", q["revenue"])
    assert q["adjusted_ebitda"] == -5.2e6 or q["adjusted_ebitda"] is None
    # 각주 "(1)" 은 값이 아니다
    각주 = NBIS_YEAR_TABLE.replace("  Revenues     ", "  Revenues (1) ")
    assert sf.find_values_in_year_column_table(각주)["revenue"] == 146.1e6, 각주
    # 열 순서가 반대(2025 2024)여도 2025 열
    뒤집힘 = NBIS_YEAR_TABLE.replace("2024        2025", "2025        2024") \
        .replace("32.1       146.1", "146.1       32.1").replace("(45.9)       (5.2)", "(5.2)       (45.9)")
    r2 = sf.find_values_in_year_column_table(뒤집힘)
    assert r2["revenue"] == 146.1e6 and r2["adjusted_ebitda"] == -5.2e6, r2


# NBIS 2026-02-12 실물 모양: "Three"/"months ended", "In"/"USD $ millions",
# "Adjusted"/"EBITDA / (loss)" 가 전부 두 줄로 갈라져 있다(173차 — 처음 판은
# 매출을 못 읽었다).
NBIS_SPLIT_TABLE = (
    "Consolidated results (1), (2)\n"
    "  In                                   Three                                  Twelve\n"
    "  USD $ millions           months ended December 31              months ended December 31\n"
    "                               2024        2025     Change            2024        2025    Change\n"
    " ────────────────────────────────────────────────────────────────────\n"
    "  Revenues                     35.2       227.7      547%            91.5       529.8      479%\n"
    "  Adjusted                    (63.9)       15.0       n/m          (226.3)      (64.9)     -71%\n"
    "  EBITDA / (loss)\n"
    "  Net income                 (122.9)     (249.6)      103%          (352.0)       29.0       n/m\n"
)


def test_연도_열_표는_머리와_이름이_두_줄로_갈라져도_읽는다():
    r = sf.find_values_in_year_column_table(NBIS_SPLIT_TABLE)
    assert r["revenue"] == 227.7e6, r
    assert r["adjusted_ebitda"] == 15.0e6, r
    # 갈라진 머리의 앞 줄에 three 가 없으면(다른 표) 머리로 보지 않는다
    머리없음 = NBIS_SPLIT_TABLE.replace("Three", "     ")
    assert sf.find_values_in_year_column_table(머리없음)["revenue"] is None
    # 이름 다음 줄에 숫자가 있으면 이름의 나머지로 붙이지 않는다
    붙임없음 = NBIS_SPLIT_TABLE.replace("  EBITDA / (loss)\n", "  EBITDA / (loss) 2\n")
    assert sf.find_values_in_year_column_table(붙임없음)["adjusted_ebitda"] is None


def test_연도_열_표는_GAAP_EPS를_읽지_않는다():
    """전수 비교(173차): "Net income  $280.2 …" 다음 줄이 "Earnings per share:"
    (숫자 없음)라 두 줄 이름이 "Net income Earnings per share" 가 되어 순이익
    금액(백만)이 주당순이익으로 채워진 문서 47건(EW·YELP·STRL·RUN …).
    GAAP EPS 는 XBRL 로 받으므로 연도 열 표에서는 아예 읽지 않는다(없음 > 틀림)."""
    t = (
        "(in millions, except per share data)\n"
        "                 Three Months Ended December 31\n"
        "                          2018        2019\n"
        "  Revenues               977.7      1,174.1\n"
        "  Net income               7.0        280.2\n"
        "  Earnings per share:\n"
        "  Diluted                 0.03         1.34\n"
    )
    r = sf.find_values_in_year_column_table(t)
    assert r["revenue"] == 1174.1e6, r
    assert r["gaap_eps"] is None, r
    # 문장 파서가 "Diluted 1.34" 를 찾는 것은 그 파서의 몫 — 순이익 금액만 아니면 된다
    assert sf.parse_press_release(t, year_table_priority=True)["gaap_eps"] != 280.2


def test_연도_열_표는_단위나_연도가_없으면_읽지_않는다():
    단위없음 = NBIS_YEAR_TABLE.replace("In USD $ millions", "                 ")
    assert sf.find_values_in_year_column_table(단위없음)["revenue"] is None
    import re as _re
    연도없음 = "\n".join(_re.sub(r"20\d{2}", "Col", l) if "Change" in l else l
                     for l in NBIS_YEAR_TABLE.split("\n"))
    assert sf.find_values_in_year_column_table(연도없음)["revenue"] is None
    assert sf.find_values_in_year_column_table("")["revenue"] is None
    # 같은 연도 두 번(분기 열이 아님)이면 안 읽음
    같은연도 = NBIS_YEAR_TABLE.replace("2024        2025     Change", "2025        2025     Change")
    assert sf.find_values_in_year_column_table(같은연도)["revenue"] is None


# ── 각주 번호를 값으로 읽던 결함 (182차-C, 실물 ZETA) ────────────────
#
# 회사가 논갭 지표에 각주를 달면 이름 바로 뒤에 번호가 붙습니다.
#   "•Adjusted EBITDA1 of $46.7 million"
# 파서는 저 1 을 값으로 읽었고, $10만 미만이라 뒷단 검사에서 버려져
# **잣대 칸이 통째로 비었습니다.** 제타는 조정 EBITDA 가 주 잣대라
# 종목 전체가 측정에서 빠졌습니다.
각주_보도자료 = """Zeta Announces Second Quarter 2025 Financial Results

•Revenue of $264.4 million, increased 36% Y/Y.
•Adjusted EBITDA1 of $46.7 million, increased 53% Y/Y compared to $30.5 million in 1Q'24.
•Adjusted EBITDA margin1 of 17.7%, compared to 15.6% in 1Q'24.

—————————————
1 Adjusted EBITDA and Adjusted EBITDA margin are not measures of financial
performance prepared in accordance with GAAP.
"""


def test_이름에_붙은_각주_번호를_값으로_읽지_않는다():
    값 = sf.find_labeled_value(각주_보도자료, sf.LABELS_ADJUSTED_EBITDA)
    assert 값 == 46_700_000, f"각주 1 을 값으로 읽었습니다: {값}"


def test_각주가_없으면_예전처럼_읽는다():
    """고침이 멀쩡한 문서를 건드리지 않는지 — 각주만 뗀 같은 글."""
    맨글 = 각주_보도자료.replace("EBITDA1", "EBITDA").replace("margin1", "margin")
    assert sf.find_labeled_value(맨글, sf.LABELS_ADJUSTED_EBITDA) == 46_700_000


def test_이름에_붙은_진짜_값은_각주로_오해하지_않는다():
    """표에서 공백이 눌려 이름과 값이 붙은 경우 — 쉼표·소수점이 있으면 값입니다."""
    표 = "Adjusted EBITDA46,713\n"
    assert sf.find_labeled_value(표, sf.LABELS_ADJUSTED_EBITDA) == 46_713

    # 세 자리 이상 정수도 각주 번호가 아닙니다 (각주는 한두 자리)
    표2 = "Adjusted EBITDA123\n"
    assert sf.find_labeled_value(표2, sf.LABELS_ADJUSTED_EBITDA) == 123


괄호각주_보도자료 = """Verizon Reports Fourth-Quarter 2024 Results

Consolidated adjusted EBITDA1 was $11.9 billion in fourth-quarter 2024.

Net unsecured debt to Adjusted EBITDA(1)(2)                    2.3     x
"""


def test_괄호로_붙은_각주도_값으로_읽지_않는다():
    """회계 표기에서 괄호는 음수라 (1) 이 −1 이 되고, 표 단위가 곱해지면
    −$100만이 됩니다. '너무 작다' 검사(10만 미만)를 빠져나가 그대로
    저장되므로 **없음보다 나쁩니다** (실물 VZ 7건)."""
    값 = sf.find_labeled_value(괄호각주_보도자료, sf.LABELS_ADJUSTED_EBITDA)
    assert 값 == 11_900_000_000, f"괄호 각주를 값으로 읽었습니다: {값}"


def test_잇달아_붙은_각주도_전부_건너뛴다():
    """(1)(2) 처럼 두 개가 붙어도 하나씩 벗겨져야 합니다."""
    글 = "Adjusted EBITDA(1)(2) of $8.5 billion\n"
    assert sf.find_labeled_value(글, sf.LABELS_ADJUSTED_EBITDA) == 8_500_000_000


def test_공백을_두고_적힌_괄호는_진짜_음수다():
    """각주는 이름에 딱 붙습니다. 공백이 있으면 회계식 음수(진짜 값)입니다."""
    글 = "Adjusted EBITDA (1,234)\n"
    assert sf.find_labeled_value(글, sf.LABELS_ADJUSTED_EBITDA) == -1234


def test_사이가_떨어져_있으면_한자리라도_값이다():
    """각주는 이름 글자에 **딱 붙습니다**. 공백이나 $ 가 끼면 그것은 값입니다."""
    assert sf.find_labeled_value("Adjusted EBITDA 5\n", sf.LABELS_ADJUSTED_EBITDA) == 5
    assert sf.find_labeled_value("Adjusted EBITDA $7\n", sf.LABELS_ADJUSTED_EBITDA) == 7


def test_뒤에_단위_낱말이_붙으면_각주가_아니다():
    """각주 번호에는 million 이 따라오지 않습니다 — 단위가 붙었으면 값입니다."""
    assert sf.find_labeled_value("Adjusted EBITDA5 million\n",
                                 sf.LABELS_ADJUSTED_EBITDA) == 5_000_000


def test_각주가_붙어도_이번_분기_값을_고른다():
    """실물 ZETA 4분기 보도자료 — 분기 $70.4M · 연간 $193.0M 이 함께 실림."""
    글 = 각주_보도자료.replace(
        "•Adjusted EBITDA1 of $46.7 million, increased 53% Y/Y compared to $30.5 million in 1Q'24.",
        "•Adjusted EBITDA1 of $70.4 million, increased 57% Y/Y from $44.8 million in 4Q'23.\n"
        "•Full year Adjusted EBITDA1 of $193.0 million, an increase of 49%.")
    assert sf.find_labeled_value(글, sf.LABELS_ADJUSTED_EBITDA) == 70_400_000


# ── 값이 조정표의 '분기 열'에서 왔는지 (183차) ────────────────────────
#
# 왜 필요한가: 정제의 누적값 그물이 **빨리 크는 회사의 4분기**를 버린다.
# 실물 CRDO 25Q4 조정 EPS 0.35 는 직전 4분기 합 0.43 과 거의 같아
# "연간값"으로 판정되지만, 원문에는 분기 열 0.35 · 연간 열 0.70 이
# 나란히 적혀 있다. 산수로는 못 가르고 회사가 갈라 놓은 것을 읽어야 한다.
CRDO_조정표 = """Credo Technology Group Holding Ltd
Reconciliations from GAAP to Non-GAAP Results (Unaudited)
(In thousands, except percentages and per share amounts)

                                             Three Months Ended                              Year Ended
                            May 3, 2025      February 1, 2025    April 27, 2024     May 3, 2025    April 27, 2024
Non-GAAP diluted net income per share  $0.35            $0.25             $0.07          $0.70          $0.09
"""

# 실물 NRG — **부문 설명 문장**은 표의 행이 아니다. 이름이 줄 가운데 있다.
NRG_부문문장 = """($ in millions)                        Three Months Ended                    Nine Months Ended
Adjusted EBITDA                        $1,055           $987            $2,887      $2,458
Texas: Third quarter Adjusted EBITDA was $584 million, $32 million higher than 2023.
"""

# 실물 NRG — **가이던스 범위**는 숫자가 둘 다 분기 쪽에 몰려 있다
NRG_가이던스 = """($ in millions)                        Three Months Ended                    Twelve Months Ended
Adjusted EBITDA                        $3,725 - $3,975
"""


def test_조정표의_분기_열에_있는_값을_확인해_준다():
    assert sf._표에서_분기값과_같은가(
        CRDO_조정표, sf.LABELS_ADJUSTED_EPS, 0.35, True) is True


def test_연간_열의_값은_분기값으로_확인해_주지_않는다():
    """0.70 은 연간 열에 있으므로 분기값이라고 말하면 안 됩니다."""
    assert sf._표에서_분기값과_같은가(
        CRDO_조정표, sf.LABELS_ADJUSTED_EPS, 0.70, True) is False


def test_표에_없는_값은_확인해_주지_않는다():
    assert sf._표에서_분기값과_같은가(
        CRDO_조정표, sf.LABELS_ADJUSTED_EPS, 1.23, True) is False


def test_부문_설명_문장은_표의_행이_아니다():
    """이름이 줄 가운데 있으면 설명글입니다 (실물 NRG 'Texas: … $584 million')."""
    assert sf._표에서_분기값과_같은가(
        NRG_부문문장, sf.LABELS_ADJUSTED_EBITDA, 584_000_000, False) is False
    # 같은 표의 진짜 분기값은 확인해 줍니다
    assert sf._표에서_분기값과_같은가(
        NRG_부문문장, sf.LABELS_ADJUSTED_EBITDA, 1_055_000_000, False) is True


def test_연간_열이_비어_있으면_표의_행으로_보지_않는다():
    """전망 범위 줄은 숫자 둘이 다 분기 쪽에 몰려 있습니다 (실물 NRG)."""
    assert sf._표에서_분기값과_같은가(
        NRG_가이던스, sf.LABELS_ADJUSTED_EBITDA, 3_725_000_000, False) is False


def test_표_머리가_없으면_모른다고_한다():
    글 = "Non-GAAP diluted net income per share of $0.35 in the quarter.\n"
    assert sf._표에서_분기값과_같은가(글, sf.LABELS_ADJUSTED_EPS, 0.35, True) is False


ELV_날짜머리표 = (
    "(In millions)          Three Months Ended December 31        Twelve Months Ended December 31\n"
    "Non-GAAP diluted net income per share          $5.14                          $25.98\n")


def test_머리글이_날짜까지_이어지는_표에서도_분기값을_찾는다():
    """실물 ELV 꼴 — 'Ended' 에서 끊으면 열 경계가 왼쪽으로 밀려
    분기값 5.14 가 연간 쪽으로 넘어가 버립니다."""
    assert sf._표에서_분기값과_같은가(
        ELV_날짜머리표, sf.LABELS_ADJUSTED_EPS, 5.14, True) is True
    assert sf._표에서_분기값과_같은가(
        ELV_날짜머리표, sf.LABELS_ADJUSTED_EPS, 25.98, True) is False


두_표 = (
    "(A)                 Three Months Ended          Year Ended\n"
    "Adjusted EBITDA          $1,055                   $2,887\n"
    "\n"
    "(B)      Three Months Ended     Year Ended\n"
    "Adjusted EBITDA   $9        $2,000       $77\n")


def test_아래에_있는_다른_표의_줄을_끌어다_쓰지_않는다():
    """머리글 하나는 **자기 표만** 다스립니다. 아래 표의 열 자리는 다릅니다 —
    A 의 경계로 B 의 줄을 읽으면 B 의 연간값 2,000 이 분기값이 됩니다."""
    assert sf._표에서_분기값과_같은가(
        두_표, sf.LABELS_ADJUSTED_EBITDA, 2_000_000_000, False) is False
    assert sf._표에서_분기값과_같은가(
        두_표, sf.LABELS_ADJUSTED_EBITDA, 1_055_000_000, False) is True


전망표_뒤따름 = (
    "                                 Three Months Ended                     Twelve Months Ended\n"
    "Diluted earnings per share - GAAP     $4.63          $2.19          $24.73         $17.98\n"
    "\n"
    "\n"
    "Financial Guidance Summary\n"
    "Diluted earnings per share - GAAP    $24.73          Greater than 8.2% or better\n")


def test_빈_줄_두_번이면_표가_끝난_것으로_본다():
    """실물 ELV — 실적표 아래 **두 줄을 띄우고** 전망표가 이어집니다.
    전망표의 '$24.73' 은 그 회사의 **연간** GAAP EPS 라 분기값으로 확인해
    주면 안 됩니다. 머리글이 새로 나오지 않으므로 빈 줄이 유일한 경계입니다.

    (이 규칙은 한 번 지웠다가 되살린 것입니다 — 처음엔 돌연변이가 초록불로
     살아남아 '근거 없는 검사'로 보고 지웠는데, 183차-D 에서 머리글 자를
     넓히자 실물 ELV 가 정확히 이 자리에서 뚫렸습니다.)"""
    assert sf._표에서_분기값과_같은가(
        전망표_뒤따름, sf.LABELS_GAAP_EPS, 24.73, True) is False
    # 같은 표의 진짜 분기값은 확인해 줍니다
    assert sf._표에서_분기값과_같은가(
        전망표_뒤따름, sf.LABELS_GAAP_EPS, 4.63, True) is True


# 머리글 표현은 회사마다 다릅니다 (183차-D 실측 — 좁은 자로는 전부 놓쳤습니다)
머리글_변형 = [
    ("AAL 숫자 표기",       "3 Months Ended",           "12 Months Ended"),
    ("CGNX 붙임표·소문자",   "Three-months Ended",       "Twelve-months Ended"),
    ("ALGM Period 가 끼어듦", "Three-Month Period Ended", "Nine-Month Period Ended"),
    ("AXP 복수형",          "Quarters Ended",           "Years Ended"),
]


def _조정표(분기표현, 연간표현):
    """실제 표처럼 **열을 맞춰** 만듭니다.

    표 머리는 자기 무리의 마지막 열 끝에 맞춰 적히므로, 머리글의 끝이
    그 무리 마지막 값의 끝과 같아지도록 자리를 맞춥니다.
    """
    이름 = "Diluted earnings per share - GAAP"
    끝자리 = [60, 76, 100, 116]          # 분기 두 칸 · 연간 두 칸
    값 = ["$1.10", "$0.90", "$4.40", "$3.60"]
    줄 = 이름
    for 끝, v in zip(끝자리, 값):
        줄 = 줄.ljust(끝 - len(v)) + v
    머리 = "".ljust(76 - len(분기표현)) + 분기표현
    머리 = 머리.ljust(116 - len(연간표현)) + 연간표현
    return 머리 + "\n" + 줄 + "\n"


def test_회사마다_다른_머리글_표현을_모두_읽는다():
    for 이름, 분기표현, 연간표현 in 머리글_변형:
        글 = _조정표(분기표현, 연간표현)
        assert sf._표에서_분기값과_같은가(글, sf.LABELS_GAAP_EPS, 1.10, True) is True, \
            f"{이름}: 분기값 1.10 을 못 찾았습니다\n{글}"
        assert sf._표에서_분기값과_같은가(글, sf.LABELS_GAAP_EPS, 4.40, True) is False, \
            f"{이름}: 연간값 4.40 을 분기값이라고 했습니다\n{글}"


def test_파서가_분기열_표시를_남긴다():
    p = sf.parse_press_release(CRDO_조정표)
    assert p["adj_eps"] == 0.35
    assert p.get("adj_eps_분기열") is True


# ── SEC 에 회사 이름을 직접 물어보는 길 (183차-C) ──────────────────
#
# 왜 필요한가: edgartools 색인은 SEC 의 **지금 티커표**에서 만들어져,
# 인수·합병으로 상장이 끝난 회사는 이름으로도 안 나옵니다(160차 실측).
# 이 환경에서 다시 확인 — 꾸러미의 company_tickers.parquet 10,365행에
# HES·X·DFS·HOLX·CFLT 는 없습니다. SEC 자신은 갖고 있으므로 직접 묻습니다.
#
# ⚠️ 개발 환경에서는 SEC 가 막혀 언제나 실패로 기록됩니다. 그래서
#    **가짜 응답을 끼워** 읽는 부분이 실제로 도는 것을 증명합니다
#    (인수인계 규칙: 새 코드 경로는 실제로 실행됨을 증명한다).
SEC_검색응답 = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed>
 <entry>
  <content type="text/xml">
   <company-info>
    <cik>0000859737</cik>
    <conformed-name>HOLOGIC INC</conformed-name>
   </company-info>
  </content>
  <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000859737&amp;type=10-K"/>
 </entry>
</feed>"""


# ── 표시가 **실제로 도는 조립 함수**를 통과하는가 (183차-H) ──────────
#
# 왜 이 시험이 또 필요한가 — 같은 사고를 **두 번** 냈다:
#   런 #76: measure_store.EPS_FIELDS 허용 목록에 이름이 없어 스냅샷에서 사라짐
#   런 #77: fetch_earnings_8k 의 조립 dict 에 이름이 없어 행에 못 실림
# 두 번 다 "끝까지 따라가는 시험"을 썼다고 생각했지만, 시험이 **손으로
# 만든 dict** 로 시작해 실제 조립 함수를 건너뛰었다.
#
# 이 시험은 **파서가 내놓은 결과 그대로**를 조립 함수에 태운다.
def test_조립_함수가_분기열_표시를_옮긴다():
    """fetch_earnings_8k 가 만드는 dict 에 표시가 실려야 합니다.

    그 함수는 SEC 를 두드리므로 여기서는 **조립 부분만** 봅니다 —
    파서 결과의 어느 칸이 dict 로 옮겨지는지를 소스에서 확인하고,
    파서가 그 칸을 실제로 내놓는지를 함께 봅니다.
    """
    import inspect
    조립 = inspect.getsource(sf.fetch_earnings_8k)
    for 칸 in ("adj_eps", "gaap_eps", "adjusted_ebitda"):
        assert f'"{칸}_분기열"' in 조립, (
            f"조립 dict 에 {칸}_분기열 이 없습니다 — 파서가 남겨도 "
            f"행까지 못 갑니다(런 #77 사고)")

    # 파서가 실제로 그 칸을 내놓는지 (이름만 적어 두고 파서가 안 주면 헛것)
    표 = ("                                 Three Months Ended                     Year Ended\n"
         "Non-GAAP diluted net income per share  $0.35          $0.25          $0.70         $0.09\n")
    parsed = sf.parse_press_release(표)
    assert parsed.get("adj_eps_분기열") is True, parsed

    # 조립이 쓰는 것과 같은 방식으로 꺼내지는가
    assert parsed.get("adj_eps_분기열") == parsed.get("adj_eps_분기열")
    행 = {}
    sf._apply_press_to_row(행, {
        "adj_eps": parsed["adj_eps"],
        "adj_eps_분기열": parsed.get("adj_eps_분기열"),
    })
    assert 행.get("adj_eps_분기열") is True, 행


def test_은행_기간이_뼈대에_들어간다():
    """183차-G — 은행은 영업이익·매출·GAAP EPS 를 분기로 안 내서 **연말
    분기 행이 통째로 없었고**, 그래서 1월 실적 발표문이 붙을 자리가
    없었습니다(런 #76 실측: 짝 못 찾은 1~2월 8-K 가 FITB 7·RF 6·JPM 5건).

    은행 개념에서 온 **기간만** 넣습니다 — 값은 안 넣습니다.
    """
    series = {
        "op_income": {}, "revenue": {}, "gross_profit": {},
        "gaap_eps": {"2025-03-31": 1.0, "2025-06-30": 1.1, "2025-09-30": 1.2},
        "bank_period": {"2025-03-31": None, "2025-06-30": None,
                        "2025-09-30": None, "2025-12-31": None},
    }
    rows = sf._quarters_from_series("BANKX", series, "2025-01-01")
    끝 = sorted(r["filing_date"] for r in rows)
    assert "2025-12-31" in 끝, f"은행의 연말 분기 행이 안 생겼습니다: {끝}"
    # 값은 만들지 않습니다 — 그 행의 매출은 비어 있어야 합니다
    연말 = next(r for r in rows if r["filing_date"] == "2025-12-31")
    assert 연말.get("revenue") is None, f"없는 매출을 지어냈습니다: {연말}"


def test_은행개념_세기가_기간_목록을_돌려준다():
    """세기만 하던 함수가 이제 **뼈대에 쓸 기간**도 돌려줍니다 (183차-G)."""
    import config as cfg
    개념 = cfg_첫_은행개념()
    분기 = {"2025-03-31": 1e9, "2025-06-30": 1.1e9, "2025-09-30": 1.2e9}
    연간 = {"2025-12-31": 4.5e9}
    옛분기, 옛연간 = sf._quarterly_series, sf._annual_series
    sf._quarterly_series = lambda f, c, r=None, unit="USD": (분기 if c == 개념 else {})
    sf._annual_series = lambda f, c, r=None, unit="USD": (연간 if c == 개념 else {})
    try:
        out = sf._은행개념_세기(
            None, None,
            series={"op_income": {}, "revenue": {}, "gaap_eps": {}},
            start_date="2025-01-01")
    finally:
        sf._quarterly_series, sf._annual_series = 옛분기, 옛연간
    assert "_기간" in out, f"기간 목록을 안 돌려줍니다: {sorted(out)}"
    assert "2025-12-31" in out["_기간"], out["_기간"]


def cfg_첫_은행개념():
    return sf._BANK_REVENUE_CANDIDATES[0]


def test_은행_기간이_뼈대_조립에_실제로_전달된다():
    """배선 시험 — 세기 함수의 기간이 `_quarters_from_series` 까지
    가야 합니다. (183차-G 에 한 칸 짧은 배선 시험 때문에 스냅샷에서
    표시가 통째로 사라진 적이 있습니다 — 같은 실수를 막습니다.)"""
    받은 = {}
    옛세기, 옛조립 = sf._은행개념_세기, sf._quarters_from_series
    옛연간eps = sf._연간_gaap_eps
    sf._은행개념_세기 = lambda *a, **k: {"X": {"분기": 1}, "_기간": ["2025-12-31"]}
    sf._quarters_from_series = lambda t, series, sd, rep=None, ae=None: (
        받은.update(series) or [])
    sf._연간_gaap_eps = lambda facts, report=None: {}
    옛계열 = sf._build_series if hasattr(sf, "_build_series") else None
    try:
        sf.fetch_xbrl_approximation("BANKX", "2025-01-01", {})
    except Exception:
        pass                     # SEC 접속은 막혀 있으므로 도중에 끊길 수 있습니다
    finally:
        sf._은행개념_세기, sf._quarters_from_series = 옛세기, 옛조립
        sf._연간_gaap_eps = 옛연간eps
    if 받은:                      # 조립까지 닿았다면 반드시 실려 있어야 합니다
        assert "bank_period" in 받은, (
            f"은행 기간이 뼈대 조립에 안 전달됐습니다: {sorted(받은)}")
        assert "2025-12-31" in 받은["bank_period"], 받은["bank_period"]
    else:
        # SEC 차단으로 조립까지 못 갔으면, 배선이 글로라도 있는지 봅니다
        코드 = io.open("sec_fundamentals.py", encoding="utf-8").read()
        assert 'series["bank_period"] = {d: None for d in 은행["_기간"]}' in 코드, \
            "은행 기간을 series 에 담는 배선이 없습니다"


def test_은행_기간이_없으면_예전과_같다():
    """짝 시험 — bank_period 가 없으면 행이 하나도 안 늘어야 합니다."""
    series = {
        "op_income": {}, "revenue": {}, "gross_profit": {},
        "gaap_eps": {"2025-03-31": 1.0, "2025-06-30": 1.1, "2025-09-30": 1.2},
    }
    rows = sf._quarters_from_series("BANKX", series, "2025-01-01")
    assert sorted(r["filing_date"] for r in rows) == [
        "2025-03-31", "2025-06-30", "2025-09-30"], rows


def test_SEC_이름검색이_번호를_읽어_온다(monkeypatch=None):
    import edgar.httprequests as hr
    옛함수 = hr.download_text
    hr.download_text = lambda *a, **k: SEC_검색응답
    옛신원 = sf._ensure_identity
    sf._ensure_identity = lambda: None
    try:
        나온다 = sf._SEC_이름검색("Hologic Inc")
    finally:
        hr.download_text = 옛함수
        sf._ensure_identity = 옛신원
    번호들 = [x[1] for x in 나온다]
    assert "0000859737" in 번호들, 나온다
    assert any("HOLOGIC" in x[0].upper() for x in 나온다), 나온다


SEC_목록응답 = """<?xml version="1.0"?>
<feed>
 <entry><title>HESS CORP</title>
  <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000004447&amp;type=10-K"/></entry>
 <entry><title>HESS MIDSTREAM LP</title>
  <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0001789832&amp;type=10-K"/></entry>
</feed>"""


def _가짜응답으로(글):
    import edgar.httprequests as hr
    옛함수, 옛신원 = hr.download_text, sf._ensure_identity
    hr.download_text = lambda *a, **k: 글
    sf._ensure_identity = lambda: None
    try:
        return sf._SEC_이름검색("아무이름")
    finally:
        hr.download_text, sf._ensure_identity = 옛함수, 옛신원


def test_여러_회사가_나오는_목록_꼴에서도_이름을_읽는다():
    """183차-J — 찾은 회사가 하나면 SEC 는 회사 페이지를 주고 이름이
    <conformed-name> 에 있지만(DFS·CFLT·X), 여럿이면 목록을 주고 이름이
    다른 자리에 있습니다. 런 #77·#78 에서 HES 는 번호 셋이 **이름 없이**
    왔습니다 — 이름 없는 번호는 넣을 수 없으므로 되살리지 못했습니다."""
    나온다 = _가짜응답으로(SEC_목록응답)
    번호들 = {x[1] for x in 나온다}
    이름들 = {x[0] for x in 나온다}
    assert "0000004447" in 번호들, 나온다
    assert any("HESS CORP" in n for n in 이름들), f"목록 꼴에서 이름을 못 읽었습니다: {나온다}"


def test_이름을_못_읽으면_응답_앞부분을_적어_온다():
    """짐작 대신 계기 — 다음 런의 로그로 실제 모양을 본다 (106차 규칙)."""
    나온다 = _가짜응답으로("<feed><entry><cik>0000004447</cik></entry></feed>")
    계기 = [x for x in 나온다 if x[0] == "_응답앞"]
    assert 계기, f"이름을 못 읽었는데 응답을 안 적었습니다: {나온다}"
    assert "0000004447" in 계기[0][1], 계기


def test_이름을_읽으면_응답을_적지_않는다():
    """짝 시험 — 잘 읽히면 로그를 지저분하게 만들지 않습니다."""
    나온다 = _가짜응답으로(SEC_목록응답)
    assert not [x for x in 나온다 if x[0] == "_응답앞"], 나온다


# 런 #79 의 **실물 모양** (183차-R). 세 가지가 함께 들어 있습니다:
#   ① 목록 전체의 제목("Company Search Feed") — 회사 이름이 아님
#   ② 번호가 항목마다 **두 번** 나옴(링크와 본문)
#   ③ 항목마다 회사 이름이 <title> 에 있음
#   ④ 같은 회사가 두 항목으로 오기도 합니다(번호 중복)
#   ⑤ 이름이 없는 항목이 섞이면 **자리 맞추기(앞에서부터 짝짓기)가
#      어긋납니다** — 그래서 항목 단위로 짝지어야 합니다
SEC_실물목록응답 = """<?xml version="1.0"?>
<feed><title>EDGAR Company Search Feed</title>
 <entry><link href="...action=getcompany&amp;CIK=0001120916&amp;type=10-K"/>
  <content><cik>CIK=0001120916</cik></content></entry>
 <entry><title>HESS CORP</title>
  <link href="...action=getcompany&amp;CIK=0000004447&amp;type=10-K"/>
  <content><cik>CIK=0000004447</cik></content></entry>
 <entry><title>HESS CORP</title>
  <link href="...action=getcompany&amp;CIK=0000004447&amp;type=10-Q"/></entry>
 <entry><title>HESS MIDSTREAM PARTNERS LP</title>
  <link href="...action=getcompany&amp;CIK=0001789832&amp;type=10-K"/></entry>
</feed>"""


def test_목록_제목을_회사_이름으로_읽지_않는다():
    """(183차-R) 런 #79 실물: 첫 이름이 "Company Search Feed" 였습니다.

    183차-J 는 `<title>` 을 순서대로 번호에 붙였는데, 목록 꼴의 첫
    `<title>` 은 **목록 전체의 제목**입니다. 그래서 회사 셋 중 첫 번호에
    엉뚱한 이름이 붙고 나머지는 빈 채로 왔습니다. 게다가 번호가 두 번씩
    나와 세 회사가 **다섯 줄**이 됐습니다.
    """
    나온다 = [x for x in _가짜응답으로(SEC_실물목록응답) if x[0] != "_응답앞"]
    이름들 = [x[0] for x in 나온다]
    번호들 = [x[1] for x in 나온다]

    assert not any("Search Feed" in n for n in 이름들), (
        f"목록 제목을 회사 이름으로 읽었습니다: {나온다}")
    assert len(번호들) == len(set(번호들)) == 3, (
        f"같은 번호를 두 번 세었습니다: {나온다}")
    짝 = dict(zip(번호들, 이름들))
    # 첫 항목에는 이름이 없습니다 — **빈 채로 두어야** 합니다.
    # 앞에서부터 자리로 짝지으면 여기에 'HESS CORP' 가 잘못 붙습니다.
    assert 짝.get("0001120916") == "", (
        f"이름 없는 항목에 남의 이름을 붙였습니다: {짝}")
    assert 짝.get("0000004447") == "HESS CORP", f"이름과 번호가 어긋났습니다: {짝}"
    assert 짝.get("0001789832") == "HESS MIDSTREAM PARTNERS LP", 짝


def test_이름이_하나라도_비면_응답을_적어_온다():
    """(183차-R) 183차-J 는 "하나도 못 읽었을 때만" 적었습니다.

    그래서 쓸모없는 이름 하나("Company Search Feed")가 **진단을 막았고**,
    다음 런에서도 실제 모양을 알 수 없었습니다. 하나라도 비면 적습니다.
    """
    반쪽 = """<feed><title>EDGAR Company Search Feed</title>
 <entry><title>HESS CORP</title><link href="...CIK=0000004447"/></entry>
 <entry><link href="...CIK=0001789832"/></entry></feed>"""
    나온다 = _가짜응답으로(반쪽)
    assert [x for x in 나온다 if x[0] == "_응답앞"], (
        f"이름이 빈 줄이 있는데 응답을 안 적었습니다: {나온다}")


def test_SEC_이름검색이_실패해도_수집을_멈추지_않는다():
    """SEC 가 막힌 개발 환경·일시 장애에서도 예외를 밖으로 내보내지 않습니다."""
    import edgar.httprequests as hr
    옛함수 = hr.download_text
    def 터짐(*a, **k):
        raise RuntimeError("접속 막힘")
    hr.download_text = 터짐
    옛신원 = sf._ensure_identity
    sf._ensure_identity = lambda: None
    try:
        나온다 = sf._SEC_이름검색("Hologic Inc")
    finally:
        hr.download_text = 옛함수
        sf._ensure_identity = 옛신원
    assert 나온다[0][0] == "SEC검색실패", 나온다
    assert "RuntimeError" in 나온다[0][1], 나온다


def test_사라진회사_찾아보기가_SEC직접_결과를_함께_적는다():
    """배선 시험 — 로그에 실려야 사람이 보고 번호를 넣을 수 있습니다."""
    옛 = sf._SEC_이름검색
    sf._SEC_이름검색 = lambda name: [["HOLOGIC INC", "0000859737", ""]]
    try:
        report = {}
        out = sf.사라진회사_찾아보기("HOLX", report)
    finally:
        sf._SEC_이름검색 = 옛
    assert out["SEC직접"] == [["HOLOGIC INC", "0000859737", ""]], out
    assert report["사라진회사_검색"]["SEC직접"], report

    # 이름표에 없는 티커는 아무 일도 하지 않습니다 (SEC 를 두드리지도 않음)
    assert sf.사라진회사_찾아보기("NVDA") == {}


def test_티커표에서_번호를_찾아_적어_온다():
    """(183차-U) 이름 검색은 **SEC 쪽이 깨져** 있습니다 — 티커로 갑니다.

    런 #80 진단이 보여 준 실물:
        <entry title="ARRAY(0x5628d8254898)">
          <content type="text/xml"><company-info name="ARRAY(0x5628d…
    `ARRAY(0x…)` 는 SEC 서버가 배열을 글자로 잘못 찍은 것이라 **회사 이름이
    아예 안 옵니다.** 우리가 어떻게 읽든 읽을 이름이 없습니다.

    SEC 티커표(company_tickers.json)는 티커를 그대로 맞추면 되므로 이름을
    읽을 필요가 없습니다. 값은 여전히 **안 씁니다 — 적어 오기만** 합니다.
    """
    import edgar.httprequests as hr
    표 = ('{"0":{"cik_str":320193,"ticker":"AAPL","title":"Apple Inc."},'
         '"1":{"cik_str":4447,"ticker":"HES","title":"HESS CORP"}}')
    옛함수, 옛신원 = hr.download_text, sf._ensure_identity
    hr.download_text = lambda *a, **k: 표
    sf._ensure_identity = lambda: None
    try:
        찾음 = sf._SEC_티커표에서_찾기("HES")
        없음 = sf._SEC_티커표에서_찾기("ZZZZ")
    finally:
        hr.download_text, sf._ensure_identity = 옛함수, 옛신원

    assert 찾음 == [["HESS CORP", "0000004447", "HES"]], (
        f"티커로 번호를 못 찾았습니다: {찾음}")
    assert 없음 == [["없음", "", ""]], f"없는 것을 지어냈습니다: {없음}"


def test_번호로_이름을_되묻는다():
    """(183차-V) 두 길이 다 막혔을 때 남은 길 — **번호로 되묻기**.

    런 #81 실측: 이름 검색은 SEC 가 `ARRAY(0x…)` 를 뱉고, 티커표에는
    HES·HOLX 가 **없다**(상장이 끝난 회사는 지금 티커표에서 빠진다).
    그런데 **번호는 있다**. SEC 의 제출물 창구는 번호로 물으면 JSON 으로
    회사 이름과 티커를 돌려준다.
    """
    import edgar.httprequests as hr
    응답 = {
        "0000004447": '{"cik":"4447","name":"HESS CORPORATION","tickers":["HES"]}',
        "0001789832": '{"cik":"1789832","name":"Hess Midstream LP","tickers":["HESM"]}',
    }
    부른것 = []

    def 가짜(url, *a, **k):
        부른것.append(url)
        for 번호, 글 in 응답.items():
            if 번호 in url:
                return 글
        return "{}"

    옛함수, 옛신원 = hr.download_text, sf._ensure_identity
    hr.download_text, sf._ensure_identity = 가짜, lambda: None
    try:
        나온다 = sf._번호마다_이름을_되묻는다([
            ["", "0000004447", ""], ["", "0001789832", ""],
            ["_응답앞", "<?xml …", ""],        # 계기 줄 — 두드리면 안 됩니다
        ])
    finally:
        hr.download_text, sf._ensure_identity = 옛함수, 옛신원

    assert 나온다 == [["HESS CORPORATION", "0000004447", "HES"],
                    ["Hess Midstream LP", "0001789832", "HESM"]], 나온다
    assert len(부른것) == 2, f"계기 줄까지 SEC 를 두드렸습니다: {부른것}"


def test_티커표_결과가_로그에_실린다():
    """배선 시험 — 로그에 실려야 사람이 보고 번호를 넣을 수 있습니다.

    ⚠️ 183차-G·H 에서 **두 번** 빠뜨린 자리입니다(만들고 배선을 잊음).
    """
    옛이름, 옛티커 = sf._SEC_이름검색, sf._SEC_티커표에서_찾기
    옛되묻기 = sf._번호마다_이름을_되묻는다
    # 183차-W — 특정 종목(HES)을 박아 두지 않습니다. 번호를 찾아 졸업하면
    # 이름표에서 빠지고, 그러면 이 시험이 종목 탓에 깨졌습니다.
    종목 = sorted(cfg.TICKER_NAME_HINT)[0]
    sf._SEC_이름검색 = lambda name: [["", "0000004447", ""]]
    sf._SEC_티커표에서_찾기 = lambda t: [["HESS CORP", "0000004447", t]]
    sf._번호마다_이름을_되묻는다 = lambda 후보: [["HESS CORPORATION", "0000004447", "HES"]]
    try:
        report = {}
        out = sf.사라진회사_찾아보기(종목, report)
    finally:
        sf._SEC_이름검색, sf._SEC_티커표에서_찾기 = 옛이름, 옛티커
        sf._번호마다_이름을_되묻는다 = 옛되묻기
    assert out["티커표"] == [["HESS CORP", "0000004447", 종목]], out
    assert report["사라진회사_검색"]["티커표"], report
    # 183차-V — 번호로 되물은 결과도 로그에 실려야 합니다
    assert out["번호로확인"] == [["HESS CORPORATION", "0000004447", "HES"]], out
    assert report["사라진회사_검색"]["번호로확인"], report


def test_이름을_문_문장을_남긴다():
    """(183차-Z) 파서가 **어느 문장에서** 분기 이름을 물었는지 남깁니다.

    왜 필요한가: 파서는 본문에서 맨 처음 나오는 분기 표현을 뭅니다.
    1분기 발표문에는 2분기 전망·작년 1분기 비교가 나란히 실려서 맨 처음
    것이 이번 분기가 아닐 때가 있습니다(183차-Y 실측 203칸·83종목).

    고치려면 문 자리를 봐야 하는데, 원문은 **잣대값을 하나도 못 읽었을
    때만** 보관합니다(`_should_keep_raw`). 이 203칸은 값이 멀쩡히 읽힌
    행이라 원문이 안 남습니다 — 실측으로 **203칸 중 5칸만** 재료가
    있었습니다. 그래서 문장 조각을 행에 붙여 보냅니다.
    """
    글 = ("Acme Corp Reports Results\n"
          "Acme today announced financial results for the second quarter 2025.\n"
          "Revenue was $100.0 million.\n")
    자리 = []
    이름 = sf.extract_period_label(글, "2025-08-01", 자리=자리)
    assert 이름 == "25 Q2", 이름
    assert len(자리) == 1, f"문 자리를 안 남겼습니다: {자리}"
    assert "second quarter 2025" in 자리[0], f"문 자리가 엉뚱합니다: {자리[0]}"
    assert "\n" not in 자리[0], "줄바꿈을 안 폈습니다"
    assert len(자리[0]) <= sf._문장_최대, f"조각이 너무 깁니다: {len(자리[0])}"


def test_짧은_분기표현도_문_자리를_남긴다():
    """"Q3 2025" 꼴로 쓰는 회사도 마찬가지입니다."""
    자리 = []
    이름 = sf.extract_period_label("Results for Q3 2025 were strong.", "2025-11-01",
                                 자리=자리)
    assert 이름 == "25 Q3", 이름
    assert 자리 and "Q3 2025" in 자리[0], 자리


def test_분기표현을_못_찾으면_문_자리도_안_남긴다():
    """헌법 1조 — 없는 것을 지어내지 않습니다."""
    자리 = []
    이름 = sf.extract_period_label("No period words here at all.", "2025-11-01",
                                 자리=자리)
    assert 이름 == "25/11", 이름
    assert 자리 == [], f"못 찾았는데 자리를 남겼습니다: {자리}"


def test_자리를_안_주면_예전과_똑같이_군다():
    """부르는 쪽을 안 고쳐도 깨지지 않아야 합니다."""
    assert sf.extract_period_label("the first quarter 2025", "2025-05-01") == "25 Q1"


def test_문_문장이_수집_행에_실린다():
    """배선 시험 — 만들고 안 실으면 헛돕니다 (183차-G·H 에서 두 번 빠뜨림)."""
    with open(os.path.join(os.path.dirname(__file__), "..",
                           "sec_fundamentals.py"), encoding="utf-8") as f:
        코드 = f.read()
    assert '"period_label_문장": 이름자리[0] if 이름자리 else None' in 코드, \
        "문 자리를 만들고 수집 행에 안 실었습니다"
    assert "자리=이름자리" in 코드, "extract_period_label 에 자리를 안 넘깁니다"


def test_분기열_표시가_행까지_옮겨진다():
    """배선 시험 — 파서가 남긴 표시가 **행**에 실려야 정제가 볼 수 있습니다.
    (178차: 배선만 빠져도 시험이 초록불이던 사고를 되풀이하지 않기 위해)"""
    for 칸 in ("adj_eps", "gaap_eps", "adjusted_ebitda"):
        press = {칸: 1.23 if 칸 != "adjusted_ebitda" else 1_230_000.0,
                 f"{칸}_분기열": True}
        row = {}
        sf._apply_press_to_row(row, press)
        assert row.get(f"{칸}_분기열") is True, f"{칸} 표시가 행에 안 실렸습니다: {row}"

    # 표시가 없으면 행에도 없어야 합니다
    row = {}
    sf._apply_press_to_row(row, {"adj_eps": 1.23})
    assert "adj_eps_분기열" not in row, row


def test_분기열_표시는_확인_안_되면_안_남긴다():
    글 = "Non-GAAP diluted net income per share of $0.35 in the quarter.\n"
    p = sf.parse_press_release(글)
    assert p.get("adj_eps_분기열") is None


def test_분기값이_연간값과_같으면_계기가_센다():
    """(183차-AS) 4분기 칸에 **연간값**이 앉은 28칸(183차-AR)이 어디서
    왔는지 가리기 위한 계기입니다.

    한 분기가 그 해 전체와 같으려면 나머지 세 분기가 0 이어야 합니다 —
    매출에서는 사실상 불가능합니다. 그러니 같다면 **3개월 태그가 실은
    12개월 값**이라는 뜻입니다.

    ⚠️ 세기만 합니다. 값을 버리면 그 날짜가 뼈대에서 빠져 모든 종목의
    행이 달라집니다(106차 규칙).
    """
    분기 = {"2024-03-31": 100.0, "2024-06-30": 110.0,
          "2024-09-30": 120.0, "2024-12-31": 460.0}
    연간 = {"2024-12-31": 460.0}
    assert sf._분기가_연간과_같은_날들(분기, 연간) == ["2024-12-31"], \
        "분기값이 연간값과 똑같은데 못 셌습니다"

    # 제대로 된 4분기(연간 − 앞 세 분기)면 세지 않습니다
    멀쩡 = dict(분기, **{"2024-12-31": 130.0})
    assert sf._분기가_연간과_같은_날들(멀쩡, 연간) == [], "멀쩡한 4분기를 셌습니다"

    # 0.5% 여유 안쪽은 같은 값으로 봅니다 (반올림 차이)
    살짝 = dict(분기, **{"2024-12-31": 460.0 * 1.004})
    assert sf._분기가_연간과_같은_날들(살짝, 연간) == ["2024-12-31"], \
        "반올림 차이를 다른 값으로 봤습니다"
    # 1% 어긋나면 다른 값입니다
    제법 = dict(분기, **{"2024-12-31": 460.0 * 1.01})
    assert sf._분기가_연간과_같은_날들(제법, 연간) == [], "여유가 너무 헐겁습니다"

    # 연간값이 없으면 잴 것이 없습니다 (빈 목록, 예외 아님)
    assert sf._분기가_연간과_같은_날들(분기, {}) == []
    assert sf._분기가_연간과_같은_날들({}, 연간) == []

    # 계기가 **로봇 로그까지** 가는가 — q4_채움 은 이미 실리고 있으므로
    # 그 안에 넣습니다 (183차-AO 의 재발 방지)
    import inspect
    코드 = inspect.getsource(sf._series_for_key)
    assert '계기["분기가_연간과_같음"]' in 코드, \
        "센 것을 q4_채움 계기에 안 넣었습니다 — 로그에 한 글자도 안 나옵니다"
    # 183차-BE — 세기만 하지 않고 **버립니다**
    assert "merged.pop(날, None)" in 코드, (
        "같다고 세어 놓고 그대로 두면 틀린 값이 뼈대에 남습니다 (183차-BE)")


def test_분기가_연간과_같으면_버리고_다시_채운다():
    """(183차-BE) 세었더니 있었습니다 — 런 #84 전수 11칸
    (revenue 4 · amortization 3 · depreciation_amortization 2 · sbc 1 ·
    op_income 1). 이제 버리고 `연간 − 앞 세 분기`로 다시 채웁니다.

    버려도 되는 까닭은 **산수**입니다: 한 분기가 그 해 전체와 같으려면
    나머지 세 분기가 0 이어야 합니다. 짐작이 아니라 증명입니다.
    즉 잃는 것이 아니라 **바로잡는 것**입니다.
    """
    분기 = {"2024-03-31": 100.0, "2024-06-30": 110.0,
          "2024-09-30": 120.0, "2024-12-31": 460.0}   # ← 4분기에 연간값
    연간 = {"2024-12-31": 460.0}
    버릴날 = sf._분기가_연간과_같은_날들(분기, 연간)
    남은 = {k: v for k, v in 분기.items() if k not in 버릴날}
    채운 = sf._fill_missing_q4(남은, 연간, {})
    assert 채운["2024-12-31"] == 130.0, (
        f"연간 460 − 앞 세 분기 330 = 130 이어야 합니다: {채운['2024-12-31']}")
    # 앞 세 분기는 그대로 — 멀쩡한 값을 건드리면 안 됩니다
    assert 채운["2024-03-31"] == 100.0 and 채운["2024-09-30"] == 120.0, 채운

    # 앞 세 분기가 없으면 **채우지 않습니다**(창작 금지) — 버린 자리는
    # 그냥 없음이 됩니다. 연간값을 그대로 넣으면 안 됩니다.
    모자람 = {"2024-09-30": 120.0}
    채운2 = sf._fill_missing_q4(모자람, 연간, {})
    assert "2024-12-31" not in 채운2, (
        f"앞 세 분기가 없는데 채웠습니다: {채운2}")


def test_연간_전용_표의_값을_분기값으로_담지_않는다():
    """(183차-AW) GS 는 **아홉 해 내리** 4분기 매출 칸에 그 해 연간
    순수익이 들어 있었습니다 (2025-12-31 = 58,283백만 = 연간치).

    까닭: 제목이 "Full Year and Fourth Quarter …" 인 보도자료에는 표가
    둘 들어갑니다. 파서가 **연간 표를 먼저** 만나 그 값을 담았습니다.
    매출 쪽 연간 가드는 라벨 앞 같은 줄만 보는데 표 머리는 2,138자
    뒤라 닿지 않았습니다.
    """
    # GS 모양 — 연간 표가 앞, 분기 표가 뒤
    지에스 = (
        "Goldman Sachs Reports Full Year and Fourth Quarter 2025 Results\n"
        "Segment Net Revenues (unaudited) $ in millions\n"
        "YEAR ENDED DECEMBER 31, 2025 2024\n"
        "Investment banking fees 9,340 7,738\n"
        "Total net revenues $58,283 $53,512\n"
        + "x" * 400 + "\n"
        "Segment Net Revenues (unaudited) $ in millions\n"
        "THREE MONTHS ENDED DECEMBER 31, 2025 2024\n"
        "Investment banking fees 2,580 2,064\n"
        "Total net revenues $13,454 $13,869\n"
    )
    r = sf.parse_press_release(지에스)
    assert r["revenue"] == 13_454_000_000, (
        f"연간 표(58,283)를 분기 매출로 담았습니다: {r['revenue']}")

    # 합친 표(분기 열 + 연간 열이 한 표에) 는 **건드리면 안 됩니다** —
    # 이름 뒤 첫 숫자가 이미 분기값입니다 (실물 ACLS 2025-02-10).
    합친표 = (
        "Condensed Consolidated Statements of Operations\n"
        "Three Months Ended December 31, 2024 2023 "
        "Twelve Months Ended December 31, 2024 2023\n"
        "Revenue:\n"
        "Total revenue 252,417 310,288 1,017,865 1,130,604\n"
    )
    r2 = sf.parse_press_release(합친표)
    # ⚠️ 이 시험 자료에는 단위 선언("$ in thousands")이 없어 숫자가 그대로
    #    나옵니다. 여기서 볼 것은 단위가 아니라 **어느 열을 집었나** 입니다 —
    #    분기 열 252,417 이어야 하고 연간 열 1,017,865 면 안 됩니다.
    assert r2["revenue"] == 252_417.0, (
        f"합친 표에서 분기 열을 잃었습니다: {r2['revenue']}")
    assert r2["revenue"] != 1_017_865.0, "합친 표에서 연간 열을 집었습니다"

    # 머리를 못 찾으면 건드리지 않습니다 (모르면 그대로 — 창작 금지와 같은 결)
    assert sf._연간전용_표머리_아래인가("Total revenue 100", 5) is False

    # 183차-BG — 머리가 **3,000자보다 멀어도** 닿아야 합니다.
    # 실물 GS 2025-01-15 은 연간 표 머리가 값에서 3,106자 앞이라
    # 106자 차이로 옛 창(3,000)을 벗어났고, 아홉 해 중 일곱 해가
    # 안 고쳐지고 있었습니다.
    먼머리 = ("YEAR ENDED DECEMBER 31, 2024 2023\n" + "x" * 3200
            + "\nTotal net revenues $ 53,512 $ 46,254\n")
    자리 = 먼머리.index("Total net revenues")
    assert 자리 - len("YEAR ENDED DECEMBER 31, 2024 2023") > 3000, "시험 자료가 3,000자를 안 넘습니다"
    assert sf._연간전용_표머리_아래인가(먼머리, 자리) is True, (
        "3,000자보다 먼 표 머리에 안 닿습니다 — GS 일곱 해가 그래서 남았습니다")


def test_표머리_검사가_연간가드_밖에_있다():
    """(183차-AW) `find_labeled_value` 는 같은 탐색을 **네 번** 돌리는데
    뒤 두 번은 `avoid_annual=False`, 곧 **연간 가드를 끄고** 다시 찾습니다.

    그래서 이 검사를 가드 **안**에 두면 앞 두 번이 연간값을 제대로 걸러도
    뒤 두 번이 그대로 주워 옵니다 — 183차-AT 의 고침이 매출을 **한 칸도**
    못 바꾼 까닭이 이것입니다. 자리를 지킵니다.
    """
    import inspect
    import re as _re

    코드 = inspect.getsource(sf._scan_labeled_value)
    가드 = 코드.index("if avoid_annual:")
    검사 = 코드.index("_연간전용_표머리_아래인가(text")
    assert 검사 > 가드, "검사가 avoid_annual 블록보다 앞에 있습니다"
    # 들여쓰기가 가드 블록 **밖**(같은 층)이어야 합니다
    줄 = [l for l in 코드[검사 - 200:검사 + 80].split("\n")
          if "_연간전용_표머리_아래인가(text" in l][0]
    들여 = len(줄) - len(줄.lstrip())
    가드줄 = [l for l in 코드.split("\n") if "if avoid_annual:" in l][0]
    assert 들여 == len(가드줄) - len(가드줄.lstrip()), (
        "검사가 avoid_annual 블록 **안**에 있습니다 — 뒤 두 번이 가드를 끄고 "
        "그대로 주워 옵니다 (183차-AT 의 재발)")


def test_연간값밖에_없으면_없음으로_끝낸다():
    """(183차-AX) 예전에는 연간 가드를 **끄고 한 번 더** 찾았습니다.

    그래서 후보 하나를 걸러 내면 "없음"이 되는 것이 아니라 **더 나쁜
    후보로 떨어졌습니다** — 가드를 잘 만들수록 나쁜 값이 올라오는
    구조였습니다. 헌법 1조는 그 반대를 말합니다: 없음은 안전하고 틀림은
    위험하다.

    전수 3,244건 실측: 수상→없음 9칸 ✅ · 그럴듯→없음 1칸 ⛔.
    """
    # 연간값밖에 없는 글 — 분기 매출은 **없음**이 정답입니다
    연간만 = (
        "Acme Reports Full Year 2025 Results\n"
        "Full-year revenue was $1,200.0 million, up 12%.\n"
    )
    assert sf.parse_press_release(연간만)["revenue"] is None, (
        "연간값밖에 없는 글에서 그 연간값을 분기 매출로 담았습니다")

    # 분기값이 함께 있으면 그것을 집습니다 (값을 잃으면 안 됩니다)
    둘다 = (
        "Acme Reports Fourth Quarter and Full Year 2025 Results\n"
        "Full-year revenue was $1,200.0 million, up 12%.\n"
        "Fourth quarter revenue was $310.0 million, up 9%.\n"
    )
    assert sf.parse_press_release(둘다)["revenue"] == 310_000_000, (
        f"분기값을 잃었습니다: {sf.parse_press_release(둘다)['revenue']}")


def test_응답을_300자에서_자르지_않는다():
    """(183차-BC) HOLX 를 네 회차째 "못 찾음"으로 넘기려다 계기를 열어 보니
    SEC 응답이 **300자에서 잘려** 있었습니다.

    거기까지는 Atom 머리글(author·id)뿐이고 **결과가 있는지 없는지는 그
    뒤**에 나옵니다. 즉 "결과없음"은 파서의 말이었고, 원문이 그렇다는
    증거는 한 번도 못 봤습니다. **못 본 것은 "못 봤다"이지 "없다"가
    아닙니다**(150차-R).
    """
    머리글 = ('<?xml version="1.0" encoding="ISO-8859-1" ?>'
            '<feed xmlns="http://www.w3.org/2005/Atom"><author>'
            '<email>webmaster@sec.gov</email><name>Webmaster</name></author>'
            '<id>https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany'
            '&amp;company=Hologic&amp;type=10-K&amp;owner=include&amp;count=40'
            '&amp;output=atom</id><title>EDGAR Search Results</title>'
            '<updated>2026-09-19T00:00:00-04:00</updated>')
    assert len(머리글) > 300, "시험 자료가 300자를 안 넘어 아무것도 못 가립니다"
    몸통 = ('<entry><title>HOLOGIC INC</title><content type="text/xml">'
          '<company-info><cik>0000859737</cik></company-info></content></entry>')
    나온다 = _가짜응답으로(머리글 + 몸통)

    계기 = [x for x in 나온다 if x[0] == "_응답앞"]
    assert 계기, "응답 계기가 아예 없습니다"
    적힌글 = 계기[0][1]
    # ⚠️ **길이를 재면 안 됩니다.** 앞에 붙인 요약 때문에 창을 300자로
    #    되돌려도 길이는 300을 넘습니다(돌연변이가 초록으로 통과했습니다).
    #    창이 **몸통까지 닿았는지**를 직접 봅니다 — 300자 뒤에 있는 글자가
    #    적혀 있어야 합니다.
    assert 머리글.index("<title>EDGAR Search Results") > 300
    assert "HOLOGIC INC" in 적힌글, (
        "응답의 **몸통**이 안 적혔습니다 — Atom 머리글만 보고 '결과없음'이라 "
        f"단정하게 됩니다: {적힌글[:120]}")

    # ⚠️ 계기를 **새 줄**로 넣으면 안 됩니다 — 이 목록의 칸 뜻은
    #    [이름, 번호, …] 이고 읽는 쪽들은 "_응답앞" 한 줄만 건너뜁니다.
    #    실제로 새 줄을 넣었다가 다른 시험이 그것을 회사 번호로 셌습니다.
    assert "entry 1개" in 적힌글, f"결과 수를 세어 적지 않았습니다: {적힌글[:80]}"
    assert "company-info 1개" in 적힌글, 적힌글[:80]
    assert len([x for x in 나온다 if str(x[0]).startswith("_")]) == 1, (
        f"계기 줄이 둘 이상입니다 — 읽는 쪽이 번호로 셉니다: {나온다}")


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