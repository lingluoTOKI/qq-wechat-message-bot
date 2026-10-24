// Restore only the explicitly configured account. Never enable global autoload.
export function personalConnector(manager, account, {now=Date.now,onState=()=>{}}={}) {
  if (!/^\d{5,10}$/.test(String(account))) throw new Error('Invalid personal QQ account');
  const attempts=new Map();
  let busy=false,stopped=false;
  async function tick(){
    if(busy||stopped)return;
    busy=true;
    try{
      const processes=await manager.listProcesses();
      const alive=new Set(processes.map(p=>p.pid));
      for(const pid of attempts.keys())if(!alive.has(pid))attempts.delete(pid);
      if(processes.some(p=>String(p.uin)===String(account)&&p.status==='online')){onState('connected');return;}
      for(const process of processes){
        if(stopped)return;
        if(['loading','connecting'].includes(process.status))continue;
        const previous=attempts.get(process.pid);
        if(previous&&now()-previous.at<previous.delay)continue;
        // Probe identity before load; unknown/other accounts must not be loaded.
        const identity=await manager.probeProcessLoginInfo(process.pid);
        if(stopped)return;
        if(!identity?.identityKnown||String(identity.uin)!==String(account))continue;
        const count=previous&&now()-previous.first<3600000?previous.count:0;
        if(count>=3){onState('retry_later');continue;}
        attempts.set(process.pid,{count:count+1,at:now(),first:count===0?now():previous.first,delay:[60000,180000,600000][count]});
        onState('connecting');
        const result=await manager.loadProcess(process.pid);
        if(stopped)return;
        onState(result.status==='online'?'connected':result.status==='error'?'retry_later':'connecting');
        return;
      }
      onState('waiting_for_account');
    }catch{onState('retry_later');}
    finally{busy=false;}
  }
  return {tick,stop(){stopped=true;}};
}

export function startPersonalConnector(manager, account, options={}){
  const connector=personalConnector(manager,account,options);
  const timer=setInterval(()=>void connector.tick(),30000);
  timer.unref?.();
  void connector.tick();
  return ()=>{clearInterval(timer);connector.stop();};
}
