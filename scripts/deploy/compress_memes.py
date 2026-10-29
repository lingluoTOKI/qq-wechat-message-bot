"""压缩 meme_manager 表情素材：把 >1MB 的图片压到 1MB 以内。

原则：
1. 只处理超过阈值的文件，其余一律不动
2. 逐级降质，每一级都要求「确实变小」才采纳
3. 任何一步失败都保留原文件，绝不留下损坏文件
"""
import os
import subprocess
import sys

ROOT = "/opt/bot-stack/astrbot/data/plugin_data/meme_manager"
TARGET = 1 * 1024 * 1024  # 1MB
IMG_EXT = (".gif", ".jpg", ".jpeg", ".png")


def fsize(p):
    return os.path.getsize(p) if os.path.exists(p) else 0


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def pick_smallest(src, tmp, candidates):
    """在所有成功产物中挑最小的那个；若都比原文件大则返回 None"""
    best = None
    for cmd in candidates:
        if os.path.exists(tmp):
            os.remove(tmp)
        r = run(cmd)
        if r.returncode != 0 or not os.path.exists(tmp):
            continue
        if fsize(tmp) == 0:
            continue
        if best is None or fsize(tmp) < fsize(best):
            best = tmp + ".best"
            shutil_copy(tmp, best)
    return best


def shutil_copy(a, b):
    import shutil
    shutil.copyfile(a, b)


def gif_plans(src, tmp):
    return [
        ["gifsicle", "-O3", "--lossy=30", "--colors", "64", "-o", tmp, src],
        ["gifsicle", "-O3", "--lossy=60", "--colors", "48",
         "--resize-fit-width", "480", "-o", tmp, src],
        ["gifsicle", "-O3", "--lossy=80", "--colors", "32",
         "--resize-fit-width", "320", "-o", tmp, src],
    ]


def img_plans(src, tmp):
    return [
        ["convert", src, "-strip", "-resize", "1024x1024>", "-quality", "78", tmp],
        ["convert", src, "-strip", "-resize", "800x800>", "-quality", "65", tmp],
        ["convert", src, "-strip", "-resize", "600x600>", "-quality", "55", tmp],
    ]


def process(path):
    before = fsize(path)
    ext = os.path.splitext(path)[1].lower()
    tmp = path + ".cmp"
    best = path + ".cmp.best"
    for junk in (tmp, best):
        if os.path.exists(junk):
            os.remove(junk)

    plans = gif_plans(path, tmp) if ext == ".gif" else img_plans(path, tmp)

    chosen = None
    for cmd in plans:
        if os.path.exists(tmp):
            os.remove(tmp)
        r = run(cmd)
        if r.returncode != 0 or fsize(tmp) == 0:
            continue
        if fsize(tmp) >= before:
            continue  # 没变小，不采纳
        if chosen is None or fsize(tmp) < fsize(chosen):
            os.replace(tmp, best)
            chosen = best
        if fsize(chosen) <= TARGET:
            break  # 已达标，不再降质

    if chosen is None:
        return before, before, False

    os.replace(chosen, path)
    for junk in (tmp, best):
        if os.path.exists(junk):
            os.remove(junk)
    after = fsize(path)
    return before, after, True


def main():
    targets = []
    for root, _, files in os.walk(ROOT):
        for f in files:
            p = os.path.join(root, f)
            if f.lower().endswith(IMG_EXT) and fsize(p) > TARGET:
                targets.append(p)

    print(f"待压缩: {len(targets)} 个\n")
    saved = 0
    changed = 0
    for i, p in enumerate(sorted(targets), 1):
        b, a, ok = process(p)
        rel = p.replace(ROOT, "")
        if ok:
            saved += b - a
            changed += 1
            flag = "达标" if a <= TARGET else "接近"
            print(f"[{i}/{len(targets)}] {b/1048576:6.2f}MB -> {a/1048576:5.2f}MB ({flag}) {rel}")
        else:
            print(f"[{i}/{len(targets)}] {b/1048576:6.2f}MB -> 跳过(无法减小) {rel}")

    print(f"\n完成: 成功压缩 {changed}/{len(targets)} 个，"
          f"共节省 {saved/1048576:.1f} MB")


if __name__ == "__main__":
    main()
