import torch
import pytest
from app.ml.ocr_normalizer import CharacterClassifier, ExperimentalNormalizer, features, normalize
from scripts.train_ocr_normalizer import split_groups, threshold_for_validation, train


def test_stable_features():
    assert normalize('001 양파') == '양파'
    assert torch.equal(features(['001 양파']), features(['양파']))
    assert torch.isfinite(features(['', '청양고추'])).all()


def test_checkpoint_roundtrip_and_abstention(tmp_path):
    model = CharacterClassifier(2)
    path = tmp_path / 'model.pt'
    torch.save({'format':'receipt_character_classifier_v1', 'state_dict':model.state_dict(),
        'labels':['__NONFOOD__','양파'], 'dimension':4096, 'threshold':1.01}, path)
    loaded = ExperimentalNormalizer(path)
    result = loaded.predict('양파')
    assert result['needs_review'] and result['canonical_name'] is None and result['is_food'] is None


def test_grouping_blocks_transitive_receipt_product_leaks():
    rows = [{'duplicate_group':str(i), 'target':{'product_name':f'상품{i}'},
             'alignment':{'ocr_text':f'상품{i}'}} for i in range(20)]
    rows[1]['duplicate_group'] = '0'
    rows[2]['target']['product_name'] = '상품1'
    parts, _ = split_groups(rows)
    membership = {i:k for k,ids in parts.items() for i in ids}
    assert membership[0] == membership[1] == membership[2]
    assert len(membership) == len(rows)
    for a in parts:
        for b in parts:
            if a != b:
                assert not {rows[i]['duplicate_group'] for i in parts[a]} & {rows[i]['duplicate_group'] for i in parts[b]}
                assert not {normalize(rows[i]['target']['product_name']) for i in parts[a]} & {normalize(rows[i]['target']['product_name']) for i in parts[b]}


def test_threshold_without_evidence_abstains():
    assert threshold_for_validation(['a']*5, ['b']*5, [.99]*5) > 1


def test_assistant_labels_require_explicit_opt_in(tmp_path):
    with pytest.raises(ValueError, match='opt-in'):
        train(tmp_path/'missing.json', tmp_path, False)
