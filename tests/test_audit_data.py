"""
tests/test_audit_data.py — 재료 오염 전수조사 검증 (73차)

이 장치의 약속은 두 가지입니다:
  ① **아무것도 지우지 않는다** (65차 §④ "표시부터, 지우지 말고")
  ② **성장을 오염으로 세지 않는다** — 이웃과만 비교하기 때문
이 두 가지가 깨지면 빨간 불이 켜지도록 짰습니다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_data  # noqa: E402


def 분기(label, **kw):
    row = {"period_label": label, "announced_date": None,
           "adj_eps": None, "adjusted_ebitda": None, "gaap_eps": None}
    row.update(kw)
    return row


def test_growth_series_is_not_counted_as_contamination():
    """꾸준히 크는 회사는 한 칸도 걸리지 않아야 합니다.

    실물 CRDO 는 조정 EPS 가 0.05 에서 1.16 까지 **23배** 자랐습니다.
    종목 전체 중앙값과 비교하는 방식이면 이 회사가 오염 1위로 올라옵니다
    (73차 실측). 이웃하고만 비교하면 한 칸도 걸리지 않습니다.
    """
    값들 = [0.05, 0.07, 0.10, 0.15, 0.22, 0.33, 0.52, 0.67, 1.07, 1.16]
    quarters = {"CRDO": [분기(f"Q{i}", adj_eps=v) for i, v in enumerate(값들)]}
    assert audit_data.spike_cells(quarters) == [], \
        "성장하는 회사를 오염으로 셌습니다"


def test_spike_that_returns_is_counted():
    """혼자 튀었다가 제자리로 돌아오는 칸은 세어야 합니다.

    실물 VZ: 분기 1.2 안팎인데 결산 행만 5.18 (연간값).
    """
    값들 = [1.18, 1.22, 5.18, 1.20, 1.21, 1.19]
    quarters = {"VZ": [분기(f"Q{i}", adj_eps=v) for i, v in enumerate(값들)]}
    걸린 = audit_data.spike_cells(quarters)
    assert len(걸린) == 1, 걸린
    assert 걸린[0]["값"] == 5.18 and 걸린[0]["종목"] == "VZ"
    assert 걸린[0]["배수"] > 4.0, 걸린[0]


def test_audit_never_changes_the_data():
    """조사는 재료를 **읽기만** 합니다 — 한 칸도 바뀌면 안 됩니다.

    지우는 규칙을 슬쩍 넣으면 이 검사가 빨간 불이 됩니다.
    """
    값들 = [1.18, 1.22, 5.18, 1.20, 1.21, 1.19]
    quarters = {"VZ": [분기(f"Q{i}", adj_eps=v) for i, v in enumerate(값들)]}
    이전 = [dict(r) for r in quarters["VZ"]]

    audit_data.spike_cells(quarters)
    audit_data.duplicate_labels(quarters)
    audit_data.repeated_ladder_values(quarters)
    audit_data.summary(quarters)
    audit_data.one_line(quarters)

    assert quarters["VZ"] == 이전, "조사가 재료를 바꿨습니다"


def test_edge_cells_are_not_judged():
    """맨 앞·맨 뒤 칸은 앞뒤 이웃이 없으므로 판단하지 않습니다."""
    값들 = [9.99, 1.20, 1.18, 1.22, 1.19, 9.99]     # 양 끝만 크다
    quarters = {"AAA": [분기(f"Q{i}", adj_eps=v) for i, v in enumerate(값들)]}
    assert audit_data.spike_cells(quarters) == []


def test_short_series_is_skipped():
    """값이 너무 적은 종목은 비교 자체가 무의미하므로 건너뜁니다."""
    quarters = {"AAA": [분기("Q0", adj_eps=1.0), 분기("Q1", adj_eps=9.0),
                        분기("Q2", adj_eps=1.0)]}
    assert audit_data.spike_cells(quarters) == []


def test_duplicate_labels_are_listed():
    """같은 기간 라벨이 여러 행에 붙으면 셉니다 (실물 VZ '23 Q1' 4행)."""
    quarters = {"VZ": [분기("23 Q1", adj_eps=1.2), 분기("23 Q1", adj_eps=1.21),
                       분기("23 Q1", adj_eps=1.22), 분기("24 Q1", adj_eps=1.15)]}
    걸린 = audit_data.duplicate_labels(quarters)
    assert len(걸린) == 1 and 걸린[0]["걸린행"] == 3, 걸린
    assert 걸린[0]["겹친라벨"] == 1, 걸린


def test_label_without_any_ladder_value_is_not_counted():
    """사다리 값이 하나도 없는 행은 라벨이 겹쳐도 세지 않습니다.

    측정에 쓰이지 않는 빈 행이라 오염이라 부를 수 없습니다.
    """
    quarters = {"AAA": [분기("23 Q1"), 분기("23 Q1"), 분기("23 Q1")]}
    assert audit_data.duplicate_labels(quarters) == []


def test_repeated_ladder_values_are_listed():
    """같은 값이 세 번 이상 되풀이되면 셉니다 (실물 DELL·NRG)."""
    값들 = [1.30, 7.99, 7.99, 7.99, 1.32, 1.31]
    quarters = {"DELL": [분기(f"Q{i}", gaap_eps=v) for i, v in enumerate(값들)]}
    걸린 = audit_data.repeated_ladder_values(quarters)
    assert len(걸린) == 1 and 걸린[0]["값"] == 7.99 and 걸린[0]["횟수"] == 3, 걸린


def test_repeated_zeros_are_not_counted():
    """적자 직전 회사의 0.00 은 진짜로 되풀이되므로 세지 않습니다."""
    값들 = [0.0, 0.0, 0.0, 0.01, 0.02, 0.0]
    quarters = {"AAA": [분기(f"Q{i}", adj_eps=v) for i, v in enumerate(값들)]}
    assert audit_data.repeated_ladder_values(quarters) == []


def test_summary_counts_match_the_lists():
    """요약 숫자는 목록에서 세어야 합니다 — 따로 계산하면 어긋납니다."""
    quarters = {
        "VZ": [분기("23 Q1", adj_eps=v) for v in [1.18, 1.22, 5.18, 1.20, 1.21]],
        "DELL": [분기(f"Q{i}", gaap_eps=v)
                 for i, v in enumerate([1.30, 7.99, 7.99, 7.99, 1.32, 1.31])],
    }
    s = audit_data.summary(quarters)
    assert s["튄칸"] == len(audit_data.spike_cells(quarters))
    assert s["되풀이_묶음"] == len(audit_data.repeated_ladder_values(quarters))
    assert s["라벨겹침_종목"] == len(audit_data.duplicate_labels(quarters))
    assert s["전체종목"] == 2
    # 앞뒤 이웃이 있는 칸: VZ 5-2=3, DELL 6-2=4
    assert s["검사한칸"] == 7, s


def test_one_line_says_it_does_not_delete():
    """계기판 한 줄은 '지우지 않는다'는 사실을 반드시 말해야 합니다.

    이 문구가 빠지면 사용자가 '알아서 걸러진 값'으로 오해합니다.
    """
    quarters = {"AAA": [분기(f"Q{i}", adj_eps=v)
                        for i, v in enumerate([1.0, 1.1, 5.0, 1.0, 1.05])]}
    줄 = audit_data.one_line(quarters)
    assert "지우지 않고" in 줄, 줄
    assert "1개" in 줄 or "1개" in 줄.replace(" ", ""), 줄


def test_empty_material_does_not_crash():
    """재료가 비어도 무너지지 않고 '확인 못함'으로 말해야 합니다."""
    s = audit_data.summary({})
    assert s["튄칸"] == 0 and s["검사한칸"] == 0
    assert s["튄칸_비율"] is None
    assert "확인 못함" in audit_data.one_line({})


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
    print(f"\n재료 오염 조사 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
