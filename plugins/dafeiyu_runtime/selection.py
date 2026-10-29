"""Bounded, process-local selection leases shared by music plugins."""
import secrets
import time

_leases = {}


def claim(origin, sender, kind, ttl):
    now=time.monotonic()
    for key,value in list(_leases.items()):
        if value[2]<=now:_leases.pop(key,None)
    while len(_leases)>=256:_leases.pop(next(iter(_leases)))
    lease=secrets.token_hex(8)
    _leases[(origin,str(sender))]=(kind,lease,now+max(1,ttl))
    return lease


def owns(origin,sender,kind,lease=None,*,allow_expired=False):
    value=_leases.get((origin,str(sender)))
    return bool(value and (allow_expired or value[2]>time.monotonic()) and value[0]==kind and (lease is None or value[1]==lease))
