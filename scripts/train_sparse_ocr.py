"""Sparse character TF-IDF/linear SVM comparator, with frozen development splits."""
import json
import hashlib
from copy import deepcopy
import numpy as np
import torch
from scipy.sparse import csr_matrix,hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC
from app.ml.ocr_normalizer import normalize
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_semantic_ocr import embeddings
from scripts.train_ocr_normalizer import target,metrics


def run():
    torch.set_num_threads(2)
    base = ROOT/'output/ocr_normalizer_experiment_v7'
    examples = json.loads((base/'training_examples.json').read_text(encoding='utf-8'))
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    texts = [r['text'] for r in examples]+[clean(r['alignment']['ocr_text']) for r in rows]
    vectorizer = TfidfVectorizer(analyzer='char',ngram_range=(1,5),sublinear_tf=True,max_features=24000,dtype=np.float64)
    train = vectorizer.fit_transform([normalize(t) for t in texts[:len(examples)]])
    evaluation = vectorizer.transform([normalize(t) for t in texts[len(examples):]])
    semantic,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    y = [r['label'] for r in examples]
    truth = [target(r) for r in rows]
    trials = []
    best = None
    for weight in (0.,.5):
        tx = hstack([train,csr_matrix(semantic[:len(examples)].numpy()*weight)],format='csr')
        ex = hstack([evaluation,csr_matrix(semantic[len(examples):].numpy()*weight)],format='csr')
        for c in (2.,8.,32.):
            model = LinearSVC(C=c,class_weight=None,dual='auto',max_iter=4000,random_state=42)
            model.fit(tx,y)
            guess = model.predict(ex[split['validation']]).tolist()
            measure = metrics([truth[i] for i in split['validation']],guess)
            trials.append({'semantic_weight':weight,'C':c,'iterations':int(model.n_iter_),**measure})
            score = (measure['canonical_accuracy'],measure['food_recall'] or 0)
            if model.n_iter_ < 4000 and (best is None or score>best['score']):
                best = {'score':score,'model':deepcopy(model),'weight':weight,'C':c}
            print(f'sparse semantic={weight} C={c} validation={measure["canonical_accuracy"]:.4f}',flush=True)
    if best is None:
        raise RuntimeError('No converged SVM trial')
    model = best['model']
    ex = hstack([evaluation,csr_matrix(semantic[len(examples):].numpy()*best['weight'])],format='csr')
    predictions = model.predict(ex).tolist()
    decision = model.decision_function(ex)
    top = np.sort(decision,axis=1)
    margins = top[:,-1]-top[:,-2]
    val = split['validation']
    threshold = None
    for candidate in (0.,.1,.2,.3,.5,.75,1.,1.5,2.):
        accepted = [i for i in val if margins[i]>=candidate]
        if len(accepted)>=5 and sum(truth[i]==predictions[i] for i in accepted)/len(accepted)>=.9:
            threshold = candidate
            break
    output = ROOT/'output/ocr_normalizer_experiment_v11'; output.mkdir(exist_ok=True)
    # Plain arrays + JSON, never an executable pickle/joblib artifact.
    np.savez_compressed(output/'model.npz',coef=model.coef_,intercept=model.intercept_,idf=vectorizer.idf_)
    metadata = {'format':'receipt_sparse_svm_v1','labels':model.classes_.tolist(),'vocabulary':{k:int(v) for k,v in vectorizer.vocabulary_.items()},
        'semantic_weight':best['weight'],'encoder_revision':revision,'threshold':threshold,'ngram_range':[1,5],
        'sublinear_tf':True,'calibrated_probability':False}
    (output/'model.json').write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
    report = {'version':11,'algorithm':'character TF-IDF + frozen semantic features / linear SVM','class_weight':None,
        'training_examples':len(examples),'classes':len(model.classes_),'vocabulary_size':len(vectorizer.vocabulary_),
        'selected':{'semantic_weight':best['weight'],'C':best['C']},'threshold':threshold,
        'base_training_sha256':hashlib.sha256((base/'training_examples.json').read_bytes()).hexdigest(),
        'base_report':'../ocr_normalizer_experiment_v7/training_report.json','validation_trials':trials,
        'evaluation':{name:metrics([truth[i] for i in ids],[predictions[i] for i in ids]) for name,ids in split.items()},
        'production_deployed':False,'independent_test':False,
        'limitations':['Assistant labels; previously inspected development holdout, not independent test.',
            'TF-IDF vocabulary and IDF fitted exclusively on training examples; validation selected hyperparameters.',
            'Same v7 data exclusions and external knowledge sources. SVM margin is not probability.']}
    for name,payload in [('training_report.json',report),('heldout_predictions.json',[
        {'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'margin':float(margins[i])} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=='__main__':
    run()
