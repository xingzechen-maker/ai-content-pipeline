/**
 * 多栏目发布：逐篇切换目标栏目 + 统一模板。
 *
 * 这个文件不含任何特定平台的接口实现 —— 它是通用框架。
 * 接入具体系统需自行适配：栏目 URL 模板、模板下拉选择器、
 * "已提交且可继续下一条"的判据。
 *
 * ────────────────────────────────────────────────────────────────
 * 坑 1（本文件的核心）：提交后表单重置，但发布上下文会保留
 * ────────────────────────────────────────────────────────────────
 * 多数后台的"保存并继续"是：**提交成功后清空表单字段、留在同一页**，
 * 好让你接着填下一条。单栏目下这没问题。
 *
 * 多栏目下它变成一个静默故障：
 *
 *     篇1 → 栏目A  → 提交 → 表单清空，仍在栏目A
 *     篇2 → 需要栏目B → 直接填 → 提交 → **进了栏目A** ❌
 *     篇3 → 同样进了栏目A                              ❌
 *
 * 每一步都"发布成功"，没有任何报错。故障要等到抓链阶段
 * 才暴露成"这批外链一条都没匹配上" —— 那时看起来像是匹配脚本坏了。
 *
 * 修法：**每篇提交前比对当前上下文与目标栏目，不一致就重新导航。**
 * 判定要读"提交时真正使用的那个值"（通常是隐藏域），
 * 而不是读 URL —— URL 可能已经变了，或被重定向改写。
 *
 * ────────────────────────────────────────────────────────────────
 * 坑 2：模板不会跟着栏目走
 * ────────────────────────────────────────────────────────────────
 * 模板下拉通常预设为「该栏目的默认模板」。跨栏目切换时它会跟着变，
 * 于是同一批文章里混着不同模板。表现是页面样式不一致，但同样**不报错**。
 *
 * 修法：切栏目之后**显式设置**目标模板，不要依赖默认值。
 * 即使当前默认值已经对，显式设一遍也是廉价的保险 ——
 * 对方改一次栏目默认模板，整批页面就静默变形。
 *
 * ────────────────────────────────────────────────────────────────
 * 坑 3：按栏目分组能提速，但别高估收益
 * ────────────────────────────────────────────────────────────────
 * 逐篇切换栏目的直觉优化是"先排序，同栏目连发"。
 * 方向对，量级容易估错。实测 200 篇：
 *
 *     原始顺序（按选题类别排）→ 栏目切换 40 次
 *     按栏目排序后           → 切换 6 次（栏目数 - 1）
 *
 *     单次切换开销：均值 1.4 秒
 *     40 次合计 55 秒，占总耗时约 3%
 *     → 省下约 47 秒（16.3 分钟 → 15.5 分钟）
 *
 * 瓶颈在**每篇填表+提交本身（约 5 秒/篇）**，切换只是小头。
 * 如果你在优化这条链路，先去量单篇耗时，别凭直觉认定瓶颈在切换。
 *
 * 排序要放在计算批次指纹**之前** —— 指纹用首尾序号，
 * 排序后首尾固定且可复现，断点续传才不会错位。
 * 排序只改**发布先后**，不改变任何文章的固有编号。
 */

'use strict';

/**
 * 栏目路由：从文章对象取目标栏目。
 *
 * @param {object} article         文章对象，需带 column_id
 * @param {object} opts.defaultColumnId  文章缺 column_id 时的兜底栏目
 * @param {string} opts.addUrlTemplate    形如 '.../add?col=<ID>'
 */
function makeColumnRouter(opts) {
  const {
    defaultColumnId = '',
    addUrlTemplate = '',
  } = opts;

  return function targetUrlFor(article) {
    const id = (article && article.column_id) || defaultColumnId;
    if (!id) throw new Error('文章缺 column_id，且未提供兜底栏目');
    return addUrlTemplate.replace('<ID>', encodeURIComponent(id));
  };
}

