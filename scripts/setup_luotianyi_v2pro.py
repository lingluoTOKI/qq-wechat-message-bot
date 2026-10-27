#!/usr/bin/env python3
"""把洛天依 v2Pro 模型装进本地 GPT-SoVITS，并把 custom 配置节切到 v2Pro。

为什么这么做：
- 权重文件名含中文和长破折号，API 走 query 参数传路径容易出编码问题 → 改 ASCII 名
- api_v2.py 的 TTS_Config 只读 yaml 的 `custom` 节，其 version 必须是 v2Pro
  才能加载 v2Pro 权重（当前是 v2，直接加载会失败）
- custom 里仍保留官方底模（s1v3.ckpt / v2Pro/s2Gv2Pro.pth），
  微调权重由 API 的 set_gpt_weights / set_sovits_weights 在运行时替换。
  这样即使微调权重有问题，API 也能正常起来，便于排查。
"""
import os
import shutil
import sys

ROOT = r"F:\github\GPT-SoVITS-v2pro-20250604"
SRC = os.path.join(
    r"F:\github\BOT用\TTS素材\lty",
    "洛天依13周年生日会语音生成模型——b站只想干饭的麦克斯韦妖制作",
)

GPT_DIR = os.path.join(ROOT, "GPT_weights_v2Pro")
VITS_DIR = os.path.join(ROOT, "SoVITS_weights_v2Pro")
REF_DIR = os.path.join(ROOT, "refs")
YAML = os.path.join(ROOT, "GPT_SoVITS", "configs", "tts_infer.yaml")

GPT_SRC = os.path.join(SRC, "洛天依13周年生日会语音生成模型——b站只想干饭的麦克斯韦妖制作-e15.ckpt")
VITS_SRC = os.path.join(SRC, "洛天依13周年生日会语音生成模型——b站只想干饭的麦克斯韦妖制作_e8_s384.pth")
REF_SRC = os.path.join(SRC, "洛天依13周年生日会语音生成模型——b站只想干饭的麦克斯韦妖制作.wav")

GPT_DST = os.path.join(GPT_DIR, "luotianyi13_e15.ckpt")
VITS_DST = os.path.join(VITS_DIR, "luotianyi13_e8_s384.pth")
REF_DST = os.path.join(REF_DIR, "luotianyi13_ref.wav")


def copy(src, dst):
    if not os.path.isfile(src):
        print("!! 源文件不存在:", src)
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
        print("  已存在且大小一致，跳过:", os.path.basename(dst))
        return True
    shutil.copy2(src, dst)
    print("  %s  (%.1f MB)" % (os.path.basename(dst), os.path.getsize(dst) / 1048576))
    return True


def patch_yaml():
    """只改 custom 节里的 version / t2s / vits 三项，其余原样保留。"""
    with open(YAML, encoding="utf-8") as f:
        lines = f.readlines()

    shutil.copy2(YAML, YAML + ".bak-luotianyi")
    out, in_custom, changed = [], False, {}
    for ln in lines:
        if ln.startswith("custom:"):
            in_custom = True
            out.append(ln)
            continue
        if in_custom and ln and not ln[0].isspace():   # 到了下一节
            in_custom = False

        if in_custom:
            if ln.startswith("  version:"):
                changed["version"] = (ln.strip(), "version: v2Pro")
                out.append("  version: v2Pro\n")
                continue
            if ln.startswith("  t2s_weights_path:"):
                new = ("  t2s_weights_path: GPT_SoVITS/pretrained_models/s1v3.ckpt\n")
                changed["t2s"] = (ln.strip(), new.strip())
                out.append(new)
                continue
            if ln.startswith("  vits_weights_path:"):
                new = ("  vits_weights_path: GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth\n")
                changed["vits"] = (ln.strip(), new.strip())
                out.append(new)
                continue
        out.append(ln)

    with open(YAML, "w", encoding="utf-8") as f:
        f.writelines(out)

    print("\n  yaml custom 节改动（备份 .bak-luotianyi）:")
    for k, (old, new) in changed.items():
        print("    %-8s %s\n          -> %s" % (k, old, new))
    if len(changed) != 3:
        print("  !! 只改到 %d 项，预期 3 项，请检查 yaml" % len(changed))
        return False
    return True


def main():
    print("=== 1. 复制权重与参考音频（ASCII 命名）===")
    ok = all([copy(GPT_SRC, GPT_DST), copy(VITS_SRC, VITS_DST), copy(REF_SRC, REF_DST)])
    if not ok:
        return 1

    print("\n=== 2. 切 custom 节到 v2Pro ===")
    if not patch_yaml():
        return 1

    print("\n=== 3. 结果 ===")
    print("  GPT 权重   :", GPT_DST)
    print("  SoVITS 权重:", VITS_DST)
    print("  参考音频   :", REF_DST)
    print("  参考文本   : 我现在需要一个长达三秒的语音")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
