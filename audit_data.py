"""
audit_data.py — 재료(수집 데이터) 오염 전수조사 (73차)
=====================================================

하는 일:
  손질을 마친 분기표(dataset.build 의 결과)를 훑어, **아직 남아 있는
  오염 의심 칸**을 세어 보여 줍니다. **아무것도 지우지 않습니다.**

왜 지우지 않는가 (65차 §④ 에 등록한 방침 "표시부터, 지우지 말고"):
  지울 규칙을 만들려면 문턱이 필요한데, 문턱을 잘못 잡으면 **진짜 실적을
  지웁니다.** 66차에서 실제로 겪었습니다 — "결산 분기 값이 크면 연간값"
  이라는 그럴듯한 규칙이 회계연도가 앞서가는 회사(NVDA·MRVL·SNOW) 307칸을
  지울 뻔했습니다. 73차에도 같은 함정을 다시 만났습니다:

    · FSLR 이 12-31 행마다 7.74 · 12.02 · 14.21 → **연간 EPS 오염**
    · ABNB 가 09-30 행마다 20억 달러 → **진짜 성수기 실적**(3분기)

  둘 다 "해마다 같은 자리에서 평소의 3~4배"라는 모양이 똑같아서, 어떤
  배수를 고르든 갈라낼 수 없었습니다. 그래서 **자동 삭제를 포기하고
  세어서 보여 주기**로 합니다. 지우지 않으므로 진짜 실적을 잃지 않고,
  세어서 보여 주므로 재료가 얼마나 더러운지를 감추지도 않습니다.

무엇을 세나 (셋 다 문턱 해석을 함께 적습니다):
  ⑴ 같은 기간 라벨이 여러 행에    — 파서가 분기를 못 읽어 앞 라벨을 되쓴 것
  ⑵ 혼자 튀었다가 돌아오는 칸     — 앞·뒤 이웃보다 몇 배 큰 값
  ⑶ 같은 값이 되풀이되는 사다리 칸 — 연간값·자리채움이 여러 분기에 복사된 것

⑵ 의 배수 3배는 **삭제 기준이 아니라 표시 기준**입니다. 성장하는 회사는
걸리지 않습니다(이웃과만 비교하므로). 지우지 않으니 틀려도 잃는 것이
없고, 크기를 가늠하는 데만 씁니다.
"""

from __future__ import annotations

from collections import defaultdict

# 잣대 사다리 3칸 (measure_engine.yardstick_of 와 같은 순서)
LADDER_FIELDS = ("adj_eps", "adjusted_ebitda", "gaap_eps")

# 표시 기준 (삭제 기준이 아닙니다 — 위 설명 참조)
SPIKE_RATIO = 3.0        # 앞·뒤 이웃 중 큰 쪽의 몇 배부터 표시할까
REPEAT_MIN = 3           # 같은 값이 몇 번 되풀이되면 표시할까
MIN_SERIES = 5           # 이만큼도 값이 없는 종목은 비교 자체가 무의미


def duplicate_labels(quarters: dict) -> list[dict]:
    """같은 기간 라벨이 여러 행에 붙은 종목 목록.

    측정은 발표일(announced_date)로 하므로 계산이 틀리지는 않습니다.
    다만 **파서가 분기를 못 읽었다는 표시**이고, 그런 종목은 다른
    칸에서도 잘못 읽었을 가능성이 큽니다. 그래서 셉니다.
    """
    out = []
    for ticker, rows in sorted(quarters.items()):
        by_label: dict[str, int] = defaultdict(int)
        for row in rows:
            label = row.get("period_label")
            if label and any(row.get(f) is not None for f in LADDER_FIELDS):
                by_label[label] += 1
        겹친행 = sum(n for n in by_label.values() if n > 1)
        if 겹친행:
            out.append({
                "종목": ticker,
                "겹친라벨": sum(1 for n in by_label.values() if n > 1),
                "걸린행": 겹친행,
                "전체행": len(rows),
            })
    out.sort(key=lambda r: -r["걸린행"])
    return out


