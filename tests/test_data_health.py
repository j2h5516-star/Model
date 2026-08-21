"""
test_data_health.py — 수집물 건강검진 검증 (108차)
==================================================

실행: python3 tests/test_data_health.py

⚠️ 이 장치가 고장 나는 방향은 **전부 조용합니다.** 세는 눈이 멀면
아무 오류도 안 나고, 그저 "이상 없음"이라고 말합니다 — 2026-08-18 에
찾아낸 사고들이 전부 그 꼴이었습니다. 그래서 눈이 실제로 보는지를
못박습니다.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import data_health as dh  # noqa: E402


def _rows(ticker_rows):
    return ticker_rows


def test_통째로_빈_칸을_드러낸다():
    """91차 사고 — `gaap_eps_xbrl` 이 3,066행 **전부** None 인데 오류
    기록이 하나도 없었습니다. 어느 한 행도 이상하지 않으니 분기 단위
    검사로는 영영 안 걸립니다. **비율을 세야만** 드러납니다."""
    eps = {"AA": [{"revenue": 100.0, "gaap_eps_xbrl": None} for _ in range(50)]}
    c = dh.fill_rates(eps)
    assert c["행"] == 50
    assert c["revenue"]["비율"] == 100.0
    assert c["gaap_eps_xbrl"]["찬칸"] == 0, "통째로 빈 칸을 못 셌습니다"
    assert c["gaap_eps_xbrl"]["비율"] == 0.0


def test_그럴듯한_쓰레기를_자기_이력으로_잡는다():
    """106차 사고 — BAC 매출이 **8달러**(그 종목 중앙값 220억)였는데
    8달러는 그 자체로는 어떤 검사에도 안 걸리는 '말이 되는' 숫자입니다.
    한 종목 안에서 자기 이력과 맞대야만 드러납니다."""
    정상 = [{"revenue": 2.0e10, "announced_date": f"2025-0{i}-01"} for i in range(1, 9)]
    쓰레기 = {"revenue": 8.0, "announced_date": "2025-09-01"}
    o = dh.outliers({"BANK": 정상 + [쓰레기]}, fields=("revenue",))
    assert o["revenue"]["건수"] == 1, o["revenue"]
    assert o["revenue"]["예시"][0]["값"] == 8.0
    assert o["revenue"]["예시"][0]["종목"] == "BANK"


def test_그냥_적자는_쓰레기로_세지_않는다():
    """(136차 부호 결함) 처음 판은 `값 ÷ 중앙값` 을 그대로 봤습니다.
    음수 ÷ 양수 = 음수라 **적자 분기가 전부** 하한 아래로 떨어졌습니다.
    실측: 영업이익 경보 1,112칸 중 **524칸이 그냥 적자**. 오경보가 절반
    이면 계기를 아무도 안 믿게 되고, 진짜 결함이 소음에 묻힙니다."""
    흑자 = [{"op_income": 1.0e8, "announced_date": f"2025-0{i}-01"}
           for i in range(1, 9)]
    적자 = {"op_income": -9.0e7, "announced_date": "2025-09-01"}
    o = dh.outliers({"AA": 흑자 + [적자]}, fields=("op_income",))
    assert o["op_income"]["건수"] == 0, \
        f"평범한 적자를 쓰레기로 셌습니다: {o['op_income']}"


def test_적자라도_자릿수가_무너지면_잡는다():
    """부호를 무시한다고 해서 눈이 멀면 안 됩니다 — **크기**가 무너진
    값은 음수여도 잡아야 합니다 (실물 WDC 매출 -1달러)."""
    정상 = [{"revenue": 3.8e9, "announced_date": f"2025-0{i}-01"}
           for i in range(1, 9)]
    무너짐 = {"revenue": -1.0, "announced_date": "2025-09-01"}
    o = dh.outliers({"WDC": 정상 + [무너짐]}, fields=("revenue",))
    assert o["revenue"]["건수"] == 1, o["revenue"]
    assert o["revenue"]["예시"][0]["값"] == -1.0


def test_이상값을_모양별로_쪼개_적는다():
    """(136차) 총 건수만으로는 무엇이 망가졌는지 알 수 없었습니다.
    쪼개 보고서야 영업이익 666칸의 정체가 **단위 혼선**임이 드러났습니다."""
    정상 = [{"op_income": 1.0e8, "announced_date": f"2025-0{i}-01"}
           for i in range(1, 9)]
    섞임 = [{"op_income": 100.0, "announced_date": "2025-09-01"},   # 백만 단위
           {"op_income": 5.0e12, "announced_date": "2025-10-01"},  # 너무 큼
           {"op_income": 0.0, "announced_date": "2025-11-01"}]     # 정확히 0
    o = dh.outliers({"AA": 정상 + 섞임}, fields=("op_income",))["op_income"]
    assert o["건수"] == 3, o
    assert o["모양별"]["이력보다 1000배 이상 작음"] == 1, o["모양별"]
    assert o["모양별"]["이력보다 1000배 이상 큼"] == 1, o["모양별"]
    assert o["모양별"]["정확히 0"] == 1, o["모양별"]
    assert o["종목수"] == 1, o


def test_이력이_짧으면_세지_않는다():
    """중앙값이 의미 없을 만큼 이력이 짧으면 억지로 판정하지 않습니다
    (없는 것은 없는 채로)."""
    짧음 = [{"revenue": 2.0e10} for _ in range(3)] + [{"revenue": 8.0}]
    o = dh.outliers({"AA": 짧음}, fields=("revenue",))
    assert o["revenue"]["건수"] == 0


def test_어제_대비_바뀐_칸을_센다():
    """87·89·103차에 사람이 매번 손으로 하던 전수 대조를 로봇에게
    넘깁니다. 파서를 고친 날 수백 칸이 바뀌면 그 고침이 의도보다 넓게
    번진 것입니다."""
    어제 = {"AA": [{"announced_date": "2025-01-01", "revenue": 100.0, "adj_eps": 1.0},
                  {"announced_date": "2025-04-01", "revenue": 200.0, "adj_eps": 2.0}]}
    오늘 = {"AA": [{"announced_date": "2025-01-01", "revenue": 100.0, "adj_eps": 1.5},
                  {"announced_date": "2025-04-01", "revenue": 999.0, "adj_eps": 2.0},
                  {"announced_date": "2025-07-01", "revenue": 300.0, "adj_eps": 3.0}]}
    d = dh.changed_cells(어제, 오늘)
    assert d["맞대본 분기"] == 2
    assert d["바뀐 칸"] == 2, d          # adj_eps 하나 · revenue 하나
    assert d["새 분기"] == 1
    assert d["사라진 분기"] == 0
    assert d["많이 바뀐 종목"][0] == {"종목": "AA", "칸": 2}


def test_어제가_없으면_지어내지_않는다():
    """첫 실행에는 비교 대상이 없습니다. 없는 것을 만들지 않습니다."""
    r = dh.report({"AA": [{"revenue": 1.0}]}, None)
    assert r["어제 대비"] is None
    assert r["채움률"]["행"] == 1


def test_건강검진은_값을_바꾸지_않는다():
    """이 파일의 첫 원칙 — **막지 않고 센다.** 문턱을 지어내 값을
    버리면 89차(근거 없는 전망 가드)의 실수를 되풀이합니다."""
    import copy
    # ⚠️ 이상값이 **실제로 걸리는** 자료를 써야 합니다. 처음에는 전부
    #    같은 값(8.0)으로 썼는데, 그러면 중앙값도 8.0 이라 아무것도 안
    #    걸리고 **값을 건드리는 줄이 아예 실행되지 않아** 돌연변이가
    #    초록 불로 통과했습니다 (오늘 네 번째 가짜 초록불).
    eps = {"AA": [{"revenue": 2.0e10, "announced_date": f"2025-{i:02d}-01"}
                  for i in range(1, 10)]
                 + [{"revenue": 8.0, "announced_date": "2025-10-01"}]}
    원본 = copy.deepcopy(eps)
    결과 = dh.report(eps, None)
    assert 결과["이상값"]["revenue"]["건수"] == 1, (
        "이상값이 안 걸리면 이 시험은 아무것도 못 지킵니다"
    )
    assert eps == 원본, "건강검진이 값을 건드렸습니다 — 세기만 해야 합니다"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
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
    print(f"\n수집물 건강검진 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
