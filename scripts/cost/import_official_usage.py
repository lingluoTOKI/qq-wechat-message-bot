"""Import an official ZIP without persisting CSV account/key fields."""
import argparse
import json
import sys
from pathlib import Path
for candidate in [Path('/AstrBot/data/pylibs'),Path('/opt/bot-stack/astrbot/data/pylibs'),
                  Path(__file__).resolve().parents[1]/'plugins']:
    if candidate.is_dir():sys.path.insert(0,str(candidate))
from dafeiyu_runtime.request_ledger import DB,Ledger

parser=argparse.ArgumentParser()
parser.add_argument('zip')
host_db=Path('/opt/bot-stack/astrbot/data/plugin_data/astrbot_plugin_request_ledger/requests.db')
parser.add_argument('--db',default=str(host_db) if host_db.parent.is_dir() else DB)
args=parser.parse_args()
print(json.dumps(Ledger(args.db).import_zip(args.zip),ensure_ascii=False,indent=2))
