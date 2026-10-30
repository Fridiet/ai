"""Frozen multilingual E5 + trainable PyTorch classifier; fixed v1 development splits."""
from copy import deepcopy
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import torch
from app.ml.ocr_normalizer import CharacterClassifier, features, normalize
from scripts.improve_ocr_normalizer import ROOT, clean, load_split, build_training
from scripts.train_ocr_normalizer import target, metrics, threshold_for_validation
from scripts.prepare_receipt_pairs import key
from scripts.ocr_catalog_supervision import catalog_examples

MODEL_ID = 'intfloat/multilingual-e5-small'


def quarantine_conflicts(examples):
    """Do not ask the learner to assign different classes to the same input."""
    candidates = defaultdict(set)
    for row in examples:
        candidates[normalize(row['text'])].add(row['label'])
    conflicts = sorted(k for k, labels in candidates.items() if len(labels) > 1)
    banned = set(conflicts)
    return [r for r in examples if normalize(r['text']) not in banned], conflicts


def exclude_reserved_training_names(examples, rows, split):
    """Quantity removal must not reconnect otherwise disjoint receipt products."""
    reserved = [rows[i] for part in ('validation','test') for i in split[part]]
    groups = {r['duplicate_group'] for r in reserved}
    files = {r['filename'] for r in reserved}
    names = {normalize(clean(r['alignment']['ocr_text'])) for r in reserved}
    names.update(normalize(clean(r['target']['product_name'])) for r in reserved)
    annotations = json.loads((ROOT/'output/local_receipt_review/assistant_annotations.json').read_text(encoding='utf-8'))
    for receipt in annotations['records']:
        if receipt['filename'] in files or receipt['duplicate_group'] in groups:
            names.update(normalize(clean(r['product_name'])) for r in receipt['expected_items'])
    removed = []
    kept = []
    for row in examples:
        # Independently existing food vocabulary is allowed external knowledge, not a receipt example.
        external = row['source'].startswith('existing_') or row['source']=='train_class_name'
        if not external and normalize(clean(row['text'])) in names:
            removed.append(row)
        else:
            kept.append(row)
    return kept, removed


def extra_visual_training(rows, split):
    annotations = json.loads((ROOT/'output/local_receipt_review/assistant_annotations.json').read_text(encoding='utf-8'))['records']
    reserved = [rows[i] for part in ('validation','test') for i in split[part]]
    reserved_groups = {r['duplicate_group'] for r in reserved}
    reserved_files = {r['filename'] for r in reserved}
    reserved_names = {key(clean(r['target']['product_name'])) for r in reserved}
    # Include all annotated names on reserved receipts, even rows absent from the 298-pair set.
    for receipt in annotations:
        if receipt['filename'] in reserved_files or receipt['duplicate_group'] in reserved_groups:
            reserved_names.update(key(clean(i['product_name'])) for i in receipt['expected_items'])
    extra = []
    for receipt in annotations:
        if receipt['filename'] in reserved_files or receipt['duplicate_group'] in reserved_groups:
            continue
        for item in receipt['expected_items']:
            if item['label_status']=='unresolved' or item['transaction_type']=='return':
                continue
            if item['is_food'] and receipt['store_type']=='other':
                continue
            if key(clean(item['product_name'])) in reserved_names:
                continue
            label = item['canonical_name'] if item['is_food'] else '__NONFOOD__'
            label = {'생수':'물','캐슈너트':'캐슈넛','과일음료':'음료'}.get(label,label)
            extra.append({'text':item['product_name'],'label':label,'source':'extra_visual_train_only',
                          'filename':receipt['filename'],'duplicate_group':receipt['duplicate_group']})
    return extra


def embeddings(texts, cache):
    from huggingface_hub import model_info
    from transformers import AutoTokenizer, AutoModel
    cache.mkdir(exist_ok=True,parents=True)
    revision_file = cache/'revision.txt'
    if revision_file.exists():
        revision = revision_file.read_text().strip()
    else:
        revision = model_info(MODEL_ID).sha
        revision_file.write_text(revision)
    fingerprint = hashlib.sha256(json.dumps([revision,texts],ensure_ascii=False).encode()).hexdigest()
    tensor_file = cache/(fingerprint+'.pt')
    if tensor_file.exists():
        return torch.load(tensor_file,weights_only=True),revision
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID,revision=revision,cache_dir=cache/'hf',trust_remote_code=False)
    model = AutoModel.from_pretrained(MODEL_ID,revision=revision,cache_dir=cache/'hf',trust_remote_code=False,use_safetensors=True)
    model.eval()
    chunks = []
    for start in range(0,len(texts),32):
        batch = tokenizer(['query: '+t for t in texts[start:start+32]],padding=True,truncation=True,max_length=96,return_tensors='pt')
        with torch.no_grad():
            hidden = model(**batch).last_hidden_state
            mask = batch['attention_mask'].unsqueeze(-1)
            vector = (hidden*mask).sum(1)/mask.sum(1)
            chunks.append(torch.nn.functional.normalize(vector,dim=1))
        if start % 320 == 0:
            print(f'encoding {min(start+32,len(texts))}/{len(texts)}',flush=True)
    result = torch.cat(chunks)
    torch.save(result,tensor_file)
    return result,revision


