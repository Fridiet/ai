"""Choose explicit lexical-prior strength on validation, never on test accuracy."""
from pathlib import Path
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
    folder = ROOT/'output/ocr_normalizer_experiment_v7'
    model_path = folder/'model.pt'
    data = torch.load(model_path,map_location='cpu',weights_only=True)
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    texts = [clean(r['alignment']['ocr_text']) for r in rows]
    vectors,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    if revision!=data['encoder_revision']:
        raise ValueError('Encoder revision changed')
    labels = data['labels']
    x = torch.cat([features(texts),vectors*data['semantic_weight']],1)
    model = CharacterClassifier(len(labels),x.shape[1])
    model.load_state_dict(data['state_dict'])
    with torch.no_grad():
        logits = model(x)
    prior = lexical_prior(texts,labels)
    truth = [target(r) for r in rows]
    trials = []
    best = None
    for strength in (0.,.5,1.,2.,3.,4.,6.):
        predictions = [labels[i] for i in (logits+prior*strength).argmax(1).tolist()]
        val = split['validation']
        measure = metrics([truth[i] for i in val],[predictions[i] for i in val])
        trials.append({'strength':strength,**measure})
        score = (measure['canonical_accuracy'],measure['food_recall'] or 0,-strength)
        if best is None or score>best[0]:
            best = score,strength
    strength = best[1]
    probabilities = (logits+prior*strength).softmax(1)
    scores,indices = probabilities.max(1)
    predictions = [labels[i] for i in indices.tolist()]
    val = split['validation']
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i].item() for i in val])
    evaluation = {name:metrics([truth[i] for i in ids],[predictions[i] for i in ids]) for name,ids in split.items()}
    output = ROOT/'output/ocr_normalizer_experiment_v8'
    output.mkdir(exist_ok=True)
    data.update(lexical_prior_strength=strength,threshold=threshold)
    torch.save(data,output/'model.pt')
    report = {'version':8,'algorithm':'v7 learned classifier + explicit longest product-head suffix evidence',
        'pure_ml':False,'production_deployed':False,'independent_test':False,
        'base_checkpoint_sha256':hashlib.sha256(model_path.read_bytes()).hexdigest(),
        'base_report':'../ocr_normalizer_experiment_v7/training_report.json',
        'selected_strength':strength,'threshold':threshold,'validation_trials':trials,'evaluation':evaluation,
        'limitations':['Development holdout already inspected; not independent testing.',
            'Assistant labels and weak catalog supervision; not human-verified truth.',
            'Suffix evidence is a transparent rule prior, not learned intelligence.']}
    for name,payload in [('training_report.json',report),('heldout_predictions.json',[
        {'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'score':scores[i].item()} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=='__main__':
    run()
