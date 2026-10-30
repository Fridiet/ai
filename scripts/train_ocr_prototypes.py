"""Frozen semantic label prototypes plus learned character weights and class biases."""
from copy import deepcopy
import hashlib
import json
import torch
from app.ml.ocr_normalizer import CharacterClassifier,features,NONFOOD
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_semantic_ocr import embeddings
from scripts.train_ocr_normalizer import target,metrics,threshold_for_validation


def initialize_prototype_classifier(prototypes, scale, char_dimension=4096):
    model = CharacterClassifier(len(prototypes),char_dimension+prototypes.shape[1])
    with torch.no_grad():
        model.linear.weight.zero_()
        model.linear.bias.zero_()
        model.linear.weight[:,char_dimension:].copy_(prototypes*scale)
    return model


def run():
    torch.set_num_threads(2)
    base = ROOT/'output/ocr_normalizer_experiment_v7'
    source = base/'training_examples.json'
    examples = json.loads(source.read_text(encoding='utf-8'))
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    labels = sorted({r['label'] for r in examples})
    texts = [r['text'] for r in examples]+[clean(r['alignment']['ocr_text']) for r in rows]
    vectors,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    descriptions = [label if label!=NONFOOD else '먹는 식품이 아닌 생활용품, 문구, 청소용품' for label in labels]
    prototypes,prototype_revision = embeddings(descriptions,ROOT/'output/semantic_ocr_cache')
    if revision!=prototype_revision:
        raise ValueError('Encoder revisions differ')
    x = torch.cat([features(texts),vectors],1)
    tx,ex = x[:len(examples)],x[len(examples):]
    y = torch.tensor([labels.index(r['label']) for r in examples])
    counts = torch.bincount(y,minlength=len(labels)).float()
    weights = counts.clamp_min(1).pow(-.5); weights/=weights.mean()
    truth = [target(r) for r in rows]
    best,trials = None,[]
    for scale in (20.,50.):
        model = initialize_prototype_classifier(prototypes,scale)
        optimizer = torch.optim.AdamW(model.parameters(),lr=.02,weight_decay=.01)
        frozen = prototypes*scale
        for epoch in range(1,101):
            optimizer.zero_grad()
            loss = torch.nn.functional.cross_entropy(model(tx),y,weight=weights)
            loss.backward()
            model.linear.weight.grad[:,4096:].zero_()
            optimizer.step()
            with torch.no_grad():
                model.linear.weight[:,4096:].copy_(frozen)
            if epoch%25==0:
                with torch.no_grad():
                    guess = [labels[i] for i in model(ex[split['validation']]).argmax(1).tolist()]
                measure = metrics([truth[i] for i in split['validation']],guess)
                trials.append({'prototype_scale':scale,'epoch':epoch,**measure})
                score = (measure['canonical_accuracy'],measure['food_recall'] or 0)
                if best is None or score>best['score']:
                    best = {'score':score,'state':deepcopy(model.state_dict()),'scale':scale,'epoch':epoch}
                print(f'prototype={scale} epoch={epoch} validation={measure["canonical_accuracy"]:.4f}',flush=True)
    model.load_state_dict(best['state'])
    with torch.no_grad():
        scores,indices = model(ex).softmax(1).max(1)
    predictions = [labels[i] for i in indices.tolist()]
    val = split['validation']
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i].item() for i in val])
    output = ROOT/'output/ocr_normalizer_experiment_v14'; output.mkdir(exist_ok=True)
    torch.save({'format':'receipt_semantic_classifier_v3','encoder':'intfloat/multilingual-e5-small',
        'encoder_revision':revision,'semantic_weight':1.,'state_dict':best['state'],'labels':labels,
        'threshold':threshold},output/'model.pt')
    report = {'version':14,'algorithm':'fixed E5 label prototypes + learned character weights and bias',
        'selected':{k:v for k,v in best.items() if k not in ('state','score')},
        'base_training_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'base_report':'../ocr_normalizer_experiment_v7/training_report.json',
        'label_descriptions':descriptions,'validation_trials':trials,
        'evaluation':{name:metrics([truth[i] for i in ids],[predictions[i] for i in ids]) for name,ids in split.items()},
        'threshold':threshold,'production_deployed':False,'independent_test':False,
        'limitations':['Assistant labels; previously inspected development holdout.',
            'All examples and exclusions identical to v7; labels come only from training sources.',
            'Label prototypes use existing pretrained language knowledge, not heldout annotations.']}
    for name,payload in [('training_report.json',report),('heldout_predictions.json',[
        {'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'score':scores[i].item()} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('label_descriptions','validation_trials')},indent=2))


if __name__=='__main__':
    run()
