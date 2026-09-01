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


def test_데이터가_마른_새_종목이_화면을_깨뜨리지_않는다():
    """(150차-E) 유니버스를 250 → 401 로 늘렸다. 새 종목 151개는 내일
    처음 수집되는데, 그중에는 **분기가 몇 개 없거나 주가가 짧은** 종목이
    반드시 섞인다. 거기서 화면 만들기가 죽으면 그날 하루를 통째로 잃는다.

    실제로 일어날 여섯 가지 마른 상태를 만들어 통과하는지 본다.
    값을 지어내지 않고 **없으면 없다고** 나와야 한다.
    """
    import dataset

    # SPY 격자: 3년치 주 단위면 52주선까지 잰다
    from datetime import date, timedelta
    d = date(2023, 1, 6)
    날들, 종가 = [], []
    for i in range(160):
        날들.append(d.isoformat())
        종가.append(100.0 + i)
        d += timedelta(days=7)

    def eps행(n):
        return [{"filing_date": f"20{23 + i // 4}-{1 + i % 4 * 3:02d}-15",
                 "announced_date": f"20{23 + i // 4}-{2 + i % 4 * 3:02d}-15",
                 "period_label": f"Q{i}", "revenue": 1e9, "op_income": 1e8,
                 "adj_eps": 1.0 + i * 0.1, "adjusted_ebitda": None,
                 "gaap_eps": 0.9 + i * 0.1, "gross_margin_pct": 50.0,
                 "gaap_eps_xbrl": None, "revenue_xbrl": None,
                 "gross_margin_pct_xbrl": None}
                for i in range(n)]

    상태 = [("빈종목", 0, 0), ("한분기", 1, 0), ("주가없음", 20, 0),
          ("주가1일", 20, 1), ("주가짧음", 20, 5), ("정상새것", 20, 160)]
    snap = {"benchmark": "SPY", "tickers": ["SPY"],
            "eps": {"SPY": []},
            "prices": {"SPY": {"dates": 날들, "close": 종가}}}
    for 이름, q, p in 상태:
        snap["tickers"].append(이름)
        snap["eps"][이름] = eps행(q)
        if p:
            snap["prices"][이름] = {"dates": 날들[-p:], "close": 종가[-p:]}

    ds = dataset.build(snap)
    payload = wb.build_payload(ds, None, {"ran_at": "시험"})

    # 죽지 않는 것만으로는 모자랍니다 — 없는 값이 0 으로 둔갑하면 안 됩니다.
    assert isinstance(payload["신호종목"], list)
    assert payload["잣대차이"]["종목수"] >= 0
    assert payload["가설"] == [], "판정 파일이 없는데 가설을 지어냈습니다"

    # 주가가 없거나 너무 짧은 종목은 정배열을 **판단하지 않아야** 합니다
    유지 = {r["종목"] for r in payload["정배열유지"]}
    for 이름 in ("빈종목", "한분기", "주가없음", "주가1일", "주가짧음"):
        assert 이름 not in 유지, f"{이름} 은 판단할 수 없는데 정배열로 셌습니다"

    # 종목별 파일도 만들어져야 합니다 (여기서 죽으면 종목 탭이 빕니다)
    import sector_model as sm
    완성 = sm.completion_events(ds)
    for 이름, _, _ in 상태:
        한종목 = wb.build_ticker(ds, 이름, 완성)
        assert 한종목["종목"] == 이름
        # 못 잰 값은 null 이어야 합니다 — 0 으로 둔갑하면 거짓말입니다
        if 이름 in ("빈종목", "한분기"):
            assert 한종목["잣대"] is None, 한종목
            assert 한종목["실적"] == [], 한종목
        if 이름 in ("주가없음",):
            assert 한종목["이격도"] is None and 한종목["주봉"] == [], 한종목


