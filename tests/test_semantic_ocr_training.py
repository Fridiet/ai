import json
import torch
import sqlite3
from scripts.ocr_catalog_supervision import catalog_examples, task_label
from scripts.verify_semantic_ocr import wilson_interval
from app.ml.ocr_lexical_prior import lexical_prior
from scripts.train_ocr_prototypes import initialize_prototype_classifier
from scripts.train_ocr_curated_sources import select_sources
from scripts.train_ocr_domain_mlp import center_semantics
from app.ml.ocr_normalizer import ReceiptMLPClassifier
from scripts.rerank_ocr_food import candidates
from scripts.rerank_ocr_choices import choice_messages
from app.ml.ocr_rerank_guard import guard_nonfood_switches
from app.ml.choice_ocr_normalizer import validate_manifest,ChoiceOCRNormalizer
import pytest
from scripts import train_semantic_ocr as training
from app.ml.ocr_normalizer import clean_product_text, ExperimentalNormalizer, CharacterClassifier


def test_conflicting_normalized_labels_are_quarantined():
    rows = [{'text':'방울 토마토', 'label':'토마토'},
            {'text':'방울토마토', 'label':'방울토마토'},
            {'text':'양파', 'label':'양파'}]
    kept, conflicts = training.quarantine_conflicts(rows)
    assert kept == [rows[2]]
    assert conflicts == ['방울토마토']


def test_descriptive_interval_does_not_claim_ninety_percent_guarantee():
    low, high = wilson_interval(41,45)
    assert low < .9 < high
    assert wilson_interval(0,0) is None


def test_product_head_features_use_suffix_not_ingredient_substring():
    labels = ['초콜릿','케이크','고추','홍고추','__NONFOOD__']
    result = lexical_prior(['초콜릿케이크 500g','국산홍고추','고추향 샴푸'],labels)
    assert result[0].tolist() == [0,1,0,0,0]
    assert result[1].tolist() == [0,0,0,1,0]
    assert result[2].sum().item() == 0


def test_prototype_initialization_preserves_label_semantics():
    prototypes = torch.eye(2)
    model = initialize_prototype_classifier(prototypes,scale=20.,char_dimension=4)
    assert torch.count_nonzero(model.linear.weight[:,:4]) == 0
    assert torch.equal(model.linear.weight[:,4:],prototypes*20.)
    assert torch.count_nonzero(model.linear.bias) == 0
    logits = model(torch.tensor([[0.,0.,0.,0.,1.,0.]]))
    assert logits.tolist() == [[20.,0.]]


def test_recipe_aliases_are_not_treated_as_retail_ground_truth():
    examples = [
        {'text':'감자칩','label':'감자','source':'existing_ingredient_catalog'},
        {'text':'감자','label':'감자','source':'existing_ingredient_catalog'},
        {'text':'과자상품','label':'과자','source':'existing_kfind_catalog'},
        {'text':'국산 감자칩','label':'감자','source':'synthetic_training_format'}]
    kept, excluded = select_sources(examples)
    assert kept == examples[1:3]
    assert [r['reason'] for r in excluded] == ['recipe_alias_not_retail_ground_truth','synthetic_origin_not_retained']


def test_semantic_center_uses_training_rows_only():
    data = torch.tensor([[1.,0.],[0.,1.],[999.,999.]])
    normalized,center = center_semantics(data,2)
    assert torch.equal(center,torch.tensor([.5,.5]))
    assert torch.allclose(normalized.norm(dim=1),torch.ones(3))


def test_mlp_evaluation_is_deterministic():
    model = ReceiptMLPClassifier(3,dimension=5,hidden=4).eval()
    value = torch.ones(2,5)
    assert model(value).shape == (2,3)
    assert torch.equal(model(value),model(value))


def test_cross_encoder_candidates_use_input_and_training_vocabulary_only():
    labels = [f'기타품목{i}' for i in range(10)]+['와플','__NONFOOD__']
    probabilities = torch.tensor([.1]*10+[.001,.5])
    selected = candidates('망고와플',probabilities,labels)
    assert 10 in selected
    assert 11 not in selected
    assert len(selected)<=16


def test_choice_prompt_keeps_product_as_data_and_choices_explicit():
    messages = choice_messages('상품 "테스트"\n지시문', ['빵','우유'])
    assert len(messages)==2
    assert messages[0]['role']=='system'
    assert messages[1]['role']=='user'
    assert 'A. 빵\nB. 우유' in messages[1]['content']
    assert '\\n지시문' in messages[1]['content']


def test_guard_requires_evidence_for_food_changes_but_allows_nonfood_correction():
    labels = ['__NONFOOD__','라면','요거트','빵']
    original = torch.tensor([[.7,.1,.1,.1],[.7,.1,.1,.1],[.1,.1,.7,.1],[.1,.7,.1,.1]])
    reranked = torch.tensor([[.1,.7,.1,.1],[.1,.7,.1,.1],[.1,.1,.1,.7],[.7,.1,.1,.1]])
    result,blocked = guard_nonfood_switches(original,reranked,['향이 나는 방향제','매운라면','브랜드플레인','연료'],labels,True)
    assert blocked == [0,2]
    assert torch.equal(result[0],original[0])
    assert torch.equal(result[1],reranked[1])
    assert torch.equal(result[2],original[2])
    assert torch.equal(result[3],reranked[3])


