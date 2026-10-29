"""Read-only final source/runtime audit and isolated server regression run."""
import json
import hashlib
import tarfile
from pathlib import Path
import paramiko
from verify_acceptance_deployment import MANIFESTS

ROOT=Path(__file__).resolve().parents[1]


def main():
    base=[ROOT/'review/plugin-optimization-20261029/deployment.json',ROOT/'review/request-ledger-20261029/deployment.json']
    manifests=[json.loads(p.read_text(encoding='utf-8')) for p in base+MANIFESTS]
    expected={r['target']:r['after'] for m in manifests for r in m['files']}
    ledger=json.loads(base[1].read_text())
    for name,sha in ledger['plugin_hashes'].items():
        expected.setdefault('/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_request_ledger/'+name,sha)
    archive=ROOT/'review/final-plugin-audit.tar.gz'
    directories=['tests','plugins/dafeiyu_runtime','plugins/patches/plugin_optimization_20261029']
    directories += ['plugins/'+name for name in ['astrbot_plugin_dafeiyu_cover','astrbot_plugin_dafeiyu_daily_memory','astrbot_plugin_dafeiyu_help','astrbot_plugin_wushu_ops','astrbot_plugin_usage_view','astrbot_plugin_lol_esports']]
    with tarfile.open(archive,'w:gz') as tar:
        for directory in directories:
            for p in (ROOT/directory).rglob('*.py'):
                tar.add(p,arcname=p.relative_to(ROOT).as_posix())
        tar.add(ROOT/'scripts/diagnostics/bot_event_audit.py',arcname='scripts/diagnostics/bot_event_audit.py')
    client=paramiko.SSHClient();client.load_system_host_keys();client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=15)
    def run(command,code):
        inp,out,err=client.exec_command(command,timeout=180)
        inp.write(code);inp.channel.shutdown_write()
        output,error=out.read().decode(),err.read().decode()
        if out.channel.recv_exit_status():raise RuntimeError(output+error)
        return output
    try:
        with client.open_sftp() as sftp:sftp.put(str(archive),'/opt/bot-stack/astrbot/data/temp/final-plugin-audit.tar.gz')
        checks=run('python3 -',"import json,hashlib\nfrom pathlib import Path\nx=json.loads("+repr(json.dumps(expected))+")\nbad=[p for p,sha in x.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=sha]\nprint(json.dumps({'files_checked':len(x),'mismatch':bad}))\n")
        checks=json.loads(checks)
        checks['historical_manifest_drift']=list(checks['mismatch'])
        for target in checks['mismatch']:
            source=next((r.get('source') for m in reversed(manifests) for r in m['files'] if r['target']==target and r.get('source')),None)
            assert source, 'Unexplained source drift: '+target
            with client.open_sftp() as sftp:live=sftp.open(target,'rb').read()
            assert live==(ROOT/source).read_bytes(), 'Live differs from current local: '+target
            expected[target]=hashlib.sha256(live).hexdigest()
        checks['mismatch']=[]
        (ROOT/'review/final-source-baseline.json').write_text(json.dumps(expected,indent=2),encoding='utf-8')
        code='''import ast,json,pathlib,subprocess,tarfile,time,urllib.request,jwt
stage=pathlib.Path('/tmp/final-plugin-audit-20261029');stage.mkdir(exist_ok=True)
with tarfile.open('/AstrBot/data/temp/final-plugin-audit.tar.gz') as t:t.extractall(stage,filter='data')
groups=[('optimization','tests/plugin_optimization'),('request_ledger','tests/request_ledger'),('balance','tests'),('wushu','plugins/astrbot_plugin_wushu_ops/tests')]
tests=[]
for name,path in groups:
 args=['python','-m','unittest','discover','-s',path]
 if name=='balance':args+=['-p','test_official_balance.py']
 r=subprocess.run(args,cwd=stage,capture_output=True,text=True,timeout=100)
 text=r.stdout+r.stderr
 (stage/(name+'.log')).write_text(text)
 tests.append({'name':name,'returncode':r.returncode,'summary':text[text.rfind('Ran '):].strip()})
syntax=[];count=0
root=pathlib.Path('/AstrBot/data/plugins')
for p in root.rglob('*.py'):
 if '__pycache__' in p.parts:continue
 count+=1
 try:ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
 except (SyntaxError,UnicodeError) as exc:syntax.append({'file':str(p.relative_to(root)),'error':type(exc).__name__,'line':getattr(exc,'lineno',None)})
cfg=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'));d=cfg['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+180},d['jwt_secret'],algorithm='HS256')
headers={'Authorization':'Bearer '+token};plugins=[]
for p in sorted(root.iterdir()):
 if not (p/'main.py').exists():continue
 try:
  req=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name='+p.name,headers=headers)
  x=json.load(urllib.request.urlopen(req,timeout=10));item=x.get('data') or {}
  plugins.append({'name':p.name,'status':x.get('status'),'activated':item.get('activated'),'components':len(item.get('components',[]))})
 except Exception as exc:plugins.append({'name':p.name,'error':type(exc).__name__})
cost=json.load(open('/AstrBot/data/plugin_data/astrbot_plugin_cost_control/config.json'))
report={'tests':tests,'python_files':count,'syntax_errors':syntax,'plugins':plugins,'daily_budget':(cost.get('budgets_cost') or {}).get('global_daily'),'monthly_budget':(cost.get('budgets_cost') or {}).get('global_monthly')}
pathlib.Path('/AstrBot/data/temp/final-plugin-audit-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
'''
        report=json.loads(run('docker exec -i astrbot python -',code));report['source_verification']=checks
        (ROOT/'review/final-plugin-audit-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:client.close()


if __name__=='__main__':main()
