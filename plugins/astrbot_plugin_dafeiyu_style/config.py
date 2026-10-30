"""配置：默认值 + 热读取。

只用标准库；任何异常都退回默认值，绝不阻塞插件加载。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULTS = {
    # 总开关：关掉后不学习、不注入
    "enabled": True,
    # 注入开关：阶段 1 保持 false（只学不注入），阶段 2 再打开
    "inject_enabled": False,
    "auto_review": True,
    # 空数组 = 全部群；测试期请显式只填测试群
    "groups": [],
    # 每日学习触发小时（0-23），建议 3，避开工作日高峰
    "hour": 3,
    # 模型：粗筛用免费档；提炼可同一模型
    "coarse_provider_id": "",
    "refine_provider_id": "",
    # 参与学习的消息长度（字符）
    "min_msg_len": 6,
    "max_msg_len": 300,
    # 每群每轮最少消息数；不足则跳过且不推进游标
    "min_messages": 30,
    # 每群每轮最多处理条数
    "max_batch": 40,
    "max_batches_per_run": 10,
    "daily_batch_cap": 10,
    "run_seconds": 180,
    "catchup_request_id": "",
    # 规则配额
    "daily_rule_cap": 8,
    "total_rule_cap": 60,
    # 注入配额
    "inject_rules": 5,
    "inject_jargon": 5,
    "inject_shots": 3,
    "inject_max_chars": 600,
    "hook_timeout_s": 1.2,
    # 仅管理员可用命令
    "admin_only": True,
    # 调用是否包预算门（dafeiyu_runtime.paid）
    "use_budget_gate": True,
    # 单次模型调用超时（秒）
    "llm_timeout_s": 90.0,
    "max_tokens": 1200,
    # 证据原文最多保留字符
    "evidence_max_chars": 120,
    # 情景记忆库路径（留空则自动定位同级插件目录）
    "memory_db_path": "",
}


def load_config(path: str) -> dict:
    """读取配置并合并默认值；类型不对就忽略该项。"""
    cfg = dict(DEFAULTS)
    try:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for key, default in DEFAULTS.items():
                    val = raw.get(key, default)
                    if type(val) is type(default) or (
                        isinstance(default, (int, float)) and isinstance(val, (int, float))
                    ):
                        cfg[key] = val
    except Exception as exc:  # 配置坏了也不能让插件起不来
        logger.warning("[dafeiyu_style] 配置读取失败，使用默认值：%s" % str(exc)[:120])
        cfg['enabled'] = False
        cfg['inject_enabled'] = False
    cfg['max_batch'] = max(1, min(40, int(cfg['max_batch'])))
    cfg['hour'] = max(0, min(23, int(cfg['hour'])))
    cfg['use_budget_gate'] = True
    for key in ['max_batches_per_run','daily_batch_cap']:
        cfg[key]=max(1,min(100,int(cfg[key])))
    cfg['run_seconds']=max(1,min(600,int(cfg['run_seconds'])))
    return cfg