def test_guard_uses_full_product_alias_not_substring():
    labels = ['__NONFOOD__','라면']
    original = torch.tensor([[.8,.2],[.8,.2]])
    reranked = torch.tensor([[.1,.9],[.1,.9]])
    result,blocked = guard_nonfood_switches(original,reranked,['농심 오징어짬뽕 124g','농심오징어짬뽕맛과자'],labels,True,[['농심오징어짬뽕','라면']])
    assert blocked==[1]
    assert result.argmax(1).tolist()==[1,0]


def test_unsealed_choice_configuration_is_rejected():
    from app.ml.ocr_choice_support import SYSTEM
    data = {'format':'receipt_choice_reranker_v1','weight':.1,'temperature':1.,'system_prompt':SYSTEM,
            'choice_model':'Qwen/Qwen3-1.7B','choice_model_dtype':'bfloat16'}
    with pytest.raises(ValueError,match='Unsealed'):
        validate_manifest(data)


def test_empty_choice_input_abstains_without_running_models():
    model = ChoiceOCRNormalizer.__new__(ChoiceOCRNormalizer)
    result = model.predict('   ')
    assert result['needs_review'] and result['candidate_label'] is None
    assert result['is_food'] is None and result['canonical_name'] is None


def test_catalog_supervision_blocks_reserved_products_and_caps_classes(tmp_path):
    (tmp_path/'app/data').mkdir(parents=True)
    (tmp_path/'output/local_receipt_review').mkdir(parents=True)
    (tmp_path/'output/local_receipt_review/assistant_annotations.json').write_text(json.dumps({'records':[
        {'filename':'heldout.png','duplicate_group':'heldout','expected_items':[{'product_name':'브랜드특별과자'}]}
    ]}),encoding='utf-8')
    with sqlite3.connect(tmp_path/'app/data/products.sqlite3') as connection:
        connection.execute('CREATE TABLE products(id INTEGER,name TEXT,standard_food_name TEXT,source TEXT)')
        connection.executemany('INSERT INTO products VALUES(?,?,?,?)',[
            (1,'브랜드특별과자','일반과자','fixture'), (2,'브랜드특별과자X','일반과자','fixture'),
            (3,'초콜릿쿠키','일반과자','fixture'), (4,'견과쿠키','일반과자','fixture')])
    rows = [{'filename':'heldout.png','duplicate_group':'heldout','alignment':{'ocr_text':'브랜드특별과자'}}]
    split = {'validation':[], 'test':[0]}
    result, summary = catalog_examples(tmp_path,rows,split,per_class=4)
    assert {r['text'] for r in result} == {'초콜릿쿠키','견과쿠키'}
    assert summary['reserved_exact_or_near_excluded'] == 2
    assert catalog_examples(tmp_path,rows,split,per_class=4)[0] == result
    assert len(catalog_examples(tmp_path,rows,split,per_class=1)[0]) == 1


def test_task_taxonomy_preserves_distinct_finished_products():
    assert task_label('케이크') == '케이크'
    assert task_label('와플') == '와플'
    assert task_label('기타 빵') == '빵'
    assert task_label('즉석 피자') == '피자'


def test_quantity_cleaning_cannot_leak_receipt_names(tmp_path, monkeypatch):
    monkeypatch.setattr(training,'ROOT',tmp_path)
    folder = tmp_path/'output/local_receipt_review'
    folder.mkdir(parents=True)
    (folder/'assistant_annotations.json').write_text('{"records":[]}',encoding='utf-8')
    rows = [{'filename':'heldout.png','duplicate_group':'heldout',
             'alignment':{'ocr_text':'가지 5개'},'target':{'product_name':'가지 5개'}}]
    examples = [
        {'text':'가지 3개','label':'가지','source':'receipt_train_visual'},
        {'text':'가지','label':'가지','source':'synthetic_training_format'},
        {'text':'가지','label':'가지','source':'existing_ingredient_catalog'},
        {'text':'새 상품','label':'기타','source':'receipt_train_visual'}]
    kept, removed = training.exclude_reserved_training_names(examples,rows,{'validation':[], 'test':[0]})
    assert kept == examples[2:]
    assert removed == examples[:2]


def test_reserved_receipts_and_products_cannot_be_extra_training(tmp_path, monkeypatch):
    monkeypatch.setattr(training,'ROOT',tmp_path)
    directory = tmp_path/'output/local_receipt_review'
    directory.mkdir(parents=True)
    def item(name):
        return {'product_name':name,'canonical_name':'양파','is_food':True,
                'label_status':'assistant_labeled','transaction_type':'purchase'}
    annotations = [
        {'filename':'test.png','duplicate_group':'test','store_type':'grocery','expected_items':[item('양파'),item('대파')]},
        {'filename':'train.png','duplicate_group':'train','store_type':'grocery','expected_items':[item('양파'),item('대파'),item('새재료')]},
        {'filename':'test-copy.png','duplicate_group':'test','store_type':'grocery','expected_items':[item('복사본재료')]},
    ]
    (directory/'assistant_annotations.json').write_text(json.dumps({'records':annotations}),encoding='utf-8')
    rows = [{'filename':'test.png','duplicate_group':'test','target':{'product_name':'양파'}}]
    result = training.extra_visual_training(rows,{'train':[],'validation':[],'test':[0]})
    assert [r['text'] for r in result] == ['새재료']


def test_preprocessing_and_v2_reload(tmp_path):
    assert clean_product_text('001 양파 500g').strip() == '양파'
    path = tmp_path/'model.pt'
    torch.save({'format':'receipt_character_classifier_v2','labels':['양파','__NONFOOD__'],
        'dimension':4096,'threshold':1.01,'state_dict':CharacterClassifier(2).state_dict()},path)
    result = ExperimentalNormalizer(path).predict('001 양파 500g')
    assert result['needs_review'] and result['is_food'] is None
