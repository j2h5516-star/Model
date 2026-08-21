"""
test_web_build.py — 웹앱 데이터 빌더 검증 (132차)
=================================================

여기서 지키는 약속:
  · 웹앱은 **계기판과 같은 숫자**를 말한다 (같은 함수를 쓰므로 어긋날 수 없다)
  · 없는 값은 **null 로 나간다** — 0 이나 빈 글자로 바꾸지 않는다 (창작 금지)
  · 판정을 **만들지 않는다** — 판정 파일에 있는 것만 옮겨 적는다
  · 이동평균은 앞쪽 모자란 자리를 채우지 않는다 (없는 값 금지)

실행: python3 tests/test_web_build.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import judge  # noqa: E402
import web_build as wb  # noqa: E402


def test_이동평균은_모자란_앞자리를_채우지_않는다():
    """52주선은 52주가 모여야 처음 값이 생깁니다 — 그 전은 None."""
    값 = [float(i) for i in range(1, 6)]
    got = wb._ma(값, 3)
    assert got[0] is None and got[1] is None, got
    assert got[2] == 2.0 and got[3] == 3.0 and got[4] == 4.0, got


def test_없는_값은_null_로_나간다():
    assert wb._round(None) is None
    assert wb._round(1.23456, 2) == 1.23
    assert wb._round(0) == 0.0, "0 은 있는 값이므로 살아야 합니다"


def test_판정을_만들지_않고_옮겨_적는다():
    """빈 판정 파일이면 빈 목록. 있으면 그 판정 글자를 그대로."""
    assert wb.hypothesis_rows(None) == []
    assert wb.hypothesis_rows({"없음": 1}) == []
    verdict = {"가설": {
        "H2_신고점": {"판정": "미채택",
                    "신규(판정)": {"신호": {"n": 5, "rate": 12.0},
                                "기준선": {"n": 50, "rate": 13.0}}},
        "H18_완성시_52주선이격도": {"판정": "판정 불가", "등록일": "2026-08-15",
                             "신규(판정)": {"신호": {"n": 0, "rate": None},
                                         "기준선": {"n": 0, "rate": None}},
                             "탐색표본(참고)": {"신호": {"n": 407, "rate": 27.8}}},
    }}
    rows = wb.hypothesis_rows(verdict)
    이름들 = [r["이름"] for r in rows]
    # 판정 불가(대기)가 미채택보다 앞 — 기다리는 것이 눈에 띄어야 합니다
    assert 이름들 == ["H18_완성시_52주선이격도", "H2_신고점"], 이름들
    h18 = rows[0]
    assert h18["판정"] == "판정 불가" and h18["등록일"] == "2026-08-15"
    assert h18["신호율"] is None, "없는 적중률을 숫자로 바꿨습니다"
    assert h18["탐색n"] == 407 and h18["탐색율"] == 27.8
    assert rows[1]["신호율"] == 12.0 and rows[1]["기준선율"] == 13.0


def test_등록된_모든_가설이_웹앱에도_실린다():
    """판정 파일에 있는 가설은 하나도 빠지지 않고 웹앱으로 넘어가야 합니다
    (128차 점검기와 같은 안전망의 화면판)."""
    이름들 = ["H2_신고점", "H24_장세조건부_첫돌파", "H25b_런업단독",
            "H18b_완성이격도_1년"]
    verdict = {"가설": {n: {"판정": "판정 불가",
                          "신규(판정)": {"신호": {"n": 0}, "기준선": {"n": 0}}}
                     for n in 이름들}}
    rows = wb.hypothesis_rows(verdict)
    assert {r["이름"] for r in rows} == set(이름들), rows


def test_주봉_자르기는_최근분을_남긴다():
    prices = {"dates": [f"2020-01-{d:02d}" for d in range(1, 11)],
              "close": [float(i) for i in range(10)]}
    rows = wb._weekly(prices, weeks=2)
    assert len(rows) <= 2
    assert rows[-1][0] == "2020-01-10", rows
    assert wb._weekly({}, weeks=5) == [], "주가가 없으면 빈 목록"


def test_파일쓰기는_실제로_읽을_수_있는_JSON_이다():
    with tempfile.TemporaryDirectory() as tmp:
        payload = {"장세": {"폭": None}, "신호종목": []}
        written = wb.write_all(payload, {"AA": {"종목": "AA", "주봉": []}},
                               root=tmp, progress=lambda *a: None)
        assert len(written) == 2
        with open(os.path.join(tmp, wb.WEB_DIR, "app.json"), encoding="utf-8") as f:
            back = json.load(f)
        assert back["장세"]["폭"] is None, "null 이 사라졌습니다"
        with open(os.path.join(tmp, wb.TICKER_DIR, "AA.json"), encoding="utf-8") as f:
            assert json.load(f)["종목"] == "AA"


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
    print(f"\n웹앱 빌더 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
