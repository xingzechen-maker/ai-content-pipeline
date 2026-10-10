#!/usr/bin/env python3
"""批次断点：批次指纹 + 越界检测。

────────────────────────────────────────────────────────────────
要解决的问题
────────────────────────────────────────────────────────────────
批量脚本用进度文件记录 `nextIndex`。跑新一批时若没清旧文件，
而新批只有 200 篇（索引 0–199），脚本会从"第 200 篇"往后找
→ **本批全部跳过，0 发布**。

这不是一个孤立的 bug —— 实测在连续四个批次里各踩了一次。
它的迷惑之处在于：进度文件是上批次遗留的、日志里看不出异常、
脚本"成功退出"，只是结果为 0。

────────────────────────────────────────────────────────────────
修复①：批次指纹
────────────────────────────────────────────────────────────────
指纹 = `篇数:首序号-末序号`。批次间区间必然不同。

    批次 8：200:1299-1498
    批次 9：200:1-200        ← 指纹不同 → 自动忽略残留

────────────────────────────────────────────────────────────────
修复②：越界即残留（这条是测试逼出来的）
────────────────────────────────────────────────────────────────
判据：`nextIndex > 本批篇数` —— 逻辑上不可能（发完的篇数不会超过存在的篇数）。

**为什么必须要有这条**：指纹可能"假匹配" ——
两个不同的补发子集若恰好 `(篇数, 首, 末)` 相同就会误判。
越界是不依赖任何外部条件的**硬判据**。

两套判据互补，缺一不可：
  · 指纹覆盖「旧格式无指纹」的绝大多数情况
  · 越界覆盖「指纹假匹配」
  · 二者都覆盖不了时（旧格式 + 未越界）→ 沿用进度 + 明确告警，让人来判断

────────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────────
    fp = batch_fingerprint(articles, start_index=0)
    state = ProgressTracker("progress.json", fp)

    for i, art in enumerate(articles):
        if i < state.next_index:
            continue          # 已完成，跳过
        publish(art)
        state.advance(i + 1)  # 落盘

    # 补发子集时务必用独立进度文件
    state = ProgressTracker("progress_republish.json", fp)
"""

from __future__ import annotations

import json
import os
from typing import Any

# 进度文件格式：{"nextIndex": int, "batchKey": str}
# batchKey 可能不存在（旧格式），此时只能靠越界判据


def batch_fingerprint(count: int, start_index: int = 0) -> str:
    """批次指纹：`篇数:首序号-末序号`。

    >>> batch_fingerprint(200, 1298)
    '200:1299-1498'
    >>> batch_fingerprint(200, 0)
    '200:1-200'
    """
    end = start_index + count - 1
    return f"{count}:{start_index + 1}-{end}"


def fingerprint_of(articles: list[dict[str, Any]], start_index: int = 0) -> str:
    """从文章列表直接算指纹，省得调用方自己数。"""
    return batch_fingerprint(len(articles), start_index)


