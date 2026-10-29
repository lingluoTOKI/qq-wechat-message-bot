"""扫描所有插件 requirements.txt，找出容器内缺失的依赖（重启即丢的定时炸弹）"""
import os
import re
import subprocess

PLUGDIR = "/opt/bot-stack/astrbot/data/plugins"


def parse_reqs(path):
    out = []
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.split("#")[0].strip()
            if not line:
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)", line)
            if m:
                out.append(m.group(1))
    except Exception as e:
        print("  读取失败 %s: %s" % (path, e))
    return out


def installed(pkg):
    r = subprocess.run(["docker", "exec", "astrbot", "pip", "show", pkg],
                       capture_output=True, text=True)
    return r.returncode == 0


missing = {}
declared = {}
if os.path.isdir(PLUGDIR):
    for d in sorted(os.listdir(PLUGDIR)):
        rp = os.path.join(PLUGDIR, d, "requirements.txt")
        if not os.path.isfile(rp):
            continue
        pkgs = parse_reqs(rp)
        if not pkgs:
            continue
        declared[d] = pkgs
        for p in pkgs:
            if not installed(p):
                missing.setdefault(p, []).append(d)

print("=" * 62)
print("插件声明的依赖：%d 个插件，%d 个包" % (
    len(declared), sum(len(v) for v in declared.values())))
print("=" * 62)
for d, pkgs in declared.items():
    print("  %-38s %s" % (d[:38], ", ".join(pkgs)))

print()
print("=" * 62)
print("❌ 容器内缺失（重启后必然失效）")
print("=" * 62)
if not missing:
    print("  无")
for p, ds in missing.items():
    print("  %-20s 被 %s 依赖" % (p, ", ".join(ds)))

print()
print("=" * 62)
print("🔍 容器内已装但未声明的包（手装，重启即丢）")
print("=" * 62)
r = subprocess.run(
    ["docker", "exec", "astrbot", "pip", "list", "--format=freeze"],
    capture_output=True, text=True)
have = {}
for line in r.stdout.splitlines():
    if "==" in line:
        k, v = line.split("==", 1)
        have[k.strip().lower().replace("_", "-")] = v.strip()
declared_flat = {p.lower().replace("_", "-") for pk in declared.values() for p in pk}
# 这些是框架自带或 AstrBot 自身依赖，不算问题
BUILTIN = {
    "astrbot", "fastapi", "uvicorn", "pydantic", "aiohttp", "httpx", "loguru",
    "sqlalchemy", "aiosqlite", "pillow", "numpy", "openai", "tiktoken",
    "sse-starlette", "starlette", "uvloop", "redis", "brotli", "zstandard",
    "psutil", "websockets", "beautifulsoup4", "lxml", "markdown", "bleach",
    "jinja2", "mako", "python-multipart", "chardet", "idna", "certifi",
    "requests", "urllib3", "attrs", "charset-normalizer", "distro",
    "platformdirs", "typing-extensions", "protobuf", "grpcio", "tzdata",
    "six", "packaging", "pyyaml", "python-dateutil", "cryptography",
    "async-timeout", "aiohappyeyeballs", "multidict", "yarl", "frozenlist",
    "aiosignal", "propcache", "tabulate", "edge-tts", "pyahocorasick",
    "boto3", "botocore", "s3transfer", "jmespath", "python-dateutil",
    "wsgiref", "jsonpickle", "dashscope", "botpy", "faiss-cpu", "lxml",
}
manual = []
for name, ver in sorted(have.items()):
    if name in declared_flat or name in BUILTIN:
        continue
    manual.append("%s==%s" % (name, ver))
print("  共 %d 个" % len(manual))
for x in manual:
    print("   ", x)
