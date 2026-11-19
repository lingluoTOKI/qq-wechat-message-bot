"""B 方案补丁：把 bot 自身在私聊里的平台标识归一到真实账号 ID"""
import io
import os
import py_compile
import shutil
import sys
import time

PLUG = "/AstrBot/data/plugins/astrbot_plugin_livingmemory"
BAK = "/AstrBot/data/backups"
TS = time.strftime("%Y%m%d-%H%M%S")
os.makedirs(BAK, exist_ok=True)

TARGETS = [
    (PLUG + "/core/processors/memory_processor_parse.py",
     '        sender_id = str(message.sender_id or "").strip()\n',
     '        sender_id = str(message.sender_id or "").strip()\n'
     '        sender_id = _SELF_SENDER_ALIASES.get(sender_id, sender_id)  # 自研补丁：私聊 bot 标识归一\n',
     "def extract_participant_identities(",
     "# 自研补丁：bot 自身在私聊中的平台标识 -> 真实账号 ID（避免图谱生成分身节点）\n"
     '_SELF_SENDER_ALIASES = {"bot:大肥鱼-官方": "4BFCCA5091D54AE72297D5EC5062A477"}\n\n\n'),
    (PLUG + "/core/tools/agentic_recall_common.py",
     '    sender_id = _event_value(event, "get_sender_id")\n',
     '    sender_id = _event_value(event, "get_sender_id")\n'
     '    sender_id = _SELF_SENDER_ALIASES.get(sender_id, sender_id)  # 自研补丁：私聊 bot 标识归一\n',
     "def derive_speaker_identity(",
     "# 自研补丁：bot 自身在私聊中的平台标识 -> 真实账号 ID\n"
     '_SELF_SENDER_ALIASES = {"bot:大肥鱼-官方": "4BFCCA5091D54AE72297D5EC5062A477"}\n\n\n'),
]

for path, old, new, anchor, const in TARGETS:
    if not os.path.exists(path):
        print("[跳过] 文件不存在:", path)
        continue
    src = io.open(path, encoding="utf-8").read()
    bak = "%s/%s.bak-selfid-%s" % (BAK, os.path.basename(path), TS)
    shutil.copy2(path, bak)
    print("[备份]", bak)

    if "_SELF_SENDER_ALIASES" in src:
        print("[已打过补丁]", os.path.basename(path))
        continue
    if src.count(old) != 1:
        print("[警告] 锚点出现 %d 次，跳过：%s" % (src.count(old), os.path.basename(path)))
        continue
    if anchor not in src:
        print("[警告] 常量锚点未找到，跳过：", os.path.basename(path))
        continue

    src = src.replace(old, new, 1)
    src = src.replace(anchor, const + anchor, 1)
    io.open(path, "w", encoding="utf-8").write(src)
    py_compile.compile(path, doraise=True)
    print("[完成] 已打补丁并编译通过:", os.path.basename(path))

# 逻辑验证
sys.path.insert(0, PLUG)
try:
    from core.processors.memory_processor_parse import _SELF_SENDER_ALIASES, extract_participant_identities

    class FakeMsg:
        role = "assistant"
        sender_id = "bot:大肥鱼-官方"
        sender_name = "大肥鱼-官方"
        platform = "qq_official"
        metadata = {}

    class FakeMsg2(FakeMsg):
        sender_id = "bot:爱吃饭的大肥鱼"
        sender_name = "爱吃饭的大肥鱼"

    r1 = extract_participant_identities([FakeMsg()])
    r2 = extract_participant_identities([FakeMsg2()])
    print("\n[验证 主号私聊]")
    print("  identity_key:", r1[0]["identity_key"])
    print("  sender_id   :", r1[0]["sender_id"])
    print("[验证 另一只bot(不应被改)]")
    print("  identity_key:", r2[0]["identity_key"])
    print("\n[判定] 主号归一成功:", r1[0]["sender_id"] == "4BFCCA5091D54AE72297D5EC5062A477")
except Exception as e:
    print("[验证失败]", type(e).__name__, e)