def test_채택까지_남은_거리가_웹앱에도_실린다():
    """(150차-C) 139·140차에 만든 "채택까지 얼마나 남았나"가 **계기판에만**
    있었습니다. 주인은 휴대폰만 쓰므로 정작 보는 화면(웹앱)에는 이 정직화
    정보가 안 닿고 있었습니다.

    판정을 새로 만들지 않고 judge 가 이미 계산한 것을 옮겨 적기만 합니다.
    """
    verdict = {"가설": {
        # 폭등률이 기준선 상한보다 높아 표본만 더 모으면 되는 가설
        "H_거의": {"판정": "미채택", "등록일": "2026-08-01",
                 "신규(판정)": {
                     # 하한 18.0 < 기준선 상한 23.0 이라 아직 완전 분리가
                     # 아니지만, 폭등률 40%는 기준선 상한보다 높으므로
                     # 표본만 더 모으면 넘습니다.
                     "신호": {"n": 40, "rate": 40.0, "ci": [18.0, 55.0]},
                     "기준선": {"n": 500, "rate": 20.0, "ci": [17.0, 23.0]}}},
        # 폭등률이 기준선보다 낮아 표본으로는 못 넘는 가설
        "H_무망": {"판정": "미채택", "등록일": "2026-08-01",
                 "신규(판정)": {
                     "신호": {"n": 40, "rate": 5.0, "ci": [1.0, 15.0]},
                     "기준선": {"n": 500, "rate": 20.0, "ci": [17.0, 23.0]}}},
        # 아직 표본이 하나도 없는 가설
        "H_대기": {"판정": "판정 불가", "등록일": "2026-08-21",
                 "신규(판정)": {"신호": {"n": 0}, "기준선": {}}},
    }}
    행 = {r["이름"]: r for r in wb.hypothesis_rows(verdict)}
    assert set(행) == {"H_거의", "H_무망", "H_대기"}, list(행)

    거의 = 행["H_거의"]
    assert 거의["채택거리"] and "표본" in 거의["채택거리"], 거의
    assert isinstance(거의["필요표본"], int) and 거의["필요표본"] > 0, 거의

    무망 = 행["H_무망"]
    assert 무망["채택거리"] == "지금 폭등률로는 표본을 더 모아도 못 넘음", 무망
    assert 무망["필요표본"] is None, "없는 값을 만들었습니다"

    assert 행["H_대기"]["채택거리"] == "표본 없음", 행["H_대기"]

    # 계기판과 같은 함수를 쓰는가 (제 손으로 다시 계산하면 두 화면이 갈립니다)
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "web_build.py"), encoding="utf-8") as f:
        assert "judge.adoption_distance(" in f.read(), \
            "웹앱이 채택 거리를 스스로 계산하고 있습니다 — 계기판과 갈립니다"
    # 화면이 실제로 그리는가 (값만 담고 안 그리면 주인은 못 봅니다)
    with open(os.path.join(root, "docs", "app.js"), encoding="utf-8") as f:
        assert "채택거리" in f.read(), "웹앱 화면이 채택 거리를 그리지 않습니다"


