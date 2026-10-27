'use strict';
const ttsUI={state:null,error:'',busy:'',reading:false,dirty:false,draft:{},updated:null,configOpen:false,logsOpen:false};
const ttsFields=[['gpt_model_path','GPT 模型权重（.ckpt）'],['gpt_ckpt_path','SoVITS 模型权重（.pth）'],['ref_audio','参考音频（wav）'],['prompt_text','参考音频对应文本'],['gpt_api','合成服务地址'],['gsv_root','GPT-SoVITS 安装目录']];
function renderTTS(){
 const s=ttsUI.state||{},l=s.launch||{},waiting=['starting_api','waiting_api','starting_client','connecting'].includes(l.state)||ttsUI.busy==='start';
 return `<div class="tts-view">${title('VOICE / 机器人语音','让大肥鱼，用熟悉的声音说话','本机合成，连接云端机器人。语音状态与控制集中在这里。',`<span class="tts-local-label">${icon('entry')} 本机运行</span>`)}
 <section class="panel tts-control"><div><div class="eyebrow">本地语音服务</div><h2 id="ttsHeading">${ttsUI.error?'状态暂不可用':!ttsUI.state?'正在读取状态':s.running&&s.api_ready&&s.server_online?'语音链路已就绪':waiting?'正在启动语音服务':l.state==='error'?'启动未完成':s.running?'客户端运行中':'本地语音未启动'}</h2><p class="muted" id="ttsLaunch">${esc(l.message||'一键启动会准备合成服务，再连接机器人。')}</p></div><div class="tts-buttons"><button class="button primary" data-tts="start" ${ttsUI.busy||waiting||s.running&&s.api_ready?'disabled':''}>${waiting?'启动中…':'启动本地语音'}</button><button class="button" data-tts="stop" ${ttsUI.busy||!s.running&&!s.api_running?'disabled':''}>停止本地语音</button></div></section>
 <p id="ttsError" class="tts-error" role="status" ${ttsUI.error?'':'hidden'}>${esc(ttsUI.error)}</p>
 <div class="tts-status-grid">${[['client','本地客户端','负责接收机器人的语音任务',s.running],['engine','合成引擎','GPT-SoVITS · 本机生成声音',s.api_ready],['server','机器人连接','服务器能否连接到本机客户端',s.server_online]].map(([key,label,desc,on])=>`<section class="panel tts-status"><div class="tts-status-head"><h3>${label}</h3><span id="ttsState-${key}" class="tts-badge ${ttsUI.error?'unknown':on?'online':''}">${ttsUI.error?'待确认':!ttsUI.state?'检测中':on?'已就绪':'未就绪'}</span></div><p>${desc}</p></section>`).join('')}</div>
 <section class="panel tts-route"><div><h3>语音备用通路</h3><p class="muted">本地不可用时按现有配置尝试备用服务，实际是否可用以调用结果为准。</p></div><div class="tts-route-steps"><span><b>01</b> 本机合成</span><i>→</i><span><b>02</b> MiMo</span><i>→</i><span><b>03</b> Edge TTS</span></div></section>
 <details class="panel tts-disclosure" id="ttsConfig" ${ttsUI.configOpen?'open':''}><summary><span><strong>音色与模型配置</strong><small>已配置好时无需修改</small></span><span>配置 ›</span></summary><form id="ttsConfigForm"><div class="tts-form-grid">${ttsFields.map(([key,label])=>`<label class="field">${label}<input name="${key}" value="${esc(ttsUI.draft[key]??s.config?.[key]??'')}" autocomplete="off" spellcheck="false"></label>`).join('')}</div><div class="tts-form-footer"><span class="muted" id="ttsSaveHint">${ttsUI.dirty?'有尚未保存的修改':'模型与音频路径保存在本机'}</span><button class="button primary" type="submit" ${ttsUI.busy?'disabled':''}>保存配置</button></div></form></details>
 <details class="panel tts-disclosure" id="ttsLogs" ${ttsUI.logsOpen?'open':''}><summary><span><strong>诊断日志</strong><small>连接或合成异常时查看</small></span><span>展开 ›</span></summary><pre class="tts-log" id="ttsLog">${esc(s.error_log||'暂无错误日志')}</pre></details>
 <div class="tts-foot"><span id="ttsTime">${ttsUI.updated?'语音状态更新于 '+ttsUI.updated.toLocaleTimeString('zh-CN'):'等待首次状态检查'}</span><span>电脑关闭或休眠后，本地合成不可用</span></div></div>`;
}
async function refreshTTS(){
 if(ttsUI.reading||typeof page==='undefined'||page!=='tts'||document.hidden)return;
 ttsUI.reading=true;
 try{ttsUI.state=await api('tts/status');ttsUI.updated=new Date();ttsUI.error='';}
 catch(e){ttsUI.error='无法读取本地语音状态：'+e.message;}
 finally{ttsUI.reading=false;}
 if(page!=='tts')return;
 // Update only status nodes, preserving focus, unsaved fields and expanded sections.
 const template=document.createElement('template');template.innerHTML=renderTTS();
 for(const id of ['ttsHeading','ttsLaunch','ttsError','ttsState-client','ttsState-engine','ttsState-server','ttsLog','ttsTime']){
  const old=document.getElementById(id),fresh=template.content.querySelector('#'+id);if(old&&fresh){old.textContent=fresh.textContent;old.className=fresh.className;old.hidden=fresh.hidden;}
 }
 for(const action of ['start','stop']){const old=document.querySelector(`[data-tts="${action}"]`),fresh=template.content.querySelector(`[data-tts="${action}"]`);if(old&&fresh){old.disabled=fresh.disabled;old.textContent=fresh.textContent;}}
 if(!ttsUI.dirty)for(const [key] of ttsFields){const input=document.querySelector(`#ttsConfigForm [name="${key}"]`);if(input&&document.activeElement!==input)input.value=ttsUI.state?.config?.[key]||'';}
}
document.addEventListener('input',e=>{if(e.target.closest('#ttsConfigForm')){ttsUI.dirty=true;ttsUI.draft[e.target.name]=e.target.value;const n=document.getElementById('ttsSaveHint');if(n)n.textContent='有尚未保存的修改';}});
document.addEventListener('toggle',e=>{if(e.target.id==='ttsConfig')ttsUI.configOpen=e.target.open;if(e.target.id==='ttsLogs')ttsUI.logsOpen=e.target.open;},true);
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-tts]');if(!b)return;
 if(ttsUI.busy)return;const action=b.dataset.tts;ttsUI.busy=action;b.disabled=true;
 try{const result=await api('tts/'+action,{});toast(result.message||'操作已提交');}catch(err){toast(err.message);}finally{ttsUI.busy='';await refreshTTS();}
});
document.addEventListener('submit',async e=>{
 if(e.target.id!=='ttsConfigForm')return;e.preventDefault();if(ttsUI.busy)return;
 const payload=Object.fromEntries(new FormData(e.target));const before=JSON.stringify(payload);ttsUI.busy='save';const b=e.target.querySelector('button[type=submit]');b.disabled=true;
 try{const r=await api('tts/config',payload);if(JSON.stringify(Object.fromEntries(new FormData(e.target)))===before){ttsUI.dirty=false;ttsUI.draft={};}toast(r.message||'配置已保存');document.getElementById('ttsSaveHint').textContent=ttsUI.dirty?'仍有尚未保存的修改':'配置已保存';}catch(err){toast('保存失败：'+err.message);}finally{ttsUI.busy='';b.disabled=false;}
});
document.addEventListener('DOMContentLoaded',()=>{
 paths.tts='<path d="M5 9v6h4l5 4V5L9 9H5zM18 8a7 7 0 0 1 0 8M21 5a11 11 0 0 1 0 14"/>';
 const previousGo=go;go=function(next){previousGo(next);if(page==='tts')refreshTTS();};
 const oldRefresh=document.getElementById('refresh').onclick;document.getElementById('refresh').onclick=function(e){if(page==='tts')return refreshTTS();return oldRefresh?.call(this,e);};
 renderNav();if(page==='tts')refreshTTS();
});
setInterval(()=>{if(typeof page!=='undefined'&&page==='tts'&&!document.hidden)refreshTTS();},5000);
