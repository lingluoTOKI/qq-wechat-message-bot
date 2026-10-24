"""Validate a local encrypted backup, or restore only after stopping the reminder."""
import argparse
import socket
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from vault import unseal

def restore(root,name,apply=False):
    root=Path(root).resolve();folder=(root/'backups').resolve();source=(folder/name).resolve()
    if source.parent!=folder or not source.name.startswith('state-') or source.suffix!='.sqlite' or not source.is_file():raise ValueError('Choose an existing state-*.sqlite in backups')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src:
        if src.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed')
        tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'matters','events','delivery','token_ledger','workbench_settings'}.issubset(tables):raise ValueError('Not a reminder database')
        for table in ('matters','workbench_settings'):
            row=src.execute(f"SELECT payload FROM {table} WHERE payload!='' LIMIT 1").fetchone()
            if row:unseal(row[0])
        if not apply:return 'Backup valid and readable by this Windows user; no changes.'
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(('127.0.0.1',31996))==0:raise RuntimeError('Stop the local reminder before restoring')
        current=root/'state.sqlite'
        safety=root/('pre-restore-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite')
        with closing(sqlite3.connect(current)) as dest:
            with closing(sqlite3.connect(safety)) as prior:dest.backup(prior)
            # Restore content without rolling back already incurred model usage.
            usage={t:dest.execute('SELECT * FROM '+t).fetchall() for t in ('ledger','token_ledger','usage_reconciliation')}
            src.backup(dest)
            for table,rows in usage.items():
                if table in tables and rows:dest.executemany('INSERT OR REPLACE INTO '+table+' VALUES('+','.join('?' for _ in rows[0])+')',rows)
            # Old queued private notifications are never replayed after rollback.
            dest.execute("UPDATE delivery SET status='uncertain' WHERE status IN ('pending','sending')")
            if 'reminders' in tables:dest.execute("UPDATE reminders SET state='cancelled' WHERE state IN ('scheduled','queued')")
            dest.commit()
        return 'Restored. Previous database saved. Review uncertain deliveries and re-create reminders before use; recalibrate token allowance.'

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('name');parser.add_argument('--restore',action='store_true')
    args=parser.parse_args();print(restore(Path(__file__).parent,args.name,args.restore))