def spike_cells(quarters: dict, ratio: float = SPIKE_RATIO) -> list[dict]:
    """앞·뒤 이웃보다 `ratio` 배 이상 큰 칸 (혼자 튀었다가 돌아오는 값).

    **왜 종목 전체 중앙값이 아니라 바로 옆 분기와 비교하는가**:
    전체 중앙값과 비교하면 CRDO(0.05 → 1.16)·NVDA·PLTR 같은 **진짜
    성장**이 상위를 채워 버립니다(73차 실측). 성장은 이웃과 가깝고,
    오염은 이웃 둘 다에서 멉니다. 그래서 이웃하고만 비교합니다.

    앞뒤 이웃이 **둘 다** 있는 칸만 봅니다 — 맨 앞·맨 뒤 칸은 판단하지
    않습니다(모르는 것은 모른다고 둡니다).
    """
    out = []
    for ticker, rows in sorted(quarters.items()):
        for field in LADDER_FIELDS:
            series = [(i, r[field]) for i, r in enumerate(rows)
                      if r.get(field) is not None]
            if len(series) < MIN_SERIES:
                continue
            for pos in range(1, len(series) - 1):
                index, value = series[pos]
                앞, 뒤 = series[pos - 1][1], series[pos + 1][1]
                이웃 = max(abs(앞), abs(뒤))
                if 이웃 <= 0:
                    continue
                if abs(value) >= 이웃 * ratio:
                    out.append({
                        "종목": ticker,
                        "칸": field,
                        "라벨": rows[index].get("period_label"),
                        "발표일": rows[index].get("announced_date"),
                        "앞": 앞,
                        "값": value,
                        "뒤": 뒤,
                        "배수": abs(value) / 이웃,
                    })
    out.sort(key=lambda r: -r["배수"])
    return out


def repeated_ladder_values(quarters: dict,
                           min_repeat: int = REPEAT_MIN) -> list[dict]:
    """같은 값이 `min_repeat` 번 이상 되풀이되는 사다리 칸.

    매출에는 이미 같은 검사가 있습니다(_drop_repeated_revenue). 사다리
    칸에도 같은 일이 일어납니다 — 실물 DELL 7.99·7.98·8.38, NRG 조정
    EBITDA 37.25억이 세 분기 연속. 분기 이익이 소수점 둘째 자리까지
    똑같이 되풀이될 확률은 사실상 없습니다.

    0 은 세지 않습니다 — 적자 직전 회사의 EPS 0.00 은 진짜로 되풀이됩니다.
    """
    out = []
    for ticker, rows in sorted(quarters.items()):
        for field in LADDER_FIELDS:
            values = [r[field] for r in rows if r.get(field) is not None]
            if len(values) < MIN_SERIES:
                continue
            셈: dict[float, int] = defaultdict(int)
            for v in values:
                셈[round(v, 2)] += 1
            for value, n in 셈.items():
                if n >= min_repeat and value != 0:
                    out.append({
                        "종목": ticker,
                        "칸": field,
                        "값": value,
                        "횟수": n,
                        "전체": len(values),
                    })
    out.sort(key=lambda r: -r["횟수"])
    return out


def summary(quarters: dict) -> dict:
    """세 검사의 합계 — 계기판 한 줄로 쓰기 위한 요약."""
    labels = duplicate_labels(quarters)
    spikes = spike_cells(quarters)
    repeats = repeated_ladder_values(quarters)
    검사한칸 = 0
    for rows in quarters.values():
        for field in LADDER_FIELDS:
            n = sum(1 for r in rows if r.get(field) is not None)
            if n >= MIN_SERIES:
                검사한칸 += max(n - 2, 0)    # 앞뒤 이웃이 있는 칸만
    return {
        "라벨겹침_종목": len(labels),
        "라벨겹침_행": sum(r["걸린행"] for r in labels),
        "튄칸": len(spikes),
        "검사한칸": 검사한칸,
        "튄칸_비율": (len(spikes) / 검사한칸 * 100.0) if 검사한칸 else None,
        "되풀이_묶음": len(repeats),
        "되풀이_칸": sum(r["횟수"] for r in repeats),
        "전체종목": len(quarters),
    }


