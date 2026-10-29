# 主机内存加固（OOM 兜底 + 预警线抬高）

对应诊断项 **② 主机内存偏紧的 OOM 风险**：实测 `availableMB` 仅 350–640MB、`swapUsed` 已 950MB+；游戏服 + Docker 同在一台 2GB 机器，OOM killer 一旦触发可能误杀游戏进程。

## 要做的两件事
1. **加 1–2GB swap 文件兜底** —— 脚本默认新增 2GB（`/var/swap-stronghold.img`），与主机原有的约 1GB swap 并存，给内核更多腾挪空间。
2. **内存预警线 200MB → 400MB** —— 提前报警，留出运维反应时间。

## 现状：本次无法直接执行
- 本机两把 SSH 私钥（`id_ed25519`、`robot.pem`）对候选主机（211.86.197.10 / 116.62.39.28 / 101.132.104.41）全部 `Permission denied (publickey)`，当前凭据未被授权登录。
- 本地隧道在线，`127.0.0.1:8765` 是自定义「BOT CONSOLE · 服务器管理面板」，但它背后的命令执行后端（`/api/status`、`/api/system`、`/api/terminal`、`/api/exec` 等）全部返回 **502（上游不可达）**；AstrBot WebUI（6185）同样 502。即面板前端在线、命令后端挂了，暂时无法经它执行命令。

> 所以这两步目前**只能准备好脚本 + 说明，等访问恢复后执行**。

## 怎么跑（在主机上，root）
把 `harden-memory.sh` 传到主机后：

```bash
# 仅做 swap（安全、可反复跑）
sudo bash harden-memory.sh

# 额外把内存预警线 200 → 400（先不加这个参数跑一次，确认候选行无误再加）
sudo bash harden-memory.sh --apply-threshold
```

脚本特性：幂等（重复跑不破坏配置）、先校验磁盘余量（至少留 512MB）、自动写 `/etc/fstab` 与 `/etc/sysctl.d/99-stronghold.conf` 实现开机持久化。

可选环境变量：`SWAP_SIZE=1G`（改大小）、`SWAP_FILE=/path`（改路径）、`SWAPPINESS=60`（更早换出）。

## 阈值改动为什么保守
预警线 200MB 写在哪里，我此刻看不到主机源码（`/opt/stronghold-stability` 等目录在远端）。脚本会先在 stronghold 相关目录里**搜索并列出候选行**：
- 不带 `--apply-threshold`：只报告，不动文件。
- 带 `--apply-threshold`：仅在**唯一一个 .py 文件**中出现 `200`、且该文件确实含 `mem/warn/threshold/alert` 上下文时才自动替换并重启 `stronghold-stability` / `stronghold-control` 服务；否则中止并提示人工确认。

目的：宁可多一步确认，也不盲改未知源码。

## 验证（执行后）
```bash
free -m              # 看 Swap 总量是否多了 2G
swapon --show        # 确认新 swap 已挂载
sysctl vm.swappiness # 应为 10
cat /etc/fstab | grep swap   # 确认持久化
```

## 恢复执行的前提（任选其一）
- **SSH**：把本机公钥重新加入主机 `~/.ssh/authorized_keys`（或提供可用密码），我即可用密钥登入跑脚本；
- **面板后端**：把管理面板背后的命令执行服务拉起（消除 502），我可通过其 `/api/terminal|exec` 接口执行；
- **你手动跑**：直接按上面命令在主机上执行，结果贴回给我核对。

> ⚠️ 注意：本机 `robot.pem`、`密码.md` 含服务器私钥与各类密码，切勿外传或提交到仓库。