/**
 * 确保页面处于本篇的目标栏目。
 *
 * 关键：`readCurrentColumn` 必须返回**提交时真正使用的那个值**。
 * 典型实现是读隐藏域（如 <input name="colid">），它才是表单提交的字段；
 * 读 URL 不可靠 —— 重定向可能已经改写了它。
 *
 * @returns {Promise<boolean>} 是否发生了切换
 */
async function ensureColumn(page, article, column) {
  const want = String((article && article.column_id) || column.defaultColumnId);
  const current = await readCurrentColumn(page);

  if (current === want) return false;

  await page.goto(column.targetUrlFor(article), {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  // 等表单真正就绪（所有字段都出现），否则后续填充会打空
  await column.waitFormReady(page);
  return true;
}

/** 读当前页面提交时使用的栏目值 —— 务必读隐藏域，勿读 URL。 */
async function readCurrentColumn(page) {
  return page.evaluate(() => {
    const hidden = document.querySelector(
      'input[type=hidden][name*="col" i], input[type=hidden][name*="catid" i]'
    );
    if (hidden && hidden.value) return String(hidden.value);
    const m = location.href.match(/[?&]col=(\d+)/);
    return m ? m[1] : '';
  }).catch(() => '');
}

/**
 * 显式设置模板，不依赖栏目默认值。
 *
 * @param {string} templateRef 模板标识。优先按option 的 value 前缀匹配
 *                             （如 '91234#tpl' 可匹配 '91234#tpl' 与 '91234#other'），
 *                             因为显示名会变、value 前缀稳定。
 */
async function setTemplate(page, selector, templateRef) {
  if (!templateRef) return { ok: false, reason: '未指定模板' };
  const id = String(templateRef).split('#')[0];

  return page.evaluate(([sel, tid]) => {
    const sel_ = document.querySelector(sel);
    if (!sel_) return { ok: false, reason: '找不到模板下拉' };
    const opt = [...sel_.options].find(
      (o) => o.value === tid || o.value.startsWith(tid + '#')
    );
    if (!opt) return { ok: false, reason: '无匹配模板选项 ' + tid };
    if (sel_.value !== opt.value) {
      sel_.value = opt.value;
      sel_.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return { ok: true, set: sel_.value };
  }, [selector, id]).catch((e) => ({ ok: false, reason: e.message }));
}

/**
 * 按栏目分组排序 —— 同栏目连发，把切换次数降到「栏目数 - 1」。
 *
 * 同栏目内用 doc_index 兜底，保证顺序稳定可复现（断点续传依赖这个）。
 * 必须在计算批次指纹之前调用。
 *
 * @param {Array} articles
 * @param {boolean} enabled false 可关闭（旧版单栏目流程）
 * @returns {Array} 同一数组引用（原地排序）
 */
function sortByColumn(articles, enabled = true) {
  if (!enabled || !Array.isArray(articles) || articles.length < 2) return articles;
  // 全部带 column_id 才排；部分缺失说明数据有问题，交给上层报错而不是静默乱序
  if (!articles.every((a) => a && a.column_id)) return articles;

  articles.sort((a, b) =>
    (a.column_id - b.column_id) || (a.doc_index - b.doc_index)
  );
  return articles;
}

/** 排序后打印分组，便于启动时目视核对。 */
function logColumnGroups(articles, label = '📂 已按栏目分组排序') {
  const groups = new Map();
  for (const a of articles) {
    const k = `${a.column || ''}(${a.column_id})`;
    groups.set(k, (groups.get(k) || 0) + 1);
  }
  console.log(`${label}（${groups.size} 组，切换次数上限 ${groups.size - 1}）：`);
  for (const [k, n] of groups) console.log(`   ${k} : ${n} 篇`);
  return groups.size;
}

module.exports = {
  makeColumnRouter,
  ensureColumn,
  readCurrentColumn,
  setTemplate,
  sortByColumn,
  logColumnGroups,
};
