import assert from 'node:assert/strict';
import {personalConnector} from './snowluma_autoconnect.mjs';
let clock=1000000,loaded=[],state='';
const manager={
  async listProcesses(){return [{pid:1,status:'available'},{pid:2,status:'available'},{pid:3,status:'available'}];},
  async probeProcessLoginInfo(pid){return pid===1?{identityKnown:true,uin:'474496195'}:pid===2?{identityKnown:false,uin:''}:{identityKnown:true,uin:'483257228'};},
  async loadProcess(pid){loaded.push(pid);return {status:'error'};}
};
const connector=personalConnector(manager,'483257228',{now:()=>clock,onState:s=>state=s});
await connector.tick();assert.deepEqual(loaded,[3]);
await connector.tick();assert.deepEqual(loaded,[3]);
clock+=61000;await connector.tick();assert.deepEqual(loaded,[3,3]);
clock+=181000;await connector.tick();assert.deepEqual(loaded,[3,3,3]);
clock+=601000;await connector.tick();assert.deepEqual(loaded,[3,3,3]);
clock+=3600000;await connector.tick();assert.equal(loaded.length,4);
connector.stop();clock+=3600000;await connector.tick();assert.equal(loaded.length,4);
manager.listProcesses=async()=>[{pid:3,status:'online',uin:'483257228'}];
const online=personalConnector(manager,'483257228',{onState:s=>state=s});await online.tick();assert.equal(state,'connected');assert.equal(loaded.length,4);online.stop();
console.log('PASS: target account only, unknown identity skipped, online no reload, bounded retry/backoff, cooldown reset, clean stop');
