"""Learn product-head evidence as features instead of forcing rule-based logits."""
from copy import deepcopy
import json
import hashlib
import torch
from app.ml.ocr_normalizer import CharacterClassifier, features
from app.ml.ocr_lexical_prior import lexical_prior
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_semantic_ocr import embeddings
from scripts.train_ocr_normalizer import target,metrics,threshold_for_validation


def run():
    torch.set_num_threads(2)
    base = ROOT/'output/ocr_normalizer_experiment_v7'
    examples = json.loads((base/'training_examples.json').read_text(encoding='utf-8'))
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    labels = sorted({r['label'] for r in examples})
    texts = [r['text'] for r in examples]+[clean(r['alignment']['ocr_text']) for r in rows]
    vectors,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    head = lexical_prior(texts,labels)
    core = torch.cat([features(texts),vectors*.5],1)
    y = torch.tensor([labels.index(r['label']) for r in examples])
    counts = torch.bincount(y,minlength=len(labels)).float()
    weights = counts.clamp_min(1).pow(-.5); weights/=weights.mean()
    truth = [target(r) for r in rows]
    best = None
    trials = []
    for head_weight in (.5,1.):
        x = torch.cat([core,head*head_weight],1)
        tx,ex = x[:len(examples)],x[len(examples):]
        torch.manual_seed(42)
        model = CharacterClassifier(len(labels),x.shape[1])
        optimizer = torch.optim.AdamW(model.parameters(),lr=.02,weight_decay=.01)
        for epoch in range(1,101):
            optimizer.zero_grad()
            loss = torch.nn.functional.cross_entropy(model(tx),y,weight=weights)
            loss.backward(); optimizer.step()
            if epoch%25==0:
                with torch.no_grad():
                    guess = [labels[i] for i in model(ex[split['validation']]).argmax(1).tolist()]
                measure = metrics([truth[i] for i in split['validation']],guess)
                trials.append({'head_weight':head_weight,'epoch':epoch,**measure})
                score = (measure['canonical_accuracy'],measure['food_recall'] or 0)
                if best is None or score>best['score']:
                    best = {'score':score,'state':deepcopy(model.state_dict()),'head_weight':head_weight,'epoch':epoch}
                print(f'head={head_weight} epoch={epoch} validation={measure["canonical_accuracy"]:.4f}',flush=True)
    ex = torch.cat([core[len(examples):],head[len(examples):]*best['head_weight']],1)
    model.load_state_dict(best['state'])
    with torch.no_grad():
        scores,indices = model(ex).softmax(1).max(1)
    predictions = [labels[i] for i in indices.tolist()]
    val = split['validation']
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i].item() for i in val])
    output = ROOT/'output/ocr_normalizer_experiment_v9'; output.mkdir(exist_ok=True)
    torch.save({'format':'receipt_semantic_classifier_v3','encoder':'intfloat/multilingual-e5-small',
        'encoder_revision':revision,'state_dict':best['state'],'labels':labels,'threshold':threshold,
        'semantic_weight':.5,'lexical_feature_weight':best['head_weight']},output/'model.pt')
    report = {'version':9,'algorithm':'learned character + frozen semantic + product-head features',
        'training_examples':len(examples),'classes':len(labels),'selected':{k:v for k,v in best.items() if k not in ('state','score')},
        'base_training_sha256':hashlib.sha256((base/'training_examples.json').read_bytes()).hexdigest(),
        'base_report':'../ocr_normalizer_experiment_v7/training_report.json',
        'validation_trials':trials,'evaluation':{name:metrics([truth[i] for i in ids],[predictions[i] for i in ids]) for name,ids in split.items()},
        'threshold':threshold,'production_deployed':False,'independent_test':False,
        'limitations':['Assistant labels; development holdout already inspected.',
            'Product-head features are engineered; their classifier coefficients are trained, not forced overrides.',
            'Training data and exclusion policy are inherited unchanged from v7.']}
    for name,payload in [('training_report.json',report),('heldout_predictions.json',[
        {'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'score':scores[i].item()} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=='__main__':
    run()
