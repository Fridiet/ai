"""Read-only HTTP regression checks for development receipt quantities."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import requests

ROOT=Path(__file__).resolve().parents[1]


def check_items(filename, items):
    checks=[]
    def check(name, predicate, description):
        matches=[x for x in items if x['name']==name]
        checks.append(dict(name=name,description=description,
                           passed=len(matches)==1 and predicate(matches[0])))
    if filename=='IMG00059.png':
        check('밀가루', lambda x: x['quantity']==2.5 and x['unit']=='kg'
              and any('복원' in n for n in x['reviewNotes'])
              and not any('검산하지 못' in n for n in x['reviewNotes']),
              '2.5kg package with corroborated purchase count; provenance retained')
        check('우유', lambda x: x['quantity']==1 and x['unit']=='개'
              and any('포장당 용량' in n for n in x['reviewNotes']),
              'Damaged m1 unit remains unconfirmed, not asserted as 1800ml')
        check('청오이', lambda x: x['category']=='vegetable', 'Specific cucumber identity retained')
    elif filename=='IMG00664.png':
        check('백오이', lambda x: x['category']=='vegetable', 'Specific cucumber identity retained')
    return checks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8011)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:
        raise ValueError('Invalid local port')
    entries={r['filename']:r for r in json.loads(
        (ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']}
    records=[]
    for filename in ['IMG00059.png','IMG00664.png']:
        entry=entries[filename]
        if entry['split']!='development':
            raise ValueError('Development only')
        payload=Path(entry['image_path']).read_bytes()
        if hashlib.sha256(payload).hexdigest()!=entry['sha256']:
            raise ValueError('Source changed')
        started=time.perf_counter()
        response=requests.post(f'http://127.0.0.1:{args.port}/api/ocr/receipt',
                               files={'file':(filename,payload,'image/png')},timeout=120)
        response.raise_for_status()
        body=response.json()
        checks=check_items(filename,body['items'])
        record=dict(filename=filename,image_sha256=entry['sha256'],seconds=time.perf_counter()-started,
                    status=response.status_code,checks=checks,
                    items=[x for x in body['items'] if x['name'] in {'밀가루','우유','청오이','백오이'}])
        records.append(record)
        print(json.dumps(dict(filename=filename,checks=checks),ensure_ascii=True),flush=True)
    passed=all(c['passed'] for r in records for c in r['checks'])
    (ROOT/'output/receipt_readiness/count_corroboration_http.json').write_text(
        json.dumps(dict(scope='development_http_regression_not_accuracy',passed=passed,records=records),
                   ensure_ascii=False,indent=2),encoding='utf-8')
    if not passed:
        raise SystemExit('HTTP regression failed')


if __name__=='__main__':
    main()
