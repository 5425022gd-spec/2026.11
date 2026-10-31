"""Live probe: 归属文案一律中文（后端随数据给出 controller / owner_name），不得再有代码映射；
且不得残留适役人口。用法：python tools/probe_owner.py [port]
"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:%s" % (sys.argv[1] if len(sys.argv) > 1 else "8938")
CODES = ("ming", "houjin", "mongol")

fail = []


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


# 1) 军队名册：中文归属随数据给出，接口不再单发一份代号→中文的映射
arm = get("/api/armies")
print("armies keys =", sorted(arm.keys()))
if "owner_labels" in arm:
    fail.append("/api/armies 仍在发 owner_labels（前端已不再需要，属第二份映射）")
units = arm["units"]
own_names = sorted({u.get("owner_name", "") for u in units})
print("units =", len(units), "owner_name 取值 =", own_names)
if any((not n) or n in CODES for n in own_names):
    fail.append("军队 owner_name 不是中文: %s" % own_names)

# 2) 省份：控制权归属为中文
st = get("/api/state")
provs = st["provinces"]
ctrl = sorted({p.get("controller", "") for p in provs})
print("provinces =", len(provs), "controller 取值 =", ctrl)
if any((not c) or c in CODES for c in ctrl):
    fail.append("省份 controller 不是中文: %s" % ctrl)
print("明控省份 =", sum(1 for p in provs if p.get("controller") == "明"))

# 3) 省详情接口：控制权归属字段在，且为中文
prov = get("/api/province?id=%s" % provs[0]["id"])["province"]
print("省详情 controller =", prov.get("controller"))
if not prov.get("controller") or prov["controller"] in CODES:
    fail.append("/api/province 的 controller 缺失或为代号: %r" % prov.get("controller"))

# 4) 适役机制已删除：状态与省详情里都不该再有
blob = json.dumps({"state": st, "province": prov}, ensure_ascii=False)
for token in ("适役", "conscription"):
    if token in blob:
        fail.append("载荷仍残留 %s" % token)

# 5) 前端那套判定的等价核对：按中文名取"我方"，结果应与引擎按归属码取的一致
mobile = [u["name"] for u in units if u.get("category") == "mobile" and u.get("owner_name") == "明"]
garrison = [u for u in units if u.get("category") == "garrison" and u.get("owner_name") == "明"]
others = [u["name"] for u in units if u.get("owner_name") != "明"]
ming_prov = [p["name"] for p in provs if p.get("controller") == "明"]
print("机动军团 =", mobile)
print("地方镇军 =", len(garrison), "支；非明属部队 =", len(others), "支")
print("明控省份 =", len(ming_prov))
if len(mobile) != 1 or len(garrison) != 17 or len(others) != 9:
    fail.append("按中文名过滤的结果与本轮实测不符：机动 %d / 镇军 %d / 他属 %d" % (len(mobile), len(garrison), len(others)))
if len(ming_prov) != 17:
    fail.append("明控省份 %d ≠ 17" % len(ming_prov))

if fail:
    print("\n".join("FAIL: " + f for f in fail))
    sys.exit(1)
print("── 检查通过")
