"""Freeze user-provided receipt groups before any annotation/OCR/model tuning."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}


def dhash(path):
    with Image.open(path) as image:
        pixels = list(ImageOps.exif_transpose(image).convert('L').resize((9,8)).getdata())
    value = 0
    for y in range(8):
        for x in range(8):
            value = (value << 1) | int(pixels[y*9+x] > pixels[y*9+x+1])
    return value


def build(folder, old_paths):
    records = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in EXTENSIONS:
            records.append(dict(image_path=str(path.resolve()), filename=path.name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), dhash=dhash(path), old=False))
    new_count = len(records)
    for path in old_paths:
        if path.exists():
            records.append(dict(image_path=str(path.resolve()),filename=path.name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),dhash=dhash(path),old=True))
    parent = list(range(len(records)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, row in enumerate(records):
        for j in range(i):
            other = records[j]
            if row['sha256'] == other['sha256'] or (row['dhash'] ^ other['dhash']).bit_count() <= 6:
                parent[find(i)] = find(j)
    groups = {}
    for i, row in enumerate(records):
        groups.setdefault(find(i), []).append(row)
    assigned = []
    for rows in groups.values():
        group_id = min(r['sha256'] for r in rows)
        exposed = any(r['old'] for r in rows)
        split = 'development_overlap' if exposed else (
            'frozen_evaluation' if int(group_id[:8],16) % 5 == 0 else 'development')
        for row in rows:
            if not row['old']:
                assigned.append({**row, 'dhash':f"{row['dhash']:016x}", 'group_id':group_id,
                    'split':split, 'training_eligible':False,
                    'annotation_status':'not_inspected', 'source_dataset_split':'train'})
    return {'purpose':'preinspection_partition', 'counts':{
        'images':new_count, 'groups':len({r['group_id'] for r in assigned}),
        **{s:sum(r['split']==s for r in assigned) for s in
           ['development','development_overlap','frozen_evaluation']}},
        'warning':'dHash grouping is conservative screening, not proof against all crops/duplicates. '
                  'Frozen evaluation images and labels must not be inspected while tuning. '
                  'No records are authorized as training labels yet.',
        'records':sorted(assigned,key=lambda r:r['filename'])}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'output/new_receipts/partition.json')
    args=parser.parse_args()
    if args.output.exists():
        raise SystemExit('Partition already exists; refusing to reshuffle frozen evaluation')
    previous=json.loads((ROOT/'output/local_receipt_review/assistant_annotations.json').read_text(encoding='utf-8'))
    old=[Path(r['image_path']) for r in previous['records']]
    examples=Path('C:/Users/your-user/OneDrive/바탕 화면/캡스톤/영수증사진')
    old += [p for p in examples.iterdir() if p.suffix.lower() in EXTENSIONS]
    report=build(args.folder,old)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['counts']))
