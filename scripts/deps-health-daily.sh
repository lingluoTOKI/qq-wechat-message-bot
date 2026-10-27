#!/bin/bash
# 每日依赖体检：检查插件声明的依赖是否在容器内可用
# 背景：edge-tts / networkx 等手装包若未持久化，容器重建后会静默失效
#       （曾导致语音 100% 失效）。此脚本每天主动检查并记录到日志。
set -u
LOG=/opt/bot-stack/logs/deps-health.log
STAMP=$(date '+%Y-%m-%d %H:%M:%S')

{
  echo "===== $STAMP ====="
  python3 /opt/bot-stack/tmp/check_imports.py 2>&1
  echo "--- pylibs 体积 ---"
  du -sh /opt/bot-stack/astrbot/data/pylibs 2>/dev/null
  echo "--- 磁盘 ---"
  df -h / | tail -1
} >> "$LOG" 2>&1

# 有 FAIL 才告警（发到 bot 日志，由 wushu_ops 的运维通道可见）
FAILS=$(python3 /opt/bot-stack/tmp/check_imports.py 2>/dev/null | grep -c "^FAIL")
if [ "$FAILS" -gt 0 ]; then
  echo "[deps-health] $FAILS 个依赖缺失，详见 $LOG" >> /opt/bot-stack/logs/deps-health-alert.log
fi

# 日志只保留 30 天
find "$(dirname "$LOG")" -name "deps-health*.log" -mtime +30 -delete 2>/dev/null
exit 0
