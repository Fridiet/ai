"""Read-only local API smoke test on existing user-provided receipts."""
import json
import time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
IMAGES = Path('C:/Users/your-user/OneDrive/바탕 화면/캡스톤/영수증사진')


def main():
    report = []
    for name in ['스크린샷 2026-07-17 212620.png',
                 'news-p.v1.20230216.4dde0d0e8a2d440595d9e9647501eb5d_Z1.jpg',
                 '3398113_3495546_3424.jpg']:
        path = IMAGES/name
        started = time.perf_counter()
        response = requests.post('http://127.0.0.1:8000/api/ocr/receipt',
            files={'file': (name, path.read_bytes(), 'image/png' if path.suffix=='.png' else 'image/jpeg')},
            timeout=120)
        response.raise_for_status()
        body = response.json()
        result = {'filename':name, 'seconds':round(time.perf_counter()-started, 3),
                  'items':body['items'], 'reviewCandidates':body.get('reviewCandidates', [])}
        report.append(result)
        print(json.dumps({'filename':name, 'seconds':result['seconds'],
                          'names':[r['name'] for r in result['items']],
                          'review_count':len(result['reviewCandidates'])},ensure_ascii=True), flush=True)
    output = ROOT/'output/receipt_readiness/live.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
