"""Resume local OCR caching for development only; frozen images are excluded."""
import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import time
import requests

ROOT=Path(__file__).resolve().parents[1]


def run(limit=None):
    partition=ROOT/'output/new_receipts/partition.json'
    manifest=json.loads(partition.read_text(encoding='utf-8'))
    records=[r for r in manifest['records'] if r['split']=='development']
    if limit:
        records=records[:limit]
    output=partition.parent/'raw'
    output.mkdir(exist_ok=True)
    results=[]
    for i, record in enumerate(records,1):
        path=Path(record['image_path'])
        cache=output/(record['sha256']+'.json')
        entry={'filename':record['filename'],'sha256':record['sha256']}
        if cache.exists():
            entry['status']='cached'
        else:
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=record['sha256']:
                raise ValueError('Image changed since partition freeze')
            started=time.perf_counter()
            try:
                response=requests.post('http://127.0.0.1:8000/ocr/raw',
                    files={'file':(path.name,data,mimetypes.guess_type(path.name)[0])},timeout=120)
                response.raise_for_status()
                result=response.json()
                if not isinstance(result.get('items'),list):
                    raise ValueError('Invalid OCR response')
                result['elapsed_seconds']=time.perf_counter()-started
                result['image_sha256']=record['sha256']
                temp=cache.with_suffix('.tmp')
                temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                temp.replace(cache)
                entry['status']='ok'
                entry['ocr_boxes']=len(result['items'])
            except (requests.RequestException, ValueError) as exc:
                entry['status']='error'
                entry['error_type']=type(exc).__name__
        results.append(entry)
        (partition.parent/'preparation.json').write_text(json.dumps({
            'scope':'development_only','partition_sha256':hashlib.sha256(partition.read_bytes()).hexdigest(),
            'training_performed':False,'records':results},ensure_ascii=False,indent=2),encoding='utf-8')
        print(f"{i}/{len(records)} {path.name} {entry['status']}",flush=True)
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--limit',type=int)
    run(p.parse_args().limit)
