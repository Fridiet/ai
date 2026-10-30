"""Development-only review queue. Store hints are not verified labels."""
import json
import re
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def store_hint(items):
    # Only upper receipt content; bank names and food brands are not store proof.
    ordered=sorted((i for i in items if i.get('box')),key=lambda i:min(p[1] for p in i['box']))
    text=' '.join(i['text'] for i in ordered[:8])
    if re.search(r'이마트\s*24|GS\s*25|세븐일레븐|(?:^|\s)CU(?:\s|$)',text,re.I):
        return 'convenience_hint'
    if re.search(r'다이소|DAISO',text,re.I):
        return 'household_hint'
    if re.search(r'이마트|emart|롯데마트|홈플러스|하나로마트|식자재마트',text,re.I):
        return 'grocery_hint'
    if re.search(r'스타벅스|투썸|이디야|메가커피|baskin|배스킨',text,re.I):
        return 'cafe_hint'
    return 'needs_visual_review'


def main():
    partition=json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    labels=json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))
    annotated={r['filename']:r for r in labels['records']}
    visual_path=ROOT/'evaluation/new_receipts_visual_inventory.json'
    visual=json.loads(visual_path.read_text(encoding='utf-8'))['records'] if visual_path.exists() else []
    development={r['filename'] for r in partition['records'] if r['split']=='development'}
    if any(r['filename'] not in development for r in visual):
        raise ValueError('Visual triage must remain development-only')
    reviewed={r['filename']:r for r in visual}
    records=[]
    counts=Counter()
    for entry in partition['records']:
        if entry['split']!='development':
            continue
        cache=ROOT/'output/new_receipts/raw'/f"{entry['sha256']}.json"
        record={'filename':entry['filename'],'image_path':entry['image_path'],'sha256':entry['sha256'],
                'annotation_status':'assistant_draft' if entry['filename'] in annotated else 'unannotated',
                'human_verified':False,'training_eligible':False}
        if entry['filename'] in reviewed:
            record['visual_triage']=reviewed[entry['filename']]
            if entry['filename'] not in annotated:
                record['annotation_status']='visual_triage_only'
        if not cache.exists():
            record.update(ocr_status='pending',store_hint=None)
        else:
            payload=json.loads(cache.read_text(encoding='utf-8'))
            if payload.get('image_sha256')!=entry['sha256']:
                raise ValueError('Raw cache identity mismatch')
            record.update(ocr_status='cached',store_hint=store_hint(payload['items']))
        records.append(record)
        counts[record['ocr_status']]+=1
        counts[record['annotation_status']]+=1
        if record['store_hint']:
            counts[record['store_hint']]+=1
    # Grocery hints first, but no hint group is discarded or turned into truth.
    records.sort(key=lambda r:(r['annotation_status']!='unannotated',r['store_hint'] not in {'grocery_hint','convenience_hint'},r['filename']))
    output=ROOT/'output/new_receipts/review_queue.json'
    output.write_text(json.dumps({'scope':'development_only','counts':dict(counts),
        'warning':'Store hints are heuristic review ordering, not verified store types or training labels.',
        'records':records},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(counts)))


if __name__=='__main__': main()
