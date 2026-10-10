/**
 * 浏览器自动化：弹窗处理与稳定性防护。
 *
 * 这个文件不含任何特定平台的接口实现 —— 它是通用的自动化框架。
 * 接入具体系统需自行适配 fill_form() / submit() / read_result()。
 *
 * ────────────────────────────────────────────────────────────────
 * 坑1：一个弹窗残留污染后续所有篇目
 * ────────────────────────────────────────────────────────────────
 * 某篇触发"标题重复"弹窗后，该弹窗**未被关闭**，
 * 后续每一篇判定结果时都会读到它；因其文本含"重复"二字，
 * 判定一律为失败 → **1 个残留弹窗连续污染了 9 篇**。
 *
 * 症状极具迷惑性：日志显示第 1 篇真重复，
 * 第 2~10 篇标题并不重复却全被跳过，而且弹窗文本里其实带着"添加成功"。
 *
 * 三层修复：
 *   ① dismissDialogs 增加「文案型按钮」点击（只点叶子节点），兜底强制移除残留层
 *   ② waitForSubmitResult：提交前**快照已有弹窗文本**，判定时**过滤陈旧弹窗**（根治误判）
 *   ③ 失败分支也调用 dismissDialogs，防止污染下一篇
 *
 * ────────────────────────────────────────────────────────────────
 * 坑2：单篇异常打死整批
 * ────────────────────────────────────────────────────────────────
 * 发布循环没有 try/catch 时，任意未捕获异常会冒泡终止整个进程
 * → 实测崩在第 45 篇，**第 45~100 篇一篇都没发**。
 *
 * ────────────────────────────────────────────────────────────────
 * 坑3：看似 A 崩了，其实是 B 死了
 * ────────────────────────────────────────────────────────────────
 * 报 `Target page, context or browser has been closed`、CDP 端口连不上，
 * 真因是常驻浏览器进程**自己没有注册 dialog 处理器**：
 *
 *   Playwright 规则：没注册 'dialog' 监听器时会**自动 dismiss** 弹窗。
 *   同一个弹窗被两个 Playwright 客户端同时抢：
 *     ① 发布脚本的 handler 调 accept()
 *     ② 常驻进程的自动处理发现"弹窗没了"→ 抛异常 → 未捕获 → 进程退出
 *     ③ 常驻进程退出，把它启动的浏览器一并带走
 *
 * "看着像发布脚本崩，其实是启动器死了。"
 *
 * 修复两层：注册 handler + 常驻进程对任何异常兜底（它的唯一使命是"活着"）。
 */

'use strict';

const CLOSE_BUTTON_TEXTS = ['修改标题', '关闭', '×', 'x', '取消', '知道了', '确定'];
const ERROR_HINTS = ['不能包含', '违禁', '敏感', '重复', '失败', '错误'];

/**
 * 给 page 挂上 dialog 处理器，阻止 Playwright 走自动处理路径。
 *
 * 必须挂在常驻进程上——否则常驻进程会因弹窗竞态而崩溃。
 *
 * @param {import('playwright').Page} p
 * @param {(msg: string) => void} log
 */
function attachDialogHandler(p, log = console.log) {
  try {
    p.on('dialog', async (d) => {
      try {
        log(`⚠️ 页面弹窗（${d.type()}）：${(d.message() || '').slice(0, 150)}`);
      } catch (_) { /* 日志失败不应影响主流程 */ }
      try {
        await d.accept();
      } catch (_) { /* 竞态：已被页面自身关闭 */ }
    });
  } catch (_) { /* 页面已关闭 */ }
}

/** 给常驻进程装上全局兜底：任何异常都不该带走它。 */
function installResilience(log = console.log) {
  process.on('uncaughtException', (e) =>
    log(`⚠️ 已忽略异常，继续常驻：${e?.message || e}`));
  process.on('unhandledRejection', (e) =>
    log(`⚠️ 已忽略拒绝，继续常驻：${e?.message || e}`));
}

/**
 * 清理页面上残留的弹窗层。
 *
 * 关键：只点**叶子节点**的按钮。jQuery 类弹窗常有嵌套 span，
 * 直接点容器点不到真正的事件绑定元素，表现为「点了完全没反应，
 * 不报错也不提交」。
 */
async function dismissDialogs(page, log = console.log) {
  let dismissed = 0;
  try {
    const dialogs = await page.locator(
      '.layui-layer, .layui-layer-dialog, .dialog, .modal, [role="dialog"]'
    ).all();

    for (const d of dialogs) {
      try {
        const visible = await d.isVisible();
        if (!visible) continue;

        // 先尝试文案型按钮，只点叶子节点
        let clicked = false;
        for (const text of CLOSE_BUTTON_TEXTS) {
          const btn = d.locator(`button:has-text("${text}"), a:has-text("${text}"), span:text-is("${text}")`).last();
          if ((await btn.count()) > 0) {
            await btn.click({ timeout: 2000 });
            clicked = true;
            break;
          }
        }

        // 兜底：强制移除残留层
        if (!clicked) {
          await d.evaluate((el) => el.remove());
        }
        dismissed++;
      } catch (_) { /* 该弹窗可能已被其他代码关掉 */ }
    }
    if (dismissed) log(`  清理残留弹窗 ${dismissed} 个`);
  } catch (_) { /* 页面导航中，下轮再来 */ }
  return dismissed;
}

