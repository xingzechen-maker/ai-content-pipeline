#!/usr/bin/env python3
"""多栏目存量快照：把「线上已有内容」抓成本地基线。

────────────────────────────────────────────────────────────────
它同时服务两个用途，缺一不可
────────────────────────────────────────────────────────────────
  ① 撞题基线（事前）—— 判断新选题是否和线上已有内容内耗
  ② 外链匹配（事后）—— 把本地文章和线上 URL 对上号

用途②对**抓取范围**的要求比用途①更苛刻：

    发布改成了多栏目，抓取仍只抓原先那个栏目
    → 新栏目的文章一条都抓不到
    → 匹配阶段全篇失配
    → 现象是"匹配脚本坏了"，实际是抓取范围没跟上发布范围

**这是一条会延迟爆发的故障**：发布全绿、隔一步才炸，且报错点与病因点
相隔一个环节。判据 —— 发布范围变了，抓取范围必须同步变；
两处配置应当来自**同一份栏目表**，而不是各自维护一份列表。

────────────────────────────────────────────────────────────────
设计一：逐栏目抓取，而不是"一个栏目翻到底"
────────────────────────────────────────────────────────────────
每个栏目独立分页、独立判断是否还有下一页，最后合并。
把栏目列表做成必填参数（不给就报错），避免"忘了改配置"退化成静默单栏目。

────────────────────────────────────────────────────────────────
设计二：跨栏目去重 + 按 ID 倒序
────────────────────────────────────────────────────────────────
同一篇文章可能同时出现在多个栏目列表里（栏目支持"包含子栏目"时尤其如此）。
不去重会让匹配阶段拿到多个候选。统一按 article_id 去重，保留首次出现。

排序按 id 倒序，让"最新发布的"排在数组最前 —— 与后台列表的直觉一致，
调试时肉眼就能对上。

────────────────────────────────────────────────────────────────
设计三：基线必须是「合并后的全量」，不是单次抓取的切片
────────────────────────────────────────────────────────────────
真实事故：某次抓取为了省时间只翻了有限页数，把那份切片直接当成了
下一批的撞题基线。结果是基线比线上少一大截，于是

    新选题和「基线里没有的老文章」撞题 → 查不出来 → 白发

基线的正确维护方式：

  - 抓取时**不限页数**，直到翻完（用平台自己报的总页数兜底自检）
  - 每批发布后，把本批新增条目**合并进基线**（按 id 去重）
  - 基线文件当作**持续累积的资产**，不是每批重新生成的中间产物

────────────────────────────────────────────────────────────────
设计四：匹配阶段用 id 下界隔离本批
────────────────────────────────────────────────────────────────
存量里可能有与本批标题高度相近的历史文章（撞题闸门漏网的那几篇）。
用标题做相似度匹配时，它们可能被误配。

给一个「本批最小 article_id」作为下界，只在 ≥ 该值的条目里匹配 ——
本批文章必然连续分配 id，下界以上的一定是本批的。

────────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────────
    python lib/scrape_inventory.py --columns examples/columns.sample.json \
        --out inventory.json --max-pages 0        # 0 = 翻完为止

    # 匹配阶段
    python lib/match_links.py --articles articles.json \
        --cms inventory.json --min-id <本批最小ID> --out links.json
"""

import json
import time


def scrape_column(client, col, list_url_tpl, max_pages=0, log=print):
    """抓单个栏目的全部分页。返回该栏目下的条目列表。

    client 需提供 go(url) -> None 与 read_rows() -> list[dict]
    （read_rows 返回字段：article_id / title / link）
    """
    out = []
    page = 1
    total_pages = 0
    while True:
        client.go(list_url_tpl.format(col=col["id"], page=page))
        rows = client.read_rows()
        log(f"   [col={col['id']}] 第 {page} 页：{len(rows)} 条")
        out.extend(rows)

        info = client.read_pagination()
        total_pages = info.get("total_pages", 0)
        if not info.get("has_next") or page >= total_pages:
            break
        if max_pages and page >= max_pages:
            log(f"   ⚠️ 达到上限 {max_pages} 页（该栏目实际 {total_pages} 页）—— "
                f"当基线用会导致漏检")
            break
        page += 1
        time.sleep(0.5)

    # 截断自检：翻完的情况下，页数应等于平台自报的总页数
    if not max_pages and total_pages and page < total_pages:
        log(f"   ⚠️ 栏目 {col['name']} 共 {total_pages} 页，只抓了 {page} 页，"
            f"数据不完整")
    return out


def merge(*snapshots):
    """跨栏目去重（按 article_id）+ 按 article_id 倒序。"""
    seen = {}
    for snap in snapshots:
        for a in snap:
            aid = str(a.get("article_id", "")).strip()
            if aid and aid not in seen:
                a["article_id"] = aid
                seen[aid] = a
    return sorted(seen.values(),
                  key=lambda a: int(a["article_id"]),
                  reverse=True)


def scrape_all(client, columns, list_url_tpl, max_pages=0, log=print):
    """按配置里的**全部**栏目抓取并合并。

    columns 配置里没有任何栏目时直接报错 —— 不给"退化成单栏目"留后门。
    """
    if not columns:
        raise ValueError("栏目表为空：多栏目发布后，抓取范围必须与之对应")
    log(f"开始抓取 {len(columns)} 个栏目：{[c['name'] for c in columns]}")
    snaps = [scrape_column(client, c, list_url_tpl, max_pages, log)
             for c in columns]
    merged = merge(*snaps)
    log(f"合计 {sum(len(s) for s in snaps)} 条 → 去重后 {len(merged)} 篇")
    return merged


def merge_into_baseline(baseline_path, new_items):
    """把本批新增条目并入基线（幂等，可反复调用）。"""
    try:
        with open(baseline_path, "r", encoding="utf-8") as f:
            base = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        base = []
    before = len(base)
    merged = merge(base, new_items)
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return {"before": before, "after": len(merged),
            "added": len(merged) - before}


def min_id_of(items):
    """本批最小 article_id —— 匹配阶段的隔离下界。"""
    ids = [int(a["article_id"]) for a in items if str(a.get("article_id", "")).isdigit()]
    return min(ids) if ids else 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="多栏目存量快照（框架示例）")
    ap.add_argument("--columns", required=True, help="栏目配置 JSON")
    ap.add_argument("--out", required=True, help="输出 inventory 路径")
    ap.add_argument("--max-pages", type=int, default=0,
                    help="每栏目翻页上限，0=翻完（当基线用必须为 0）")
    args = ap.parse_args()
    cfg = json.load(open(args.columns, encoding="utf-8"))
    cols = [{"name": c["name"], "id": c["id"]} for c in cfg.get("columns", [])]
    print(json.dumps({"columns": cols,
                      "max_pages": args.max_pages,
                      "note": "接入时实现 client 的 go/read_rows/read_pagination"},)
          if False else f"栏目 {len(cols)} 个 → {args.out}")
