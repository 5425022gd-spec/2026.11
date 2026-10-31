"""角色库：召集/群策群力用角色状态树。

角色池来自 data/character_cards.json 即"人物统计"栏目：
  id/name = 卡名；title = 现职 缺则官职；location = state.驻地 只到省份；bio = 性格摘要

召集规则 玩家定义：只允许在京师 北直隶的角色参与群策群力——按 location 属性取。
定位由逻辑引擎/AI 通过 update_location(更新 接口，同时写回角色卡 state.驻地。)
"""

# ── 从角色卡构建角色池 ──

def _build_characters():
    from .char_cards import load_cards
    from .officials import canonical_location
    cards = load_cards()
    out = []
    for name, c in cards.items():
        b = c.get("basic", {})
        st = c.get("state", {})
        xg = c.get("性格") or []
        bio = "；".join("%s：%s" % (x.get("标题", ""), (x.get("述", "") or "")[:50])
                        for x in xg[:3])
        out.append({
            "id": name,
            "name": name,
            "title": st.get("现职") or b.get("官职") or "",
            "location": canonical_location(st.get("驻地")) or "",
            "bio": bio or "暂无简介",
        })
    return out


def list_characters():
    """全部角色 实时从人物卡库动态读取构建，含新增/修改。"""
    return _build_characters()


def list_callable():
    """可召集角色：人在京畿 北直隶的角色——位置只到省份，故按省名取。"""
    from .officials import CAPITAL_PROVINCE
    return [c for c in list_characters() if c.get("location") == CAPITAL_PROVINCE]


def get_character(cid):
    chars = list_characters()
    for c in chars:
        if c["id"] == cid or c["name"] == cid:
            return c
    return None


def update_location(cid, location):
    """定位追踪 接口：逻辑引擎/AI 更新角色所在；同步写回角色卡 state.驻地。

    位置只到省份：写入前一律归到省名 京师诸写法 → 北直隶。
    """
    try:
        from .char_cards import patch_state
        from .officials import canonical_location
        return patch_state(cid, {"驻地": canonical_location(location)})
    except Exception:
        return False


def prompt_profile(char_or_name):
    """角色卡 data/character_cards.json-> 群策群力 {{random_selection}} 资料。

    取角色卡的官职/阵营 + 全部性格条目 + 最近两条生平要点；
    卡不存在时回退到角色库 title 与 bio。
    """
    if isinstance(char_or_name, dict):
        name = char_or_name.get("name") or ""
        fallback_bio = char_or_name.get("bio", "")
        fallback_title = char_or_name.get("title", "")
    else:
        name = str(char_or_name or "")
        fallback_bio = ""
        fallback_title = ""

    try:
        from .char_cards import get_card
    except Exception:
        return f"{name} {fallback_title}\n简介：{fallback_bio}" if name else None

    c = get_card(name) if name else None
    if not c:
        if name and (fallback_title or fallback_bio):
            return f"{name} {fallback_title}\n简介：{fallback_bio}"
        return name or None

    b = c.get("basic", {})
    title = b.get("官职") or b.get("阵营") or fallback_title or ""
    parts = ["%s %s" % (b.get("姓名", name), title)]
    xg = c.get("性格") or []
    if xg:
        parts.append("性格：" + "；".join(
            "%s——%s" % (x.get("标题", ""), x.get("述", "")) for x in xg))
    sp = c.get("生平") or []
    if sp:
        parts.append("生平：" + "；".join(
            "%s：%s" % (x.get("年", ""), (x.get("事", "") or "")[:80])
            for x in sp[-2:]))
    return "\n".join(parts)
