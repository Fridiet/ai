import pytest
from scripts.evaluate_receipt_llm import validate_manifest


def fixture():
    return {'receipts': [{'split': 'dev' if i < 10 else 'eval', 'image_sha256': str(i),
        'duplicate_group': str(i), 'human_verified': True, 'verified_by': 'test-reviewer',
        'analysis': {'products': [{'row_index': 0}]},
        'expected_rows': [{'row_id': 0, 'canonical_name': '양파', 'verified': True}]} for i in range(30)]}


def test_requires_real_verified_ground_truth():
    manifest = fixture()
    validate_manifest(manifest)
    manifest['receipts'][0]['human_verified'] = False
    with pytest.raises(ValueError, match='Human-verified'):
        validate_manifest(manifest)


def test_duplicates_unverified_rows_and_unknown_identity_block():
    for mutation in ('duplicate', 'unverified', 'unknown'):
        manifest = fixture()
        r = manifest['receipts'][1]
        if mutation == 'duplicate':
            r['duplicate_group'] = '0'
        elif mutation == 'unverified':
            r['expected_rows'][0]['verified'] = False
        else:
            r['expected_rows'][0]['canonical_name'] = '없는재료명123'
        with pytest.raises(ValueError):
            validate_manifest(manifest)