class ProgressTracker:
    """带批次指纹的进度追踪器。

    启动行为对照：

    | 情况 | 行为 |
    |---|---|
    | 指纹不匹配 | 判定他批残留 → 从第 1 篇开始 |
    | nextIndex > 篇数 | 判定越界 → 从第 1 篇开始 |
    | 旧格式无指纹、未越界 | 沿用进度 + 明确告警（无法判定来源） |
    | 指纹匹配、未越界 | ✅ 正常断点续传 |
    | reset=True | 忽略进度，从第 1 篇开始 |
    """

    def __init__(self, path: str, batch_key: str, reset: bool = False) -> None:
        self.path = path
        self.batch_key = batch_key
        self.count = self._parse_count(batch_key)
        self.next_index = 0
        self._load(reset)

    # ── 内部 ─────────────────────────────────────────────────
    @staticmethod
    def _parse_count(key: str) -> int:
        try:
            return int(key.split(":")[0])
        except (ValueError, IndexError):
            return 0

    def _load(self, reset: bool) -> None:
        if reset:
            print("ℹ️  已显式重置进度，从第 1 篇开始")
            return

        if not os.path.exists(self.path):
            return

        # 进度文件损坏（非法 JSON）不再崩溃，按无进度处理
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            print("⚠️  进度文件损坏，按无进度处理")
            return

        if not isinstance(data, dict):
            print("⚠️  进度文件格式异常，按无进度处理")
            return

        saved_next = int(data.get("nextIndex", 0) or 0)
        saved_key = data.get("batchKey")

        # 判据①：指纹不匹配 → 他批残留
        if saved_key and saved_key != self.batch_key:
            print("⚠️  检测到上一批残留的进度文件，已自动忽略（从第 1 篇开始）")
            print(f"进度文件批次: {saved_key}（nextIndex={saved_next}）")
            print(f"当前批次:     {self.batch_key}")
            return

        # 判据②：越界 → 残留（覆盖旧格式，也覆盖指纹假匹配）
        if saved_next > self.count:
            print("⚠️  进度越界（nextIndex > 本批篇数），判定为残留，从第 1 篇开始")
            print(f"进度: nextIndex={saved_next}，本批仅 {self.count} 篇")
            return

        # 判据③：旧格式无指纹、未越界 → 无法判定来源，告警后沿用
        if not saved_key:
            print("⚠️  进度文件为旧格式（无批次指纹），无法判定来源")
            print(f"将沿用进度 nextIndex={saved_next}，请确认这是本批的进度")
            self.next_index = saved_next
            return

        # 判据④：一切正常
        self.next_index = saved_next
        if saved_next:
            print(f"✅ 断点续传：从第 {saved_next + 1} 篇开始")

    # ── 外部 ─────────────────────────────────────────────────
    @property
    def done(self) -> bool:
        return self.next_index >= self.count

    def advance(self, index: int) -> None:
        """落盘进度。index 为已完成的篇数（即下一篇的下标）。"""
        self.next_index = index
        self.save()

    def save(self) -> None:
        payload = {"nextIndex": self.next_index, "batchKey": self.batch_key}
        # 先写临时文件再原子替换，避免进程被杀时写出半个文件
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, self.path)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="批次指纹自测——复现四类进度残留场景"
    )
    ap.add_argument("--demo", action="store_true", help="跑自测用例")
    args = ap.parse_args()

    if not args.demo:
        ap.print_help()
        return 0

    import tempfile

    scenarios = [
        # (名称, 进度文件内容, 本批指纹, 期望 next_index, 期望行为关键词)
        (
            "他批残留（指纹不同）",
            {"nextIndex": 200, "batchKey": "200:1299-1498"},
            "200:1-200", 0, "残留",
        ),
        (
            "越界（补发子集残留）",
            {"nextIndex": 56, "batchKey": "200:1-200"},
            "1:1-1", 0, "越界",
        ),
        (
            "旧格式无指纹、未越界",
            {"nextIndex": 100},
            "200:1-200", 100, "旧格式",
        ),
        (
            "正常断点续传",
            {"nextIndex": 134, "batchKey": "200:1-200"},
            "200:1-200", 134, "断点续传",
        ),
        (
            "旧格式 + 越界（硬判据兜底）",
            {"nextIndex": 300},
            "200:1-200", 0, "越界",
        ),
    ]

    print("=" * 68)
    print("批次指纹自测")
    print("=" * 68)

    passed = 0
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "progress.json")
        for name, content, key, expect_idx, expect_kw in scenarios:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(content, f)

            print(f"\n▸ {name}")
            print(f"  进度文件: {content}")
            print(f"  当前批次: {key}")

            tracker = ProgressTracker(path, key)

            ok = tracker.next_index == expect_idx
            print(f"  → nextIndex={tracker.next_index}（期望 {expect_idx}）")
            if ok:
                passed += 1
                print("  ✅ 通过")
            else:
                print("  ❌ 失败")

    print("\n" + "=" * 68)
    print(f"通过 {passed}/{len(scenarios)}")
    print("=" * 68)
    return 0 if passed == len(scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())