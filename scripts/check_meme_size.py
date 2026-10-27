import os
import shutil

D = "/opt/bot-stack/astrbot/data/plugin_data/meme_manager"

print("=== 压缩工具 ===")
for t in ("gifsicle", "convert", "mogrify", "ffmpeg"):
    print(f"{t}: {'有' if shutil.which(t) else '无'}")
try:
    import PIL
    print("Pillow:", PIL.__version__)
except Exception:
    print("Pillow: 无")

print("\n=== 素材体积分布 ===")
buckets = {">3MB": 0, "1-3MB": 0, "0.5-1MB": 0, "<0.5MB": 0}
items = []
total = 0.0
for root, _, files in os.walk(D):
    for f in files:
        if f.lower().endswith((".gif", ".jpg", ".jpeg", ".png")):
            p = os.path.join(root, f)
            s = os.path.getsize(p) / 1048576
            total += s
            items.append((s, p))
            if s > 3:
                buckets[">3MB"] += 1
            elif s > 1:
                buckets["1-3MB"] += 1
            elif s > 0.5:
                buckets["0.5-1MB"] += 1
            else:
                buckets["<0.5MB"] += 1

for k in (">3MB", "1-3MB", "0.5-1MB", "<0.5MB"):
    print(f"  {k}: {buckets[k]} 个")
print(f"  总计: {len(items)} 个，合计 {total:.1f} MB")

print("\n=== 最大的 10 个 ===")
for s, p in sorted(items, reverse=True)[:10]:
    print(f"  {s:6.2f} MB  {p.replace(D, '')}")

big = [x for x in items if x[0] > 1]
print(f"\n>1MB 的共 {len(big)} 个，合计 {sum(s for s, _ in big):.1f} MB")
