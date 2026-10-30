"""Centered semantic features and source-weighted nonlinear receipt classification."""
from copy import deepcopy
import hashlib
import json
import torch
from app.ml.ocr_normalizer import ReceiptMLPClassifier,features
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_semantic_ocr import embeddings
from scripts.train_ocr_normalizer import target,metrics,threshold_for_validation


def center_semantics(vectors, train_count):
    center = vectors[:train_count].mean(0)
    return torch.nn.functional.normalize(vectors-center,dim=1),center


def run():
    torch.set_num_threads(2)
    base = ROOT/'output/ocr_normalizer_experiment_v15'
    source = base/'training_examples.json'
    examples = json.loads(source.read_text(encoding='utf-8'))
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    labels = sorted({r['label'] for r in examples})
    texts = [r['text'] for r in examples]+[clean(r['alignment']['ocr_text']) for r in rows]
    vectors,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    vectors,center = center_semantics(vectors,len(examples))
    x = torch.cat([features(texts),vectors],1)
    tx,ex = x[:len(examples)],x[len(examples):]
    y = torch.tensor([labels.index(r['label']) for r in examples])
    counts = torch.bincount(y,minlength=len(labels)).float()
    weights = counts.clamp_min(1).pow(-.5); weights/=weights.mean()
    receipt = torch.tensor([r['source'].startswith(('receipt_','extra_visual_')) for r in examples])
    truth = [target(r) for r in rows]
    best,trials = None,[]
    for boost in (1.,4.):
        torch.manual_seed(42)
        model = ReceiptMLPClassifier(len(labels),x.shape[1])
        optimizer = torch.optim.AdamW(model.parameters(),lr=.003,weight_decay=.01)
        sample_weights = torch.where(receipt,boost,1.)
        for epoch in range(1,151):
            model.train()
            optimizer.zero_grad()
            loss = torch.nn.functional.cross_entropy(model(tx),y,weight=weights,reduction='none')
            loss = (loss*sample_weights).sum()/sample_weights.sum()
            loss.backward(); optimizer.step()
            if epoch%25==0:
                model.eval()
                with torch.no_grad():
                    guess = [labels[i] for i in model(ex[split['validation']]).argmax(1).tolist()]
                measure = metrics([truth[i] for i in split['validation']],guess)
                trials.append({'receipt_boost':boost,'epoch':epoch,**measure})
                score = (measure['canonical_accuracy'],measure['food_recall'] or 0)
                if best is None or score>best['score']:
                    best = {'score':score,'state':deepcopy(model.state_dict()),'receipt_boost':boost,'epoch':epoch}
                print(f'mlp boost={boost} epoch={epoch} validation={measure["canonical_accuracy"]:.4f}',flush=True)
    model.load_state_dict(best['state']); model.eval()
    with torch.no_grad():
        scores,indices = model(ex).softmax(1).max(1)
    predictions = [labels[i] for i in indices.tolist()]
    val = split['validation']
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i].item() for i in val])
    output = ROOT/'output/ocr_normalizer_experiment_v17'; output.mkdir(exist_ok=True)
    torch.save({'format':'receipt_semantic_classifier_mlp_v1','encoder':'intfloat/multilingual-e5-small',
        'encoder_revision':revision,'semantic_weight':1.,'semantic_center':center,'state_dict':best['state'],
        'dimension':x.shape[1],'hidden':128,'labels':labels,'threshold':threshold},output/'model.pt')
    report = {'version':17,'algorithm':'centered E5 + character features / source-weighted MLP',
        'selected':{k:v for k,v in best.items() if k not in ('state','score')},
        'training_examples':len(examples),'receipt_examples':receipt.sum().item(),'classes':len(labels),
        'base_training_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'base_report':'../ocr_normalizer_experiment_v15/training_report.json','validation_trials':trials,
        'evaluation':{name:metrics([truth[i] for i in ids],[predictions[i] for i in ids]) for name,ids in split.items()},
        'threshold':threshold,'production_deployed':False,'independent_test':False,
        'limitations':['Assistant labels; previously inspected development holdout.',
            'Semantic center fitted on training examples only; no validation/test statistics.',
            'Receipt source weight and checkpoint selected on validation only.']}
    for name,payload in [('training_report.json',report),('heldout_predictions.json',[
        {'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'score':scores[i].item()} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='validation_trials'},indent=2))


if __name__=='__main__':
    run()
