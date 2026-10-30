"""Local small-language-model multiple-choice scoring, without generating free text."""
import hashlib
import argparse
import json
import torch
from app.ml.ocr_normalizer import CharacterClassifier,features,NONFOOD,normalize
from scripts.improve_ocr_normalizer import ROOT,clean,load_split
from scripts.train_semantic_ocr import embeddings
from scripts.train_ocr_normalizer import target,metrics,threshold_for_validation
from scripts.rerank_ocr_food import candidates
from app.ml.ocr_rerank_guard import guard_nonfood_switches
from app.ml.ocr_choice_support import SYSTEM,choice_messages,choice_options

MODEL_ID = 'Qwen/Qwen3-0.6B'


def choice_scores(items,cache,model_id=MODEL_ID):
    from huggingface_hub import model_info
    from transformers import AutoTokenizer,AutoModelForCausalLM
    cache.mkdir(exist_ok=True,parents=True)
    revision_path = cache/'revision.txt'
    if not revision_path.exists():
        revision_path.write_text(model_info(model_id).sha)
    revision = revision_path.read_text().strip()
    fingerprint = hashlib.sha256(json.dumps([revision,SYSTEM,items],ensure_ascii=False).encode()).hexdigest()
    path = cache/(fingerprint+'.pt')
    if path.exists():
        return torch.load(path,weights_only=True),revision
    options = dict(revision=revision,cache_dir=cache/'hf',trust_remote_code=False)
    tokenizer = AutoTokenizer.from_pretrained(model_id,**options)
    dtype = torch.bfloat16 if model_id.endswith('1.7B') else torch.float32
    model = AutoModelForCausalLM.from_pretrained(model_id,use_safetensors=True,dtype=dtype,**options).eval()
    encoded_letters = [tokenizer.encode(chr(65+i),add_special_tokens=False) for i in range(16)]
    if any(len(ids)!=1 for ids in encoded_letters):
        raise ValueError('Choice letters are not single tokens')
    letter_ids = [ids[0] for ids in encoded_letters]
    results = []
    for i,(text,names) in enumerate(items):
        prompt = tokenizer.apply_chat_template(choice_messages(text,names),tokenize=False,add_generation_prompt=True,enable_thinking=False)
        inputs = tokenizer(prompt,return_tensors='pt')
        if inputs['input_ids'].shape[1]>1024:
            raise ValueError('Unexpected long classification prompt')
        with torch.no_grad():
            logits = model(**inputs,use_cache=False,logits_to_keep=1).logits[0,-1]
        results.append(logits[letter_ids[:len(names)]].float().clone())
        print(f'local choice scoring {i+1}/{len(items)}',flush=True)
    torch.save(results,path)
    return results,revision


