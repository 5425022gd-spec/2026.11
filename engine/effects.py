"""效果应用：把 {province?, var, op, amount, class?} 写到状态上，并返回人话。

op 仅支持 add / sub。所有写入都经过数值钳制：
- 国库 treasury>= 0；
- 地方字段 local：infra 0-100、carrying >= 0；
- 军队字段 garrison 人数 / paid 发饷比例：写在军队名册里 world/armies.json，钳制 >= 0 / 0-1；
- 阶级字段 class 指定阶级：attitude/tax_rate 0-100 或 0-1 见上，
  output/support/pop >= 0。
这是引擎里唯一直接改写状态数值的地方之一 另一个是 time.drift。

效果字段与数据归属 engine/localdata.py 结构约定 + engine/armies.py 名册：
  infra / carrying 地方数据
  garrison 镇军人数/ paid 镇军发饷比例 军队名册，唯一一份
  attitude / tax_rate / output / support / pop 需 class 指定阶级
"""


def _clamp(nv, lo, hi):
    nv = max(lo, nv)
    if hi is not None:
        nv = min(hi, nv)
    return nv


def apply_one(state, eff):
    """应用单个效果。eff: {province?, var, op(add|sub), amount, class?}。"""
    pid = eff.get("province")
    var = eff["var"]
    op = eff["op"]
    amount = float(eff["amount"])

    if var == "treasury":
        nv = state.treasury + amount if op == "add" else state.treasury - amount
        state.treasury = max(0.0, nv)
        signed = amount if op == "add" else -amount
        return "国库%+.1f万两" % signed

    loc = state.provinces[pid].get("local") or {}
    if var == "infra":
        target, key, lo, hi = loc, "infra", 0.0, 100.0
    elif var == "carrying":
        # 【v0.3.5】承载力只认 base_carrying localdata 与 economy 都先取它，
        # 原来写 carrying 会被完全忽略——界面承载人口纹丝不动
        target, key, lo, hi = loc, "base_carrying", 0.0, None
        loc.setdefault("base_carrying", 0.0)
    elif var in ("garrison", "paid"):
        # 镇军人数与发饷比例写在军队名册里 唯一一份，省份数据不存副本
        from .armies import garrison_unit, garrison_pay_rate, save_armies_data, load_armies_data
        data = load_armies_data()
        unit = garrison_unit(pid, data.get("units") or [])
        if unit is None:
            return "无此省份镇军：%s" % state.provinces[pid]["name"]
        key = "count" if var == "garrison" else "pay_rate"
        cur = float(unit.get("count") or 0) if var == "garrison" \
            else float(garrison_pay_rate(pid, unit))
        nv = cur + amount if op == "add" else cur - amount
        nv = max(0.0, nv) if var == "garrison" else _clamp(nv, 0.0, 1.0)
        unit[key] = int(nv) if var == "garrison" else round(nv, 4)
        save_armies_data(data)
        return "%s%s%+.1f" % (state.provinces[pid]["name"], key, amount)
    elif var in ("attitude", "tax_rate", "output", "support", "pop"):
        cname = eff.get("class")
        cls = (loc.get("classes") or {}).get(cname)
        if cls is None:
            return "无此阶级：%s %s" % (cname, state.provinces[pid]["name"])
        target = cls
        key = var
        lo, hi = (0.0, 100.0) if var == "attitude" else \
                 (0.0, 1.0) if var == "tax_rate" else (0.0, None)
    else:
        return "未知效果字段：%s" % var

    cur = target.get(key, 0.0)
    nv = cur + amount if op == "add" else cur - amount
    target[key] = _clamp(nv, lo, hi)
    pname = state.provinces[pid]["name"]
    signed = amount if op == "add" else -amount
    label = "%s%s" % (pname, key) if not eff.get("class") else \
            "%s%s%s" % (pname, eff["class"], key)
    return "%s%+.1f" % (label, signed)