def test_델타_흐름이_종목화면에_실린다():
    """(150차-L, 주인 요청) "델타 상승/하락의 흐름을 보게 그래프를 추가하자."

    주인이 CRDO 에서 본 것 — **델타는 계속 상승인데 TTM 은 빈칸**이었습니다.
    둘은 다른 것을 잽니다: 델타는 **분기 대 분기**, TTM 은 **네 분기 합**.
    한 분기가 통째로 빠지면 델타는 나오는데 TTM 만 못 만듭니다.

    값을 못 잰 분기는 **그리지 않습니다** — 0 으로 그리면 "이익이 0"으로
    읽혀 거짓이 됩니다.
    """
    import dataset
    import sector_model as sm

    from datetime import date, timedelta
    d = date(2023, 1, 6)
    날들, 종가 = [], []
    for i in range(160):
        날들.append(d.isoformat()); 종가.append(100.0 + i)
        d += timedelta(days=7)

    # 91일 간격으로 또박또박 오르는 조정 EPS 12분기
    행 = []
    나 = date(2023, 2, 15)
    for i in range(12):
        # ⚠️ 이 저장소에서 **분기끝 노릇을 하는 칸은 `filing_date`** 입니다
        #    (138차 상식 검사가 그것을 씁니다 — 발표일과 10~70일 차이여야
        #    합니다). 처음에 filing_date 를 발표일과 같게 뒀더니 지연 0일로
        #    걸려 발표일이 통째로 지워지고 델타가 0건이었습니다.
        행.append({"filing_date": (나 - timedelta(days=31)).isoformat(),
                   "announced_date": 나.isoformat(),
                   "period_end": (나 - timedelta(days=31)).isoformat(),
                   "period_label": f"Q{i}", "revenue": 1e9, "op_income": 1e8,
                   "adj_eps": 1.0 + i * 0.1, "adjusted_ebitda": None,
                   "gaap_eps": 0.9, "gross_margin_pct": 50.0,
                   "gaap_eps_xbrl": None, "revenue_xbrl": None,
                   "gross_margin_pct_xbrl": None})
        나 += timedelta(days=91)
    snap = {"benchmark": "SPY", "tickers": ["SPY", "UP"],
            "eps": {"SPY": [], "UP": 행},
            "prices": {"SPY": {"dates": 날들, "close": 종가},
                       "UP": {"dates": 날들, "close": 종가}}}
    ds = dataset.build(snap)
    완성 = sm.completion_events(ds)
    한종목 = wb.build_ticker(ds, "UP", 완성)

    흐름 = 한종목["델타흐름"]
    assert 흐름, "델타 흐름이 종목 자료에 없습니다 — 그래프를 그릴 수 없습니다"
    assert all("성장률" in x and "가속" in x for x in 흐름), (
        "성장률·가속이 없습니다 — 값의 방향만 보면 1→10→20 이 셋 다 "
        f"'상승'으로 보입니다: {흐름[0]}")
    assert all(x["상승"] for x in 흐름), f"또박또박 오르는데 하락이 섞였습니다: {흐름}"
    assert all(x["값"] is not None for x in 흐름), 흐름
    # 계기판과 같은 함수를 쓰는가 (스스로 세면 두 화면이 갈립니다)
    assert [x["발표일"] for x in 흐름] == [d for d, _ in sm._delta_series(ds, "UP")]

    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "web_build.py"), encoding="utf-8") as f:
        assert "sm._delta_series(" in f.read(), \
            "웹앱이 델타를 스스로 계산합니다 — 계기판과 갈립니다"
    with open(os.path.join(root, "docs", "app.js"), encoding="utf-8") as f:
        js = f.read()
    # ⚠️ **글자가 어딘가 있기만 하면 통과하는 검사는 헛돕니다.**
    #    150차-J 에서 한 번 겪고 여기서 또 했습니다 — `델타막대(` 는 함수
    #    **정의**에도 있어서 호출을 지워도 통과했고, `x.값 !== null` 은
    #    다른 줄에도 있어서 0 채움 돌연변이가 통과했습니다.
    #    그래서 **호출 지점**과 **함수 몸통**을 각각 콕 집습니다.
    assert "html += 델타막대(델타)" in js, \
        "화면이 델타 그래프를 실제로 그리지 않습니다 (호출이 없습니다)"
    assert "d.델타흐름" in js, "화면이 델타 자료를 읽지 않습니다"
    몸통 = js.split("function 델타막대(", 1)
    assert len(몸통) == 2, "델타막대 함수가 없습니다"
    몸통 = 몸통[1].split("\nfunction ", 1)[0]
    assert "filter" in 몸통 and "!== null" in 몸통, (
        "델타막대가 값 없는 분기를 걸러내지 않습니다 — 0 으로 그려져 "
        f"'이익이 0'으로 읽힙니다: {몸통[:200]}")


