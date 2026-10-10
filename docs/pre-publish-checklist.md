# 上架前自查清单

**每次 `git push` 前跑一遍。** 公开仓库一旦推上去，删除历史也难彻底——
GitHub 的 fork 缓存、其他用户的克隆都可能已留存。

## 一条命令搞定

仓库根目录已附带 `tools/scan_secrets.sh`，它扫描的是 **git 实际跟踪的文件**
（不是磁盘文件——磁盘上可能有 `.gitignore` 掉的敏感文件，而那些不会被推送）。

**Linux / macOS / Git Bash：**

```bash
bash tools/scan_secrets.sh
```

**Windows PowerShell：**

```powershell
bash tools/scan_secrets.sh
```

输出 `✅ 未发现问题` = 通过。有输出就停下来清理，**不要 push**。

## 为什么要扫「git 跟踪的文件」

`grep -r .` 会连 `.git` 目录和被 `.gitignore` 排除的业务数据一起扫，
结果噪声极大、容易让人麻木。

而**真正会泄露的，只有被 `git add` 跟踪的文件**。所以正确做法是：

```bash
git ls-files -z | xargs -0 grep -rn -i -E "$PATTERN"
```

配套的排除清单已在 `.gitignore` 里（业务快照、生成正文、日志、进度文件、密钥）。

## 手动确认清单

- [ ] `bash tools/scan_secrets.sh` 输出「未发现问题」
- [ ] `git ls-files` 里没有 `.env`、业务数据快照、生成正文、`*.log`、`*.docx`
- [ ] `examples/` 里的示例是**虚构的示例**，不是真实业务内容
- [ ] 没有任何截图/图片带 EXIF 定位信息
- [ ] `git log --all` 的 commit message 里没有内部信息
- [ ] README 里的场景描述用的是中性词汇，能被读成"任何电商/内容团队都遇到过的问题"

## 自查表本身也不能泄露

⚠️ 这份清单就是个真实教训：我第一版把内部关键词表**原样**写进了这个文件，
结果仓库里就等于留了一份"敏感词索引"——扫描器命中了自己。

**规则**：自查清单里不要出现具体的目标机构名、产品名、服务商标识。
用「机构名」「服务商标识」「地域名」这类**类别描述**代替。

## 如果已经推送后发现泄露

GitHub 只能**改写历史**，不能保证彻底删除（fork、缓存、第三方镜像可能已留存）：

```bash
# 1. 最干净的做法：删掉远程仓库后重建
#    GitHub 网页 → Settings → Danger Zone → Delete repository

# 2. 若只是想移除某个文件，用 git-filter-repo，不要用 filter-branch
pip install git-filter-repo
git filter-repo --invert-paths --path secrets/ --path .env
git push --force
```

**结论**：泄露后唯一可靠的手段是**删仓库重建**。
所以务必在首次 push 前完成上面全部检查。

## 长期维护约定

这个仓库会持续迭代。每新增文件前问自己一句：

> 这个文件里有没有"只有我这份工作知道"的东西？

具体指：
- 具体机构名、地名、校名、班级名、门店名
- 具体的选题列表、已发布内容清单
- 敏感词字典（即便脱敏过，原始版绝不能进）
- 账号、手机号、内部工单号、内部群名
- 任何未经公开的业务数据截图

**替代做法**：这类内容放进 `.gitignore`，另存本地或私有仓库。
公开仓库里只保留"方法与机制"，**数据永远不入库**。