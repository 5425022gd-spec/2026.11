# -*- coding: utf-8 -*-
"""将 Prompt/unzhixian 目录下的 14 个模板文件的全部内容直接硬编码导出为 engine/unzhixian_data.py。
"""

import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNZHIXIAN_DIR = os.path.join(BASE_DIR, "Prompt", "unzhixian")
OUTPUT_PATH = os.path.join(BASE_DIR, "engine", "unzhixian_data.py")
JSON_OUTPUT_PATH = os.path.join(BASE_DIR, "world", "filler_events.json")

CATEGORY_FILES = {
    "弹劾地方": "弹劾地方.txt",
    "弹劾中央": "弹劾中央.txt",
    "贪腐": "贪腐.txt",
    "举荐": "举荐.txt",
    "旱灾": "旱灾.txt",
    "水涝": "水涝.txt",
    "高温": "高温.txt",
    "严寒": "严寒.txt",
    "台风": "台风.txt",
    "小黄汛": "小黄汛.txt",
    "大黄汛": "大黄汛.txt",
    "地震": "地震.txt",
    "夏丰收": "夏丰收.txt",
    "秋丰收": "秋丰收.txt",
}


def parse_unzhixian_templates():
    all_templates = []
    t_idx = 1

    for cat_name, fname in CATEGORY_FILES.items():
        fpath = os.path.join(UNZHIXIAN_DIR, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            text = f.read()

        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # 提取共用奖励
        reward_proc = ""
        reward_unproc = ""
        content_lines = []
        for l in lines:
            if "积极拟旨奖励" in l:
                reward_proc = re.sub(r"^.*?[:：]", "", l).strip()
            elif "消极拟旨奖励" in l:
                reward_unproc = re.sub(r"^.*?[:：]", "", l).strip()
            elif "如上共用奖励" in l:
                continue
            else:
                content_lines.append(l)

        # 严格按分界线或新奏折开头分块
        blocks = []
        cur_block = []
        for l in content_lines:
            if re.match(r"^[-—=]{3,}$", l):
                if cur_block:
                    blocks.append(cur_block)
                    cur_block = []
            elif "谨题" in l and cur_block and any("内阁拟旨" in x for x in cur_block):
                blocks.append(cur_block)
                cur_block = [l]
            else:
                cur_block.append(l)
        if cur_block:
            blocks.append(cur_block)

        for b in blocks:
            mem_lines = []
            d_proc = ""
            d_unproc = ""
            for l in b:
                if "内阁拟旨（积极）" in l or "内阁拟旨(积极)" in l:
                    d_proc = re.sub(r"^.*?[:：]", "", l).strip()
                elif "内阁拟旨（消极）" in l or "内阁拟旨(消极)" in l:
                    d_unproc = re.sub(r"^.*?[:：]", "", l).strip()
                else:
                    if not d_proc and not d_unproc:
                        mem_lines.append(l)

            mem_text = "\n".join(mem_lines).strip()
            if mem_text and d_proc and d_unproc:
                m_title = re.search(r"为(.*?)事[。，]?", mem_text)
                if m_title:
                    t_core = m_title.group(1).strip()
                    title = f"题本 · {t_core}事"
                else:
                    title = f"题本 · {cat_name}奏报事"

                cat_label = "灾异奏报" if cat_name in ("旱灾", "水涝", "高温", "严寒", "台风", "小黄汛", "大黄汛", "地震") else (
                    "言官" if "弹劾" in cat_name or cat_name == "贪腐" else "钱粮类"
                )

                all_templates.append({
                    "id": f"n-tpl-{t_idx:03d}",
                    "sub_category": cat_name,
                    "title": title,
                    "format": "题本",
                    "category": cat_label,
                    "content_template": mem_text,
                    "draft_processed": d_proc,
                    "draft_unprocessed": d_unproc,
                    "reward_processed": reward_proc,
                    "reward_unprocessed": reward_unproc
                })
                t_idx += 1

    return all_templates


def main():
    templates = parse_unzhixian_templates()

    # 1. 写入 engine/unzhixian_data.py（原生 Python 硬编码数据结构）
    code_lines = [
        '# -*- coding: utf-8 -*-',
        '"""大明王朝 1627 · 不重要支线（unzhixian / n 层）全量硬编码模板库。',
        '',
        '本文件由 tools/export_unzhixian_py.py 自动导出生成，',
        '硬编码收录 14 个类别的全部 104 个完整模板，作为系统内部原生数据源。',
        '"""',
        '',
        'from typing import Any, Dict, List',
        '',
        'UNZHIXIAN_TEMPLATES: List[Dict[str, Any]] = [',
    ]

    for tpl in templates:
        code_lines.append('    {')
        for k in ('id', 'sub_category', 'title', 'format', 'category'):
            code_lines.append(f'        "{k}": {json.dumps(tpl[k], ensure_ascii=False)},')
        code_lines.append(f'        "content_template": {json.dumps(tpl["content_template"], ensure_ascii=False)},')
        code_lines.append(f'        "draft_processed": {json.dumps(tpl["draft_processed"], ensure_ascii=False)},')
        code_lines.append(f'        "draft_unprocessed": {json.dumps(tpl["draft_unprocessed"], ensure_ascii=False)},')
        code_lines.append(f'        "reward_processed": {json.dumps(tpl["reward_processed"], ensure_ascii=False)},')
        code_lines.append(f'        "reward_unprocessed": {json.dumps(tpl["reward_unprocessed"], ensure_ascii=False)},')
        code_lines.append('    },')

    code_lines.extend([
        ']',
        '',
        '',
        'def get_unzhixian_templates() -> List[Dict[str, Any]]:',
        '    """获取全部硬编码支线章奏模板列表。"""',
        '    return list(UNZHIXIAN_TEMPLATES)',
        ''
    ])

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(code_lines))
    print(f"Exported {len(templates)} templates into {OUTPUT_PATH}")

    # 2. 同步更新 world/filler_events.json
    data = {
        "description": "不重要支线（unzhixian / n 层）模板库，来源于 Prompt/unzhixian/ 14类规范模板。已彻底移除旧的“特纠不职有司”和“特纠纵兵殃民”。",
        "templates": templates
    }
    with open(JSON_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Synced {len(templates)} templates into {JSON_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
