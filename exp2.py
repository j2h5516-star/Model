import dataset, leadership as L, lab, time
from statistics import median
ds = dataset.build(dataset.load())
base = lab.prep(ds)
TRIALS = 8
t0 = time.time()
shaken = []
for i in range(TRIALS):
    d = L._drop_random_values(ds, L.STABILITY_DROP_RATE, L.STABILITY_SEED + i)
    shaken.append(lab.prep(d))
print("흔든 판 8개 준비", round(time.time()-t0,1), "초", flush=True)

def run(window, streak, memory=None):
    memory = memory or window
    b = lab.timeline(lab.states(base, window=window), memory=memory, streak=streak)
    bmap = {r["주"]: r["주도"] for r in b}
    blast = b[-1]["주도"]
    changed, last_bad = [], 0
    for P in shaken:
        tl = lab.timeline(lab.states(P, window=window), memory=memory, streak=streak)
        changed.append(sum(1 for r in tl if bmap.get(r["주"]) != r["주도"]))
        if tl and tl[-1]["주도"] != blast: last_bad += 1
    return dict(주수=len(b), 전환=len(L.switch_events(b)),
                바뀐주中=int(median(changed)), 최소=min(changed), 최대=max(changed),
                비율=round(median(changed)/len(b)*100,1),
                끝주불일치=last_bad, 현주도=blast)

print(f"{'창':>3} {'연속':>4} | {'전환':>4} {'바뀐주中':>7} {'최소':>4} {'최대':>4} {'%':>5} {'끝주불일치':>6}  현재주도")
for w in (13, 26):
    for s in (1, 2, 4):
        r = run(w, s)
        print(f"{w:>3} {s:>4} | {r['전환']:>4} {r['바뀐주中']:>7} {r['최소']:>4} {r['최대']:>4} {r['비율']:>5} {r['끝주불일치']:>4}/8  {r['현주도']}", flush=True)
print("--- 창만 (연속=1) 더 넓게 ---")
for w in (20, 39, 52):
    r = run(w, 1)
    print(f"{w:>3} {1:>4} | {r['전환']:>4} {r['바뀐주中']:>7} {r['최소']:>4} {r['최대']:>4} {r['비율']:>5} {r['끝주불일치']:>4}/8  {r['현주도']}", flush=True)
print("--- 연속만 (창=13) 더 길게 ---")
for s in (3, 6, 8):
    r = run(13, s)
    print(f"{13:>3} {s:>4} | {r['전환']:>4} {r['바뀐주中']:>7} {r['최소']:>4} {r['최대']:>4} {r['비율']:>5} {r['끝주불일치']:>4}/8  {r['현주도']}", flush=True)
