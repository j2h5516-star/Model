"""모의투자 시뮬레이션 (탐색 — 채택 근거 아님).
규칙은 실행 전에 고정:
 · 진입 = 발표 다음 거래일 종가, 보유 60거래일(등록된 기본형 창), 끝나지 않은 건 마지막 종가로 평가
 · 비용 = 진입·청산 각 0.1%
 · 포트폴리오 = 그날 열린 포지션 동일가중, 포지션 없으면 현금(0%)
 · 전략: S0 모든 발표(기준선) / S1 H5b 장세 좋음(유일한 채택 신호) / S2 첫 돌파(H2b, 미채택) / S3 H5b∧첫돌파(H6, 미채택)
 · 표본 = 조정 EPS 잣대 사건 (판정기와 같은 표본 필터)
"""
import bisect, json, os, sys, math
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dataset, measure_engine as me

snap = json.load(open(os.path.join(sys.path[0], "data/measure/snapshot.json")))
ds = dataset.build(snap, splits=dataset.load_splits())
ev, _ = me.collect_events(ds)   # 끝난 창만
# 우측검열(아직 안 끝난) 사건도 모의투자에는 넣어야 하므로 직접 다시 모읍니다
spy = ds["prices"]["SPY"]
series = me.gauge_series(ds)
allev = []
for t in ds["tickers"]:
    rows = ds["quarters"].get(t) or []
    y = me.yardstick_of(rows)
    if y != "adj_eps": continue
    pr = ds["prices"].get(t)
    if not pr or not pr.get("dates"): continue
    for st in me.earnings_states(rows, field=y):
        a = st["announced"]
        i = bisect.bisect_right(pr["dates"], str(a)[:10])
        if i >= len(pr["dates"]): continue
        if (me._to_date(pr["dates"][i]) - me._to_date(a)).days > me.ENTRY_MAX_GAP_DAYS: continue
        allev.append(dict(t=t, a=str(a)[:10], i=i, first=st["newhigh_streak"] == 1,
                          h5b=me.gauge_h5b_on(series, a)))
print("조정EPS 사건(진입 가능)", len(allev), "· 판정기 사건(창 끝난 것, 전 잣대)", len(ev))

STRATS = {
  "S0 모든 발표(기준선)": lambda e: True,
  "S1 장세 좋음(H5b, 채택)": lambda e: e["h5b"] is True,
  "S2 첫 돌파(H2b, 미채택)": lambda e: e["first"],
  "S3 장세∧첫돌파(H6, 미채택)": lambda e: e["h5b"] is True and e["first"],
}
COST = 0.001
H = me.WINDOW_TRADING_DAYS
cal = spy["dates"]; ci = {d: k for k, d in enumerate(cal)}

def pos_path(e):
    pr = ds["prices"][e["t"]]; d, c = pr["dates"], pr["close"]
    j = min(e["i"] + H, len(d) - 1)
    return d, c, e["i"], j, (e["i"] + H) >= len(d)

def simulate(evs, start=None, end=None):
    daily = defaultdict(list)   # date -> list of position daily returns
    trades = []
    for e in evs:
        d, c, i, j, still = pos_path(e)
        if start and d[i] < start: continue
        if end and d[i] > end: continue
        r = c[j] / c[i] * (1 - COST) * (1 - (0 if still else COST)) - 1
        si, sj = bisect.bisect_right(cal, d[i]) - 1, bisect.bisect_right(cal, d[j]) - 1
        sr = cal and spy["close"][sj] / spy["close"][si] - 1
        trades.append(dict(t=e["t"], 진입=d[i], 청산=d[j], 진행중=still, r=r, ex=r - sr))
        for k in range(i + 1, j + 1):
            x = c[k] / c[k - 1] - 1
            if k == i + 1: x = (1 + x) * (1 - COST) - 1
            if k == j and not still: x = (1 + x) * (1 - COST) - 1
            daily[d[k]].append(x)
    return trades, daily

def curve(daily, start, end):
    days = [d for d in cal if start <= d <= end]
    v, peak, mdd, held = 1.0, 1.0, 0.0, 0
    for d in days:
        xs = daily.get(d)
        if xs: v *= 1 + sum(xs) / len(xs); held += 1
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    yrs = len(days) / 252
    return v - 1, (v ** (1 / yrs) - 1) if yrs > 0 else None, mdd, held / max(len(days), 1)

def spy_curve(start, end):
    days = [d for d in cal if start <= d <= end]
    c = [spy["close"][ci[d]] for d in days]
    peak, mdd = c[0], 0
    for x in c: peak = max(peak, x); mdd = min(mdd, x / peak - 1)
    yrs = len(days) / 252
    return c[-1] / c[0] - 1, (c[-1] / c[0]) ** (1 / yrs) - 1, mdd