/**
 * 快照当前所有可见弹窗的文本。
 * 提交前调用，判定结果时用来过滤陈旧弹窗。
 */
async function snapshotDialogs(page) {
  try {
    return await page.evaluate(() => {
      const out = [];
      document.querySelectorAll(
        '.layui-layer, .layui-layer-dialog, .dialog, .modal, [role="dialog"]'
      ).forEach((el) => {
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') return;
        const t = (el.innerText || el.textContent || '').trim();
        if (t) out.push(t.slice(0, 200));
      });
      return out;
    });
  } catch (_) {
    return [];
  }
}

/**
 * 判断提交结果。
 *
 * ⚠️ 这里有个容易踩的坑：**不要用 `/成功/` 匹配弹窗就判定成功。**
 * 平台的敏感词提示本身就可能包含「成功」二字
 * （如「内容不能包含关键字：成功案例」），会被误判为发布成功
 * → 该篇不重试、直接关浏览器、实际未落库，
 * 症状是「汇总 50 但 CMS 里只有 49」。
 *
 * 正确做法：必须匹配"动作词"，且**不含错误特征词**。
 */
function looksLikeSuccess(text, staleTexts = []) {
  if (!text) return false;
  if (staleTexts.some((s) => s && s === text)) return false; // 陈旧弹窗
  if (ERROR_HINTS.some((h) => text.includes(h))) return false;
  const SUCCESS_VERBS = ['添加成功', '保存成功', '发布成功', '提交成功', '操作成功'];
  return SUCCESS_VERBS.some((v) => text.includes(v));
}

/**
 * 等待提交结果，并**过滤提交前的陈旧弹窗**（根治误判）。
 *
 * @param {import('playwright').Page} page
 * @param {number} timeout ms
 * @param {string[]} staleTexts 提交前的弹窗快照
 */
async function waitForSubmitResult(page, timeout = 15000, staleTexts = []) {
  const deadline = Date.now() + timeout;
  let last = '';
  while (Date.now() < deadline) {
    const now = await snapshotDialogs(page);
    const fresh = now.filter((t) => !staleTexts.includes(t));
    if (fresh.length) {
      last = fresh[fresh.length - 1];
      if (looksLikeSuccess(last, staleTexts)) return { ok: true, text: last };
      if (ERROR_HINTS.some((h) => last.includes(h))) {
        return { ok: false, text: last, uncertain: false };
      }
    }
    await page.waitForTimeout(400);
  }
  // ⚠️ 超时是「不确定」，不是「失败」—— 请求可能已到达服务端并落库。
  //    不要直接重发，否则会产生重复内容。
  return { ok: false, text: last, uncertain: true };
}

/**
 * 发布单篇：任何异常都降级为「跳过本篇」，绝不终止整批。
 *
 * ⚠️ 等待表单就绪时必须等**所有**输入框。
 *    只等第一个选择器是必踩的坑—— 后续框渲染更晚，等到就崩。
 *
 * @param {import('playwright').Page} page
 * @param {object} item
 * @param {{selectors: object, log?: Function}} cfg
 */
async function publishOne(page, item, cfg) {
  const log = cfg.log || console.log;
  try {
    await waitForFormReady(page, cfg.selectors);

    // 提交前先清理 + 快照弹窗
    await dismissDialogs(page, log);
    const stale = await snapshotDialogs(page);

    await fillForm(page, item, cfg.selectors);
    await submit(page, cfg.selectors);

    const res = await waitForSubmitResult(page, cfg.timeout || 15000, stale);
    if (!res.ok) await dismissDialogs(page, log); // 失败也要清理，防污染
    return res;
  } catch (err) {
    log(`⚠️ 本篇异常，跳过：${err?.message || err}`);
    await dismissDialogs(page, log).catch(() => {});
    return { ok: false, text: '', uncertain: false, error: String(err) };
  }
}

/** 适配层：按选择器等待全部输入框就绪。 */
async function waitForFormReady(page, selectors) {
  await page.waitForSelector(selectors.title, { timeout: 20000 });
  // ⚠️ 必须等所有字段，漏一个就永久超时或必崩
  await page.waitForSelector(selectors.keywords, { timeout: 20000 });
  await page.waitForSelector(selectors.summary, { timeout: 20000 });
}

/** 适配层：填写表单。接入具体系统时重写。 */
async function fillForm(page, item, selectors) {
  await page.fill(selectors.title, item.title || '');
  await page.fill(selectors.keywords, item.keywords || '');
  await page.fill(selectors.summary, item.summary || '');
}

/** 适配层：提交。接入具体系统时重写。 */
async function submit(page, selectors) {
  await page.click(selectors.submit);
}

/**
 * ⚠️ 调试辅助代码绝不能拖垮主流程。
 * 实测：在登录跳转页上截图（字体加载慢）超时 30s 抛异常
 * → **整批 200 篇一篇没发**。
 * 所有调试截图都必须包 try/catch + 短超时。
 */
async function safeScreenshot(page, path, timeout = 5000) {
  try {
    await page.screenshot({ path, timeout });
    return true;
  } catch (_) {
    return false;
  }
}

module.exports = {
  attachDialogHandler,
  installResilience,
  dismissDialogs,
  snapshotDialogs,
  looksLikeSuccess,
  waitForSubmitResult,
  publishOne,
  safeScreenshot,
};