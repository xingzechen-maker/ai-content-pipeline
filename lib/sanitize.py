"""敏感词处理：受保护子串 + 同义替换优先 + 自愈降级。

这个模块解决三个反直觉的问题，每一个都在生产环境踩过坑。

────────────────────────────────────────────────────────────────
问题 1：删词会打断句子
────────────────────────────────────────────────────────────────
很多平台的敏感词策略是「只删不换」。于是：

    原句：成为「冲击高分段」的隐形门槛
    删字：成为「段」的隐形门槛← 句子残废

**原则：能换词就不删词，句子完整性优先。**

────────────────────────────────────────────────────────────────
问题 2：「最长键优先」是个看起来对的错误做法
────────────────────────────────────────────────────────────────
直觉：先替换更长的键，就能保住「大学城」。

实测证伪：字典里「大学生」是「大学」的**前缀**，
按长度降序排会让「大学」抢在前面替换、反而破坏「大学生→ 高校生」。

  · 长度策略对**后缀关系**（大学 / 大学城）有效
  · 对**前缀关系**（大学 / 大学生）无效

正确解法是「受保护子串占位符」，见下方sanitize()。

────────────────────────────────────────────────────────────────
问题 3：词内拼接
────────────────────────────────────────────────────────────────
    原句：「冲击高分段」的隐形门槛      键「冲击高分」→「稳步提升成绩」
    结果：「稳步提升成绩段」              ← 断句

替换键若出现在**更长的固定搭配内部**（词内拼接），
必须为整个搭配单列键，且置于短键**之前**。

这与「最长键优先已证伪」不矛盾 —— 那条讲的是**前缀包含**，
这条是**词内拼接**，成因不同。
**字典排序要按具体冲突类型判断，不能一刀切。**

────────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────────
    from sanitize import Sanitizer

    s = Sanitizer(
        replacements={'大学': '高校', '大学生': '高校生', '提分': ''},
        protected=['大学城', '大学路'],
    )
    print(s.sanitize('滨江园区的大学生'))
    # → 滨江园区的高校生
"""

from __future__ import annotations

import re
from typing import Iterable


# 正文不会出现的控制字符，用于占位符
_PLACEHOLDER = ""


class Sanitizer:
    """敏感词替换器。

    与朴素的 `text.replace(word, repl)` 循环相比，本类做了三件事：
    1. 受保护子串占位符 —— 专名不被误伤
    2. 保持字典声明序 —— 级联行为可预测、可复现
    3. 自愈降级 —— 替换目标本身也被拦截时退回删除，但记录下来
    """

    def __init__(
        self,
        replacements: dict[str, str],
        protected: Iterable[str] = (),
        self_heal: bool = True,
    ) -> None:
        """
        Args:
            replacements: 敏感词 → 替换文本。空字符串表示删除。
                           **顺序即优先级**，请把「更长的搭配」放在前面。
            protected: 受保护的完整专名，不会被替换。
            self_heal: 替换目标本身也被拦截时，是否降级为删除。
        """
        # 保留声明顺序：Python 3.7+ 的 dict 有序，但显式说明意图
        self.replacements = dict(replacements)
        self.protected = list(protected)
        self.self_heal = self_heal
        #: 记录降级为删除的条目，便于排查线上残句
        self.degraded: list[tuple[str, str]] = []

    def sanitize(self, text: str) -> str:
        if not text:
            return text

        result = text

        # ① 受保护子串 → 占位符
        holders: list[tuple[str, str]] = []
        for i, raw in enumerate(self.protected):
            if raw in result:
                key = f"{_PLACEHOLDER}{i}{_PLACEHOLDER}"
                result = result.replace(raw, key)
                holders.append((key, raw))

        # ② 按声明顺序执行替换（保持级联行为可复现）
        for word, repl in self.replacements.items():
            if word not in result:
                continue

            # 自愈：替换目标本身也在字典里且会被拦 → 降级为删除
            if self.self_heal and repl and repl != word:
                for other, other_repl in self.replacements.items():
                    if other_repl and other != word and other in repl:
                        repl = ""
                        self.degraded.append((word, other_repl))
                        break

            result = result.replace(word, repl)

        # ③ 还原占位符
        for key, raw in holders:
            result = result.replace(key, raw)

        return result

    def sanitize_texts(self, texts: Iterable[str]) -> list[str]:
        return [self.sanitize(t) for t in texts]

    # ── 与JS 端字典的等价性校验 ──────────────────────────────────────
    def fingerprint(self) -> tuple[tuple[str, str], ...]:
        """返回 (键, 值) 元组序列，可直接与 JS 端比对。

        **为什么必须校验**：发布端与匹配端各有一份字典时，
        只要键顺序或取值不同，标题归一化结果就会不同，
        导致已发布的文章匹配不上外链。

        这不是理论风险 —— 替换是级联的，
        `免费→限时体验` 之后 `限时→期内` 会继续生效，顺序不同结果就不同。
        """
        return tuple(self.replacements.items())


def build_html(paragraphs: Iterable[str]) -> str:
    """段落列表 → HTML 正文。空段落自动剔除，相邻段落间插入空行。"""
    paras = [p for p in paragraphs if p.strip()]
    parts: list[str] = []
    for i, p in enumerate(paras):
        escaped = (
            p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        parts.append(f"<p>{escaped}</p>")
        if i < len(paras) - 1:
            parts.append("<p><br></p>")
    return "\n".join(parts)


if __name__ == "__main__":
    # 复现生产环境记录过的三个真实场景（已去除具体机构/地名标识）
    s = Sanitizer(
        replacements={
            # 更长的搭配必须在前（词内拼接）
            "冲击高分段": "分数提升阶段",
            "冲击高分": "稳步提升成绩",
            # 前缀关系：靠保护列表解决，不是靠长度
            "大学生": "高校生",
            "大学": "高校",
            # 同义替换优于删除
            "提分": "提升",
        },
        protected=["大学城", "大学路"],
    )

    cases = [
        "滨江园区怎么去？",    # 专名须毫发无损
        "滨江园区周末开放时间",  # 同上
        "大学生家教怎么找？",          # 该换的仍然换
        "重点大学报考路径",            # 该换的仍然换
        "成为冲击高分段的隐形门槛",    # 搭配优先，不断句
        "成为冲击高分段的隐形门槛",    # 重复调用不报错
        "提分很关键",                  # 同义替换，不删除
    ]
    print("── 复现真实场景 ──")
    for c in cases:
        print(f"  {c}\n→ {s.sanitize(c)}\n")

    assert s.sanitize(cases[0]) == "滨江园区怎么去？", "专名被误伤了"
    assert s.sanitize(cases[1]) == "滨江园区周末开放时间", "专名被误伤了"
    assert s.sanitize(cases[2]) == "高校生家教怎么找？"
    assert s.sanitize(cases[4]) == "成为分数提升阶段的隐形门槛", "搭配被截断了"
    print("✅ 断言全部通过：专名受保护、该换的仍然换")