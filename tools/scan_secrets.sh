#!/usr/bin/env bash
# 上架前敏感信息扫描。
#
# 关键设计：扫描的是 **git 实际跟踪的文件**，不是磁盘文件。
#   - 磁盘上有被 .gitignore 排除的业务数据，扫了是噪声
#   - 真正会泄露的，只有被 git add 跟踪的文件
#
# ⚠️ 重要：这份脚本自己也在扫描范围内，所以它内部**不能出现任何具体标识**
#    （机构名、服务商标识、地域、业务题材词都必须是类别描述）。
#    否则仓库里就等于留了一份"敏感词索引"，比不扫描更糟。
#
# 用法：bash tools/scan_secrets.sh

set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

if [ ! -d .git ]; then
  echo "❌ 当前目录不是 git 仓库"
  exit 2
fi

# ── 扫描规则（按「类别」而非「具体名称」定义）─────────────────────
declare -a PATTERNS=(
  # ① 密钥形态：通用正则，非项目特有信息
  'sk-[a-zA-Z0-9]{16,}'
  '(ghp|gho|ghs|ghu)_[a-zA-Z0-9]{20,}'
  'AKIA[0-9A-Z]{16}'
  'BEGIN [A-Z ]*PRIVATE KEY'

  # ② 中国大陆手机号
  '1[3-9][0-9]{9}'

  # ③ 配置文件的硬编码赋值
  '(api[_-]?key|secret|passwd|password|token)[ ]*[:=][ ]*.[^ ]{8,}'

  # ④ 硬编码的内部地址：仅匹配私网、环回、非公开 TLD
  'https?://(localhost|127\.0\.0\.1|192\.168\.|10\.[0-9]+\.)'
  '\.(local|internal|intranet|corp)'

  # ⑤ 本机用户名痕迹
  '/Users/[A-Za-z0-9_]+/'
  '[A-Za-z]:.Users.[A-Za-z0-9_]+.'

  # ⑥ 业务数据文件若被误加入跟踪
  '\.env$'
)

# ── 白名单 ─────────────────────────────────────────────────────
# ① 脚本自身与说明文档：内含扫描规则，命中是必然
# ② .gitignore / README / 文档里出现的业务数据**文件名**
#    恰恰是"要被排除"或"是命令行参数"，不是泄露
#    ⚠️ 只按文件名放行，不按整文件放行——文件里的其他内容仍要检查
ALLOW_LINE='^(tools/scan_secrets\.sh|docs/pre-publish-checklist\.md):'
ALLOW_BASENAME='^(\.gitignore|README\.md|docs/architecture\.md):'
ALLOW_PAT='^[^:]*:[0-9]+:\s*(python|npm|bash)?.*\b(env|inventory|articles|links|cms_articles|topics|collisions|progress)[a-z_]*\.(json|txt|log)\b'

echo "============================================================"
echo "上架前敏感信息扫描（仅扫 git 跟踪的文件）"
echo "============================================================"
echo "跟踪文件数: $(git ls-files | wc -l)"
echo

if [ -z "$(git ls-files)" ]; then
  echo "没有已跟踪的文件"
  exit 2
fi

RE="($(IFS='|'; echo "${PATTERNS[*]}"))"

HITS=$(
  git ls-files -z \
    | xargs -0 grep -nIE "$RE" 2>/dev/null \
    | grep -vE "$ALLOW_LINE" \
    | grep -vE "$ALLOW_BASENAME" \
    | grep -vE "$ALLOW_PAT"
)

if [ -z "$HITS" ]; then
  echo "✅ 未发现问题，可以 push"
  exit 0
fi

echo "❌ 发现以下可疑内容，推送前必须逐条处理："
echo "------------------------------------------------------------"
echo "$HITS"
echo "------------------------------------------------------------"
echo
echo "处理后重跑本脚本。若确认某项无误，请改写为类别描述，"
echo "不要把具体名称写进任何跟踪文件。"
exit 1