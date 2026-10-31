"""驿站传令与角色/军队沿途逐省移动调度系统 (Relay & Movement System)

核心功能规范：
1. 角色位置调动 中枢发令：
   - 总延迟 = 北直隶到旧驻地传令时间 + 旧驻地到新驻地旅途时间
   - 第一阶段 中枢快马传骑发往旧驻地：角色状态显示为「候旨开拔中」
   - 第二阶段 角色自旧驻地启程赴新驻地：沿途经过的省份逐段推进 Dijkstra segments，
     每走完一段，角色的实际驻地即时切换为当前所在省份！
   - 抵达新驻地后恢复正常在职。
2. 军队作战指令与战备姿态变更 中枢发令：
   - 延迟时间 = 北直隶到军队当前所在省份的快马传骑时间
   - 在令箭送达之前，维持原指令；令箭送达后，正式生效并切姿态。
3. 军队跨省调动行军：
   - 总延迟 = 北直隶到军队当前驻地传令时间 + 军队自当前省份行军至目标省份时间
   - 大军沿途经过的省份逐段推进，每走完一段，军队的 current_location 即时变更为当前省份！
"""

import os
from typing import Any, Dict, List, Optional, Tuple
from .distances import find_shortest_path, PROVINCE_NAMES
from .char_cards import get_card, patch_state, save_card
from .armies import load_armies_data, save_armies_data, update_army_naval_posture, find_unit
from .logger import CONSOLE
from .variations import record_variation


def dispatch_person_movement(name: str, target_province: str, state: Any = None) -> Tuple[bool, str]:
    """启动角色位置迁移任务 经过北直隶发令 + 沿途逐省行进。"""
    card = get_card(name)
    if not card:
        return False, f"未找到人物【{name}】档案卡。"

    st = card.setdefault("state", {})
    old_loc = st.get ("驻地", "北直隶")

    # 规范化目标省份名
    from .operations import _get_prov_id, PROV_REV
    dst_pid = _get_prov_id(target_province)
    dst_name = PROVINCE_NAMES.get(dst_pid, dst_pid)

    src_pid = _get_prov_id(old_loc)
    src_name = PROVINCE_NAMES.get(src_pid, src_pid)

    if src_pid == dst_pid:
        return True, f"人物【{name}】已在【{dst_name}】，无需调动。"

    # 1. 第一段：北直隶到旧驻地 传骑传旨
    order_path = find_shortest_path("beizhili", src_pid, state, base_speed=350.0)  # 传令快马 350km/日
    order_days = float(order_path.get("time_days", 1.0))

    # 2. 第二段：旧驻地到新驻地 官员仪从/快马启程
    travel_path = find_shortest_path(src_pid, dst_pid, state, base_speed=180.0)  # 官员行旅 180km/日
    travel_days = float(travel_path.get("time_days", 1.0))
    travel_segments = travel_path.get("segments", [])
    path_provs = travel_path.get("path", [src_pid, dst_pid])

    # 写入 state.pending_person_movements
    # 移除该人物已存在的在途迁移
    state.pending_person_movements = [m for m in state.pending_person_movements if m.get("name") != name]

    movement_entry = {
        "name": name,
        "phase": "waiting_order",  # waiting_order  候旨 -> traveling  途中 -> arrived
        "order_remain_days": round(order_days, 2),
        "total_order_days": round(order_days, 2),
        "src_pid": src_pid,
        "src_name": src_name,
        "dst_pid": dst_pid,
        "dst_name": dst_name,
        "current_prov_pid": src_pid,
        "current_prov_name": src_name,
        "travel_segments": travel_segments,
        "path_provs": path_provs,
        "segment_idx": 0,
        "segment_remain_days": float(travel_segments[0]["time_days"]) if travel_segments else 0.0,
        "total_travel_days": round(travel_days, 2),
        "total_remain_days": round(order_days + travel_days, 2)
    }
    state.pending_person_movements.append(movement_entry)

    # 人物卡临时状态标记
    if order_days > 0:
        st["状态_bak"] = st.get ("状态", "在职")
        st["状态"] = f"候旨中 朝廷传骑在途，预计{order_days:.1f}天受命"
    else:
        st["状态"] = f"赴任途中 预计{travel_days:.1f}天抵{dst_name}"

    save_card(name, card)
    msg = f"中枢已发传骑敕命：调任【{name}】自{src_name}移驻{dst_name} 传旨需{order_days:.1f}天，沿途行旅需{travel_days:.1f}天，预计共{order_days+travel_days:.1f}天完成。"
    CONSOLE.log("official", f"官员调迁发令：{name}", msg)
    return True, msg


