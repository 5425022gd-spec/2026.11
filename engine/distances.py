"""大明疆域省际邻接距离图、移动修正与 Dijkstra 最短路径算法。

数据与规则：
1. 邻接省份距离图：基于明代驿路实际里程与关隘设定相邻省份中枢之间的距离 千米。
2. 省际移动修正 movement_modifiers：开局默认为 1.00，存储于 state 中。
3. 驿站传递时间与路线选择：
   - 基础速度：250 千米/天 即 250 km/day。
   - 每段耗时 = 邻接距离 /  250 × 移动修正。
   - 任意两省之间 或从某省至北京的传递路线总是选所有连通路径中时间总和最少的一条 Dijkstra 最短路径。
4. 军队移动规则同驿站规则，沿最短路径行军推进。
"""

import heapq
from typing import Any, Dict, List, Optional, Tuple

# 邻接省份间基准距离 千米
ADJACENT_DISTANCES: Dict[Tuple[str, str], int] = {
    ("beizhili", "shanxi"): 410,
    ("beizhili", "shandong"): 370,
    ("beizhili", "henan"): 490,
    ("beizhili", "liaodong"): 420,
    ("shandong", "henan"): 350,
    ("shandong", "nanzhili"): 650,
    ("shanxi", "shaanxi"): 550,
    ("shanxi", "henan"): 420,
    ("henan", "shaanxi"): 480,
    ("henan", "nanzhili"): 520,
    ("henan", "huguang"): 500,
    ("shaanxi", "sichuan"): 650,
    ("shaanxi", "huguang"): 600,
    ("nanzhili", "huguang"): 450,
    ("nanzhili", "jiangxi"): 450,
    ("nanzhili", "zhejiang"): 280,
    ("sichuan", "huguang"): 750,
    ("sichuan", "guizhou"): 500,
    ("sichuan", "yunnan"): 650,
    ("huguang", "jiangxi"): 300,
    ("huguang", "guizhou"): 600,
    ("huguang", "guangxi"): 650,
    ("jiangxi", "zhejiang"): 400,
    ("jiangxi", "fujian"): 550,
    ("jiangxi", "guangdong"): 650,
    ("zhejiang", "fujian"): 550,
    ("fujian", "guangdong"): 650,
    ("fujian", "dongfan"): 250,
    ("guangdong", "guangxi"): 500,
    ("guangdong", "dongfan"): 350,
    ("guangxi", "guizhou"): 550,
    ("guangxi", "yunnan"): 600,
    ("guizhou", "yunnan"): 500,
    ("liaodong", "shengjing"): 200,
    ("liaodong", "haixi"): 350,
    ("shengjing", "haixi"): 180,
    ("shengjing", "nenkeerqin"): 350,
    ("shengjing", "wuliangha"): 300,
    ("haixi", "suolun"): 500,
    ("haixi", "wuliangha"): 300,
    ("nenkeerqin", "chahar"): 500,
    ("nenkeerqin", "suolun"): 400,
    ("suolun", "chahar"): 600,
    ("suolun", "donghai"): 500,
    ("donghai", "kuwu"): 350,
    ("donghai", "wuliangha"): 400,
    ("kuwu", "wuliangha"): 300,
    ("wuliangha", "chahar"): 400,
    ("chahar", "tumote"): 350,
    ("chahar", "shanxi"): 400,
    ("tumote", "ordos"): 400,
    ("tumote", "shanxi"): 350,
    ("tumote", "shaanxi"): 500,
    ("ordos", "shaanxi"): 450,
}

# 省份中文名映射
PROVINCE_NAMES = {
    "beizhili": "北直隶",
    "shandong": "山东",
    "shanxi": "山西",
    "henan": "河南",
    "shaanxi": "陕西",
    "nanzhili": "南直隶",
    "sichuan": "四川",
    "huguang": "湖广",
    "jiangxi": "江西",
    "zhejiang": "浙江",
    "fujian": "福建",
    "guangdong": "广东",
    "guangxi": "广西",
    "yunnan": "云南",
    "guizhou": "贵州",
    "liaodong": "辽东",
    "shengjing": "盛京",
    "haixi": "海西",
    "nenkeerqin": "嫩科尔沁",
    "suolun": "索伦",
    "donghai": "东海",
    "kuwu": "苦兀",
    "wuliangha": "兀良哈",
    "chahar": "察哈尔",
    "tumote": "土默特",
    "ordos": "鄂尔多斯",
    "dongfan": "东番",
}

