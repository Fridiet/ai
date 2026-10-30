"""Offline, reusable inference for a sealed OCR classifier/choice-model experiment."""
import hashlib
import json
from pathlib import Path
import torch
from app.ml.ocr_normalizer import NONFOOD,features,normalize,clean_product_text
from app.ml.semantic_ocr_normalizer import SemanticOCRNormalizer
from app.ml.ocr_choice_support import SYSTEM,choice_options,choice_messages
from app.ml.ocr_rerank_guard import guard_nonfood_switches

ROOT = Path(__file__).resolve().parents[2]
INFERENCE_FILES = [
    'app/ml/choice_ocr_normalizer.py','app/ml/ocr_choice_support.py','app/ml/ocr_rerank_guard.py',
    'app/ml/ocr_normalizer.py','app/ml/semantic_ocr_normalizer.py','app/ml/ocr_lexical_prior.py']


def validate_manifest(data):
    if data.get('format')!='receipt_choice_reranker_v1':
        raise ValueError('Unsupported choice model manifest')
    if not 0<=data['weight']<=1 or data['temperature']<=0:
        raise ValueError('Invalid ensemble parameters')
    if data['system_prompt']!=SYSTEM:
        raise ValueError('Prompt differs from the evaluated protocol')
    if data['choice_model'] not in ('Qwen/Qwen3-0.6B','Qwen/Qwen3-1.7B'):
        raise ValueError('Unsupported choice model')
    if data.get('choice_model_dtype') not in ('float32','bfloat16'):
        raise ValueError('Unsupported numerical precision')
    if not data.get('base_checkpoint_sha256') or not data.get('inference_code_sha256'):
        raise ValueError('Unsealed manifest: run the verifier with --seal first')


class ChoiceOCRNormalizer:
    def __init__(self,manifest_path:Path):
        from transformers import AutoTokenizer,AutoModelForCausalLM
        self.manifest_path = Path(manifest_path).resolve()
        self.config = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        data = self.config
        validate_manifest(data)
        base_path = (self.manifest_path.parent/data['base_checkpoint']).resolve()
        if hashlib.sha256(base_path.read_bytes()).hexdigest()!=data['base_checkpoint_sha256']:
            raise ValueError('Base checkpoint changed after sealing')
        for relative,expected in data['inference_code_sha256'].items():
            if relative not in INFERENCE_FILES or hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=expected:
                raise ValueError('Inference code differs from the sealed protocol')
        if set(data['inference_code_sha256'])!=set(INFERENCE_FILES):
            raise ValueError('Incomplete inference code seal')
        self.base = SemanticOCRNormalizer(base_path,ROOT/'output/semantic_ocr_cache')
        if self.base.food_gate is not None or self.base.semantic_center is not None or self.base.lexical_feature_weight or self.base.lexical_strength:
            raise ValueError('Unsupported base classifier transformation')
        self.labels = self.base.labels
        self.threshold = data['threshold']
        self.choice_model = None
        if data['weight']:
            cache_name = 'choice_ocr_cache_1_7b' if data['choice_model'].endswith('1.7B') else 'choice_ocr_cache'
            options = dict(revision=data['choice_model_revision'],cache_dir=ROOT/'output'/cache_name/'hf',
                           trust_remote_code=False,local_files_only=True)
            self.tokenizer = AutoTokenizer.from_pretrained(data['choice_model'],**options)
            dtype = torch.bfloat16 if data['choice_model_dtype']=='bfloat16' else torch.float32
            self.choice_model = AutoModelForCausalLM.from_pretrained(data['choice_model'],use_safetensors=True,dtype=dtype,**options).eval()
            letters = [self.tokenizer.encode(chr(65+i),add_special_tokens=False) for i in range(16)]
            if any(len(ids)!=1 for ids in letters):
                raise ValueError('Choice letters must be single tokens')
            self.letter_ids = [ids[0] for ids in letters]

    @torch.inference_mode()
    def _base_distribution(self,cleaned):
        batch = self.base.tokenizer(['query: '+cleaned],padding=True,truncation=True,max_length=96,return_tensors='pt')
        hidden = self.base.encoder(**batch).last_hidden_state
        mask = batch['attention_mask'].unsqueeze(-1)
        vector = torch.nn.functional.normalize((hidden*mask).sum(1)/mask.sum(1),dim=1)
        x = torch.cat([features([cleaned]),vector*self.base.weight],1)
        return self.base.classifier(x).softmax(1)

    @torch.inference_mode()
    def predict(self,text):
        cleaned = clean_product_text(text)
        if not normalize(cleaned):
            return {'input':text,'candidate_label':None,'canonical_name':None,'is_food':None,
                    'needs_review':True,'model_score':0.,'experimental':True,'calibrated_probability':False}
        original = self._base_distribution(cleaned)
        before = original.argmax(1).item()
        nonfood = self.labels.index(NONFOOD)
        include_nonfood = self.config['include_nonfood']
        mixed = original.clone()
        blocked = []
        if include_nonfood or before!=nonfood:
            indices,names = choice_options(cleaned,original[0],self.labels,include_nonfood)
            if not include_nonfood:
                original[0,nonfood] = 0.
                original /= original.sum(1,keepdim=True)
                mixed = original.clone()
            if self.choice_model is not None:
                prompt = self.tokenizer.apply_chat_template(choice_messages(cleaned,names),tokenize=False,add_generation_prompt=True,enable_thinking=False)
                inputs = self.tokenizer(prompt,return_tensors='pt')
                if inputs['input_ids'].shape[1]>1024:
                    raise ValueError('Unexpected long classification prompt')
                logits = self.choice_model(**inputs,use_cache=False,logits_to_keep=1).logits[0,-1]
                choice = logits[self.letter_ids[:len(names)]].float()
                reranked = torch.zeros_like(original)
                reranked[0,indices] = (choice/self.config['temperature']).softmax(0)
                mixed = original*(1-self.config['weight'])+reranked*self.config['weight']
                if self.config.get('nonfood_switch_guard'):
                    mixed,blocked = guard_nonfood_switches(original,mixed,[cleaned],self.labels,
                        self.config.get('all_food_switch_guard',False),self.config.get('exact_product_aliases',[]))
        score,index = mixed[0].max(0)
        candidate = self.labels[index.item()]
        accepted = score.item()>=self.threshold
        return {'input':text,'candidate_label':candidate,'base_candidate_label':self.labels[before],
                'canonical_name':candidate if accepted and candidate!=NONFOOD else None,
                'is_food':candidate!=NONFOOD if accepted else None,'needs_review':not accepted,
                'model_score':score.item(),'change_blocked':bool(blocked),
                'experimental':True,'calibrated_probability':False,'guard_is_learned':False}
