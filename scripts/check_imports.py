"""逐个 import 验证插件依赖是否真的可用（包名 != import 名时 pip show 会误报）"""
import subprocess

CHECKS = [
    ("edge_tts", "clonetts 语音合成"),
    ("networkx", "livingmemory 图记忆"),
    ("pytz", "livingmemory 时区"),
    ("cv2", "group_guardian 图像/武器识别"),
    ("tomli", "proactive_chat 配置解析"),
    ("apscheduler", "proactive_chat 调度"),
    ("jieba", "livingmemory 分词"),
    ("faiss", "meme_manager 向量检索"),
    ("boto3", "meme_manager R2 图床"),
    ("ahocorasick", "group_guardian AC 自动机"),
    ("edge_tts_x", "__占位__"),
]
for pkg, use in CHECKS:
    if pkg == "edge_tts_x":
        continue
    r = subprocess.run(
        ["docker", "exec", "astrbot", "python", "-c",
         "import %s; print('OK', getattr(%s,'__version__','?'))" % (pkg, pkg)],
        capture_output=True, text=True)
    ok = r.returncode == 0
    detail = r.stdout.strip().splitlines()[-1] if ok else r.stderr.strip().splitlines()[-1][:90]
    print("%s  %-16s %-22s %s" % ("OK  " if ok else "FAIL", pkg, use, detail))