def run():
    os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING','1')
    torch.set_num_threads(2)
    source = ROOT/'output/local_receipt_review/pairs/candidates.json'
    rows = json.loads(source.read_text(encoding='utf-8'))
    split = load_split(rows)
    examples,_ = build_training(rows,split)
    extra = extra_visual_training(rows,split)
    examples.extend(extra)
    kfind, kfind_summary = catalog_examples(ROOT,rows,split)
    examples.extend(kfind)
    examples, reserved_removed = exclude_reserved_training_names(examples,rows,split)
    # Catalog aliases sometimes represent subtypes (e.g. mushroom -> enoki), not synonyms.
    # Use only explicit equivalent names; never infer equality from arbitrary alias membership.
    train_labels = {target(rows[i]) for i in split['train']}
    mapping = {k:v for k,v in {'요구르트':'요거트','생수':'물','캐슈너트':'캐슈넛'}.items() if v in train_labels}
    unique = {}
    for row in examples:
        label = mapping.get(row['label'],row['label'])
        text = clean(row['text'])
        unique[(text,label)] = {'text':text,'label':label,'source':row['source']}
    examples = list(unique.values())
    examples, conflicts = quarantine_conflicts(examples)
    labels = sorted({r['label'] for r in examples})
    train_texts = [r['text'] for r in examples]
    eval_texts = [clean(r['alignment']['ocr_text']) for r in rows]
    all_texts = train_texts+eval_texts
    vectors,revision = embeddings(all_texts,ROOT/'output/semantic_ocr_cache')
    char = features(all_texts)
    y = torch.tensor([labels.index(r['label']) for r in examples])
    counts = torch.bincount(y,minlength=len(labels)).float()
    truth = [target(r) for r in rows]
    best, trials = None,[]
    for semantic_weight in (.5,1.,2.):
        x = torch.cat([char,vectors*semantic_weight],1)
        tx, ex = x[:len(examples)],x[len(examples):]
        for balance in (0.,.5):
            torch.manual_seed(42)
            model = CharacterClassifier(len(labels),x.shape[1])
            optimizer = torch.optim.AdamW(model.parameters(),lr=.02,weight_decay=.01)
            w = counts.clamp_min(1).pow(-balance); w/=w.mean()
            for epoch in range(1,151):
                optimizer.zero_grad()
                loss = torch.nn.functional.cross_entropy(model(tx),y,weight=w)
                loss.backward(); optimizer.step()
                if epoch%25==0:
                    with torch.no_grad():
                        guesses = [labels[i] for i in model(ex[split['validation']]).argmax(1).tolist()]
                    m = metrics([truth[i] for i in split['validation']],guesses)
                    trials.append({'semantic_weight':semantic_weight,'balance':balance,'epoch':epoch,**m})
                    score = (m['canonical_accuracy'],m['food_recall'] or 0)
                    if best is None or score>best['score']:
                        best = {'score':score,'state':deepcopy(model.state_dict()),'weight':semantic_weight,'balance':balance,'epoch':epoch}
            print(f'trial complete semantic={semantic_weight} balance={balance}',flush=True)
    ex = torch.cat([char[len(examples):],vectors[len(examples):]*best['weight']],1)
    model.load_state_dict(best['state'])
    with torch.no_grad():
        scores,indices = model(ex).softmax(1).max(1)
    predictions = [labels[i] for i in indices.tolist()]
    val = split['validation']
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i].item() for i in val])
    evaluation = {name:{**metrics([truth[i] for i in ids],[predictions[i] for i in ids]),
        'unknown_class_rows':sum(truth[i] not in labels for i in ids)} for name,ids in split.items()}
    folder = ROOT/'output/ocr_normalizer_experiment_v7'; folder.mkdir(exist_ok=True)
    report = {'version':7,'encoder':MODEL_ID,'encoder_revision':revision,'encoder_frozen':True,
        'trainable':'character + semantic linear classifier','rows':len(rows),'training_examples':len(examples),
        'selected':{k:v for k,v in best.items() if k not in ('state','score')},'catalog_synonym_mapping':mapping,
        'extra_visual_rows':len(extra),'extra_visual_sources':extra,
        'quarantined_conflict_keys':conflicts,
        'kfind_supervision':kfind_summary,
        'reserved_receipt_examples_removed':len(reserved_removed),
        'hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [source,ROOT/'output/local_receipt_review/assistant_annotations.json',
             ROOT/'app/data/ingredients_ko.json',ROOT/'app/data/processed_products_ko.json',
             ROOT/'app/data/products.sqlite3',Path(__file__),ROOT/'scripts/ocr_catalog_supervision.py']},
        'evaluation':evaluation,'threshold':threshold,'validation_trials':trials,'independent_test':False,
        'production_deployed':False,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'limitations':['Assistant annotations; automatically aligned pairs.', 'Previously inspected development holdout, not independent test.',
            'Pretrained encoder and existing catalog add external knowledge. No holdout receipt example is used in gradient training.']}
    torch.save({'format':'receipt_semantic_classifier_v3','encoder':MODEL_ID,'encoder_revision':revision,
        'state_dict':best['state'],'labels':labels,'threshold':threshold,'semantic_weight':best['weight']},folder/'model.pt')
    (folder/'kfind_training_sources.json').write_text(json.dumps(kfind,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'excluded_reserved_training.json').write_text(json.dumps(reserved_removed,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,data in [('training_report.json',report),('training_examples.json',examples),
        ('heldout_predictions.json',[{'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
            'truth':truth[i],'prediction':predictions[i],'score':scores[i].item()} for i in split['test']])]:
        (folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('validation_trials','extra_visual_sources')},ensure_ascii=True,indent=2))


if __name__ == '__main__':
    run()
