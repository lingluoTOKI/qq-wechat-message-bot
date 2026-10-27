#!/usr/bin/env bash
# 大肥鱼 每日全量备份（保留 7 天）
# 备份内容：docker-compose.yml、bin 脚本、astrbot 配置、cmd_config.json、data_v4.db
set -uo pipefail

SRC=/opt/bot-stack
DST="$SRC/backups"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$DST/daily-$STAMP.tar.gz"

mkdir -p "$DST" "$SRC/logs"

tar -czf "$OUT" \
  -C "$SRC" docker-compose.yml bin \
  -C "$SRC/astrbot/data" config cmd_config.json data_v4.db \
  2>/dev/null

if [ -f "$OUT" ]; then
  echo "$(date '+%F %T') backup OK -> $(basename "$OUT") ($(du -h "$OUT" | cut -f1))" >> "$SRC/logs/backup.log"
else
  echo "$(date '+%F %T') backup FAILED" >> "$SRC/logs/backup.log"
fi

# 清理 7 天前的每日备份
find "$DST" -maxdepth 1 -name 'daily-*.tar.gz' -mtime +7 -delete 2>/dev/null

# 清理 14 天前的 wushu-ops 插件备份目录
find "$DST" -maxdepth 1 -type d -name 'wushu-ops-*' -mtime +14 -exec rm -rf {} + 2>/dev/null

# docker 日志轮转兜底（compose 已配置 max-size，此处仅清理历史未受限日志）
find /var/lib/docker/containers -name '*-json.log' -size +50M -mtime +3 \
  -exec sh -c 'cat /dev/null > "$1"' _ {} \; 2>/dev/null

exit 0
