"""Validation-selected ensemble of independently fitted dense and sparse classifiers."""
import json
import hashlib
import numpy as np
import torch
from scipy.sparse import csr_matrix,hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from app.ml.ocr_normalizer import CharacterClassifier,features,normalize
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_semantic_ocr import embeddings
from scripts.train_ocr_normalizer import target,metrics,threshold_for_validation


def run():
    torch.set_num_threads(2)
    dense_folder = ROOT/'output/ocr_normalizer_experiment_v7'
    sparse_folder = ROOT/'output/ocr_normalizer_experiment_v11'
    dense = torch.load(dense_folder/'model.pt',map_location='cpu',weights_only=True)
    metadata = json.loads((sparse_folder/'model.json').read_text(encoding='utf-8'))
    with np.load(sparse_folder/'model.npz',allow_pickle=False) as archive:
        coefficients,intercept,idf = archive['coef'],archive['intercept'],archive['idf']
    labels = dense['labels']
    if labels != metadata['labels']:
        raise ValueError('Different class ordering')
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    texts = [clean(r['alignment']['ocr_text']) for r in rows]
    vectors,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    if revision!=dense['encoder_revision'] or revision!=metadata['encoder_revision']:
        raise ValueError('Encoder mismatch')
    model = CharacterClassifier(len(labels),dense['state_dict']['linear.weight'].shape[1])
    model.load_state_dict(dense['state_dict'])
    with torch.no_grad():
        dense_prob = model(torch.cat([features(texts),vectors*dense['semantic_weight']],1)).softmax(1)
    vectorizer = TfidfVectorizer(analyzer='char',ngram_range=tuple(metadata['ngram_range']),
        sublinear_tf=metadata['sublinear_tf'],vocabulary=metadata['vocabulary'],dtype=np.float64)
    vectorizer.idf_ = idf
    sparse = vectorizer.transform([normalize(t) for t in texts])
    x = hstack([sparse,csr_matrix(vectors.numpy()*metadata['semantic_weight'])],format='csr')
    decisions = torch.tensor(x@coefficients.T+intercept,dtype=torch.float32)
    truth = [target(r) for r in rows]
    best,trials = None,[]
    val = split['validation']
    for temperature in (.05,.1,.2,.5,1.):
        sparse_prob = (decisions/temperature).softmax(1)
        for weight in (0.,.1,.25,.5,.75,1.):
            combined = dense_prob*(1-weight)+sparse_prob*weight
            predictions = [labels[i] for i in combined.argmax(1).tolist()]
            measure = metrics([truth[i] for i in val],[predictions[i] for i in val])
            trials.append({'temperature':temperature,'sparse_weight':weight,**measure})
            score = (measure['canonical_accuracy'],measure['food_recall'] or 0,-weight)
            if best is None or score>best['score']:
                best = {'score':score,'temperature':temperature,'weight':weight}
    combined = dense_prob*(1-best['weight'])+(decisions/best['temperature']).softmax(1)*best['weight']
    scores,indices = combined.max(1)
    predictions = [labels[i] for i in indices.tolist()]
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i].item() for i in val])
    output = ROOT/'output/ocr_normalizer_experiment_v13'; output.mkdir(exist_ok=True)
    manifest = {'format':'receipt_ensemble_v1','dense_checkpoint':'../ocr_normalizer_experiment_v7/model.pt',
        'sparse_folder':'../ocr_normalizer_experiment_v11','sparse_weight':best['weight'],
        'temperature':best['temperature'],'threshold':threshold,'hashes':{
            str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [dense_folder/'model.pt',sparse_folder/'model.npz',sparse_folder/'model.json']}}
    (output/'model.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    report = {'version':13,'algorithm':'dense and sparse learned classifier probability ensemble',
        'selected':{k:v for k,v in best.items() if k!='score'},'validation_trials':trials,
        'evaluation':{name:metrics([truth[i] for i in ids],[predictions[i] for i in ids]) for name,ids in split.items()},
        'threshold':threshold,'production_deployed':False,'independent_test':False,
        'limitations':['Assistant labels; previously inspected development holdout.',
            'Both models use v7 training-only examples; ensemble parameters selected on validation only.',
            'Mixture scores are not calibrated probabilities.']}
    for name,payload in [('training_report.json',report),('heldout_predictions.json',[
        {'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'score':scores[i].item()} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='validation_trials'},indent=2))


if __name__=='__main__':
    run()