# 매출총이익률이 한 종목 안에서 크게 오르내리는 것 (77·78차)
# ---------------------------------------------------------------------------
# 야후와 대조해 보니 매출총이익률이 44% 어긋났고, 실물(AMBA)로 원인을
# 확인했습니다 — 각주의 "두 이익률의 **차이** 1.4%" 를 이익률로 읽고
# 있었습니다. WMT·DELL 은 원문이 없어 원인을 확인 못 했습니다.
#
# 매출총이익률은 회사의 **체질**이라 분기마다 몇 %p 안에서 움직입니다.
# 한 종목 안에서 60% ↔ 2% 를 오간다면 **둘 중 하나는 틀린 값**입니다.
#
# ⚠️ 처음엔 "종목 중앙값에서 멀면 이상치"로 재려 했는데 **거꾸로 나왔습니다** —
#    AMBA 는 틀린 값(2% 대)이 더 많아 중앙값이 3.9% 가 되고, 그러면 진짜
#    값(63%)이 이상치로 찍힙니다. 어느 쪽이 옳은지 우리는 모릅니다.
#    그래서 **어느 쪽이 틀렸는지 정하지 않고**, "이 종목은 폭이 너무 넓다"는
#    사실만 말하고 **양 끝 원문을 둘 다** 부탁합니다. 원문을 봐야 압니다.
GM_SPAN_PCT = 20.0     # 한 종목 안 최대-최소가 이만큼(%p)을 넘으면 표시


def gross_margin_unstable(quarters: dict,
                          span: float = GM_SPAN_PCT) -> list[dict]:
    """매출총이익률 폭이 너무 넓은 종목과, 그 **양 끝** 행.

    어느 쪽이 틀렸는지 정하지 않습니다 — 양 끝을 둘 다 돌려줍니다.
    **아무것도 지우지 않습니다.**
    """
    out = []
    for ticker, rows in sorted(quarters.items()):
        있는행 = [r for r in rows if r.get("gross_margin_pct") is not None]
        if len(있는행) < MIN_SERIES:
            continue
        차례 = sorted(있는행, key=lambda r: r["gross_margin_pct"])
        폭 = 차례[-1]["gross_margin_pct"] - 차례[0]["gross_margin_pct"]
        if 폭 < span:
            continue
        for row, 쪽 in ((차례[0], "가장 낮음"), (차례[-1], "가장 높음")):
            out.append({
                "종목": ticker,
                "칸": "gross_margin_pct",
                "라벨": row.get("period_label"),
                "발표일": row.get("announced_date"),
                "값": row["gross_margin_pct"],
                "폭": round(폭, 1),
                "쪽": 쪽,
            })
    out.sort(key=lambda r: -r["폭"])
    return out


