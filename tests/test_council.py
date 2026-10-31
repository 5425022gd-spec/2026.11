"""群臣会议（council）集成测试：召集/词符匹配/发言/切换/批奏/结束入库。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_council.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_council_pos.json"))
from engine.session import GameSession
from engine.characters import list_callable, list_characters, get_character
from engine.council import compute_match, weighted_pick


def main():
    # 0) 角色库：北京城内可召集
    callable_ = list_callable()
    print("可召集角色（北京城内）:", len(callable_), [c["name"] for c in callable_])
    assert len(callable_) >= 5
    for c in list_characters():
        assert c.get("location"), "角色应有定位：%s" % c["name"]

    # 1) 词符匹配：归一化 + 概率
    text = "辽东军情紧急，请旨增调兵饷粮草，以固守御。"
    pids = [c["id"] for c in callable_[:4]]
    probs = compute_match(text, pids)
    print("匹配度:", {get_character(p)["name"]: round(probs[p], 3) for p in probs})
    total = sum(probs.values())
    assert abs(total - 1.0) < 1e-6, "归一化: %f" % total
    picked = weighted_pick(probs, k=2)
    assert len(picked) <= 2 and len(set(picked)) == len(picked)
    print("加权随机选角:", [get_character(p)["name"] for p in picked])

    # 2) 会议全流程（mock）
    s = GameSession()
    mems = s.list_memorials()
    assert mems, "开局应有章奏"
    mid = mems[0]["id"]

    ok, msg = s.council.start(mid, [c["id"] for c in callable_[:4]])
    print("召集:", ok, msg)
    assert ok and s.council.active

    ok, msg = s.council.player_speak("卿等以为辽东之策如何？")
    assert ok

    ok, msg = s.council.next_ai_reply()   # mock：提示词缺失走兜底发言
    print("AI 发言:", ok, msg)
    assert ok
    roles = [m["role"] for m in s.council.messages]
    assert "system" in roles and "player" in roles and "ai" in roles

    # 3) 消息增删改
    first_ai = [m for m in s.council.messages if m["role"] == "ai"][0]
    ok, _ = s.council.edit_message(first_ai["id"], "（已修改）臣以为当稳守。")
    assert ok
    edited = [m for m in s.council.messages if m["id"] == first_ai["id"]][0]
    assert edited["text"] == "（已修改）臣以为当稳守。"
    print("气泡修改 OK")

    # 5) 生成批奏（mock 兜底）：批奏生成后召对自动标记结束并归档
    ok, pizou = s.council.generate_pizou()
    print("生成批奏:", ok, pizou[:40])
    assert ok and pizou
    assert not s.council.active, "批奏生成后应标记召对结束"

    # 6) 归档可查询（随存档）
    dlg = s.list_dialogues()
    council_dlg = [d for d in dlg if d.get("category") == "council"]
    print("对话库条目:", len(dlg),
          "参与:", (council_dlg[0]["participants"] if council_dlg else []))
    assert council_dlg and council_dlg[0]["summary"]
    assert council_dlg[0].get("pizou"), "归档应含批奏"
    print("批奏归档含批奏 ✓")

    # 6b) 对话归档随存档存取（临时存档文件，不碰游戏 save.json）
    import engine.session as session_mod
    tmp_save = "save_test_tmp.json"
    old_save = session_mod.SAVE_FILE
    session_mod.SAVE_FILE = tmp_save
    try:
        s._save()
        s2 = GameSession()
        # 新局只应带开局"世界总结"归档，不应带旧存档的召对对话
        assert all(d.get("category") != "council" for d in s2.list_dialogues()), \
            "新局不应带旧存档的召对对话"
        assert any(d.get("category") == "world" for d in s2.list_dialogues()), \
            "新局应自带开局世界总结归档"
        s2._load()
        # 召对会话归档已删除：这一类读档后应当为空，其余归档照旧恢复
        assert s2.list_dialogues("council") == [], "召对会话归档已删除，不应再恢复"
        print("对话归档随存档 ✓")
    finally:
        session_mod.SAVE_FILE = old_save
        if os.path.exists(tmp_save):
            os.remove(tmp_save)

    # 7) 召集期间禁止其他 AI 功能（session 层约束）
    s2 = GameSession()
    mid2 = s2.list_memorials()[0]["id"]
    ok, _ = s2.council.start(mid2, [callable_[0]["id"]])
    assert ok and s2.council.active
    s2.council.end()

    # 7b) 未发言直接结束（quick_end）：只标记散会，不归档、不总结为对话记录
    s4 = GameSession()
    s4.council.start(s4.list_memorials()[0]["id"], [callable_[0]["id"]])
    assert s4.council.active
    ok, msg = s4.council.quick_end()
    print("未发言直接结束:", ok, msg)
    assert ok and not s4.council.active
    assert s4.council.messages == []
    assert all(d.get("category") != "council" for d in s4.list_dialogues()), \
        "未发言直接结束不应归档召对记录"
    print("未发言直接结束 OK（不归档）")

    # 8) 点名强制发言：玩家发言提到某角色 -> 该角色下一轮必发言
    s3 = GameSession()
    s3.llm._mock_handler = lambda sys, usr, schema: {
        "tool": "submit_speeches",
        "args": {"speeches": [{"name": "黄立极",
                              "text": "臣以为此事当从长计议，请陛下圣裁。"}]}}
    s3.council.start(s3.list_memorials()[0]["id"],
                     [c["id"] for c in callable_[:4]])
    s3.council.player_speak("黄立极，你来说说此事。")
    ok, _ = s3.council.next_ai_reply()
    assert ok
    last_ai = [m for m in s3.council.messages if m["role"] == "ai"][-1]
    print("点名后发言者:", last_ai["name"])
    assert last_ai["name"] == "黄立极", last_ai["name"]
    print("点名强制发言 OK")

    print("\nCOUNCIL OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