def dispatch_army_order_delay(army_id: str,
                              mission: Optional[str] = None,
                              risk: Optional[str] = None,
                              retreat: Optional[str] = None,
                              battle_order: Optional[str] = None,
                              state: Any = None) -> Tuple[bool, str]:
    """通过驿站快马传骑向军队送达新的战备姿态或作战指令 带延迟。"""
    armies_data = load_armies_data()
    unit = find_unit(armies_data.get("units") or [], army_id)
    if not unit:
        return False, f"未找到部队【{army_id}】。"

    u_name = unit.get("name", army_id)
    cur_loc = unit.get("current_location") or unit.get("province_id") or "beizhili"
    unit["current_location"] = cur_loc
    unit["province_id"] = cur_loc
    loc_name = PROVINCE_NAMES.get(cur_loc, cur_loc)

    # 驿站快马自北直隶传至军队驻地
    path_info = find_shortest_path("beizhili", cur_loc, state, base_speed=350.0)
    delay_days = float(path_info.get("time_days", 0.0))

    if delay_days <= 0.0:
        # 驻扎在北直隶或同地，立即生效
        from .armies import update_army_naval_posture
        return update_army_naval_posture(army_id, mission=mission, risk=risk, retreat=retreat, battle_order=battle_order)

    # 挂接 pending_army_orders
    state.pending_army_orders = [o for o in state.pending_army_orders if o.get("army_id") != army_id]
    order_entry = {
        "army_id": army_id,
        "army_name": u_name,
        "location": cur_loc,
        "location_name": loc_name,
        "remain_days": round(delay_days, 2),
        "mission": mission,
        "risk": risk,
        "retreat": retreat,
        "battle_order": battle_order,
    }
    state.pending_army_orders.append(order_entry)
    msg = f"兵部令箭已出京：传达【{u_name}】作战指令变更，快马经驿站送往{loc_name}，预计需 {delay_days:.1f} 天送达部队军营生效。"
    CONSOLE.log("war", f"令箭出京：{u_name}", msg)
    # 发令时立即打上"变化"标签 唯一登记口：engine/variations.record_variation
    if state is not None:
        # 组装具体指令明细
        order_desc_parts = []
        if mission: order_desc_parts.append ("任务：%s" % mission)
        if risk: order_desc_parts.append ("交战风险：%s" % risk)
        if retreat: order_desc_parts.append ("撤退：%s" % retreat)
        if battle_order: order_desc_parts.append ("战役指令：%s" % battle_order)
        order_desc_str = " ".join(order_desc_parts) if order_desc_parts else "常规调令"
        record_variation(
            state, "military",
            "【朝廷调兵令箭出京】传发【%s】 %s新战备指令【%s】 预计%.1f天达"
            % (u_name, loc_name, order_desc_str, delay_days),
            province=cur_loc)

    return True, msg


