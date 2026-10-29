"""压缩 meme_manager 表情素材 v2：改用「降尺寸」路线，比有损优化快一个量级。

v1 用 gifsicle --lossy 处理 6MB 的 GIF 会跑几分钟，太慢；
v2 直接降宽度（表情发到群里本来也不需要原图尺寸），秒级完成。
"""
import os
import shutil
import subprocess

ROOT = "/opt/bot-stack/astrbot/data/plugin_data/meme_manager"
TARGET = 1 * 1024 * 1024
IMG_EXT = (".gif", ".jpg", ".jpeg", ".png")
TIMEOUT = 90


def fsize(p):
    return os.path.getsize(p) if os.path.exists(p) else 0


def cleanup_junk(root):
    n = 0
    for r, _, fs in os.walk(root):
        for f in fs:
            if ".cmp" in f:
                try:
                    os.remove(os.path.join(r, f))
                    n += 1
                except Exception:
                    pass
    return n


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None


def gif_plans(src, tmp):
    # 逐级缩小宽度，不做 --lossy（太慢）
    return [
        ["gifsicle", "-O3", "--resize-fit-width", "480", "-o", tmp, src],
        ["gifsicle", "-O3", "--colors", "64", "--resize-fit-width", "360", "-o", tmp, src],
        ["gifsicle", "-O3", "--colors", "32", "--resize-fit-width", "260", "-o", tmp, src],
    ]


def img_plans(src, tmp):
    return [
        ["convert", src, "-strip", "-resize", "1024x1024>", "-quality", "80", tmp],
        ["convert", src, "-strip", "-resize", "768x768>", "-quality", "68", tmp],
        ["convert", src, "-strip", "-resize", "560x560>", "-quality", "58", tmp],
    ]


def process(path):
    before = fsize(path)
    ext = os.path.splitext(path)[1].lower()
    tmp = path + ".cmp"
    best = path + ".cmpB"
    for j in (tmp, best):
        if os.path.exists(j):
            os.remove(j)

    plans = gif_plans(path, tmp) if ext == ".gif" else img_plans(path, tmp)
    chosen = None

    for cmd in plans:
        if os.path.exists(tmp):
            os.remove(tmp)
        r = run(cmd)
        if r is None or r.returncode != 0 or fsize(tmp) == 0:
            continue
        if fsize(tmp) >= before:
            continue
        if chosen is None or fsize(tmp) < fsize(chosen):
            shutil.copyfile(tmp, best)
            chosen = best
        if fsize(chosen) <= TARGET:
            break

    if chosen is None:
        for j in (tmp, best):
            if os.path.exists(j):
                os.remove(j)
        return before, before, False

    os.replace(chosen, path)
    for j in (tmp, best):
        if os.path.exists(j):
            os.remove(j)
    return before, fsize(path), True


def main():
    junk = cleanup_junk(ROOT)
    if junk:
        print(f"清理残留临时文件: {junk} 个\n")

    targets = []
    for root, _, files in os.walk(ROOT):
        for f in files:
            p = os.path.join(root, f)
            if f.lower().endswith(IMG_EXT) and fsize(p) > TARGET:
                targets.append(p)

    print(f"待压缩: {len(targets)} 个")
    saved = 0
    ok_n = 0
    for i, p in enumerate(sorted(targets), 1):
        b, a, ok = process(p)
        rel = p.replace(ROOT, "")
        if ok:
            saved += b - a
            ok_n += 1
            tag = "达标" if a <= TARGET else "仍偏大"
            print(f"[{i}/{len(targets)}] {b/1048576:6.2f} -> {a/1048576:5.2f} MB ({tag}) {rel}")
        else:
            print(f"[{i}/{len(targets)}] {b/1048576:6.2f} MB 跳过 {rel}")

    print(f"\n完成 {ok_n}/{len(targets)}，节省 {saved/1048576:.1f} MB")


if __name__ == "__main__":
    main()
