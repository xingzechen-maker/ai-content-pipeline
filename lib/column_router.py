#!/usr/bin/env python3
"""按内容把文章路由到发布栏目 —— 多栏目发布体系的第一块砖。

────────────────────────────────────────────────────────────────
为什么需要它
────────────────────────────────────────────────────────────────
单栏目流水线里，"发布到哪里"是常数。改成多栏目后，它变成一个
**必须逐篇判定**的决策，而且判定错了不会立刻报错：

    串栏目了 → 文章照样"发布成功" → 只有抓链阶段才发现匹配不上
    → 表现为"这批外链一条都没匹配到"，看起来像匹配脚本坏了。

所以栏目判定必须是**显式、可复核**的一步，而不是散落在发布脚本里的临时逻辑。

────────────────────────────────────────────────────────────────
设计一：栏目表是配置，不是代码
────────────────────────────────────────────────────────────────
栏目 ID 是平台侧的资源，会随对方调整而变化。把映射写死在代码里，
等于给自己埋一个"某天全线发错栏目"的雷。

    ColumnRouter.load("columns.json")

配置形如（见 examples/columns.sample.json）：

    {
      "default": "fallback_column",
      "columns": [
        {"name": "资讯类", "id": 90001,
         "categories": ["升学政策"],
         "keywords": ["志愿", "录取", "分数线", "学籍"]},
        {"name": "服务类", "id": 90008,
         "categories": [],
         "keywords": ["试听", "预约", "签约", "退费"]}
      ]
    }

────────────────────────────────────────────────────────────────
设计二：判定顺序是「内容关键词 → 选题类别 → 兜底」
────────────────────────────────────────────────────────────────
直觉做法是"用选题类别映射栏目"（生成阶段已经给每篇打了类别）。
**这不够。** 实测三类问题：

  1. **部分栏目没有对应类别。** 「试听预约」这类事务性栏目，
     不属于任何一个内容类别，只能靠标题内容识别。
  2. **部分类别标注会偏。** 生成器把一篇讲"某个校区怎么选"的文章
     标成了「机构选择」，但标题里的"校区"是更强的信号。
  3. **同一类别内部粒度不够。** 「痛点解决」下既有学习习惯问题，
     也有学科方法问题，落到不同栏目才合理。

所以关键词优先、类别兜底。实测 200 篇的分布：
仅按类别映射 vs 关键词优先，后者纠正了约 3% 的明显错配。

────────────────────────────────────────────────────────────────
设计三：歧义词要主动剔除
────────────────────────────────────────────────────────────────
把"收费 / 费用 / 价格"放进「试听预约」的关键词，结果是：

    「某校区周边停车收费标准是多少」→ 命中"收费" → 错判成试听预约
    「1对1 和小组课费用差多少」    → 命中"费用" → 错判成试听预约

这两条本该分别落在「校区查询」和「班型对比」。
**泛化的交易类词会和任何业务词碰撞**，要么不放，要么放到最后兜底。

────────────────────────────────────────────────────────────────
设计四：「A还是B」型需要单独识别
────────────────────────────────────────────────────────────────
班型/形式对比类标题的稳定句式是"选 A 还是 B"，
但 A、B 的取值太发散，靠穷举词表必然漏。直接判句式更稳：

    if "还是" in title and re.search(CHOOSE_PATTERN, title):
        return 该栏目

────────────────────────────────────────────────────────────────
设计五（最容易踩）：按序号关联，不要按标题
────────────────────────────────────────────────────────────────
发布前会对标题做敏感词替换，**线上标题 ≠ 本地标题**。
于是任何"用标题回查栏目/ID"的关联都会静默失配。

实测：200 篇里按标题匹配漏掉 11 篇（5.5%），且不报错。

    ✅ 正确：文章对象带 doc_index，贯穿生成→导出→发布→归档全程不变
    ❌ 错误：拿线上标题 join 本地标题

────────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────────
    router = ColumnRouter.load("columns.json")
    for art in articles:
        art["column"] = router.route(art["title"], art.get("category", ""))
        art["column_id"] = router.column_id(art["column"])
    router.print_distribution(articles)   # 发布前目视核对
"""

import json
import re


class ColumnRouter:
    def __init__(self, config):
        self.default = config.get("default") or ""
        self.columns = config.get("columns") or []
        if not self.columns:
            raise ValueError("columns.json 里没有任何栏目定义")
        # name -> id
        self._id_by_name = {c["name"]: c["id"] for c in self.columns}
        # 有关键词的栏目 = 走关键词优先；只有 categories 的走类别兜底
        self._kw_cols = [c for c in self.columns if c.get("keywords")]
        # 「A还是B」型额外句式
        self._choose_pattern = config.get("choose_pattern", "")

    # ---------------------------------------------------------------- 加载
    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f))

    def column_id(self, name):
        return self._id_by_name.get(name, self._id_by_name.get(self.default))

    # ---------------------------------------------------------------- 判定
    def route(self, title, category=""):
        t = title or ""

        # ① 内容关键词优先（命中即停，按配置里的栏目顺序）
        for col in self._kw_cols:
            for pat in col.get("keywords", []):
                if re.search(pat, t):
                    return col["name"]

        # ② 「…还是…」句式（班型/形式对比类）
        if self._choose_pattern and "还是" in t and re.search(self._choose_pattern, t):
            for col in self._kw_cols:
                if col.get("choose_fallback"):
                    return col["name"]

        # ③ 选题类别兜底
        for col in self.columns:
            if category and category in (col.get("categories") or []):
                return col["name"]

        # ④ 默认栏目
        return self.default

    def route_all(self, articles):
        """就地写入 column / column_id，并返回统计分布。"""
        dist = {}
        for a in articles:
            name = self.route(a.get("title"), a.get("category", ""))
            a["column"] = name
            a["column_id"] = self.column_id(name)
            dist[name] = dist.get(name, 0) + 1
        return dist

    def print_distribution(self, articles):
        """发布前目视核对 —— 分布明显失衡时，先回头查分类规则。"""
        dist = self.route_all(articles)
        by_id = {c["name"]: c["id"] for c in self.columns}
        order = sorted(self._id_by_name.items(), key=lambda kv: kv[1])
        print(f"📂 栏目自动分类分布（{len(articles)} 篇）：")
        for name, cid in order:
            print(f"   {name}[id:{cid}] : {dist.get(name, 0)} 篇")
        empty = [n for n, _ in order if dist.get(n, 0) == 0]
        if empty:
            print(f"   ⚠️ 空栏目：{', '.join(empty)}（若无对应选题，属正常）")
        return dist


# ── 自检 ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    cfg = sys.argv[1] if len(sys.argv) > 1 else "examples/columns.sample.json"
    arts = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else []
    r = ColumnRouter.load(cfg)
    if arts:
        r.print_distribution(arts)
    else:
        print(f"已加载 {len(r.columns)} 个栏目，默认={r.default}")
        for t, c in [("某校区周边停车怎么安排", ""),
                     ("选一对一还是小组课？", ""),
                     ("志愿填报有哪些坑", "升学政策")]:
            print(f"  {t!r} → {r.route(t, c)}")
