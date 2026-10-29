"""Event-local sharing. No cross-user/session cache and no chat persistence."""
import asyncio
import uuid


def state(event):
    current = event.get_extra('dafeiyu_request_context', None)
    if current is None:
        current = {'request_id': uuid.uuid4().hex[:12], 'reads': {}}
        event.set_extra('dafeiyu_request_context', current)
    return current


async def read_once(event, key, factory):
    reads = state(event)['reads']
    if key not in reads:
        reads[key] = asyncio.create_task(factory())
    task = reads[key]
    try:
        return await asyncio.shield(task)
    except BaseException:
        if task.done() and reads.get(key) is task:
            reads.pop(key, None)
        raise


def claim_feature(event, feature, owner):
    """One handler may consume a matched feature in one incoming event."""
    claims=state(event).setdefault('claims',{})
    if feature in claims:return False
    claims[feature]=owner
    return True
