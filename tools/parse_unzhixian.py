"""将 Prompt/unzhixian 目录下的 14 个模板文件与规则转换为 world/filler_events.json。
"""

import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNZHIXIAN_DIR = os.path.join(BASE_DIR, "Prompt", "unzhixian")
OUTPUT_PATH = os.path.join(BASE_DIR, "world", "filler_events.json")

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

        parts = re.split(r"[-—=]{3,}", text)
        body = parts[0]
        reward_part = parts[1] if len(parts) > 1 else ""

        reward_proc = ""
        reward_unproc = ""
        for line in reward_part.splitlines():
            line = line.strip()
            if "积极拟旨奖励" in line or "积极拟旨" in line:
                reward_proc = re.sub(r"^.*?[:：]", "", line).strip()
            elif "消极拟旨奖励" in line or "消极拟旨" in line:
                reward_unproc = re.sub(r"^.*?[:：]", "", line).strip()

        if not reward_proc or not reward_unproc:
            for line in body.splitlines():
                line = line.strip()
                if "积极拟旨奖励" in line:
                    reward_proc = re.sub(r"^.*?[:：]", "", line).strip()
                elif "消极拟旨奖励" in line:
                    reward_unproc = re.sub(r"^.*?[:：]", "", line).strip()

        # 严格按每篇奏折开头的“谨题，为”或“（...）谨题”分段，避免因正文内“臣谨题请旨”或中间词语误切分
        blocks = re.split(r"\n(?=[^\n]*?谨题[，,]为|\s*?[（(][^）)]*?[）)]谨题)", body)
        for b in blocks:
            b = b.strip()
            if not b or "内阁拟旨" not in b:
                continue
            lines = [l.strip() for l in b.splitlines() if l.strip()]
            mem_lines = []
            d_proc = ""
            d_unproc = ""
            for l in lines:
                if "内阁拟旨（积极）" in l or "内阁拟旨(积极)" in l:
                    d_proc = re.sub(r"^.*?[:：]", "", l).strip()
                elif "内阁拟旨（消极）" in l or "内阁拟旨(消极)" in l:
                    d_unproc = re.sub(r"^.*?[:：]", "", l).strip()
                elif "积极拟旨奖励" in l or "消极拟旨奖励" in l or "————" in l:
                    continue
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
    data = {
        "description": "不重要支线（unzhixian / n 层）模板库，来源于 Prompt/unzhixian/ 14类规范模板。已彻底移除旧的“特纠不职有司”和“特纠纵兵殃民”。",
        "templates": templates
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Parsed {len(templates)} templates into {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
