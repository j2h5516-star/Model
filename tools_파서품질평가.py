# 파서 결과의 **질**을 숫자로 잰다 (183차-AW)
#
# 지금까지 "값이 바뀌었다"만 셀 수 있었고 좋아졌는지 나빠졌는지는
# 눈으로 몇 개 보는 수밖에 없었다. 잣대를 만든다:
#   그 종목의 **이웃 분기 매출 중앙값**과 견줘 0.4~2.5배면 "그럴듯",
#   밖이면 "수상", 값이 없으면 "없음".
# 완벽한 정답은 아니지만 **같은 잣대로 앞뒤를 견줄 수** 있다.
import json, re, sys, statistics
sys.path.insert(0, "/home/user/Model")
import dataset

ds = dataset.build(dataset.load())
중앙 = {}
for t, rows in ds["quarters"].items():
    vals = [r["revenue"] for r in rows
            if isinstance(r.get("revenue"), (int, float)) and r["revenue"] > 1e6]
    if len(vals) >= 4:
        중앙[t] = statistics.median(vals)

def 재기(경로):
    d = json.load(open(경로))
    셈 = {"그럴듯": 0, "수상": 0, "없음": 0, "종목모름": 0}
    수상목록 = []
    for f, r in d.items():
        m = re.match(r"([A-Z]+)_", f)
        t = m.group(1) if m else None
        v = r.get("revenue")
        if t not in 중앙:
            셈["종목모름"] += 1
            continue
        if v is None:
            셈["없음"] += 1
            continue
        비 = v / 중앙[t]
        if 0.4 <= 비 <= 2.5:
            셈["그럴듯"] += 1
        else:
            셈["수상"] += 1
            수상목록.append((f, v, round(비, 3)))
    return 셈, 수상목록

for 이름 in sys.argv[1:]:
    셈, 수상 = 재기(이름)
    print(f"{이름.split('/')[-1]:14} " + " · ".join(f"{k} {v}" for k, v in 셈.items()))
