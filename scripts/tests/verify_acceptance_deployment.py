"""Verify scoped acceptance deployments and prepare a guarded reverse-order rollback.

Does not run rollback, restart services, or read production configuration.
"""
import hashlib
import json
from pathlib import Path

import paramiko

ROOT = next(p for p in Path(__file__).resolve().parents if (p / '.git').exists())
MANIFESTS = [ROOT / 'review' / f'acceptance-{name}.json'
             for name in ('second', 'followup', 'media', 'workflow')]
MANIFESTS += [ROOT / 'review' / f'{name}-deployment.json' for name in
              ('official-balance', 'usage-simple', 'selection-owner', 'esports-output', 'balance-reload', 'voice-delivery', 'subscriptions', 'media-flow', 'direct-budget', 'meme-budget', 'embedding-flow', 'help-refresh', 'birthday-recovery', 'birthday-input', 'birthday-style', 'session-budget')]


def main():
    manifests = [json.loads(p.read_text(encoding='utf-8')) for p in MANIFESTS]
    final = {row['target']: row['after'] for manifest in manifests for row in manifest['files']}
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('101.132.104.41', username='root', key_filename=str(ROOT / '.robot-nopass.pem'), timeout=15)
    try:
        with client.open_sftp() as sftp:
            # Check the whole batch on the host instead of a round trip per file.
            checks = dict(final)
            for manifest in manifests:
                for row in manifest['files']:
                    if row['before']:
                        checks[row['backup']] = row['before']
            verify = ('import hashlib,json\nfrom pathlib import Path\nchecks=json.loads('
                      + repr(json.dumps(checks)) + ')\n'
                      + 'for p,sha in checks.items():\n'
                      + ' assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha,p\n')
            inp, out, err = client.exec_command('python3 -', timeout=30)
            inp.write(verify)
            inp.channel.shutdown_write()
            output, error = out.read(), err.read()
            if out.channel.recv_exit_status():
                raise RuntimeError((output + error).decode(errors='replace'))
            # All prerequisites are checked before this script performs any restore.
            rollback = '''import hashlib,json,subprocess
from pathlib import Path
manifests=json.loads(MANIFESTS)
final={r['target']:r['after'] for m in manifests for r in m['files']}
for target,sha in final.items():
    assert hashlib.sha256(Path(target).read_bytes()).hexdigest()==sha, 'Live source changed: '+target
for m in manifests:
    for r in m['files']:
        if r['before']:
            assert hashlib.sha256(Path(r['backup']).read_bytes()).hexdigest()==r['before'], r['backup']
for m in reversed(manifests):
    for r in reversed(m['files']):
        target=Path(r['target'])
        if r['before']:
            target.write_bytes(Path(r['backup']).read_bytes())
        else:
            target.unlink(missing_ok=True)
subprocess.run(['docker','restart','astrbot'],check=True)
print('Acceptance patches restored; bot restarted; game services untouched.')
'''.replace('MANIFESTS', repr(json.dumps(manifests)))
            dest = manifests[-1]['backup'] + '/rollback_acceptance.py'
            with sftp.open(dest, 'w') as f:
                f.write(rollback)
            report = {'live_files_verified': len(final), 'backup_files_verified': sum(bool(r['before']) for m in manifests for r in m['files']), 'rollback': dest, 'rollback_executed': False}
            (ROOT / 'review' / 'acceptance-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report))
    finally:
        client.close()


if __name__ == '__main__':
    main()
