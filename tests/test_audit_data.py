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


def test_wanted_list_only_holds_rows_with_a_date():
    """발표일이 없는 행은 부탁 목록에 담지 않습니다.

    날짜가 없으면 로봇이 **어느 공시인지 짚을 수 없어** 아무 소용이 없고,
    자리만 차지합니다.
    """
    값들 = [1.18, 1.22, 5.18, 1.20, 1.21, 9.99, 1.19, 1.20]
    rows = [분기(f"Q{i}", adj_eps=v) for i, v in enumerate(값들)]
    rows[2]["announced_date"] = "2023-01-24"      # 5.18 — 날짜 있음
    rows[5]["announced_date"] = None              # 9.99 — 날짜 없음
    목록 = audit_data.wanted_raw_filings({"VZ": rows})
    assert [r["발표일"] for r in 목록] == ["2023-01-24"], 목록
    assert 목록[0]["종목"] == "VZ" and 목록[0]["값"] == 5.18


def test_wanted_list_is_capped():
    """부탁 목록에는 상한이 있습니다 — 로봇 보관 자리가 유한합니다.

    78차부터 목록을 두 이유(튐 / 매출총이익률 폭)가 **나눠 씁니다**.
    한쪽만 있는 재료에서는 그쪽 몫(상한의 절반)까지만 담깁니다.
    """
    quarters = {}
    for t in range(20):
        값들 = [1.0, 1.0, 9.0, 1.0, 1.0, 9.0, 1.0, 1.0]
        rows = [분기(f"Q{i}", adj_eps=v, announced_date=f"2023-01-{i + 1:02d}")
                for i, v in enumerate(값들)]
        quarters[f"T{t}"] = rows
    담김 = audit_data.wanted_raw_filings(quarters, limit=10)
    assert len(담김) == 5, f"튐만 있는 재료에서는 절반(5)까지: {len(담김)}"


def test_writing_the_wanted_list_does_not_change_the_data():
    """목록을 적는 것도 재료를 읽기만 합니다."""
    import json
    import tempfile

    값들 = [1.18, 1.22, 5.18, 1.20, 1.21]
    rows = [분기(f"Q{i}", adj_eps=v, announced_date=f"2023-0{i + 1}-01")
            for i, v in enumerate(값들)]
    quarters = {"VZ": rows}
    이전 = [dict(r) for r in rows]

    경로 = tempfile.mktemp(suffix=".json")
    개수 = audit_data.write_wanted(quarters, 경로)
    assert 개수 == 1
    with open(경로, encoding="utf-8") as f:
        적힌것 = json.load(f)
    assert 적힌것["목록"][0]["종목"] == "VZ"
    assert "지우지 않습니다" in 적힌것["설명"]
    assert quarters["VZ"] == 이전, "목록을 적으면서 재료를 바꿨습니다"


def test_wide_gross_margin_swing_is_flagged_from_both_ends():
    """매출총이익률 폭이 넓으면 **양 끝을 둘 다** 부탁합니다 (78차).

    ⚠️ 처음엔 "종목 중앙값에서 멀면 이상치"로 재려 했는데 **거꾸로**
    나왔습니다 — AMBA 는 틀린 값(2% 대)이 더 많아 중앙값이 3.9% 가 되고,
    그러면 진짜 값(63%)이 이상치로 찍힙니다. 어느 쪽이 옳은지 우리는
    모릅니다. 그래서 폭만 말하고 양 끝을 둘 다 담습니다.
    """
    값들 = [63.3, 63.0, 2.0, 2.0, 61.2, 61.1, 1.4]     # 실물 AMBA 모양
    rows = [분기(f"Q{i}", gross_margin_pct=v,
                announced_date=f"2025-0{i % 9 + 1}-01")
            for i, v in enumerate(값들)]
    걸린 = audit_data.gross_margin_unstable({"AMBA": rows})
    assert len(걸린) == 2, 걸린
    쪽 = {r["쪽"]: r["값"] for r in 걸린}
    assert 쪽["가장 낮음"] == 1.4 and 쪽["가장 높음"] == 63.3, 쪽
    # 틀린 쪽이 다수라도 진짜 값을 "이상치"라 부르지 않습니다
    assert all(r["폭"] > 60 for r in 걸린), 걸린


def test_steady_gross_margin_is_not_flagged():
    """분기마다 몇 %p 안에서 움직이는 정상 종목은 걸리지 않습니다."""
    값들 = [58.4, 59.2, 59.8, 60.7, 60.1, 59.5]
    rows = [분기(f"Q{i}", gross_margin_pct=v) for i, v in enumerate(값들)]
    assert audit_data.gross_margin_unstable({"AAA": rows}) == []


def test_wanted_list_mixes_both_reasons():
    """부탁 목록에는 두 가지 이유가 **섞여** 담겨야 합니다.

    한쪽이 목록을 다 차지하면 다른 쪽 원인을 영영 못 봅니다.
    """
    튐 = [분기(f"Q{i}", adj_eps=v, announced_date=f"2024-0{i+1}-01")
         for i, v in enumerate([1.0, 1.1, 9.0, 1.0, 1.05, 1.02])]
    폭 = [분기(f"Q{i}", gross_margin_pct=v, announced_date=f"2025-0{i+1}-01")
         for i, v in enumerate([63.0, 2.0, 61.0, 1.5, 62.0, 2.2])]
    목록 = audit_data.wanted_raw_filings({"AAA": 튐, "BBB": 폭}, limit=10)
    이유들 = {("매출총이익률" if "매출총이익률" in r["이유"] else "튐")
            for r in 목록}
    assert 이유들 == {"튐", "매출총이익률"}, 목록


