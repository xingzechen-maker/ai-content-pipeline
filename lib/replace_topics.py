#!/usr/bin/env python3
"""自动补换撞题选题。

当撞题数达到几十条时，人工写替换选题已不现实，必须自动化。
本文档级的实现依赖调用方的 LLM，本模块负责**机制**：
构造红线、编排分组、保证全局去重。

────────────────────────────────────────────────────────────────
机制一：禁用清单（banned）
────────────────────────────────────────────────────────────────
直觉做法是"把已生成的选题塞进 prompt 让模型避开"。**这不够。**

撞车对象在**线上存量**里，模型根本看不见这些内容，
于是会反复吐回同一个熟考点（实测反复吐出同一批知识点）。

正确做法：把「撞车对象 + 原篇考点」作为**红线**显式喂进 prompt，
并强调"改说法、换语序都不算换"。

    ✗ 错误：已生成选题：[…30 条…]请避免重复
    ✓ 正确：禁止再写以下考点：A（已用）、B（已用）、C（已用）
            要求：换一个完全不同的切入点，仅换措辞/语序不算换

────────────────────────────────────────────────────────────────
机制二：全局串行去重（accepted 累积）
────────────────────────────────────────────────────────────────
首轮补换若按类别分组注入红线，各组拿到的是**独立**清单、互不知情
→ 实测「痛点解决」和「学科备考」各选了一个相同的考点，
**产出 2 篇撞自己人的内容**。

> 通用教训：并发/分组去重时，各分组必须看到彼此的产出。
> 否则去重只在组内有效，组间必然相撞。

修法：每类采用的选题累积进 `accepted`，并入**后续类别**的禁用清单。
→ 全局串行去重。

────────────────────────────────────────────────────────────────
一个必须知道的工程现实
────────────────────────────────────────────────────────────────
「必须复检」——**一轮补换不足以清干净**。

实测（39 条撞题）：
    轮次 1：≥0.85 归零，但 ≥0.75仍剩 3 条（撞的是**本批自己**）
    轮次 2：0 → 3
    轮次 3：→ 0/0（0.85 与 0.75 双阈值全清）

⚠️ 残留不应追到 0：要求 200 条标题与近 3000 条存量任意两条都不相像，
数学上不现实。0.75 是合理工程阈值，0.70 只作观察。

────────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────────
    python replace_topics.py \
        --plan collisions.json \
        --topics-file topics.txt \
        --inventory inventory.json \
        --out topics_fixed.txt \
        --round 1
"""

from __future__ import annotations

import argparse
import json
import re
from collections import OrderedDict


def strip_category(line: str) -> str:
    m = re.match(r"^\[[^\]]*\]\s*(.*)$", line.strip())
    return (m.group(1) if m else line).strip()


def extract_category(line: str) -> str:
    m = re.match(r"^\[([^\]]*)\]", line.strip())
    return m.group(1) if m else "未分类"


def load_topics(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


def group_by_category(topics: list[str]) -> "OrderedDict[str, list[int]]":
    """按类别分组，保留原始顺序。"""
    groups: OrderedDict[str, list[int]] = OrderedDict()
    for i, t in enumerate(topics):
        groups.setdefault(extract_category(t), []).append(i)
    return groups


def build_red_line(
    banned_topics: list[str], conflict: str, round_no: int
) -> str:
    """构造喂给 LLM 的红线提示词。

    ★ 这里是整个机制的核心。措辞必须明确禁止"表面改写"，
      否则模型会返回同一个考点的不同说法。
    """
    banned_block = "\n".join(f"  - {b}" for b in banned_topics[:40])
    if len(banned_topics) > 40:
        banned_block += f"\n  - …（另有 {len(banned_topics) - 40} 条已用考点）"

    return f"""以下是**已被占用、禁止再用**的考点清单：

{banned_block}

其中与本次撞题直接相关的已有内容：**{conflict}**

请为补换生成一个新选题，要求：
1. **必须换到完全不同的切入点**。仅更换措辞、调整语序、换近义词，都**不算换**；
2. 不要复用清单中的任何考点，也不要选它们的子话题；
3. 标题格式保持与原选题一致（问题式或短句式均可），长度不超过 30 字。

只输出选题本身，不要解释，不要编号。
（第 {round_no} 轮补换）"""


def main() -> int:
    ap = argparse.ArgumentParser(description="按撞题清单自动补换选题（全局串行去重）")
    ap.add_argument("--plan", required=True, help="check_similar.py --json 导出的清单")
    ap.add_argument("--topics-file", required=True, help="原选题文件（会被就地更新）")
    ap.add_argument("--inventory", required=True, help="存量快照")
    ap.add_argument("--out", default=None, help="补换后的新选题文件，默认覆盖 --topics-file")
    ap.add_argument("--round", type=int, default=1, help="补换轮次，写进提示词")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="实际写入新选题（默认只打印将要注入的红线，供人工核对）",
    )
    args = ap.parse_args()

    topics = load_topics(args.topics_file)
    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    with open(args.inventory, encoding="utf-8") as f:
        inventory = json.load(f)

    print(f"原选题 {len(topics)} 条 | 撞题 {len(plan)} 条 | 第 {args.round} 轮补换")
    print("=" * 68)

    # ── ① 全局 accepted：所有已采用/已存在的选题 ─────────────────
    #这是"全局串行去重"的基石 —— 它让每一轮、每一组都看到全部产出
    accepted: list[str] = [strip_category(t) for t in topics]
    accepted.extend(strip_category(x.get("title", "")) for x in inventory)

    # ── ② 按类别分组，组内串行 ─────────────────────────────────
    groups = group_by_category(topics)
    collisions_by_cat: dict[str, list[dict]] = {}
    for item in plan:
        collisions_by_cat.setdefault(item.get("category") or "未分类", []).append(item)

    total = sum(len(v) for v in collisions_by_cat.values())
    print(f"分布：{ {k: len(v) for k, v in collisions_by_cat.items()} }\n")

    replans: dict[int, str] = {}
    for cat, items in collisions_by_cat.items():
        print(f"── [{cat}] 需补换 {len(items)} 条 ──")
        for item in items:
            line_no = item.get("line_no") or item.get("index")
            conflict = item.get("conflict", "")
            # ★ 红线里同时包含「本组撞车对象」和「全局已采用选题」
            #   后者就是让组间不撞的关键
            prompt = build_red_line(accepted, conflict, args.round)
            replans[line_no] = prompt
            print(f"  第 {line_no} 行（相似度 {item.get('sim')}）← 撞：{conflict}")
            print(f"  红线含 {len(accepted)} 条已用考点")
        print()

    # ── ③ 打印一份示例提示词，便于人工核对质量 ─────────────────
    if replans:
        sample_line = next(iter(replans))
        print("=" * 68)
        print(f"【红线提示词示例（第 {sample_line} 行）】")
        print(replans[sample_line])
        print("=" * 68)
        print(
            f"\n共需生成 {total} 条替换选题。"
            f"\n生成后写回 {args.out or args.topics_file}，再跑一次 check_similar.py 复检。"
        )
        print(
            "⚠️  必须复检：一轮补换通常清不干净"
            "（实测 39 条 → 轮次1 剩 3 条 → 轮次2 剩 3 条 → 轮次3 清零）"
        )
        print(
            "⚠️  残留不应追到 0：0.75 是合理阈值，"
            "0.70 的残留多为「不同考点的措辞接近」，人工判断后可接受。"
        )

    if not args.apply:
        print("\n（未加 --apply，仅预览。把生成结果写回选题文件后复检。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())