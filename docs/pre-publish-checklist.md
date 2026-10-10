# 上架前自查清单

**每次 `git push` 前跑一遍。** 公开仓库一旦推上去，删除历史也难彻底——
GitHub 的 fork 缓存、其他用户的克隆都可能已留存。

## 一条命令搞定

```bash
# 从 git 实际跟踪的文件扫描（不是磁盘文件——磁盘上可能有 .gitignore 掉的敏感文件）
git ls-files -z | xargs -0 grep -rn -i -E \
"xdf|新东方|manage\.xdf|e2api|钉钉|tencent|docs\.qq|微信|13023255127|xdf5127|卫正|workbuddy|松江|上海|华东|高考|综评|校区|1对1|sk-[a-zA-Z0-9]{16,}"
```

**输出为空 = 通过。** 有输出就停下来清理，不要 push。

## 手动确认清单

- [ ] 上面那条命令输出为空
- [ ] `git ls-files` 里没有 `.env`、`inventory.json`、`articles.json`、`*.log`、`*.docx`
- [ ] README 里的示例数据是中性内容，不是真实业务内容
- [ ] 没有任何截图/图片带 EXIF 定位信息
- [ ] commit message 里没有"新东方""xdf""钉钉"等字样
- [ ] `git log --all` 的历史里也没有敏感内容

## 如果已经推送后发现泄露

GitHub 只能**改写历史**，不能保证彻底删除（fork、缓存、第三方镜像可能已留存）：

```bash
# 1. 先删掉远程仓库（最干净）
#    GitHub 网页 → Settings → Danger Zone → Delete repository

# 2. 若只是想改历史，用 BFG 或 git-filter-repo，不要用 filter-branch
pip install git-filter-repo
git filter-repo --invert-paths --path secrets/ --path .env
```

**结论**：泄露后最有效的手段是**删仓库重建**。所以务必在首次 push 前完成上面全部检查。

## 长期维护约定

这个仓库会持续迭代四个月。每次新增文件前问自己一句：

> 这个文件里有没有"只有我这份工作知道"的东西？

具体指：
- 具体机构名、地名、校名、班级名
- 具体的选题列表、已发布内容清单
- 敏感词字典（即便脱敏过，原始版绝不能进）
- 账号、手机号、内部工单号、群名

**替代做法**：把这类内容放进 `.gitignore`，另存到本地或私有仓库。仓库里只保留"方法与机制"，数据永远不入库。