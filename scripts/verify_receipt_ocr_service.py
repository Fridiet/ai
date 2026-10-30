"""Upload an actual receipt to the read-only operational OCR endpoint."""
import argparse
import json
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image', type=Path)
    parser.add_argument('--route', choices=['/ocr', '/api/ocr/receipt'], default='/api/ocr/receipt')
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--output', type=Path, default=Path('output/receipt_llm/operational_receipt.json'))
    args = parser.parse_args()
    content_type = 'image/png' if args.image.suffix.lower() == '.png' else 'image/jpeg'
    started = time.perf_counter()
    with args.image.open('rb') as image, httpx.Client(timeout=180) as client:
        response = client.post(args.url+args.route, files={'file': (args.image.name, image, content_type)})
    response.raise_for_status()
    payload = response.json()
    # Save food output only, not whole receipts/customer data or request secrets.
    report = dict(route=args.route, http_status=response.status_code, seconds=time.perf_counter()-started,
                  items=[{key:item.get(key) for key in ('name','quantity','unit','consumeByDate','locationCategory','reviewNotes')}
                         for item in payload['items']],
                  review_count=len(payload['reviewCandidates']))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(http_status=report['http_status'], seconds=report['seconds'],
                         names=[item['name'] for item in report['items']],
                         item_note_count=sum(len(item['reviewNotes']) for item in report['items']),
                         blank_date_count=sum(not item['consumeByDate'] for item in report['items']),
                         review_count=report['review_count']), ensure_ascii=False))
    print('report:', args.output.resolve())


if __name__ == '__main__':
    main()
