"""Build conservative OCR/visual-label pairs; never train or change production data."""
import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import unicodedata

# Annotation vocabulary only; production ingredient catalog is not modified.
CANONICAL_ALIASES = {'생수': '물', '캐슈너트': '캐슈넛', '과일음료': '음료'}


def key(text):
    text = unicodedata.normalize('NFKC', text).lower()
    text = re.sub(r'^\s*\d{3}(?=\s*[^\d])', '', text)
    return re.sub(r'[^가-힣a-z0-9]', '', text)


def candidates(items):
    result = []
    for n, item in enumerate(items):
        text = item.get('text', '').strip()
        if not re.search(r'[가-힣a-zA-Z]', text):
            continue
        result.append({'ocr_text': text, 'ocr_indices': [n]})
        # Adjacent OCR fragments only; never join arbitrary receipt-wide text.
        if n + 1 < len(items):
            other = items[n + 1]
            a, b = item.get('box'), other.get('box')
            if a and b:
                ay = sum(p[1] for p in a) / len(a)
                by = sum(p[1] for p in b) / len(b)
                height = max(p[1] for p in a) - min(p[1] for p in a)
                if abs(ay - by) <= max(12, height * .65):
                    result.append({'ocr_text': text + ' ' + other.get('text', ''), 'ocr_indices': [n, n + 1]})
    return result


def align(labels, items):
    pool = candidates(items)
    output = []
    for label in labels:
        target = key(label['product_name'])
        ranked = sorted([(SequenceMatcher(None, target, key(c['ocr_text']), autojunk=False).ratio(), c)
                         for c in pool], key=lambda x: x[0], reverse=True)
        if not ranked:
            output.append({'status': 'no_candidate'})
            continue
        score, best = ranked[0]
        # Overlapping spans are alternatives for the same OCR row, not competitors.
        rival = max((s for s, c in ranked[1:] if not set(c['ocr_indices']) & set(best['ocr_indices'])), default=0)
        accepted = score >= .94 and score - rival >= .08 and (len(target) >= 5 or score == 1)
        output.append({'status': 'aligned' if accepted else 'alignment_review',
                       'similarity': round(score, 4), 'margin': round(score-rival, 4),
                       **(best if score >= .75 else {})})
    uses = Counter(i for r in output if r['status'] == 'aligned' for i in r['ocr_indices'])
    for row in output:
        if row['status'] == 'aligned' and any(uses[i] > 1 for i in row['ocr_indices']):
            row['status'] = 'shared_ocr_span'
    return output


def prepare(directory):
    annotations = json.loads((directory / 'assistant_annotations.json').read_text(encoding='utf-8'))
    baseline = json.loads((directory / 'review.json').read_text(encoding='utf-8'))
    by_hash = {r['image_sha256']: r for r in baseline['records']}
    records = annotations['records']
    for receipt in records:
        for item in receipt['expected_items']:
            item['original_canonical_name'] = item['canonical_name']
            item['canonical_name'] = CANONICAL_ALIASES.get(item['canonical_name'], item['canonical_name'])
    labels_by_name = defaultdict(set)
    for r in records:
        for item in r['expected_items']:
            if item['label_status'] != 'unresolved':
                labels_by_name[key(item['product_name'])].add((item['is_food'], item['canonical_name']))
    conflicts = {k for k, v in labels_by_name.items() if len(v) > 1}
    paired, excluded = [], []
    for receipt in records:
        original = by_hash[receipt['image_sha256']]
        image = Path(original['image_path'])
        if hashlib.sha256(image.read_bytes()).hexdigest() != receipt['image_sha256']:
            raise ValueError(f'Image changed: {receipt["filename"]}')
        cache = Path(original['raw_cache'])
        raw = json.loads(cache.read_text(encoding='utf-8'))
        labels = receipt['expected_items']
        matches = align(labels, raw['items'])
        for n, (label, match) in enumerate(zip(labels, matches), 1):
            reason = None
            if label['label_status'] == 'unresolved':
                reason = 'unresolved_label'
            elif label['transaction_type'] == 'return':
                reason = 'return_transaction'
            elif key(label['product_name']) in conflicts:
                reason = 'conflicting_canonical_labels'
            elif receipt['store_type'] == 'other' and label['is_food']:
                reason = 'outside_grocery_food_scope'
            elif match['status'] != 'aligned':
                reason = match['status']
            row = {'filename': receipt['filename'], 'image_sha256': receipt['image_sha256'],
                'annotation_row': n, 'duplicate_group': receipt['duplicate_group'],
                'source_split': receipt['source_split'], 'usage': 'development_only',
                'store_type': receipt['store_type'], 'label_source': receipt['annotation_source'],
                'human_reviewed': False, 'training_eligible': False,
                'alignment_verified': False, 'target': label, 'alignment': match}
            if reason:
                row['exclusion_reason'] = reason
                # Do not export arbitrary header/payment text as rejected examples.
                row['alignment'] = {k: v for k, v in match.items() if k not in ('ocr_text', 'ocr_indices')}
                excluded.append(row)
            else:
                row['task'] = 'food_name_normalization' if label['is_food'] else 'nonfood_classification'
                paired.append(row)
    summary = {'receipt_count': len(records), 'annotation_rows': sum(len(r['expected_items']) for r in records),
        'aligned_candidate_pairs': len(paired), 'food_pairs': sum(r['target']['is_food'] for r in paired),
        'nonfood_pairs': sum(not r['target']['is_food'] for r in paired),
        'excluded_rows': len(excluded), 'excluded_reasons': dict(Counter(r['exclusion_reason'] for r in excluded)),
        'changed_text_pairs': sum(key(r['alignment']['ocr_text']) != key(r['target']['product_name']) for r in paired),
        'training_performed': False, 'independent_test': False,
        'note': '문자열 유사도는 정답 확률이 아닙니다. 자동 연결 후보이며 학습 승인 전 원문 연결 검증이 필요합니다.'}
    output = directory / 'pairs'
    output.mkdir(exist_ok=True)
    for name, data in [('candidates.json', paired), ('excluded.json', excluded), ('summary.json', summary)]:
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'README.md').write_text('# OCR–검수 결과 연결 후보\n\n' +
        '\n'.join(f'- {k}: {v}' for k,v in summary.items()) +
        '\n\n현재 파일은 학습 완료 데이터가 아니라 연결 후보입니다. 동일 이미지/거래 그룹을 나누어 학습·평가에 중복 배치하지 마세요. '
        '원래 test 자료를 이미 열람했으므로 독립 성능 평가용으로 사용하지 않습니다. '
        '식품 true는 신선 식재료만을 뜻하지 않습니다. 과자와 가공식품도 포함됩니다. '
        'canonical_name은 주석 분류 제안이며 DB 마스터 ID 검증 전입니다. 수량은 교정 모델 타깃에 자동 사용하지 않습니다.\n', encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('output/local_receipt_review'))
    print(json.dumps(prepare(parser.parse_args().directory), ensure_ascii=False, indent=2))
