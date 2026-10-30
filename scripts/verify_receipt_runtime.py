"""Local HTTP semantic regression checks, analysis only (no inventory writes)."""
import argparse
import hashlib
import json
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
CASES=['IMG00218.png','IMG00238.png','IMG00376.png','IMG00390.png','IMG00130.png','IMG00131.png']


def check(filename, body):
    items = body['items']
    if filename in {'IMG00130.png','IMG00131.png'}:
        all_rows=items+body.get('reviewCandidates',[])
        if any('IRC' in i.get('rawText','').upper() for i in all_rows):return False
        if any(i.get('rawText','').replace(' ','').lower().startswith(
                ('cashier:', 'reg#', '세종종합운동장1로14t')) for i in all_rows):return False
        tortillas=[i for i in items if i['name']=='또띠아']
        waters=[i for i in items if i['name']=='물']
        if filename=='IMG00130.png':
            water_ml=waters[0]['quantity'] * {'ml':1,'l':1000}.get(waters[0]['unit'],0) if len(waters)==1 else 0
            return (len(tortillas)==3 and all(i['quantity']==1 for i in tortillas)
                    and water_ml==24000
                    and any(i['name']=='디너롤' and i['quantity']==72 and i['unit']=='개' for i in items)
                    and any('CRISP SPICY' in i.get('rawText','') for i in body.get('reviewCandidates',[])))
        return (len(tortillas)==2 and len(waters)==3
                and any(i['name']=='체다치즈' and i['quantity']==907 and i['unit']=='g' for i in items))
    if filename == 'IMG00218.png':
        return any(i['name']=='백오이' and i['quantity']==5 and i['unit']=='개' for i in items)
    if filename == 'IMG00238.png':
        return any(i['name']=='감자칩' and i['quantity']==110 and i['unit']=='g' for i in items)
    if filename == 'IMG00376.png':
        return (all(i['name']!='사워크림' for i in items)
                and any('김자침' in i.get('rawText','') for i in body.get('reviewCandidates',[])))
    if filename == 'IMG00390.png':
        return any(i['name']=='감자칩' for i in items)
    raise ValueError('Unknown regression case')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8011)
    parser.add_argument('--filenames',nargs='+',choices=CASES,default=CASES)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:raise ValueError('Invalid local port')
    entries={r['filename']:r for r in json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']}
    results=[]
    for name in args.filenames:
        entry=entries[name]
        if entry['split']!='development':raise ValueError('Development only')
        image=Path(entry['image_path']).read_bytes()
        if hashlib.sha256(image).hexdigest()!=entry['sha256']:raise ValueError('Image changed')
        response=requests.post(f'http://127.0.0.1:{args.port}/api/ocr/receipt',
                               files={'file':(name,image,'image/png')},timeout=120)
        response.raise_for_status()
        body=response.json()
        result=dict(filename=name,image_sha256=entry['sha256'],passed=check(name,body),
                    names=[i['name'] for i in body['items']],review_count=len(body.get('reviewCandidates',[])))
        results.append(result)
        print(json.dumps(result,ensure_ascii=True),flush=True)
    output=ROOT/f'output/receipt_readiness/http_semantics_{args.port}.json'
    output.write_text(json.dumps(dict(scope='selected_development_regressions_not_accuracy',
        port=args.port,results=results),ensure_ascii=False,indent=2),encoding='utf-8')
    if not all(r['passed'] for r in results):raise AssertionError('HTTP semantic regression failed')


if __name__=='__main__':main()
