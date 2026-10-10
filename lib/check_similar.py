#!/usr/bin/env python3
"""近似重复检测 —— 平台判重的盲区。

────────────────────────────────────────────────────────────────
为什么需要它
────────────────────────────────────────────────────────────────
平台的判重看标题字符串是否**全等**。问题是：

    「入门流程怎么走？第一步先弄清规则」    ← 本批
    「入门流程怎么走？第一步先弄清规则吗」  ← 存量

平台认为：**不重复，放行。**

但两个页面在抢同一个搜索词，互相稀释权重——
这叫**内容内耗（cannibalization）**。平台查不出来，因为它只比字符串。

实测（200 篇新内容 vs 存量 2182 条）：

    ≥0.85（近乎同题）5篇     平台全部放行
    ≥0.75（同考点不同措辞）  8 篇     平台全部放行
    ≥0.70（措辞接近）        17 篇    平台全部放行

而且**这个数字随存量增长而单调恶化** —— 存量越大，撞得越多。

────────────────────────────────────────────────────────────────
关键设计：把检查提到写正文之前
────────────────────────────────────────────────────────────────
撞题在**选题阶段就已确定**，没必要等 200 篇正文写完才发现。

    事后替换：白生成 21篇正文 + 二次生成 + 二次查重
    事前拦截：1 分钟选题检查（约 1% 成本）

────────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────────
    # 事前（推荐）：只查选题，便宜
    python check_similar.py --topics-file topics.txt --threshold 0.75

    # 事后：查已成稿的标题
    python check_similar.py --articles articles.json --threshold 0.85

    # 导出机器可读清单，供 replace_topics.py 自动补换
    python check_similar.py --topics-file topics.txt --json collisions.json

    # 串联流水线：超阈值则退出码 1
    python check_similar.py --topics-file topics.txt --fail-on-similar
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys

DEFAULT_ARTICLES = "./articles.json"
DEFAULT_INVENTORY = "./inventory.json"


def normalize(s: str) -> str:
    """归一化：去掉标点空格，只留中英文与数字。

    避免标点差异干扰相似度 —— 「A，B」与「AB」不该算两篇。
    """
    return re.sub(r"[^\w\u4e00-\u9fa5]", "", s or "")


def strip_category(line: str) -> str:
    """去掉选题行的 [类别] 前缀。

    ⚠️ 不剥掉的话，类别名会进入相似度计算、污染结果 ——
    「[入门指南] A…」和「[入门指南] B…」会凭空多出一段共同前缀，
    相似度虚高。
    """
    m = re.match(r"^\[[^\]]*\]\s*(.*)$", line.strip())
    return (m.group(1) if m else line).strip()


def extract_category(line: str) -> str:
    m = re.match(r"^\[([^\]]*)\]", line.strip())
    return m.group(1) if m else ""


def best_match(title: str, inventory_titles: list[tuple[str, str]]) -> tuple[float, str]:
    """找出最相似的存量标题。

    inventory_titles: [(归一化标题, 原始标题), ...]
    """
    nt = normalize(title)
    if not nt:
        return 0.0, ""
    best = (0.0, "")
    for ct, orig in inventory_titles:
        if not ct:
            continue
        r = difflib.SequenceMatcher(None, nt, ct).ratio()
        if r > best[0]:
            best = (r, orig)
    return best


def main() -> int:
    ap = argparse.ArgumentParser(
        description="检测近似重复标题（平台判重的盲区：内容内耗）"
    )
    ap.add_argument(
        "--articles", default=DEFAULT_ARTICLES, help="本批articles.json（事后检查）"
    )
    ap.add_argument(
        "--inventory",
        default=DEFAULT_INVENTORY,
        help="存量快照 inventory.json：[{title: ...}, ...]",
    )
    ap.add_argument(
        "--topics-file",
        default=None,
        help="选题文件，每行一条、可带 [类别] 前缀。"
        "指定后忽略 --articles（事前检查，推荐）",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="相似度阈值。默认 0.85；越低越严格。"
        "参考：0.85 必换/ 0.75 应换 / 0.70 仅观察",
    )
    ap.add_argument("--top", type=int, default=15, help="最多展示多少组")
    ap.add_argument(
        "--fail-on-similar",
        action="store_true",
        help="存在超阈值项时以退出码 1 结束（串联流水线用）",
    )
    ap.add_argument(
        "--json",
        dest="json_out",
        default=None,
        help="导出超阈值项为 JSON，供 replace_topics.py 精确改写",
    )
    args = ap.parse_args()

    # ── 参数校验 ──────────────────────────────────────────────
    need = [args.inventory]
    if args.topics_file:
        need.append(args.topics_file)
    else:
        need.append(args.articles)
    for p in need:
        if not os.path.exists(p):
            print(f"❌ 文件不存在: {p}", file=sys.stderr)
            return 2

    # ── 载入来源 ──────────────────────────────────────────────
    # indexed_raw 记录 (序号, 原始完整行)；序号统一 1-based，
    # 与 topics-file 行号一致，补换脚本靠它定位「第几篇」
    if args.topics_file:
        with open(args.topics_file, encoding="utf-8") as f:
            raw_lines = [l.strip() for l in f if l.strip()]
        indexed_raw = list(enumerate(raw_lines, start=1))
        titles = [strip_category(l) for l in raw_lines]
        source_desc = f"选题 {len(titles)} 条({args.topics_file})"
    else:
        with open(args.articles, encoding="utf-8") as f:
            articles = json.load(f)
        indexed_raw = [(i, a.get("title", "")) for i, a in enumerate(articles, 1)]
        titles = [a.get("title", "") for a in articles]
        source_desc = f"本批 {len(titles)} 篇"

    with open(args.inventory, encoding="utf-8") as f:
        inventory = json.load(f)

    inv_titles = [
        (normalize(x.get("title", "")), x.get("title", "")) for x in inventory
    ]

    # ── 逐条比对 ──────────────────────────────────────────────
    pairs = []
    for (idx, raw), t in zip(indexed_raw, titles):
        sim, conflict = best_match(t, inv_titles)
        pairs.append((sim, raw, conflict, idx))
    pairs.sort(key=lambda x: -x[0])

    hits = [p for p in pairs if p[0] >= args.threshold]

    # ── 报告 ─────────────────────────────────────────────────
    print(f"{source_desc} | 存量 {len(inventory)} 条 | 阈值 {args.threshold}")
    print("-" * 68)
    if not hits:
        print(f"✅ 无近似重复标题（全部低于 {args.threshold}）")
    else:
        print(
            f"⚠️  {len(hits)} 条与存量高度相似 —— 平台判重查不出，"
            f"但会造成内容内耗："
        )
        for s, a, b, _ in hits[: args.top]:
            print(f"  [{s:.2f}] 本批: {a}")
            print(f"          存量: {b}")
        if len(hits) > args.top:
            print(f"  …另有 {len(hits) - args.top} 条，用 --top 调整展示数量")

    # ── 导出补换清单 ─────────────────────────────────────────
    if args.json_out:
        payload = [
            {
                "index": idx,
                "line_no": idx,
                "topic": raw,
                "category": extract_category(raw),
                "sim": round(s, 4),
                "conflict": b,
            }
            for s, raw, b, idx in hits
        ]
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"\n撞题清单已导出: {args.json_out}（{len(payload)} 条）")

    return 1 if (args.fail_on_similar and hits) else 0


if __name__ == "__main__":
    raise SystemExit(main())