# 构建双向邻接图
ADJACENT_GRAPH: Dict[str, Dict[str, int]] = {}
for (u, v), dist in ADJACENT_DISTANCES.items():
    ADJACENT_GRAPH.setdefault(u, {})[v] = dist
    ADJACENT_GRAPH.setdefault(v, {})[u] = dist


def get_edge_modifier(u: str, v: str, state: Any = None) -> float:
    """获取两相邻省份之间的移动修正倍率 默认 1.00。"""
    if state is not None:
        key = "_".join(sorted([u, v]))
        return float(state.movement_modifiers.get(key, 1.0))
    return 1.0


def set_edge_modifier(u: str, v: str, mod: float, state: Any = None) -> None:
    """设置两相邻省份之间的移动修正。"""
    if state is not None:
        key = "_".join(sorted([u, v]))
        state.movement_modifiers[key] = round(max(0.1, float(mod)), 2)


def find_shortest_path(src: str, dst: str, state: Any = None, base_speed: float = 250.0) -> Dict[str, Any]:
    """使用 Dijkstra 算法计算从 src 到 dst 的最短用时路径、总距离与沿途明细。"""
    if src not in ADJACENT_GRAPH or dst not in ADJACENT_GRAPH:
        return {
            "path": [src],
            "path_names": [PROVINCE_NAMES.get(src, src)],
            "dist_km": 0,
            "time_days": 1.0,
            "segments": []
        }

    if src == dst:
        return {
            "path": [src],
            "path_names": [PROVINCE_NAMES.get(src, src)],
            "dist_km": 0,
            "time_days": 0.0,
            "segments": []
        }

    # 优先队列： 累计时间天数, 累计里程km, 当前节点, 经过的节点路径
    pq = [(0.0, 0, src, [src])]
    visited: Dict[str, float] = {}

    while pq:
        time_days, dist_km, node, path = heapq.heappop(pq)
        if node in visited and visited[node] <= time_days:
            continue
        visited[node] = time_days

        if node == dst:
            # 组装分段详情
            segments = []
            for i in range(len(path) - 1):
                u_n = path[i]
                v_n = path[i + 1]
                d_seg = ADJACENT_GRAPH[u_n][v_n]
                mod = get_edge_modifier(u_n, v_n, state)
                t_seg = round(d_seg / (base_speed * mod), 2)
                segments.append({
                    "from_id": u_n,
                    "from_name": PROVINCE_NAMES.get(u_n, u_n),
                    "to_id": v_n,
                    "to_name": PROVINCE_NAMES.get(v_n, v_n),
                    "dist_km": d_seg,
                    "modifier": mod,
                    "time_days": t_seg,
                })

            path_names = [PROVINCE_NAMES.get(p, p) for p in path]
            return {
                "path": path,
                "path_names": path_names,
                "dist_km": dist_km,
                "time_days": round(time_days, 1),
                "segments": segments,
            }

        for nxt, d_seg in ADJACENT_GRAPH.get(node, {}).items():
            mod = get_edge_modifier(node, nxt, state)
            step_days = d_seg / (base_speed * mod)
            heapq.heappush(pq, (time_days + step_days, dist_km + d_seg, nxt, path + [nxt]))

    return {
        "path": [src, dst],
        "path_names": [PROVINCE_NAMES.get(src, src), PROVINCE_NAMES.get(dst, dst)],
        "dist_km": 1000,
        "time_days": 4.0,
        "segments": []
    }


def get_province_neighbor_distances(pid: str, state: Any = None) -> List[Dict[str, Any]]:
    """获取指定省份到所有邻接省份的距离、移动修正与用时 用于地方界面展示。"""
    neighbors = ADJACENT_GRAPH.get(pid, {})
    out = []
    for nb_id, dist_km in neighbors.items():
        mod = get_edge_modifier(pid, nb_id, state)
        days = round(dist_km / (250.0 * mod), 1)
        out.append({
            "neighbor_id": nb_id,
            "neighbor_name": PROVINCE_NAMES.get(nb_id, nb_id),
            "dist_km": dist_km,
            "modifier": mod,
            "time_days": days,
            "desc": f"{dist_km} km 修正 {mod:.2f} {days} 日达"
        })
    out.sort(key=lambda x: x["dist_km"])
    return out