def wanted_raw_filings(quarters: dict, limit: int = 60) -> list[dict]:
    """로봇에게 "이 공시 원문은 꼭 가져와 달라"고 부탁할 목록.

    **왜 필요한가** — 지금 막혀 있는 곳이 정확히 여기입니다.
    VZ 5.18 · GS 59.45 · IPGP 99.10 같은 칸이 왜 그렇게 들어왔는지 알려면
    **그 보도자료 원문**을 봐야 하는데, 개발 환경은 SEC 가 막혀 있어 받을
    수 없습니다. 지금 보관되는 원문은 "잣대값을 하나도 못 읽은" 실패
    공시뿐이라, **잘못 읽은** 공시는 한 건도 남지 않습니다.

    그래서 조사가 찾아낸 칸의 (종목, 발표일)을 목록으로 적어 저장소에
    커밋하면, 다음 로봇 실행이 그 공시만 골라 원문을 담아 옵니다.
    다음 세션은 그 실물로 파서를 고칠 수 있습니다.

    발표일이 없는 행은 담지 않습니다 — 어느 공시인지 짚을 수 없습니다.
    """
    out = []
    for cell in spike_cells(quarters):
        if not cell.get("발표일"):
            continue
        out.append({
            "종목": cell["종목"],
            "발표일": cell["발표일"],
            "칸": cell["칸"],
            "값": cell["값"],
            "배수": round(cell["배수"], 1),
            "이유": "이웃 분기보다 크게 튐",
        })
        if len(out) >= limit // 2:
            break
    # 매출총이익률 이상치도 절반쯤 담습니다 (78차) — 이 칸의 원인을 아직
    # 원문으로 확인 못 한 종목이 있습니다(WMT·DELL).
    담긴종목 = {r["종목"] for r in out}
    for cell in gross_margin_unstable(quarters):
        if not cell.get("발표일"):
            continue
        out.append({
            "종목": cell["종목"],
            "발표일": cell["발표일"],
            "칸": cell["칸"],
            "값": cell["값"],
            "배수": None,
            "이유": f"매출총이익률 폭이 {cell['폭']}%p 로 넓음 "
                  f"({cell['쪽']} 쪽 — 어느 쪽이 틀렸는지는 원문을 봐야 압니다)",
        })
        담긴종목.add(cell["종목"])
        if len(out) >= limit:
            break
    return out


def one_line(quarters: dict) -> str:
    """계기판에 그대로 띄울 한 줄 (모바일 폭에서도 읽히도록 짧게)."""
    s = summary(quarters)
    비율 = "확인 못함" if s["튄칸_비율"] is None else f"{s['튄칸_비율']:.1f}%"
    return (f"재료 상태 — 이웃보다 {SPIKE_RATIO:g}배 튄 칸 {s['튄칸']}개"
            f"({비율}) · 같은 값 되풀이 {s['되풀이_칸']}칸 · "
            f"기간 라벨 겹침 {s['라벨겹침_행']}행({s['라벨겹침_종목']}종목). "
            "지우지 않고 세기만 합니다 — 지울 규칙을 만들면 진짜 실적까지 "
            "지운다는 것을 66차·73차에 실측했습니다.")


# ---------------------------------------------------------------------------
# 실행 방법:  python3 audit_data.py
#   조사 결과를 화면에 찍고, 로봇에게 부탁할 원문 목록을 파일로 적습니다.
# ---------------------------------------------------------------------------
WANTED_PATH = "data/measure/wanted_raw.json"


def write_wanted(quarters: dict, path: str = WANTED_PATH) -> int:
    """로봇이 읽을 '원문 부탁 목록'을 파일로 적고, 건수를 돌려줍니다."""
    import json

    목록 = wanted_raw_filings(quarters)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "설명": ("조사(audit_data.py)가 고른 '원문이 필요한 공시' 목록입니다. "
                   "수집 로봇이 이 목록에 있는 공시는 값을 읽었더라도 원문을 "
                   "함께 담아 옵니다 — 잘못 읽은 값의 원인을 다음 세션이 "
                   "실물로 확인할 수 있게 하려는 것입니다. 값은 지우지 않습니다."),
            "목록": 목록,
        }, f, ensure_ascii=False, indent=1)
    return len(목록)


if __name__ == "__main__":
    import dataset

    _ds = dataset.build(dataset.load())
    _q = _ds["quarters"]
    print(one_line(_q))
    print()
    for _row in spike_cells(_q)[:20]:
        print(f"  {_row['배수']:6.1f}배 {_row['종목']:6s} {_row['칸']:16s} "
              f"{str(_row['라벨']):8s} {_row['앞']:>12,.2f} → "
              f"{_row['값']:>12,.2f} → {_row['뒤']:>12,.2f}")
    print()
    print(f"원문 부탁 목록 {write_wanted(_q)}건 → {WANTED_PATH}")
