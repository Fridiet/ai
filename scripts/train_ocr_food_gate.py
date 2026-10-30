"""Separate binary food detection from canonical food-category prediction."""
from copy import deepcopy
import json
import hashlib
import torch
from app.ml.ocr_normalizer import CharacterClassifier,features,NONFOOD
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_semantic_ocr import embeddings
from scripts.train_ocr_normalizer import target,metrics,threshold_for_validation


def run():
    torch.set_num_threads(2)
    base = ROOT/'output/ocr_normalizer_experiment_v7'
    examples = json.loads((base/'training_examples.json').read_text(encoding='utf-8'))
    checkpoint = torch.load(base/'model.pt',weights_only=True,map_location='cpu')
    labels = checkpoint['labels']
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    texts = [r['text'] for r in examples]+[clean(r['alignment']['ocr_text']) for r in rows]
    vectors,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    if revision != checkpoint['encoder_revision']:
        raise ValueError('Encoder mismatch')
    x = torch.cat([features(texts),vectors*checkpoint['semantic_weight']],1)
    tx,ex = x[:len(examples)],x[len(examples):]
    classifier = CharacterClassifier(len(labels),x.shape[1])
    classifier.load_state_dict(checkpoint['state_dict'])
    with torch.no_grad():
        probabilities = classifier(ex).softmax(1)
        food_logits = classifier(ex)
        nonfood = labels.index(NONFOOD)
        food_logits[:,nonfood] = -torch.inf
        food_indices = food_logits.argmax(1).tolist()
    y = torch.tensor([float(r['label']!=NONFOOD) for r in examples])
    counts = torch.bincount(y.long()).float()
    weights = len(examples)/(2*counts[y.long()])
    truth = [target(r) for r in rows]
    torch.manual_seed(42)
    gate = torch.nn.Linear(x.shape[1],1)
    optimizer = torch.optim.AdamW(gate.parameters(),lr=.025,weight_decay=.01)
    best,trials = None,[]
    for epoch in range(1,251):
        optimizer.zero_grad()
        loss = torch.nn.functional.binary_cross_entropy_with_logits(gate(tx).squeeze(1),y,weight=weights)
        loss.backward(); optimizer.step()
        if epoch%25==0:
            with torch.no_grad():
                p = gate(ex).sigmoid().squeeze(1).tolist()
            for threshold in (.2,.3,.4,.5,.6,.7,.8,.9):
                guess = [labels[food_indices[i]] if p[i]>=threshold else NONFOOD for i in split['validation']]
                measure = metrics([truth[i] for i in split['validation']],guess)
                trials.append({'epoch':epoch,'food_threshold':threshold,**measure})
                score = (measure['canonical_accuracy'],measure['food_recall'] or 0)
                if best is None or score>best['score']:
                    best = {'score':score,'state':deepcopy(gate.state_dict()),'epoch':epoch,'threshold':threshold}
            print(f'food gate epoch={epoch} best_validation={best["score"][0]:.4f}',flush=True)
    gate.load_state_dict(best['state'])
    with torch.no_grad():
        p = gate(ex).sigmoid().squeeze(1).tolist()
    indices = [food_indices[i] if p[i]>=best['threshold'] else nonfood for i in range(len(rows))]
    predictions = [labels[i] for i in indices]
    scores = [probabilities[i,j].item() for i,j in enumerate(indices)]
    val = split['validation']
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i] for i in val])
    output = ROOT/'output/ocr_normalizer_experiment_v12'; output.mkdir(exist_ok=True)
    checkpoint.update(food_gate_state=best['state'],food_gate_threshold=best['threshold'],threshold=threshold)
    torch.save(checkpoint,output/'model.pt')
    report = {'version':12,'algorithm':'v7 canonical classifier + separately trained binary food gate',
        'selected':{k:v for k,v in best.items() if k not in ('state','score')},'validation_trials':trials,
        'base_checkpoint_sha256':hashlib.sha256((base/'model.pt').read_bytes()).hexdigest(),
        'base_report':'../ocr_normalizer_experiment_v7/training_report.json',
        'evaluation':{name:metrics([truth[i] for i in ids],[predictions[i] for i in ids]) for name,ids in split.items()},
        'threshold':threshold,'production_deployed':False,'independent_test':False,
        'limitations':['Assistant labels; previously inspected development holdout.',
            'Binary detector uses same training-only v7 sources; thresholds selected on validation only.',
            'Food gate sigmoid and canonical softmax are not calibrated probabilities.']}
    for name,payload in [('training_report.json',report),('heldout_predictions.json',[
        {'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'score':scores[i]} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='validation_trials'},indent=2))


if __name__=='__main__':
    run()
