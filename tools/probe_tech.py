# -*- coding: utf-8 -*-
"""活体验证：科技读口 + 解锁接口（前置门槛 / 扣点 / 重复拒绝）。"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8941"


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=30).read().decode("utf-8"))


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))


d = get("/api/tech")
print("开局：科技点 =", d["points"])
for n in d["nodes"]:
    print("  %-6s 消耗=%g 状态=%-6s 按钮=%s" % (n["name"], n["cost"], n["unlock_state"], n["unlock_text"]))

print("尝试解锁 中等化学（前置未完成）:", post("/api/tech/unlock", {"tech_id": "zhongdeng_huaxue"})["msg"])
r = post("/api/tech/unlock", {"tech_id": "chudeng_shuxue"})
print("解锁 初等数学:", r["ok"], r["msg"], "→ 剩余点数", r["tech"]["points"])
print("再次解锁:", post("/api/tech/unlock", {"tech_id": "chudeng_shuxue"})["msg"])
r = post("/api/tech/unlock", {"tech_id": "kexue_fangfa"})
print("解锁 科学方法:", r["ok"], r["msg"], "→ 剩余点数", r["tech"]["points"])
after = {n["id"]: n for n in r["tech"]["nodes"]}
print("中等数学现在:", after["zhongdeng_shuxue"]["unlock_state"], after["zhongdeng_shuxue"]["unlock_text"])
print("主界面点数:", get("/api/state")["tech_points"])