def run(include_nonfood=False,model_id=MODEL_ID,safety_gate=False,all_food_switches=False):
    if safety_gate and not include_nonfood:
        raise ValueError('Safety gate requires nonfood candidates')
    if all_food_switches and not safety_gate:
        raise ValueError('All-food guard requires safety gate')
    torch.set_num_threads(2)
    base = ROOT/'output/ocr_normalizer_experiment_v7'
    checkpoint = torch.load(base/'model.pt',map_location='cpu',weights_only=True)
    labels = checkpoint['labels']; nonfood = labels.index(NONFOOD)
    rows = json.loads((ROOT/'output/local_receipt_review/pairs/candidates.json').read_text(encoding='utf-8'))
    split = load_split(rows)
    texts = [clean(r['alignment']['ocr_text']) for r in rows]
    vectors,revision = embeddings(texts,ROOT/'output/semantic_ocr_cache')
    if revision!=checkpoint['encoder_revision']:
        raise ValueError('Encoder revision mismatch')
    model = CharacterClassifier(len(labels),4480); model.load_state_dict(checkpoint['state_dict'])
    with torch.no_grad():
        original = model(torch.cat([features(texts),vectors*checkpoint['semantic_weight']],1)).softmax(1)
    ids = split['validation']+split['test']
    food = list(ids) if include_nonfood else [i for i in ids if original[i].argmax().item()!=nonfood]
    options = {i:choice_options(texts[i],original[i],labels,include_nonfood) for i in food}
    candidate_map = {i:options[i][0] for i in food}
    items = [[texts[i],options[i][1]] for i in food]
    cache_name = 'choice_ocr_cache' if model_id==MODEL_ID else 'choice_ocr_cache_1_7b'
    choice_logits,llm_revision = choice_scores(items,ROOT/'output'/cache_name,model_id)
    if not include_nonfood:
        original[food,nonfood] = 0.
        original[food] /= original[food].sum(1,keepdim=True)
    truth = [target(r) for r in rows]; val = split['validation']
    aliases = []
    if all_food_switches:
        products = json.loads((ROOT/'app/data/processed_products_ko.json').read_text(encoding='utf-8'))
        aliases = sorted({(normalize(clean(name)),item['standard_food_name']) for item in products
                          for name in [item['name'],*item.get('aliases',[])]})
    best,trials = None,[]
    for temperature in (1.,2.,4.):
        reranked = original.clone()
        for i,logits in zip(food,choice_logits):
            reranked[i].zero_()
            reranked[i,candidate_map[i]] = (logits/temperature).softmax(0)
        for weight in (0.,.1,.25,.5,.75,1.):
            mixed = original*(1-weight)+reranked*weight
            if safety_gate:
                mixed,_ = guard_nonfood_switches(original,mixed,texts,labels,all_food_switches,aliases)
            predictions = [labels[j] for j in mixed.argmax(1).tolist()]
            measure = metrics([truth[i] for i in val],[predictions[i] for i in val])
            trials.append({'temperature':temperature,'weight':weight,**measure})
            score = (measure['canonical_accuracy'],-weight)
            if best is None or score>best['score']:
                best = {'score':score,'temperature':temperature,'weight':weight,'probabilities':mixed.clone()}
    scores,indices = best['probabilities'].max(1)
    predictions = [labels[i] for i in indices.tolist()]
    threshold = threshold_for_validation([truth[i] for i in val],[predictions[i] for i in val],[scores[i].item() for i in val])
    version = (20 if include_nonfood else 19) if model_id==MODEL_ID else (21 if include_nonfood else 22)
    if safety_gate:
        version = 23 if model_id.endswith('1.7B') else 24
    if all_food_switches:
        version = 25 if model_id.endswith('1.7B') else 26
    output = ROOT/f'output/ocr_normalizer_experiment_v{version}'; output.mkdir(exist_ok=True)
    report = {'version':version,'algorithm':'trained v7 classifier + local multiple-choice LM reranker',
        'selected':{k:v for k,v in best.items() if k not in ('score','probabilities')},
        'choice_model':model_id,'choice_model_revision':llm_revision,'locally_finetuned_lm':False,
        'choice_model_dtype':'bfloat16' if model_id.endswith('1.7B') else 'float32',
        'choice_evaluations':len(items),'nonfood_candidate_enabled':include_nonfood,'validation_trials':trials,
        'nonfood_switch_guard':safety_gate,'guard_is_learned':False,
        'all_food_switch_guard':all_food_switches,'exact_product_aliases':aliases,
        'evaluation':{name:metrics([truth[i] for i in split[name]],[predictions[i] for i in split[name]]) for name in ('validation','test')},
        'threshold':threshold,'production_deployed':False,'independent_test':False,
        'limitations':['Assistant labels; previously inspected development holdout.',
            'Base classifier trained on v7 data; LM supplies existing pretrained knowledge, not locally learned receipt behavior.',
            'Only candidate letter logits are scored. No free-text generation or external inference API.',
            'Prompt contains product text and training-vocabulary candidates, never truth labels.']}
    manifest = {'format':'receipt_choice_reranker_v1','base_checkpoint':'../ocr_normalizer_experiment_v7/model.pt',
        'choice_model':model_id,'choice_model_revision':llm_revision,'system_prompt':SYSTEM,
        'choice_model_dtype':'bfloat16' if model_id.endswith('1.7B') else 'float32',
        'include_nonfood':include_nonfood,
        'nonfood_switch_guard':safety_gate,
        'all_food_switch_guard':all_food_switches,'exact_product_aliases':aliases,
        'weight':best['weight'],'temperature':best['temperature'],'threshold':threshold}
    for name,payload in [('training_report.json',report),('model.json',manifest),('heldout_predictions.json',[
        {'filename':rows[i]['filename'],'input':rows[i]['alignment']['ocr_text'],
         'truth':truth[i],'prediction':predictions[i],'score':scores[i].item()} for i in split['test']])]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='validation_trials'},indent=2))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-nonfood',action='store_true')
    parser.add_argument('--choice-model',choices=[MODEL_ID,'Qwen/Qwen3-1.7B'],default=MODEL_ID)
    parser.add_argument('--safety-gate',action='store_true')
    parser.add_argument('--all-food-switches',action='store_true')
    args = parser.parse_args()
    run(args.include_nonfood,args.choice_model,args.safety_gate,args.all_food_switches)
