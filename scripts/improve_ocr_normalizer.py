"""v2 experiment: frozen partitions, training-only augmentation, existing catalog supervision."""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import re
import torch
from app.ml.ocr_normalizer import CharacterClassifier, NONFOOD, features, normalize, clean_product_text
from scripts.train_ocr_normalizer import metrics, target, threshold_for_validation

ROOT = Path(__file__).resolve().parents[1]


def clean(text):
    return clean_product_text(text)


def load_split(rows):
    manifest = json.loads((ROOT/'output/ocr_normalizer_experiment_v1/split_manifest.json').read_text(encoding='utf-8'))
    lookup = {(r['filename'], r['annotation_row']): i for i,r in enumerate(rows)}
    return {name:[lookup[(r['filename'], r['annotation_row'])] for r in records] for name,records in manifest.items()}


def build_training(rows, split):
    rng = random.Random(42)
    examples = []
    def add(text, label, source):
        if text.strip():
            examples.append({'text':text, 'label':label, 'source':source})
    for i in split['train']:
        r = rows[i]
        label = target(r)
        text = r['alignment']['ocr_text']
        add(text,label,'receipt_train')
        add(clean(text),label,'receipt_train_cleaned')
        add(r['target']['product_name'],label,'receipt_train_visual')
        if label != NONFOOD:
            add(label,label,'train_class_name')
    catalog = json.loads((ROOT/'app/data/ingredients_ko.json').read_text(encoding='utf-8'))
    processed = json.loads((ROOT/'app/data/processed_products_ko.json').read_text(encoding='utf-8'))
    for item in catalog:
        for text in [item['name'], *item['aliases']]:
            add(text, item['name'], 'existing_ingredient_catalog')
    for item in processed:
        for text in [item['name'], *item['aliases']]:
            add(text,item['standard_food_name'],'existing_processed_catalog')
    # Conflicting source labels are quarantined, not silently overwritten.
    mapping = defaultdict(set)
    for row in examples:
        mapping[normalize(row['text'])].add(row['label'])
    conflicts = {k for k,v in mapping.items() if len(v)>1}
    examples = [r for r in examples if normalize(r['text']) not in conflicts]
    originals = list(examples)
    for row in originals:
        text = row['text']
        if row['label'] != NONFOOD:
            add('국산 '+text+' 500g',row['label'],'synthetic_training_format')
            if len(normalize(text)) >= 5:
                at = rng.randrange(len(text))
                add(text[:at]+text[at+1:],row['label'],'synthetic_training_deletion')
    return examples, sorted(conflicts)


def run():
    torch.set_num_threads(2)
    torch.manual_seed(42)
    source = ROOT/'output/local_receipt_review/pairs/candidates.json'
    rows = json.loads(source.read_text(encoding='utf-8'))
    split = load_split(rows)
    examples, conflicts = build_training(rows,split)
    labels = sorted({r['label'] for r in examples})
    train_x = features([clean(r['text']) for r in examples])
    train_y = torch.tensor([labels.index(r['label']) for r in examples])
    eval_x = features([clean(r['alignment']['ocr_text']) for r in rows])
    truth = [target(r) for r in rows]
    val_ids = split['validation']
    counts = torch.bincount(train_y,minlength=len(labels)).float()
    runs = []
    best = None
    for balance in (0.0, .5, 1.0):
        torch.manual_seed(42)
        model = CharacterClassifier(len(labels))
        optimizer = torch.optim.AdamW(model.parameters(),lr=.03,weight_decay=.01)
        weights = counts.clamp_min(1).pow(-balance)
        weights /= weights.mean()
        for epoch in range(1,201):
            optimizer.zero_grad()
            loss = torch.nn.functional.cross_entropy(model(train_x),train_y,weight=weights)
            loss.backward()
            optimizer.step()
            if epoch % 25 == 0:
                with torch.no_grad():
                    pred = model(eval_x[val_ids]).argmax(1).tolist()
                measure = metrics([truth[i] for i in val_ids],[labels[j] for j in pred])
                score = (measure['canonical_accuracy'], measure['food_recall'] or 0)
                runs.append({'balance':balance,'epoch':epoch,**measure})
                if best is None or score > best['score']:
                    best = {'score':score,'state':deepcopy(model.state_dict()),'balance':balance,'epoch':epoch}
        print(f'completed class balance={balance}',flush=True)
    model.load_state_dict(best['state'])
    with torch.no_grad():
        probabilities = model(eval_x).softmax(1)
        scores, indices = probabilities.max(1)
    predicted = [labels[i] for i in indices.tolist()]
    threshold = threshold_for_validation([truth[i] for i in val_ids],[predicted[i] for i in val_ids],[scores[i].item() for i in val_ids])
    evaluation = {}
    for name, ids in split.items():
        accepted = [i for i in ids if scores[i]>=threshold]
        evaluation[name] = {**metrics([truth[i] for i in ids],[predicted[i] for i in ids]),
            'unknown_class_rows':sum(truth[i] not in labels for i in ids),
            'accepted_rows':len(accepted),'coverage':len(accepted)/len(ids),
            'accepted_accuracy':sum(truth[i]==predicted[i] for i in accepted)/len(accepted) if accepted else None}
    folder = ROOT/'output/ocr_normalizer_experiment_v2'
    folder.mkdir(exist_ok=True)
    torch.save({'format':'receipt_character_classifier_v2','state_dict':best['state'],'labels':labels,
                'dimension':4096,'threshold':threshold}, folder/'model.pt')
    catalog_keys = {normalize(r['text']) for r in examples if r['source'].startswith('existing_')}
    report = {'version':2,'algorithm':'catalog-supervised character classifier',
        'training_rows':len(examples),'classes':len(labels),'selected_balance':best['balance'],'selected_epoch':best['epoch'],
        'threshold':threshold,'evaluation':evaluation,'validation_trials':runs,
        'conflicting_source_keys':conflicts, 'sources':dict(Counter(r['source'] for r in examples)),
        'external_catalog_exact_overlap':{k:sum(normalize(rows[i]['target']['product_name']) in catalog_keys for i in ids) for k,ids in split.items()},
        'hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [source,ROOT/'app/data/ingredients_ko.json',ROOT/'app/data/processed_products_ko.json']},
        'production_deployed':False,'independent_test':False,
        'limitations':['v1 partitions unchanged; only validation selected hyperparameters.',
            'Existing catalog is additional weak supervision and may contain wrong ingredient aliases.',
            'v1 development errors informed design. This is not a fresh independent test.',
            'No heldout receipt rows or labels were added as training examples.']}
    for name,data in [('training_report.json',report),('training_examples.json',examples),
        ('heldout_predictions.json',[{'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
            'truth':truth[i],'prediction':predicted[i],'score':scores[i].item()} for i in split['test']])]:
        (folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('validation_trials','conflicting_source_keys','hashes')},ensure_ascii=True,indent=2))


if __name__ == '__main__':
    run()
