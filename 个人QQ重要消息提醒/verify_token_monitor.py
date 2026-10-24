import tempfile,time
from datetime import datetime
from pathlib import Path
from verify_workflows import fixture
from relay import TZ

with tempfile.TemporaryDirectory() as directory:
    r=fixture(Path(directory));start=datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    r.db.executemany('INSERT INTO token_ledger VALUES(?,?,?,?)',[
        ('yesterday',100,'measured',start-1),('today',250,'measured',start+1),
        ('pending',999,'reserved',start+2),('old',888,'measured',start-8*86400),
        ('baseline-extra',1234,'baseline',start+3)])
    r.db.commit();u=r.token_monitor(start+100)
    assert u['today']==dict(day=datetime.fromtimestamp(start,TZ).date().isoformat(),measured=250,requests=1,reserved=999)
    assert u['week_measured']==350 and len(u['days'])==7
    assert u['days'][-2]['measured']==100
    r.db.close()
print('PASS: Beijing day boundary, 7-day sum, measured/reserved/baseline separation')
