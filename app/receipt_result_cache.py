"""Bounded process-local cache of server-validated GPT row decisions, never dates."""
import copy
import hashlib
import json
import os
import time
from collections import OrderedDict
from threading import Lock

_entries = OrderedDict()
_lock = Lock()
MAX_ENTRIES = 1024
TTL_SECONDS = 3600


def enabled():
    return os.getenv('OCR_LLM_CACHE_ENABLED', 'false').lower() in {'1', 'true', 'yes'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def clear():
    with _lock:
        _entries.clear()


def _key(namespace, context, row):
    return fingerprint([namespace, context, {k:v for k,v in row.items() if k != 'row_id'}])


def _get(key):
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            return None
        if entry[0] <= time.monotonic():
            del _entries[key]
            return None
        _entries.move_to_end(key)
        return copy.deepcopy(entry[1])


def _put(key, value):
    with _lock:
        _entries[key] = (time.monotonic()+TTL_SECONDS, copy.deepcopy(value))
        _entries.move_to_end(key)
        while len(_entries)>MAX_ENTRIES:
            _entries.popitem(last=False)


async def resolve(rows, *, namespace, context, fetch, response_type, validate, accept):
    """Validate the complete response before caching only accepted individual rows.

    Original row IDs are rebound on lookup. No failure, abstention, unsupported
    identity, absolute date, API credential, quantity, or image is cached.
    """
    keys={row['row_id']:_key(namespace,context,row) for row in rows}
    found={row['row_id']:_get(keys[row['row_id']]) for row in rows}
    missing=[row for row in rows if found[row['row_id']] is None]
    usage={}
    fresh={}
    if missing:
        response,usage=await fetch(missing)
        ids=[r.row_id for r in response.rows]
        if len(ids)!=len(set(ids)) or set(ids)!={r['row_id'] for r in missing}:
            raise ValueError('Invalid cached response identifiers')
        fresh={r.row_id:r.model_dump() for r in response.rows}
    combined=response_type.model_validate({'rows':[
        dict(found[row['row_id']] or fresh[row['row_id']],row_id=row['row_id']) for row in rows]})
    # All existing server checks execute for both hits and new results.
    validate(combined)
    for row in missing:
        decision=next(r for r in combined.rows if r.row_id==row['row_id'])
        if accept(row,decision):
            _put(keys[row['row_id']], {k:v for k,v in decision.model_dump().items() if k!='row_id'})
    return combined,dict(usage,cache_hits=len(rows)-len(missing),cache_misses=len(missing))
