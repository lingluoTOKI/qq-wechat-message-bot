#!/usr/bin/env python3
"""改造 tts.html：显示 GPT-SoVITS 就绪状态、启动进度，并支持填 gsv_root。"""
import py_compile
from pathlib import Path

P = Path(r"F:\github\BOT用\管理面板\tts.html")
src = P.read_text(encoding="utf-8")

# 1) 客户端卡片：加"一键启动"语义 + GPT-SoVITS 状态行 + 进度提示
old = """        <div class="tts-actions">
          <button class="button primary" id="startBtn">启动客户端</button>
          <button class="button" id="stopBtn">停止</button>
        </div>
        <p class="tts-note" style="margin-top:12px">PID：<code id="clientPid">—</code></p>"""
new = """        <div class="tts-actions">
          <button class="button primary" id="startBtn">一键启动</button>
          <button class="button" id="stopBtn">停止</button>
        </div>
        <p class="tts-note" style="margin-top:12px">PID：<code id="clientPid">—</code></p>
        <div class="tts-state" id="apiState" style="margin-top:10px"><span class="dot"></span><span>GPT-SoVITS 检测中</span></div>
        <p class="tts-note" id="launchMsg" style="margin-top:8px">—</p>"""
assert old in src, "客户端卡片未匹配"
src = src.replace(old, new, 1)

# 2) 加 gsv_root 输入框
old = """      <div class="tts-field"><label>GPT-SoVITS API 地址</label><input id="gpt_api" placeholder="http://127.0.0.1:9880"></div>"""
new = """      <div class="tts-field"><label>GPT-SoVITS API 地址</label><input id="gpt_api" placeholder="http://127.0.0.1:9880"></div>
      <div class="tts-field"><label>GPT-SoVITS 安装目录</label><input id="gsv_root" placeholder="F:\\github\\GPT-SoVITS-v2pro-20250604"></div>"""
assert old in src, "gpt_api 字段未匹配"
src = src.replace(old, new, 1)

# 3) 状态渲染：api_ready + launch 进度
old = """    setState($('clientState'),s.running,s.running?'运行中（PID '+s.pid+'）':'未运行');
    $('clientPid').textContent=s.pid||'—';
    setState($('serverState'),s.server_online,s.server_online?'已连接服务器':'未连接（将回落 MiMo）');"""
new = """    setState($('clientState'),s.running,s.running?'运行中（PID '+s.pid+'）':'未运行');
    $('clientPid').textContent=s.pid||'—';
    setState($('apiState'),s.api_ready,s.api_ready?'GPT-SoVITS 已就绪':'GPT-SoVITS 未运行（点一键启动）');
    const L=s.launch||{};
    $('launchMsg').textContent=L.message||'—';
    setState($('serverState'),s.server_online,s.server_online?'已连接服务器':'未连接（将回落 MiMo）');"""
assert old in src, "状态渲染未匹配"
src = src.replace(old, new, 1)

# 4) 配置读写纳入 gsv_root
src = src.replace(
    "for(const k of ['gpt_model_path','gpt_ckpt_path','ref_audio','prompt_text','gpt_api']){",
    "for(const k of ['gpt_model_path','gpt_ckpt_path','ref_audio','prompt_text','gpt_api','gsv_root']){")

# 5) 启动后更频繁刷新（等模型加载）
old = """$('startBtn').onclick=async()=>{try{const r=await api('tts/start',{});toast(r.message||'已启动');setTimeout(refresh,1500);}catch(e){toast(e.message);}};"""
new = """$('startBtn').onclick=async()=>{try{const r=await api('tts/start',{});toast(r.message||'已启动');
  // 启动分几个阶段（拉 API → 等加载 → 起客户端），2 秒一次跟到就绪为止
  let n=0;const t=setInterval(async()=>{n++;await refresh();
    const L=(window.__lastLaunch||{});
    if(n>90||L.state==='ready'||L.state==='error'||L.state==='idle')clearInterval(t);},2000);
  setTimeout(refresh,1500);}catch(e){toast(e.message);}};"""
assert old in src, "startBtn 未匹配"
src = src.replace(old, new, 1)

# 6) 把 launch 存到 window 供上面的轮询判断
old = """    const L=s.launch||{};
    $('launchMsg').textContent=L.message||'—';"""
new = """    const L=s.launch||{};
    window.__lastLaunch=L;
    $('launchMsg').textContent=L.message||'—';"""
assert old in src
src = src.replace(old, new, 1)

P.write_text(src, encoding="utf-8")
print("tts.html 已更新")
for kw in ("apiState", "gsv_root", "__lastLaunch", "一键启动", "launchMsg"):
    print("  %-14s 出现 %d 次" % (kw, src.count(kw)))
