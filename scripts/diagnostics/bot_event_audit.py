"""Persist bounded, content-free operational events from Docker logs."""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ANSI = re.compile(r'\x1b\[[0-9;]*m')
LIMIT = 2 * 1024 * 1024  # Per UTC day, maximum seven days.


def parse_event(line):
    line = ANSI.sub('', line)
    match = re.match(r'(\d{4}-\d\d-\d\dT\S+Z) ', line)
    if not match:
        return None
    stamp = match[1]
    if '[WARN]' in line or '[ERRO]' in line or '[CRIT]' in line:
        tags = re.findall(r'\[([^\]]+)\]', line)
        source = next((x for x in reversed(tags) if re.fullmatch(r'[A-Za-z0-9_.]+:\d+', x)), 'unknown')
        # Only a code location is persisted, never arbitrary exception text.
        return {'time':stamp,'kind':'error' if '[ERRO]' in line or '[CRIT]' in line else 'warning','source':source}
    if '[CacheGuard 1.3] cache ' in line:
        values = dict(re.findall(r'\b(input|cached|miss|predicted)=(\d+)', line))
        return {'time':stamp,'kind':'cache_usage',**{k:int(v) for k,v in values.items()}}
    if '[主动消息]' in line and '跳过' in line and '预算' in line:
        return {'time':stamp,'kind':'proactive_budget_skip'}
    return None


def main():
    os.umask(0o077)
    directory = Path('/opt/bot-stack/logs/plugin-events')
    directory.mkdir(parents=True, exist_ok=True)
    state = directory/'cursor.json'
    try:
        cursor = json.loads(state.read_text())['cursor']
    except (OSError, ValueError, KeyError):
        cursor = (datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
    result = subprocess.run(['docker','logs','--timestamps','--since',cursor,'astrbot'],
                            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=25,check=True)
    latest = cursor
    for line in result.stdout.decode('utf-8',errors='replace').splitlines():
        match = re.match(r'(\d{4}-\d\d-\d\dT\S+Z) ',line)
        if not match:
            continue
        stamp = match[1]
        if stamp <= cursor:
            continue
        latest = max(latest, stamp)
        event = parse_event(line)
        if event is None:
            continue
        path = directory/('events-'+stamp[:10]+'.jsonl')
        if not path.exists() or path.stat().st_size < LIMIT:
            with path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(event,ensure_ascii=False)+'\n')
    temporary = state.with_suffix('.tmp')
    temporary.write_text(json.dumps({'cursor':latest}),encoding='utf-8')
    temporary.replace(state)
    cutoff = (datetime.now(timezone.utc)-timedelta(days=6)).date().isoformat()
    for path in directory.glob('events-????-??-??.jsonl'):
        if path.name[7:17] < cutoff:
            path.unlink()


if __name__ == '__main__':
    main()