def tick_movements_and_relays_daily(state: Any, days: float = 1.0) -> List[str]:
    """每日推进角色移动与军队令箭送达。"""
    notes = []
    if not state or days <= 0:
        return notes

    # 1. 推进军队作战令箭传达
    pending_orders = state.pending_army_orders
    rem_orders = []
    for od in pending_orders:
        od["remain_days"] -= days
        if od["remain_days"] <= 0:
            # 令箭送达，正式执行更新
            from .armies import update_army_naval_posture
            update_army_naval_posture(od["army_id"],
                                      mission=od.get("mission"),
                                      risk=od.get("risk"),
                                      retreat=od.get("retreat"),
                                      battle_order=od.get("battle_order"))
            note = f"【兵部令箭送达】快马传骑已抵达{od['location_name']}军营，【{od['army_name']}】正式执行新战备指令！"
            notes.append(note)
            CONSOLE.log("war", f"令箭抵达：{od['army_name']}", note)
            # 到达时打上"变化"标签 唯一登记口：engine/variations.record_variation
            if state is not None:
                arr_parts = []
                if od.get("mission"): arr_parts.append ("任务：%s" % od["mission"])
                if od.get("risk"): arr_parts.append ("交战风险：%s" % od["risk"])
                if od.get("retreat"): arr_parts.append ("撤退：%s" % od["retreat"])
                if od.get("battle_order"): arr_parts.append ("战役指令：%s" % od["battle_order"])
                arr_str = " ".join(arr_parts) if arr_parts else (od.get("mission") or "新战令")
                record_variation(
                    state, "military",
                    "【军令变更】%s %s战备指令正式更新为【%s】"
                    % (od['army_name'], od['location_name'], arr_str),
                    province=od.get("location", ""))
        else:
            rem_orders.append(od)
    state.pending_army_orders = rem_orders

    # 2. 推进角色迁移 沿途逐省移动
    pending_moves = state.pending_person_movements
    rem_moves = []
    for mv in pending_moves:
        name = mv["name"]
        card = get_card(name)
        if not card:
            continue
        c_st = card.setdefault("state", {})

        # 阶段 A：候旨传令中
        if mv["phase"] == "waiting_order":
            mv["order_remain_days"] -= days
            mv["total_remain_days"] -= days
            if mv["order_remain_days"] <= 0:
                # 传骑抵达到任，角色启程出发
                overflow_days = abs(mv["order_remain_days"])
                mv["phase"] = "traveling"
                first_seg_time = float(mv["travel_segments"][0]["time_days"]) if mv["travel_segments"] else 0.0
                mv["segment_remain_days"] = max(0.0, first_seg_time - overflow_days)
                mv["segment_idx"] = 0
                path_str = " → ".join(PROVINCE_NAMES.get(p, p) for p in mv["path_provs"])
                c_st["状态"] = f"赴任途中 {path_str}，预计{mv['total_travel_days']:.1f}天达{mv['dst_name']}"
                note = f"【朝廷敕命送达】传令官抵达{mv['src_name']}，【{name}】接旨启程，经【{path_str}】奔赴新驻地{mv['dst_name']}！"
                notes.append(note)
                CONSOLE.log("official", f"官员启程：{name}", note)
                record_variation(state, "official", note, province=mv["src_pid"])

                # 若本次推进时间有溢出并且走完了第一段，继续检查阶段 B
                if mv["segment_remain_days"] > 0:
                    rem_moves.append(mv)
                    save_card(name, card)
                    continue
            else:
                rem_moves.append(mv)
                save_card(name, card)
                continue

        # 阶段 B：沿途逐省行进
        if mv["phase"] == "traveling":
            mv["segment_remain_days"] -= days
            mv["total_remain_days"] -= days
            seg_idx = mv["segment_idx"]
            segs = mv["travel_segments"]

            # 若当前分段走完，移入下一个省份节点 (to_id)
            if mv["segment_remain_days"] <= 0:
                # 当前段走完，说明已经到达了该段的目标省份 segs[seg_idx]["to_id"]
                arrived_node_pid = segs[seg_idx]["to_id"] if seg_idx < len(segs) else mv["dst_pid"]
                arrived_node_name = segs[seg_idx]["to_name"] if seg_idx < len(segs) else mv["dst_name"]
                mv["current_prov_pid"] = arrived_node_pid
                mv["current_prov_name"] = arrived_node_name

                # 实时更新角色卡当前驻地为该省
                c_st["驻地"] = arrived_node_name

                seg_idx += 1
                if seg_idx < len(segs):
                    mv["segment_idx"] = seg_idx
                    mv["segment_remain_days"] = float(segs[seg_idx]["time_days"])
                    path_str = " → ".join(PROVINCE_NAMES.get(p, p) for p in mv["path_provs"])
                    c_st["状态"] = f"赴任途经{arrived_node_name} {path_str}，尚余{max(0.1, mv['total_remain_days']):.1f}天达{mv['dst_name']}"
                    note = f"【官员行程】调任官员【{name}】车轿行抵【{arrived_node_name}】境内，正赶赴新驻地{mv['dst_name']}。"
                    notes.append(note)
                    CONSOLE.log("official", f"官员行抵：{name}", note)
                    record_variation(state, "official", note, province=arrived_node_pid)
                    rem_moves.append(mv)
                else:
                    # 全部路段走完，正式抵达终点新驻地！
                    c_st["驻地"] = mv["dst_name"]
                    c_st["状态"] = "在职"
                    note = f"【官员就任】调任官员【{name}】已如期抵达新驻地【{mv['dst_name']}】，正式履新莅事！"
                    notes.append(note)
                    CONSOLE.log("official", f"官员履新：{name}", note)
                    record_variation(state, "official", note, province=mv["dst_pid"])
            else:
                rem_moves.append(mv)

            save_card(name, card)

    state.pending_person_movements = rem_moves
    return notes
