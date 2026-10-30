import hashlib
import json
import pytest
from scripts.assemble_receipt_visual_review import assemble


def fixture(directory, items):
    image = directory / 'receipt.png'
    image.write_bytes(b'test')
    baseline = {'records': [{'filename': image.name, 'image_path': str(image),
        'image_sha256': hashlib.sha256(b'test').hexdigest(), 'source_split': 'test'}]}
    (directory / 'review.json').write_text(json.dumps(baseline), encoding='utf-8')
    (directory / 'visual_001.json').write_text(json.dumps({'receipts': {
        image.name: {'store_type': 'grocery', 'items': items}}}), encoding='utf-8')


def test_provenance_uncertainty_returns_and_safe_html(tmp_path):
    fixture(tmp_path, [['</script><script>alert(1)</script>', None, True, 1, '개', '불명'],
        ['반품', None, False, -1, '개', '교환 전표']])
    summary = assemble(tmp_path)
    assert summary['images'] == 1 and summary['unresolved_rows'] == 1
    data = json.loads((tmp_path / 'assistant_annotations.json').read_text(encoding='utf-8'))
    row = data['records'][0]
    assert row['assistant_review_complete'] and not row['reviewed'] and not row['human_reviewed']
    assert not row['training_eligible']
    assert row['expected_items'][1]['transaction_type'] == 'return'
    assert '</script><script>alert(1)</script>' not in (tmp_path / 'assistant_annotation.html').read_text(encoding='utf-8')


def test_missing_annotations_rejected(tmp_path):
    fixture(tmp_path, [])
    (tmp_path / 'visual_001.json').write_text('{"receipts": {}}', encoding='utf-8')
    with pytest.raises(ValueError, match='Coverage mismatch'):
        assemble(tmp_path)


def test_changed_image_rejected(tmp_path):
    fixture(tmp_path, [])
    (tmp_path / 'receipt.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='Image changed'):
        assemble(tmp_path)


def test_duplicate_annotation_rejected(tmp_path):
    fixture(tmp_path, [])
    (tmp_path / 'visual_002.json').write_bytes((tmp_path / 'visual_001.json').read_bytes())
    with pytest.raises(ValueError, match='Duplicate annotation'):
        assemble(tmp_path)
