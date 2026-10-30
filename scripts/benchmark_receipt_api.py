"""Serial local OCR benchmark; analysis endpoint only, never inventory writes."""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'http://127.0.0.1:8000/api/ocr/receipt'


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered)-1)*fraction
    lo = int(position)
    hi = min(lo+1, len(ordered)-1)
    return ordered[lo] + (ordered[hi]-ordered[lo])*(position-lo)


def summarize(records):
    timings = [r['seconds'] for r in records if r.get('phase') == 'repeat' and r.get('status') == 200]
    hashes = {}
    for row in records:
        if row.get('status') == 200:
            hashes.setdefault(row['filename'], set()).add(row['response_sha256'])
    return {'repeat_success_count': len(timings), 'request_count': len(records),
            'failed_requests': sum(r.get('status') != 200 for r in records),
            'repeat_p50_seconds': percentile(timings,.5), 'repeat_p95_seconds': percentile(timings,.95),
            'response_stable_by_image': {k:len(v)==1 for k,v in hashes.items()},
            'first_request_is_proven_cold': False,
            'caveat': 'Small serial sample on this PC, not a production p95/SLA. First request is not a controlled cold start. Responses hashed, not stored.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--filenames', nargs='+', default=['IMG00014.png','IMG00023.png','IMG00238.png'])
    parser.add_argument('--output', type=Path, default=ROOT/'output/receipt_readiness/api_benchmark.json')
    args=parser.parse_args()
    if not 1 <= args.repeats <= 20:
        raise ValueError('repeats must be 1..20')
    if not 1024 <= args.port <= 65535:
        raise ValueError('Invalid local port')
    endpoint = f'http://127.0.0.1:{args.port}/api/ocr/receipt'
    manifest=json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    by_name={r['filename']:r for r in manifest['records']}
    selected=[]
    for name in args.filenames:
        entry=by_name[name]
        if entry['split']!='development':
            raise ValueError('Frozen evaluation excluded')
        payload=Path(entry['image_path']).read_bytes()
        if hashlib.sha256(payload).hexdigest()!=entry['sha256']:
            raise ValueError('Source changed')
        selected.append((name,payload,entry['sha256']))
    records=[]
    output=args.output.resolve()
    if not output.is_relative_to(ROOT/'output'):
        raise ValueError('Benchmark output must remain under AI/output')
    output.parent.mkdir(parents=True,exist_ok=True)
    with requests.Session() as session:
        for iteration in range(args.repeats+1):
            for name,payload,sha in selected:
                row={'filename':name,'image_sha256':sha,'iteration':iteration,
                     'phase':'initial' if iteration==0 else 'repeat',
                     'started_at':datetime.now(timezone.utc).isoformat()}
                started=time.perf_counter()
                try:
                    response=session.post(endpoint,files={'file':(name,payload,'image/jpeg' if name.lower().endswith(('.jpg','.jpeg')) else 'image/png')},timeout=120)
                    row['seconds']=time.perf_counter()-started
                    row['status']=response.status_code
                    if response.status_code==200:
                        body=response.json()
                        row.update(item_count=len(body['items']),review_count=len(body.get('reviewCandidates',[])),
                            response_sha256=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
                except (requests.RequestException, ValueError, KeyError) as exc:
                    row.update(status=None,seconds=time.perf_counter()-started,error_type=type(exc).__name__)
                records.append(row)
                report={'endpoint':endpoint,'scope':'local_serial_development_images','summary':summarize(records),'records':records}
                temp=output.with_suffix('.tmp')
                temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                temp.replace(output)
                print(json.dumps(row),flush=True)


if __name__=='__main__': main()