def test_끊긴_구간을_건너뛴_폭에_표시를_단다():
    """(150차-P) 주인이 CRDO 에서 "폭 3700%"를 봤습니다.

    직전 정점 0.09(2024-05-29)와 지금 3.42(2026-06-01) 사이 **2년의 TTM 이
    전부 없어서**, 여러 분기에 걸쳐 오른 것이 한 번에 뛴 것처럼 계산된
    값입니다. 산수는 맞지만 그대로 읽으면 거짓입니다 — 인수인계 4장 7번
    "끊긴 이력을 이어붙이지 마라"와 같은 병입니다.

    ⚠️ `신고점폭` **계산은 고치지 않습니다** — H22·H22b 의 사전 등록된
    신호값입니다(헌법 8조). 화면 표시만 답니다.
    """
    import dataset
    import sector_model as sm
    from datetime import date, timedelta

    d = date(2023, 1, 6); 날들, 종가 = [], []
    for i in range(200):
        날들.append(d.isoformat()); 종가.append(100.0 + i); d += timedelta(days=7)

    행 = []
    나 = date(2023, 2, 15)
    # 앞: 또박또박 8분기 (정점을 만든다)
    for i in range(8):
        행.append({"filing_date": (나 - timedelta(days=31)).isoformat(),
                   "announced_date": 나.isoformat(),
                   "period_end": (나 - timedelta(days=31)).isoformat(),
                   "period_label": f"A{i}", "revenue": 1e9 + i, "op_income": 1e8,
                   "adj_eps": 1.0, "adjusted_ebitda": None, "gaap_eps": 1.0,
                   "gross_margin_pct": 50.0, "gaap_eps_xbrl": None,
                   "revenue_xbrl": None, "gross_margin_pct_xbrl": None})
        나 += timedelta(days=91)
    # 여기서 **두 해를 건너뛴다** (분기가 통째로 빠진 모양)
    나 += timedelta(days=730)
    # ⚠️ 값을 5.0 → 6.0 처럼 급하게 올리면 **YTD 오염 검사가 지웁니다**
    #    ("직전 4분기 합과 거의 같고 중앙값의 N배" → 누적값으로 보고 버림).
    #    한 칸이 지워지면 구간이 또 쪼개져 TTM 이 아예 안 생깁니다.
    #    두 번 걸렸습니다 — 앞 구간 값이 너무 작으면 끊김 뒤 "직전 4분기
    #    합"이 새 값과 비슷해져 또 걸립니다. 앞 1.0 · 뒤 2.0~2.8 로 둡니다.
    for i in range(5):
        행.append({"filing_date": (나 - timedelta(days=31)).isoformat(),
                   "announced_date": 나.isoformat(),
                   "period_end": (나 - timedelta(days=31)).isoformat(),
                   "period_label": f"B{i}", "revenue": 2e9 + i, "op_income": 2e8,
                   "adj_eps": 2.0 + i * 0.2, "adjusted_ebitda": None,
                   "gaap_eps": 2.0 + i * 0.2,
                   "gross_margin_pct": 50.0, "gaap_eps_xbrl": None,
                   "revenue_xbrl": None, "gross_margin_pct_xbrl": None})
        나 += timedelta(days=91)

    snap = {"benchmark": "SPY", "tickers": ["SPY", "GAP"],
            "eps": {"SPY": [], "GAP": 행},
            "prices": {"SPY": {"dates": 날들, "close": 종가},
                       "GAP": {"dates": 날들, "close": 종가}}}
    ds = dataset.build(snap)
    한종목 = wb.build_ticker(ds, "GAP", sm.completion_events(ds))

    폭있는 = [s for s in 한종목["실적"] if s["신고점폭"] is not None]
    assert 폭있는, "신고점폭이 하나도 안 잡혔습니다 — 시험 데이터가 잘못됐습니다"
    assert any(s["끊김너머"] for s in 폭있는), (
        f"끊긴 구간을 건너뛴 폭에 표시가 안 붙었습니다: {폭있는}")

    # 분기가 통째로 빠진 자리를 화면 자료가 알고 있는가
    구간 = 한종목["빠진구간"]
    assert 구간, "빠진 분기 구간을 못 찾았습니다"
    assert any(g["일수"] > 400 for g in 구간), 구간

    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "docs", "app.js"), encoding="utf-8") as f:
        js = f.read()
    # 글자만 있으면 헛돕니다(150차-J·L) — **표에 실제로 붙는지** 봅니다
    assert "s.끊김너머 ?" in js, "표가 끊김 표시를 조건으로 안 씁니다"
    assert "d.빠진구간" in js, "화면이 빠진 분기를 말하지 않습니다"
    assert "빠진 구간을 건너뛰어" in js, "설명 문구가 없습니다"