def report(title, start, end, evfilter_start=None, evfilter_end=None):
    print(f"\n### {title}  ({start} ~ {end})")
    tr, cg, md = spy_curve(start, end)
    print(f"  SPY 사서 묵히기: 총 {tr*100:+.1f}% · 연 {cg*100:+.1f}% · 최대낙폭 {md*100:.1f}%")
    for name, f in STRATS.items():
        trades, daily = simulate([e for e in allev if f(e)], evfilter_start or start, evfilter_end or end)
        if not trades: print(f"  {name}: 거래 0"); continue
        tot, cagr, mdd, expo = curve(daily, start, end)
        done = [x for x in trades if not x["진행중"]]
        win = sum(1 for x in trades if x["ex"] > 0) / len(trades)
        boom = sum(1 for x in done if x["ex"] > 0.20) / len(done) if done else float("nan")
        avg = sum(x["ex"] for x in trades) / len(trades)
        print(f"  {name}: 거래 {len(trades)}(진행중 {len(trades)-len(done)}) · 총 {tot*100:+.1f}% · 연 {cagr*100:+.1f}% · "
              f"최대낙폭 {mdd*100:.1f}% · 투자일 {expo*100:.0f}% · SPY대비 평균 {avg*100:+.1f}%p · SPY 이긴 비율 {win*100:.0f}% · 폭등(+20%p) {boom*100:.1f}%")
    return

first_day = min(e["a"] for e in allev)
last = cal[-1]
report("전체 기간 (탐색 — 판정에 쓴 바로 그 표본)", "2017-01-03", last)
report("앞 절반", "2017-01-03", "2021-12-31")
report("뒤 절반", "2022-01-03", last)
for y in range(2017, 2027):
    report(f"{y}년", f"{y}-01-01", min(f"{y}-12-31", last))
report("앱 완성 뒤 (2026-08-13~, 진짜 앞을 보지 않은 구간 — 대부분 진행 중)", "2026-08-13", last)

print("\n### 검산: 앱 완성 뒤 S0 거래 낱개")
trades, daily = simulate(allev, "2026-08-13", last)
rs = sorted(trades, key=lambda x: x["진입"])
print("  평균 원수익", round(sum(x["r"] for x in rs)/len(rs)*100,2), "평균 초과", round(sum(x["ex"] for x in rs)/len(rs)*100,2))
for x in rs[:8] + rs[-4:]: print("  ", x["t"], x["진입"], f"{x['r']*100:+.1f}", f"{x['ex']*100:+.1f}")
by = defaultdict(list)
for x in rs: by[x["진입"][:7]].append(x["r"])
print("  월별 진입수", {k: (len(v), round(sum(v)/len(v)*100,1)) for k,v in by.items()})

def curve_spy_idle(daily, start, end):
    days = [d for d in cal if start <= d <= end]
    v, peak, mdd = 1.0, 1.0, 0.0
    for k, d in enumerate(days):
        xs = daily.get(d)
        if xs: v *= 1 + sum(xs)/len(xs)
        elif k: v *= spy["close"][ci[d]] / spy["close"][ci[days[k-1]]]
        peak = max(peak, v); mdd = min(mdd, v/peak - 1)
    yrs = len(days)/252
    return v-1, v**(1/yrs)-1, mdd
print("\n### 쉬는 날은 SPY 를 들고 있는 변형 (전체 2017~)")
for name, f in STRATS.items():
    trades, daily = simulate([e for e in allev if f(e)], "2017-01-03", last)
    t, c, m = curve_spy_idle(daily, "2017-01-03", last)
    print(f"  {name}: 총 {t*100:+.1f}% · 연 {c*100:+.1f}% · 최대낙폭 {m*100:.1f}%")

# 장세 게이지가 켜진 날 비율
on = [d for d in cal if d >= "2017-01-03" and me.gauge_h5b_on(series, d) is True]
off = [d for d in cal if d >= "2017-01-03" and me.gauge_h5b_on(series, d) is False]
print("\n게이지 켜진 날", len(on), "꺼진 날", len(off))

# C. 내 포트폴리오 원장
L = json.load(open(os.path.join(sys.path[0], "data/measure/portfolio_ledger.json")))["days"]
d0 = sorted(L)[0]
def ret(t, a, b):
    pr = ds["prices"].get(t)
    if not pr: return None
    i = bisect.bisect_right(pr["dates"], a) - 1; j = bisect.bisect_right(pr["dates"], b) - 1
    if i < 0 or pr["dates"][i] < a[:8]: pass
    return pr["close"][j] / pr["close"][i] - 1
print(f"\n### 내 포트폴리오 (원장 첫날 {d0} 종가 → {last})")
hold = [x["종목"] for x in L[d0]["보유"]]
rr = {t: ret(t, d0, last) for t in hold}
for t, r in sorted(rr.items(), key=lambda kv: -(kv[1] or -9)): print(f"  {t}: {r*100:+.1f}%")
hv = [r for r in rr.values() if r is not None]
print(f"  보유 동일가중 {sum(hv)/len(hv)*100:+.1f}% · SPY {ret('SPY', d0, last)*100:+.1f}%")
print("\n### 교체후보: 처음 이름이 오른 날 종가에 샀다면 → ", last)
first = {}
for day in sorted(L):
    for x in L[day].get("교체후보") or []:
        first.setdefault(x["종목"], day)
cr = []
for t, day in sorted(first.items(), key=lambda kv: kv[1]):
    r = ret(t, day, last); s = ret("SPY", day, last)
    if r is None: print("  ", t, day, "주가없음"); continue
    cr.append((r, r - s)); print(f"  {t} {day}: {r*100:+.1f}% (SPY대비 {(r-s)*100:+.1f}%p)")
print(f"  후보 {len(cr)}개 평균 {sum(a for a,_ in cr)/len(cr)*100:+.1f}% · SPY대비 평균 {sum(b for _,b in cr)/len(cr)*100:+.1f}%p · SPY 이긴 수 {sum(1 for _,b in cr if b>0)}")