def test_empty_material_does_not_crash():
    """재료가 비어도 무너지지 않고 '확인 못함'으로 말해야 합니다."""
    s = audit_data.summary({})
    assert s["튄칸"] == 0 and s["검사한칸"] == 0
    assert s["튄칸_비율"] is None
    assert "확인 못함" in audit_data.one_line({})


def test_write_wanted_actually_puts_the_outside_ruler_in_front():
    """합치는 규칙만 맞고 **부르는 쪽이 순서를 뒤집으면** 소용없습니다.

    82차에 겪은 일 — 규칙은 옳은데 배선이 틀려도 시험이 초록이었습니다.
    그래서 여기서는 규칙이 아니라 `write_wanted` 가 실제로 적어 낸
    **파일 내용**을 봅니다.
    """
    import json
    import tempfile

    값들 = [1.18, 1.22, 5.18, 1.20, 1.21]
    rows = [분기(f"Q{i}", adj_eps=v, announced_date=f"2023-0{i + 1}-01")
            for i, v in enumerate(값들)]
    바깥 = [{"종목": "CRM", "발표일": "2025-09-03", "이유": "야후는 1.96"}]

    경로 = tempfile.mktemp(suffix=".json")
    개수 = audit_data.write_wanted({"VZ": rows}, 경로, extra=바깥)
    with open(경로, encoding="utf-8") as f:
        목록 = json.load(f)["목록"]
    assert 개수 == 2
    assert [r["종목"] for r in 목록] == ["CRM", "VZ"], "바깥 자가 앞자리여야 합니다"


def test_merge_wanted_keeps_the_outside_ruler_first_and_drops_repeats():
    """두 곳에서 온 부탁을 합칠 때 바깥 자(야후)를 앞자리에 둡니다 (86차).

    같은 공시가 양쪽에 있으면 한 번만 남깁니다 — 로봇은 어차피 한 번만
    담아 오는데, 두 줄이 목록 자리를 두 칸 먹으면 다른 공시가 밀립니다.
    """
    야후 = [{"종목": "CRM", "발표일": "2025-09-03", "이유": "야후는 1.96"}]
    조사 = [{"종목": "CRM", "발표일": "2025-09-03", "이유": "이웃보다 튐"},
          {"종목": "AMBA", "발표일": "2026-02-26", "이유": "이웃보다 튐"}]
    합 = audit_data.merge_wanted(야후, 조사)
    assert [r["종목"] for r in 합] == ["CRM", "AMBA"]
    assert 합[0]["이유"] == "야후는 1.96"          # 야후 쪽이 살아남는다


def test_merge_wanted_drops_rows_that_cannot_point_at_a_filing():
    """종목이나 발표일이 비면 어느 공시인지 짚을 수 없어 버립니다."""
    합 = audit_data.merge_wanted([
        {"종목": "AAA", "발표일": None},
        {"종목": None, "발표일": "2026-01-01"},
        {"종목": "BBB", "발표일": "2026-01-01"},
    ])
    assert [r["종목"] for r in 합] == ["BBB"]


def test_merge_wanted_respects_the_limit():
    """목록이 무한정 길어지지 않아야 합니다 (로봇 보관 자리는 유한)."""
    많이 = [{"종목": f"T{i}", "발표일": "2026-01-01"} for i in range(100)]
    assert len(audit_data.merge_wanted(많이, limit=7)) == 7




def test_부탁목록_갱신은_바깥자_재료를_앞에_두고_실패해도_안멈춘다():
    """(134차) 로봇이 매일 부르는 자리. 야후 재료가 있으면 그 칸이 앞에
    오고, 재료 만들기가 실패해도 목록 자체는 적혀야 합니다 — 수집이
    부탁 목록 때문에 멈추면 안 됩니다."""
    import json
    import tempfile

    quarters = {"AA": [
        {"announced_date": f"2025-0{i}-01", "period_label": f"25/0{i}",
         "adj_eps": 1.0, "revenue": 1000.0} for i in range(1, 5)
    ]}
    vendor = {"tickers": {"AA": {"announcements": [
        {"announced_date": "2025-01-01", "날짜뜻": "발표일",
         "street_eps": 9.99, "street_estimate": 9.99},
    ]}}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        path = f.name
    n = audit_data.refresh_wanted(quarters, vendor, path=path,
                                  progress=lambda *a: None)
    assert n >= 1, "목록이 비었습니다"
    with open(path, encoding="utf-8") as f:
        목록 = json.load(f)["목록"]
    assert any(r["종목"] == "AA" for r in 목록), 목록

    # 야후 파일이 깨져 있어도 (재료 실패) 목록은 적힌다
    말 = []
    n2 = audit_data.refresh_wanted(quarters, {"tickers": "깨짐"}, path=path,
                                   progress=말.append)
    assert n2 >= 0, "재료 실패가 갱신을 멈췄습니다"
    assert any("실패" in m for m in 말), 말

    # 재료가 아예 없어도(None) 조용히 목록만 적는다
    audit_data.refresh_wanted(quarters, None, path=path,
                              progress=lambda *a: None)
    os.unlink(path)


def test_로봇이_부탁목록을_매일_갱신한다():
    """(134차) 배선이 빠지면 부탁 목록이 **사람 손에만 매입니다** — 실제로
    4차 확장의 새 90종목이 한 건도 못 들어갔던 사고(133차)의 재발 방지."""
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "collect_job.py"), encoding="utf-8") as f:
        text = f.read()
    assert "audit_data.refresh_wanted(" in text, \
        "로봇이 부탁 목록을 갱신하지 않습니다"


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