def test_값이_올라도_성장이_꺾이면_둔화로_표시한다():
    """(150차-M, 주인 지적) "1-10-20 이면 올라갔다 내려가야지 — 10배에서
    2배가 된 거니까."

    값의 **방향**만 보면 1 → 10 → 20 이 셋 다 "상승"입니다. 하지만
    성장은 **+900% → +100%** 로 반토막입니다. 실물 CRDO 도 값은 계속
    오르는데 성장률이 257% → 168% → 60% → 8% 로 뚜렷이 둔화 중입니다.

    ⚠️ `상승`(방향)은 **사전 등록된 델타 정의**라 건드리지 않습니다
    (H19·H21 등이 씁니다). 성장률·가속은 **화면 표시**일 뿐입니다.
    """
    import dataset
    from datetime import date, timedelta

    d = date(2023, 1, 6); 날들, 종가 = [], []
    for i in range(160):
        날들.append(d.isoformat()); 종가.append(100.0 + i); d += timedelta(days=7)

    값들 = [1.0, 10.0, 20.0] + [20.0 + i for i in range(1, 10)]
    행 = []; 나 = date(2023, 2, 15)
    for i, v in enumerate(값들):
        행.append({"filing_date": (나 - timedelta(days=31)).isoformat(),
                   "announced_date": 나.isoformat(),
                   "period_end": (나 - timedelta(days=31)).isoformat(),
                   "period_label": f"Q{i}", "revenue": 1e9 + i, "op_income": 1e8,
                   "adj_eps": v, "adjusted_ebitda": None, "gaap_eps": 0.9,
                   "gross_margin_pct": 50.0, "gaap_eps_xbrl": None,
                   "revenue_xbrl": None, "gross_margin_pct_xbrl": None})
        나 += timedelta(days=91)
    snap = {"benchmark": "SPY", "tickers": ["SPY", "SLOW"],
            "eps": {"SPY": [], "SLOW": 행},
            "prices": {"SPY": {"dates": 날들, "close": 종가},
                       "SLOW": {"dates": 날들, "close": 종가}}}
    ds = dataset.build(snap)
    import sector_model as sm
    한종목 = wb.build_ticker(ds, "SLOW", sm.completion_events(ds))
    표 = {x["발표일"]: x for x in 한종목["델타흐름"]}
    행날 = [r["announced_date"] for r in ds["quarters"]["SLOW"]]

    열 = 표.get(행날[1])       # 1 → 10
    스물 = 표.get(행날[2])     # 10 → 20
    assert 열 and 스물, (행날[:3], list(표))
    assert 열["상승"] is True and 스물["상승"] is True, "둘 다 값은 올랐습니다"
    assert 열["성장률"] == 900.0, 열
    assert 스물["성장률"] == 100.0, 스물
    # 여기가 주인이 말한 "올라갔다 내려가야지" — 성장이 꺾였음
    assert 스물["가속"] is False, (
        f"1→10→20 인데 둔화로 표시되지 않았습니다: {스물}")

    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "docs", "app.js"), encoding="utf-8") as f:
        js = f.read()
    몸통 = js.split("function 델타막대(", 1)[1].split("\nfunction ", 1)[0]
    assert "x.가속" in 몸통, "그래프가 가속/둔화를 색으로 나누지 않습니다"
    assert "성장률 흐름" in js, "화면이 성장률 흐름을 적지 않습니다"


def test_표본이_0인_가설은_언제_나올_수_있는지_말한다():
    """(150차-J) 웹앱 검증 탭에서 11개 가설이 전부 "표본 없음"이라고만
    말하고 있었습니다. 주인이 보면 시스템이 멈춘 줄 압니다.

    실제 이유는 구조적입니다 — 표적 창이 60거래일이라 2026-08-15 에
    등록한 가설은 **2026-11-07 이전에 판정이 나올 수가 없습니다**.
    140차에 `judge.first_verdict_floor` 로 계산해 두었는데 계기판에만
    있고 웹앱에는 없었습니다(150차-C 와 같은 병).

    날짜를 지어내지 않습니다 — 등록일 상수를 못 찾은 가설은 없음(null).
    """
    import judge

    이름 = judge.H22_LEVELS[0][0]          # 등록일 상수가 있는 가설
    verdict = {"가설": {
        이름: {"판정": "판정 불가",
              "신규(판정)": {"신호": {"n": 0}, "기준선": {}}},
        "H_모르는가설": {"판정": "판정 불가",
                    "신규(판정)": {"신호": {"n": 0}, "기준선": {}}},
    }}
    행 = {r["이름"]: r for r in wb.hypothesis_rows(verdict)}

    있는것 = 행[이름]
    assert 있는것["가장이른날"], f"등록일이 있는 가설인데 날짜가 없습니다: {있는것}"
    assert 있는것["창_거래일"], 있는것
    # judge 가 계산한 것과 같아야 합니다 (웹앱이 스스로 세면 갈립니다)
    바닥 = {d["가설"]: d for d in judge.first_verdict_floor(verdict)}
    assert 있는것["가장이른날"] == 바닥[이름]["가장이른날"], 있는것

    없는것 = 행["H_모르는가설"]
    assert 없는것["가장이른날"] is None, "모르는 가설의 날짜를 지어냈습니다"

    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "web_build.py"), encoding="utf-8") as f:
        assert "judge.first_verdict_floor(" in f.read(), \
            "웹앱이 첫 표본 시점을 스스로 계산하고 있습니다 — 계기판과 갈립니다"
    with open(os.path.join(root, "docs", "app.js"), encoding="utf-8") as f:
        js = f.read()
    # ⚠️ 글자만 찾으면 헛돕니다 — `h.가장이른날 ?` 를 `false ?` 로 바꾸는
    #    돌연변이가 통과했습니다(문자열은 esc(...) 쪽에 남아 있었으므로).
    #    **값을 실제로 조건으로 쓰는가**를 봅니다.
    assert "h.가장이른날 ?" in js, \
        "웹앱 화면이 첫 표본 시점을 조건으로 쓰지 않습니다 — 안 그려집니다"
    assert "이전에는 나올 수 없습니다" in js, "설명 문구가 없습니다"
    assert "|| 60" not in js, "화면이 창 길이를 60으로 지어냅니다"


