import dataset, leadership as L, lab, collections
from statistics import median
ds = dataset.build(dataset.load())
P = lab.prep(ds)
print("=== 실험1: 창(WINDOW) 길이 ===")
print(f"{'창':>4} {'판정주':>5} {'전환':>4} {'주도완성수中':>10} {'1-2위점수차中':>11} {'동점주':>5} {'주도묶음종류':>7}")
for w in (13, 20, 26, 39, 52):
    st = lab.states(P, window=w)
    tl = lab.timeline(st, memory=w)
    sw = L.switch_events(tl)
    by = {}
    for r in st: by.setdefault(r["주"], []).append(r)
    counts, gaps, ties = [], [], 0
    for day, rows in by.items():
        q = sorted((r for r in rows if r["조건충족"]), key=lambda r: -r["주도점수"])
        if not q: continue
        counts.append(q[0]["완성수"])
        if len(q) > 1:
            g = q[0]["주도점수"] - q[1]["주도점수"]; gaps.append(g)
            if g == 0: ties += 1
    kinds = len({r["주도"] for r in tl if r["주도"]})
    print(f"{w:>4} {len(tl):>5} {len(sw):>4} {median(counts):>10.1f} {median(gaps):>11.1f} {ties:>5} {kinds:>7}")
