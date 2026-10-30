"""Frozen cross-attention re-ranking of food candidates; no binary-food overrides."""
import hashlib
import json
import torch
from app.ml.ocr_normalizer import CharacterClassifier,features,normalize,NONFOOD
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_semantic_ocr import embeddings
from scripts.train_ocr_normalizer import target,metrics,threshold_for_validation
from app.ml.ocr_choice_support import food_candidates as candidates

MODEL_ID = 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'


def score_pairs(pairs,cache):
    from huggingface_hub import model_info
    from transformers import AutoTokenizer,AutoModelForSequenceClassification
    cache.mkdir(exist_ok=True,parents=True)
    revision_file = cache/'revision.txt'
    if not revision_file.exists():
        revision_file.write_text(model_info(MODEL_ID).sha)
    revision = revision_file.read_text().strip()
    digest = hashlib.sha256(json.dumps([revision,pairs],ensure_ascii=False).encode()).hexdigest()
    path = cache/(digest+'.pt')
    if path.exists():
        return torch.load(path,weights_only=True),revision
    options = dict(revision=revision,cache_dir=cache/'hf',trust_remote_code=False)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID,**options)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID,use_safetensors=True,**options).eval()
    chunks = []
    for start in range(0,len(pairs),32):
        batch = pairs[start:start+32]
        inputs = tokenizer([p[0] for p in batch],[p[1] for p in batch],padding=True,truncation=True,max_length=96,return_tensors='pt')
        with torch.no_grad():
            chunks.append(model(**inputs).logits.reshape(-1))
        if start%320==0:
            print(f'cross encoder {min(start+32,len(pairs))}/{len(pairs)}',flush=True)
    result = torch.cat(chunks)
    torch.save(result,path)
    return result,revision


def run():
    torch.set_num_threads(2)
    base = ROOT/'output/ocr_normalizer_experiment_v7'
    checkpoint = torch.load(base/'model.pt',map_location='cpu',weights_only=True)
    labels = checkpoint['labels']; nonfood = labels.index(NONFOOD)
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    texts = [clean(r['alignment']['ocr_text']) for r in rows]
    vectors,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    assert revision==checkpoint['encoder_revision']
    model = CharacterClassifier(len(labels),4480); model.load_state_dict(checkpoint['state_dict'])
    with torch.no_grad():
        probability = model(torch.cat([features(texts),vectors*checkpoint['semantic_weight']],1)).softmax(1)
    food_rows = [i for i in range(len(rows)) if probability[i].argmax().item()!=nonfood]
    candidate_map = {i:candidates(texts[i],probability[i],labels) for i in food_rows}
    pairs = [[texts[i],f'상품 종류는 {labels[j]}입니다.'] for i in food_rows for j in candidate_map[i]]
    cross_scores,cross_revision = score_pairs(pairs,ROOT/'output/cross_ocr_cache')
    original = probability.clone()
    original[food_rows,nonfood] = 0.
    original[food_rows] /= original[food_rows].sum(1,keepdim=True)
    truth = [target(r) for r in rows]
    val = split['validation']; best,trials = None,[]
    for temperature in (1.,2.,4.):
        cross_prob = original.clone(); offset = 0
        for i in food_rows:
            choices = candidate_map[i]
            values = (cross_scores[offset:offset+len(choices)]/temperature).softmax(0)
            cross_prob[i].zero_(); cross_prob[i,choices] = values
            offset += len(choices)
        for weight in (0.,.1,.25,.5,.75,1.):
            mixed = original*(1-weight)+cross_prob*weight
            prediction = [labels[i] for i in mixed.argmax(1).tolist()]
            measure = metrics([truth[i] for i in val],[prediction[i] for i in val])
            trials.append({'temperature':temperature,'weight':weight,**measure})
            score = (measure['canonical_accuracy'],-weight)
            if best is None or score>best['score']:
                best = {'score':score,'weight':weight,'temperature':temperature,'probabilities':mixed.clone()}
    scores,indices = best['probabilities'].max(1)
    predictions = [labels[i] for i in indices.tolist()]
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i].item() for i in val])
    output = ROOT/'output/ocr_normalizer_experiment_v18'; output.mkdir(exist_ok=True)
    report = {'version':18,'algorithm':'trained v7 classifier + frozen cross-encoder food re-ranking',
        'selected':{k:v for k,v in best.items() if k not in ('score','probabilities')},
        'cross_encoder':MODEL_ID,'cross_encoder_revision':cross_revision,'cross_encoder_finetuned':False,
        'binary_decisions_unchanged':True,'evaluated_pairs':len(pairs),'validation_trials':trials,
        'evaluation':{name:metrics([truth[i] for i in ids],[predictions[i] for i in ids]) for name,ids in split.items()},
        'threshold':threshold,'production_deployed':False,'independent_test':False,
        'base_checkpoint_sha256':hashlib.sha256((base/'model.pt').read_bytes()).hexdigest(),
        'limitations':['Assistant labels; previously inspected development holdout.',
            'Cross encoder trained for multilingual retrieval, not specifically Korean receipts.',
            'Candidates use training vocabulary and input only, never annotation truth.',
            'Mixing parameters selected on validation; nonfood decisions remain unchanged.']}
    manifest = {'format':'receipt_food_cross_reranker_v1','base_checkpoint':'../ocr_normalizer_experiment_v7/model.pt',
        'cross_encoder':MODEL_ID,'cross_encoder_revision':cross_revision,'threshold':threshold,
        'weight':best['weight'],'temperature':best['temperature']}
    for name,payload in [('training_report.json',report),('model.json',manifest),('heldout_predictions.json',[
        {'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'score':scores[i].item()} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='validation_trials'},indent=2))


if __name__=='__main__':
    run()