def test_종목의_신호는_가장_최근_완성으로_판단한다():
    """(150차) 주인 지적 — "제타는 정배열인데 왜 화면에 없지?"

    실측한 결함: 웹앱의 ZETA 가 **신호 False** 로 나가고 있었습니다.
    이격도 49.4%에 08-14 완성의 이격도가 50.7%인데도 그랬습니다.
    `{r["종목"]: r for r in recent_completion_rows(...)}` 이 한 종목당
    한 줄을 **가정**했는데 실제로는 여러 줄이 와서, 뒤에 온 **낡은**
    완성(06-26, 이격도 3.2%)이 최신 완성을 덮어썼습니다.

    화면에 신호를 띄울 때 낡은 값을 쓰면 정직화 원칙이 깨집니다.
    """
    import app

    완성사건 = [
        {"ticker": "ZETA", "day": "2026-06-26", "이격도": 3.2,
         "델타": False, "초과60": None, "초과250": None},
        {"ticker": "ZETA", "day": "2026-08-14", "이격도": 50.7,
         "델타": True, "초과60": None, "초과250": None},
    ]
    최근 = {r["종목"]: r
          for r in app.recent_completion_rows(완성사건, "2026-08-21")}
    assert 최근["ZETA"]["신호"] is True, (
        "낡은 완성이 최신 완성을 덮어 신호가 꺼졌습니다: "
        f"{최근['ZETA']}")
    assert 최근["ZETA"]["완성일"] == "2026-08-14", 최근["ZETA"]

    # 이 줄이 실제로 웹앱 배선에 쓰이는지 (배선이 빠지면 시험이 헛돕니다)
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "web_build.py"), encoding="utf-8") as f:
        text = f.read()
    assert 'app.recent_completion_rows(' in text, \
        "웹앱이 계기판과 같은 함수를 쓰지 않습니다"


def test_웹앱_경로는_전부_상대경로다():
    """웹앱은 `/Model/` **하위**에 배포됩니다 — 절대경로를 쓰면 404 입니다.

    왜 시험으로 막는가 (150차-R):
      주소는 `https://j2h5516-star.github.io/Model/` 입니다. 사이트 뿌리가
      아니라 **한 칸 아래**입니다. 그래서 `/data/app.json` 처럼 슬래시로
      시작하는 경로를 쓰면 브라우저가 `github.io/data/app.json` 을 찾아
      **아무것도 안 나옵니다.** 지금은 전부 상대경로지만, 누가 나중에
      절대경로를 하나만 넣어도 화면이 통째로 빕니다.

      개발 환경은 `github.io` 가 조직 정책으로 막혀 있어(403) **세션이
      사이트를 직접 열어 확인할 수 없습니다.** 사람이 못 보는 자리는
      기계가 막아야 합니다.

    허용하는 것: `"/"`(구분자) · `/>`(SVG 닫기) · `//`(프로토콜 생략) ·
    `#/home`(해시 주소) · `data:`·`https:` 로 시작하는 것.
    막는 것: 슬래시 **하나**로 시작해 글자·숫자가 이어지는 경로.
    """
    import re

    root = os.path.join(os.path.dirname(__file__), "..", "docs")
    # 슬래시 하나 + 글자/숫자 — 이것만이 "사이트 뿌리부터"라는 뜻입니다
    문자열속 = re.compile(r"""["'`]/(?![/])[A-Za-z0-9_.-]""")
    태그속 = re.compile(r"""\b(?:src|href)\s*=\s*["']/(?![/])""")

    걸린 = []
    for 이름 in ("index.html", "app.js", "manifest.json", "style.css"):
        p = os.path.join(root, 이름)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            글 = f.read()
        패턴 = (태그속,) if 이름.endswith(".html") else (문자열속,)
        for pat in 패턴:
            for m in pat.finditer(글):
                줄 = 글.count("\n", 0, m.start()) + 1
                걸린.append(f"{이름}:{줄} {글[m.start():m.start() + 40]!r}")

    assert not 걸린, (
        "절대경로가 있습니다 — `/Model/` 하위 배포에서 404 가 납니다:\n  "
        + "\n  ".join(걸린))


