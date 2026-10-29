#!/usr/bin/env bash
#
# harden-memory.sh  ——  卫戍协议 / 大肥鱼 主机内存加固脚本
#
# 目标（对应诊断项 ② 主机内存偏紧的 OOM 风险）：
#   1) 新增一个 2GB 的 swap 文件作为 OOM 兜底（主机原本已有约 1GB swap 且已用 ~950MB，
#      说明内存压力真实存在；再补 2GB 给内核更多腾挪空间）。
#   2) 把内存预警线从 200MB 提到 400MB（提前报警，给运维反应时间）。
#
# 设计原则：
#   - 全程幂等：重复运行不会破坏已有配置，也不会重复挂载 swap。
#   - 安全：先校验磁盘余量（至少留 512MB），再创建 swap；只用 root 运行。
#   - 阈值改动保守：默认只“搜索并列出候选”，加 --apply-threshold 才替换，
#     且仅在命中唯一明确的常量时才改，避免误伤无关代码。
#
# 用法：
#   sudo bash harden-memory.sh                 # 仅做 swap（安全、可反复跑）
#   sudo bash harden-memory.sh --apply-threshold   # 额外把内存预警线 200→400
#
# 可调环境变量：
#   SWAP_SIZE   新 swap 文件大小，默认 2G（支持 G / M，如 1G）
#   SWAP_FILE   新 swap 文件路径，默认 /var/swap-stronghold.img（刻意避开常见 /swapfile，不覆盖已有 swap）
#   SWAPPINESS  内核换出倾向，默认 10（保内存为主，逼近 OOM 才换出）；想更早换出可调到 60

set -euo pipefail

SWAP_SIZE="${SWAP_SIZE:-2G}"
SWAP_FILE="${SWAP_FILE:-/var/swap-stronghold.img}"
SWAPPINESS="${SWAPPINESS:-10}"
THRESHOLD_OLD=200
THRESHOLD_NEW=400
APPLY_THRESHOLD=0
[[ "${1:-}" == "--apply-threshold" ]] && APPLY_THRESHOLD=1

if [[ $EUID -ne 0 ]]; then
  echo "❌ 请使用 root 运行：sudo bash $0" >&2
  exit 1
fi

echo "=================================================="
echo " 内存加固脚本开始"
echo " swap 大小 = $SWAP_SIZE    swap 文件 = $SWAP_FILE"
echo " swappiness = $SWAPPINESS    阈值改动 = $(((APPLY_THRESHOLD)) && echo 启用 || echo 仅报告)"
echo "=================================================="

echo
echo "===== [0] 当前内存 / swap 状态 ====="
free -m
echo "--- 磁盘可用（根分区） ---"
df -h /

# 计算所需 MB（仅支持末尾 G 或 M）
unit="${SWAP_SIZE: -1}"
num="${SWAP_SIZE%?}"
if [[ "$unit" == "G" ]]; then need_mb=$(( num * 1024 )); fi
if [[ "$unit" == "M" ]]; then need_mb=$(( num )); fi
avail_mb=$(df -m / | awk 'NR==2 {print $4}')
if (( avail_mb < need_mb + 512 )); then
  echo "❌ 根分区可用 ${avail_mb}MB，不足以安全创建 ${SWAP_SIZE} swap（需额外留 512MB 余量）。" >&2
  echo "   可改用更小值：SWAP_SIZE=1G sudo bash $0" >&2
  exit 1
fi
echo "✓ 磁盘余量充足（可用 ${avail_mb}MB ≥ 需要 ${need_mb}MB + 512MB 余量）"

echo
echo "===== [1] 创建并启用新 swap ====="
if swapon --show | awk '{print $1}' | grep -qx "$SWAP_FILE"; then
  echo "↺ $SWAP_FILE 已在启用，跳过创建。"
else
  if [[ -f "$SWAP_FILE" ]]; then
    echo "↺ $SWAP_FILE 已存在但未启用，先停用并重建以确保大小正确。"
    swapoff "$SWAP_FILE" 2>/dev/null || true
    rm -f "$SWAP_FILE"
  fi
  echo "→ 分配 $SWAP_SIZE（fallocate 失败则回退 dd）..."
  if ! fallocate -l "$SWAP_SIZE" "$SWAP_FILE" 2>/dev/null; then
    dd if=/dev/zero of="$SWAP_FILE" bs=1M count="$need_mb" status=progress
  fi
  chmod 600 "$SWAP_FILE"
  mkswap "$SWAP_FILE"
  swapon "$SWAP_FILE"
fi
echo "--- 当前 swap 列表 ---"
swapon --show

echo
echo "===== [2] 持久化到 /etc/fstab ====="
fstab_line="$SWAP_FILE none swap sw 0 0"
if grep -qxF "$fstab_line" /etc/fstab; then
  echo "↺ /etc/fstab 已有该条目，跳过。"
