"""Event bus: worker threads publish, every open SSE connection receives."""
import asyncio
import json
import threading

_lock = threading.Lock()
_subs = []   # (loop, asyncio.Queue)


def subscribe():
    loop = asyncio.get_running_loop()
    q = asyncio.Queue(maxsize=2000)
    with _lock:
        _subs.append((loop, q))
    return q


def unsubscribe(q):
    with _lock:
        _subs[:] = [s for s in _subs if s[1] is not q]


def publish(event: dict):
    data = json.dumps(event, ensure_ascii=False)
    with _lock:
        subs = list(_subs)
    for loop, q in subs:
        def put(q=q):
            if not q.full():
                q.put_nowait(data)
        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:   # loop closed: client went away
            unsubscribe(q)