def test_웹앱_주소가_문서에_적혀_있다():
    """주인이 휴대폰으로 들어갈 주소는 문서에 있어야 합니다 (150차-R).

    세션들이 "Pages 를 켜세요"를 여러 회차에 걸쳐 되풀이했습니다 —
    이미 켜져 있었는데도. 주소가 적혀 있으면 그 넘겨짚기가 안 나옵니다.
    """
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "인수인계.md"), encoding="utf-8") as f:
        글 = f.read()
    assert "j2h5516-star.github.io/Model" in 글, \
        "인수인계.md 에 웹앱 주소가 없습니다"


def test_성장_속도표가_웹앱에_실리고_화면이_그린다():
    """(162차, 주인 지시 "저런 표를 앞으로의 기준으로 삼아")

    CRDO 사건에서 "델타 상승은 계속인데 속도가 꺾인" 것을 첫 돌파 자가
    가리지 못했습니다. 성장 속도표를 웹앱에 상설로 싣습니다. 판정이
    아니므로 **판정 아님·과거 실측 없음**이 자료와 화면 양쪽에 있어야
    합니다(정직화). 값은 계기판과 같은 함수(app.growth_*)로 만듭니다.
    """
    import app
    root = os.path.join(os.path.dirname(__file__), "..")
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_app as ta

    ds = ta._성장_ds({"가속": ta._성장_eps행([1, 1, 1, 1, 1, 1, 1.2, 1.6]),
                     "감속": ta._성장_eps행([1, 1, 1, 1, 1, 1, 1.6, 1.2])})
    payload = wb.build_payload(ds, None, {"ran_at": "시험"})
    성장 = payload["성장속도"]
    assert 성장["종목들"] == app.growth_table_rows(ds), "계기판과 다른 계산입니다"
    assert 성장["묶음별"] == app.growth_sector_rows(ds)
    assert [r["종목"] for r in 성장["종목들"]] == ["가속", "감속"]
    assert "판정" in 성장["정직화"] and "재지 않았" in 성장["정직화"], 성장["정직화"]
    assert "140일" in 성장["기준"] and "6분기" in 성장["기준"], 성장["기준"]
    # 없는 값은 null 로 나가야 합니다 (JSON 으로 돌려도 None)
    빈 = ta._성장_ds({"음수": ta._성장_eps행([1, 1, -2, -2, -2, -2, 1, 2])})
    r = json.loads(json.dumps(wb.build_payload(빈, None, None)["성장속도"]))["종목들"][0]
    assert r["TTM증가"] is None and r["가속"] is None, r

    # 화면이 실제로 그리는가 — 값만 담고 안 그리면 주인은 못 봅니다
    with open(os.path.join(root, "docs", "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert "성장속도판(" in js and "a.성장속도" in js, "장세 화면이 성장 속도표를 그리지 않습니다"
    assert "가속비율" in js and "직전TTM증가" in js, "묶음 비율 또는 직전 증가율을 안 그립니다"
    assert "성장.정직화" in js, "화면이 정직화 문구를 옮겨 적지 않습니다"
    assert "APP.성장속도" in js, "종목 상세 화면에 성장 속도 줄이 없습니다"


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
