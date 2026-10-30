"""Whole-product classification without recipe aliases or untraceable augmentations."""
from copy import deepcopy
import argparse
import json
import hashlib
import torch
from app.ml.ocr_normalizer import CharacterClassifier,features,normalize
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_semantic_ocr import embeddings,quarantine_conflicts
from scripts.ocr_catalog_supervision import catalog_examples
from scripts.train_ocr_normalizer import target,metrics,threshold_for_validation


def select_sources(examples):
    kept,excluded = [],[]
    for row in examples:
        source = row['source']
        if source.startswith('synthetic_'):
            excluded.append({**row,'reason':'synthetic_origin_not_retained'})
        elif source=='existing_ingredient_catalog' and normalize(row['text'])!=normalize(row['label']):
            excluded.append({**row,'reason':'recipe_alias_not_retail_ground_truth'})
        else:
            kept.append(row)
    return kept,excluded


def run(catalog_per_class=0,version=15):
    torch.set_num_threads(2)
    base = ROOT/'output/ocr_normalizer_experiment_v7'
    source = base/'training_examples.json'
    examples,excluded = select_sources(json.loads(source.read_text(encoding='utf-8')))
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    catalog_summary = None
    if catalog_per_class:
        if version==15:
            raise ValueError('Use a new version for expanded catalog experiments')
        examples = [r for r in examples if r['source']!='existing_kfind_catalog']
        extra,catalog_summary = catalog_examples(ROOT,rows,split,per_class=catalog_per_class)
        examples.extend({**r,'text':clean(r['text'])} for r in extra)
        examples,conflicts = quarantine_conflicts(examples)
        catalog_summary['quarantined_keys'] = conflicts
    labels = sorted({r['label'] for r in examples})
    texts = [r['text'] for r in examples]+[clean(r['alignment']['ocr_text']) for r in rows]
    vectors,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    char = features(texts)
    y = torch.tensor([labels.index(r['label']) for r in examples])
    counts = torch.bincount(y,minlength=len(labels)).float()
    truth = [target(r) for r in rows]
    best,trials = None,[]
    for semantic_weight in ((.5,) if catalog_per_class else (.5,1.)):
        x = torch.cat([char,vectors*semantic_weight],1)
        tx,ex = x[:len(examples)],x[len(examples):]
        for balance in ((.5,) if catalog_per_class else (0.,.5)):
            torch.manual_seed(42)
            model = CharacterClassifier(len(labels),x.shape[1])
            optimizer = torch.optim.AdamW(model.parameters(),lr=.02,weight_decay=.01)
            weights = counts.clamp_min(1).pow(-balance); weights/=weights.mean()
            for epoch in range(1,101):
                optimizer.zero_grad()
                loss = torch.nn.functional.cross_entropy(model(tx),y,weight=weights)
                loss.backward(); optimizer.step()
                if epoch%25==0:
                    with torch.no_grad():
                        guess = [labels[i] for i in model(ex[split['validation']]).argmax(1).tolist()]
                    measure = metrics([truth[i] for i in split['validation']],guess)
                    trials.append({'semantic_weight':semantic_weight,'balance':balance,'epoch':epoch,**measure})
                    score = (measure['canonical_accuracy'],measure['food_recall'] or 0)
                    if best is None or score>best['score']:
                        best = {'score':score,'state':deepcopy(model.state_dict()),'semantic_weight':semantic_weight,'balance':balance,'epoch':epoch}
                    print(f'curated semantic={semantic_weight} balance={balance} epoch={epoch} validation={measure["canonical_accuracy"]:.4f}',flush=True)
    ex = torch.cat([char[len(examples):],vectors[len(examples):]*best['semantic_weight']],1)
    model.load_state_dict(best['state'])
    with torch.no_grad():
        scores,indices = model(ex).softmax(1).max(1)
    predictions = [labels[i] for i in indices.tolist()]
    val = split['validation']
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i].item() for i in val])
    output = ROOT/f'output/ocr_normalizer_experiment_v{version}'; output.mkdir(exist_ok=True)
    torch.save({'format':'receipt_semantic_classifier_v3','encoder':'intfloat/multilingual-e5-small',
        'encoder_revision':revision,'semantic_weight':best['semantic_weight'],'state_dict':best['state'],'labels':labels,
        'threshold':threshold},output/'model.pt')
    report = {'version':version,'algorithm':'character + semantic classifier with source hygiene',
        'selected':{k:v for k,v in best.items() if k not in ('state','score')},
        'training_examples':len(examples),'excluded_examples':len(excluded),'classes':len(labels),
        'base_training_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'base_report':'../ocr_normalizer_experiment_v7/training_report.json','validation_trials':trials,
        'evaluation':{name:metrics([truth[i] for i in ids],[predictions[i] for i in ids]) for name,ids in split.items()},
        'threshold':threshold,'production_deployed':False,'independent_test':False,
        'catalog_expansion':catalog_summary,
        'limitations':['Assistant labels; previously inspected development holdout.',
            'Only training sources filtered; original validation and test rows/labels unchanged.',
            'K-FIND categories are weak supervision, not automatically verified truth.']}
    for name,payload in [('training_report.json',report),('training_examples.json',examples),('excluded_training.json',excluded),
        ('heldout_predictions.json',[{'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'score':scores[i].item()} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='validation_trials'},indent=2))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog-per-class',type=int,default=0)
    parser.add_argument('--version',type=int,default=15)
    args = parser.parse_args()
    run(args.catalog_per_class,args.version)
