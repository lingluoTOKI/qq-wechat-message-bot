"""拼接洛天依参考音频 → 适合 MiMo voiceclone 的参考样本"""
import os
import wave

SRC_DIR = r"F:\github\BOT用\TTS素材\洛天依gptsovits模型\V1\参考音频"
OUT = r"F:\github\BOT用\TTS素材\洛天依gptsovits模型\luotianyi_ref_10s.wav"

FILES = ["大家好，我是虚拟歌手洛天依.wav", "怎么还是这张图捏，能不能换一张呀.wav"]
GAP = 0.3  # 中间静音秒数

chunks = []
sr = None
for f in FILES:
    p = os.path.join(SRC_DIR, f)
    with wave.open(p, "rb") as w:
        if sr is None:
            sr = w.getframerate()
        assert w.getframerate() == sr, "采样率不一致"
        assert w.getnchannels() == 1, "需要单声道"
        assert w.getsampwidth() == 2, "需要 16bit"
        chunks.append(w.readframes(w.getnframes()))
        print("  读入 %-40s %.1fs" % (f, w.getnframes() / w.getframerate()))

gap = b"\x00\x00" * int(sr * GAP)
data = chunks[0] + gap + chunks[1]

with wave.open(OUT, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(data)

dur = len(data) / 2 / sr
print()
print("输出: %s" % os.path.basename(OUT))
print("  时长 %.1fs | %d Hz | 单声道 | %.0f KB" % (dur, sr, os.path.getsize(OUT) / 1024))
print("  (MiMo 建议 10-30 秒，此样本落在最佳区间)")
