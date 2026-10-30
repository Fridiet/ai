"""Validate image-by-image assistant annotations and build a local-only report.

This does not create labels from OCR predictions and does not train a model.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

FIELDS = ('product_name', 'canonical_name', 'is_food', 'quantity', 'unit', 'note')


def assemble(directory: Path):
    baseline = json.loads((directory / 'review.json').read_text(encoding='utf-8'))
    annotations = {}
    for source in sorted(directory.glob('visual_*.json')):
        batch = json.loads(source.read_text(encoding='utf-8'))['receipts']
        overlap = annotations.keys() & batch.keys()
        if overlap:
            raise ValueError(f'Duplicate annotation: {sorted(overlap)}')
        annotations.update(batch)
    names = [r['filename'] for r in baseline['records']]
    if len(names) != len(set(names)) or set(names) != set(annotations):
        raise ValueError(f'Coverage mismatch: missing={set(names)-set(annotations)}, extra={set(annotations)-set(names)}')
    records, unresolved = [], []
    for original in baseline['records']:
        name = original['filename']
        image = Path(original['image_path'])
        if hashlib.sha256(image.read_bytes()).hexdigest() != original['image_sha256']:
            raise ValueError(f'Image changed: {name}')
        annotation = annotations[name]
        if annotation['store_type'] not in ('grocery', 'convenience', 'other'):
            raise ValueError(f'Invalid store: {name}')
        items = []
        for index, values in enumerate(annotation['items'], 1):
            if len(values) != len(FIELDS):
                raise ValueError(f'Invalid fields: {name} row {index}')
            item = dict(zip(FIELDS, values))
            if not isinstance(item['product_name'], str) or not item['product_name'].strip():
                raise ValueError(f'Empty name: {name} row {index}')
            if item['is_food'] is not None and type(item['is_food']) is not bool:
                raise ValueError(f'Invalid food label: {name} row {index}')
            for field in ('canonical_name', 'unit', 'note'):
                if item[field] is not None and not isinstance(item[field], str):
                    raise ValueError(f'Invalid {field}: {name} row {index}')
            quantity = item['quantity']
            if quantity is not None and (type(quantity) not in (int, float) or not math.isfinite(quantity) or quantity == 0):
                raise ValueError(f'Invalid quantity: {name} row {index}')
            if quantity is not None and quantity < 0 and not item['note']:
                raise ValueError(f'Return quantity needs note: {name}')
            item['transaction_type'] = 'return' if quantity is not None and quantity < 0 else 'purchase'
            item['label_status'] = ('unresolved' if item['is_food'] is None or
                (item['is_food'] and not item['canonical_name']) or quantity is None else 'assistant_labeled')
            if item['label_status'] == 'unresolved':
                unresolved.append({'filename': name, 'row': index, **item})
            items.append(item)
        records.append({
            'filename': name, 'image_path': str(image), 'image_uri': image.as_uri(),
            'image_sha256': original['image_sha256'], 'source_split': original.get('source_split'),
            'store_type': annotation['store_type'], 'expected_items': items,
            'annotation_note': annotation.get('note', ''),
            'annotation_source': 'assistant_direct_image_review',
            'assistant_review_complete': True, 'human_reviewed': False, 'reviewed': False,
            'training_eligible': False, 'evaluation_use': 'development_review_not_independent_test',
            'duplicate_group': 'same_receipt_00358_00378' if name in ('IMG00358.png', 'IMG00378.png') else original['image_sha256'],
        })
    items = [item for record in records for item in record['expected_items']]
    summary = {
        'images': len(records), 'assistant_reviewed_images': len(records), 'product_rows': len(items),
        'food_rows': sum(i['is_food'] is True for i in items),
        'nonfood_rows': sum(i['is_food'] is False for i in items),
        'unknown_food_rows': sum(i['is_food'] is None for i in items),
        'unresolved_rows': len(unresolved),
        'store_types': dict(Counter(r['store_type'] for r in records)),
        'empty_product_receipts': sum(not r['expected_items'] for r in records),
        'training_performed': False,
    }
    payload = {'purpose': 'assistant_visual_annotation', 'summary': summary,
        'caveat': 'AI가 원본 이미지를 대조한 주석이며 사람 검증 정답은 아닙니다. 불명확한 항목은 학습 정답에서 제외해야 합니다.',
        'quantity_policy': '영수증 구매 단위 수량. 포장 내부 개수/중량과 다름. 음수는 반품.',
        'records': records}
    for name, content in [('assistant_annotations.json', payload), ('assistant_unresolved.json', unresolved), ('assistant_summary.json', summary)]:
        (directory / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
    data = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    page = PAGE.replace('__DATA__', data)
    (directory / 'assistant_annotation.html').write_text(page, encoding='utf-8')
    (directory / 'assistant_review_report.md').write_text(
        '# 영수증 156장 시각 검수 결과\n\n' + '\n'.join(f'- {k}: {v}' for k, v in summary.items()) +
        '\n\n156개 파일과 SHA-256을 대조했습니다. 기존 review.json 및 annotation.html/json은 변경하지 않았습니다.\n'
        '\n모든 상품 행을 이미지에서 확인했습니다. 식품 여부 또는 품목/수량이 불확실한 행은 unresolved로 분리했습니다.\n'
        '\n이 결과는 AI 보조 주석이며 독립적인 사람 검증 정답이나 OCR 정확도 수치가 아닙니다. 원래 test 폴더지만 이미 개발에 열람했으므로 독립 테스트로 재사용하면 안 됩니다.\n'
        '\nIMG00358.png와 IMG00378.png는 동일 거래의 다른 이미지로 관찰되어 같은 duplicate_group에 묶었습니다. 기타 동일 거래 중복은 추가 점검이 필요합니다.\n'
        '\n학습/DB 등록/운영 모델 교체는 실행하지 않았습니다. 개인정보가 포함된 원본은 로컬 파일로만 참조하며 주석에는 상품 정보만 기록했습니다.\n', encoding='utf-8')
    return summary


PAGE = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>156장 영수증 · AI 시각 검수 완료</title><style>
body{margin:24px;font:15px system-ui;background:#f5f6f0;color:#253222}header{position:sticky;top:0;background:#f5f6f0;padding:12px 0;z-index:1}button,select,input{padding:10px;margin:4px;border:1px solid #bac5ac;border-radius:6px}main{display:grid;grid-template-columns:minmax(300px,45%) 1fr;gap:24px}img{width:100%;max-height:80vh;object-fit:contain;object-position:top;background:white}table{border-collapse:collapse;width:100%;background:white}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left;overflow-wrap:anywhere}.pending{background:#fff0cb}.note{color:#687362;font-size:13px}pre{white-space:pre-wrap}a{color:#41632a}@media(max-width:800px){main{display:block}img{max-height:55vh}body{margin:12px}}
</style><h1>영수증 156장 검수 결과</h1><p id="summary"></p>
<p>156장 모두 AI가 원본 이미지와 대조했습니다. 사람 검증 완료를 뜻하지 않으며, 노란 행은 판단 보류 항목입니다. 추가 입력 없이 결과를 확인할 수 있습니다.</p>
<header><button id="prev">이전</button><span id="position"></span><button id="next">다음</button><select id="filter"><option value="all">전체</option><option value="grocery">마트/식료품</option><option value="convenience">편의점</option><option value="other">기타</option><option value="unresolved">보류 포함</option></select><select id="jump" aria-label="영수증 선택"></select><a href="assistant_annotations.json" download>전체 JSON</a> · <a href="assistant_unresolved.json" download>보류 목록</a></header>
<main><section><a id="original" target="_blank" rel="noopener"><img id="receipt" alt="원본 영수증"></a></section><section><h2 id="name"></h2><p id="note"></p><table><thead><tr><th>상품 원문</th><th>분류 제안</th><th>식품</th><th>구매 수량</th></tr></thead><tbody id="items"></tbody></table><details><summary>이 영수증 JSON</summary><pre id="json"></pre></details></section></main>
<script type="application/json" id="payload">__DATA__</script><script>
const p=JSON.parse(document.getElementById('payload').textContent),el=id=>document.getElementById(id);let rows=p.records,index=0;
el('summary').textContent=`원본 ${p.summary.images}장 / 상품 행 ${p.summary.product_rows}개 / 식품 ${p.summary.food_rows}개 / 비식품 ${p.summary.nonfood_rows}개 / 보류 ${p.summary.unresolved_rows}개`;
function show(){const r=rows[index];el('position').textContent=`${index+1} / ${rows.length}`;el('jump').value=String(index);el('name').textContent=r.filename;el('receipt').src=r.image_uri;el('original').href=r.image_uri;el('note').textContent=r.annotation_note||'AI 원본 대조 완료 · 구매 포장 수량 기준';el('items').replaceChildren();
for(const i of r.expected_items){const tr=document.createElement('tr');if(i.label_status==='unresolved')tr.className='pending';for(const value of [i.product_name,i.canonical_name??'미확정',i.is_food===null?'보류':i.is_food?'식품':'비식품',`${i.quantity??'?'} ${i.unit??''}${i.transaction_type==='return'?' (반품)':''}`]){const td=document.createElement('td');td.textContent=value;tr.append(td);}el('items').append(tr);if(i.note){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=4;td.className='note';td.textContent=i.note;tr.append(td);el('items').append(tr);}}
if(!r.expected_items.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=4;td.textContent='추출할 구매 상품 행 없음';tr.append(td);el('items').append(tr);}el('json').textContent=JSON.stringify(r,null,2);}
function refresh(){el('jump').replaceChildren();rows.forEach((r,n)=>{const o=document.createElement('option');o.value=n;o.textContent=r.filename;el('jump').append(o);});index=0;show();}
el('prev').onclick=()=>{index=Math.max(0,index-1);show();};el('next').onclick=()=>{index=Math.min(rows.length-1,index+1);show();};el('jump').onchange=()=>{index=Number(el('jump').value);show();};el('filter').onchange=()=>{const v=el('filter').value;rows=p.records.filter(r=>v==='all'||r.store_type===v||(v==='unresolved'&&r.expected_items.some(i=>i.label_status==='unresolved')));refresh();};refresh();
</script></html>'''

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('output/local_receipt_review'))
    print(json.dumps(assemble(parser.parse_args().directory), ensure_ascii=False, indent=2))