else
  echo "$fstab_line" >> /etc/fstab
  echo "✓ 已追加到 /etc/fstab（开机自动挂载）"
fi

echo
echo "===== [3] 调整 vm.swappiness 并持久化 ====="
sysctl -w "vm.swappiness=$SWAPPINESS"
conf=/etc/sysctl.d/99-stronghold.conf
touch "$conf"
if grep -q '^vm.swappiness=' "$conf"; then
  sed -i "s/^vm.swappiness=.*/vm.swappiness=$SWAPPINESS/" "$conf"
  echo "↺ 已更新 $conf"
else
  echo "vm.swappiness=$SWAPPINESS" >> "$conf"
  echo "✓ 已写入 $conf（开机生效）"
fi

echo
echo "===== [4] 内存预警线 ${THRESHOLD_OLD}MB → ${THRESHOLD_NEW}MB ====="
SEARCH_DIRS=()
for d in /opt/stronghold-stability /opt/stronghold-control /opt/bot-stack; do
  [[ -d "$d" ]] && SEARCH_DIRS+=("$d")
done
if (( ${#SEARCH_DIRS[@]} == 0 )); then
  echo "⚠️ 未找到 stronghold 相关目录（/opt/stronghold-stability 等），无法定位阈值常量。"
  echo "   请在服务器上手动查找：grep -rn '200' /opt | grep -iE 'mem|avail|warn|threshold|alert'"
else
  echo "搜索目录：${SEARCH_DIRS[*]}"
  echo "--- 与内存/预警相关的候选行（含数字 200，上下文 1 行） ---"
  mapfile -t HITS < <(grep -rEn -C1 -e "[^0-9]200([^0-9]|$)" \
      -e "mem(ory)?[_ ]?(warn|threshold|alert|limit)" \
      -e "(warn|threshold|alert|limit)[_ ]?(mem|memory|avail)" \
      "${SEARCH_DIRS[@]}" --include=*.py 2>/dev/null | head -60)
  if (( ${#HITS[@]} == 0 )); then
    echo "（未匹配到明显候选，阈值可能不在 .py 源码的常量里，或用了其它写法）"
  else
    printf '%s\n' "${HITS[@]}"
  fi

  if (( APPLY_THRESHOLD == 1 )); then
    echo
    echo "--- 尝试自动替换（仅当命中唯一明确赋值/比较时才执行） ---"
    # 只在形如  NAME = 200 / NAME: 200 / < 200 / <= 200 / > 200 且上下文含 mem/avail/warn 的行上替换
    MAPFILE_OUT=$(grep -rEl -e "[^0-9]200([^0-9]|$)" "${SEARCH_DIRS[@]}" --include=*.py 2>/dev/null)
    file_count=$(echo "$MAPFILE_OUT" | grep -c . || true)
    if (( file_count == 0 )); then
      echo "⚠️ 未找到含 200 的 .py 文件，跳过自动替换。"
    elif (( file_count > 1 )); then
      echo "⚠️ 在 $file_count 个文件中都出现了 200，盲目替换风险高，已中止。"
      echo "   请人工确认上述候选行后，再针对具体文件手动执行："
      echo "   sed -i 's/200/${THRESHOLD_NEW}/g' <具体文件>"
    else
      target_file="$MAPFILE_OUT"
      # 进一步确认这一行确实和内存预警相关
      if grep -Eq -e "mem(ory)?[_ ]?(warn|threshold|alert|limit)" -e "(warn|threshold|alert|limit)[_ ]?(mem|memory|avail)" "$target_file"; then
        echo "→ 命中文件：$target_file"
        echo "→ 替换前相关行："; grep -nE -C1 "[^0-9]200([^0-9]|$)" "$target_file" | head
        sed -i -E "s/([^0-9])200([^0-9]|$)/\1${THRESHOLD_NEW}\2/g" "$target_file"
        echo "✓ 已将 $target_file 中的 200 替换为 ${THRESHOLD_NEW}"
        # 重启 stronghold 相关服务使配置生效
        for svc in stronghold-stability stronghold-control; do
          if systemctl list-unit-files 2>/dev/null | grep -q "^${svc}.service"; then
            systemctl restart "$svc" && echo "✓ 已重启 $svc" || echo "⚠️ 重启 $svc 失败，请手动检查"
          fi
        done
      else
        echo "⚠️ 该文件中的 200 与内存预警无关，已中止自动替换（安全优先）。"
      fi
    fi
  else
    echo
    echo "（未加 --apply-threshold，未做任何修改。确认候选行无误后，加该参数重跑即可替换。）"
  fi
fi

echo
echo "=================================================="
echo " 完成 —— 最终状态"
echo "=================================================="
free -m
echo "--- swap ---"; swapon --show
echo "--- swappiness ---"; sysctl vm.swappiness
