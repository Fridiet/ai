"""Explicit, local-only inference for experimental E5/classifier checkpoints."""
from pathlib import Path
import torch
from app.ml.ocr_normalizer import CharacterClassifier, ReceiptMLPClassifier, NONFOOD, features, normalize, clean_product_text
from app.ml.ocr_lexical_prior import lexical_prior


class SemanticOCRNormalizer:
    def __init__(self, checkpoint: Path, cache: Path):
        from transformers import AutoTokenizer, AutoModel
        data = torch.load(checkpoint,map_location='cpu',weights_only=True)
        if data.get('format') not in ('receipt_semantic_classifier_v3','receipt_semantic_classifier_mlp_v1'):
            raise ValueError('Unsupported semantic OCR checkpoint')
        options = dict(revision=data['encoder_revision'],cache_dir=cache/'hf',trust_remote_code=False,local_files_only=True)
        self.tokenizer = AutoTokenizer.from_pretrained(data['encoder'],**options)
        self.encoder = AutoModel.from_pretrained(data['encoder'],use_safetensors=True,**options).eval()
        self.labels = data['labels']
        self.threshold = data['threshold']
        self.weight = data['semantic_weight']
        self.semantic_center = data.get('semantic_center')
        self.lexical_strength = data.get('lexical_prior_strength',0.)
        self.lexical_feature_weight = data.get('lexical_feature_weight',0.)
        if data['format']=='receipt_semantic_classifier_mlp_v1':
            self.classifier = ReceiptMLPClassifier(len(self.labels),data['dimension'],data['hidden'])
        else:
            self.classifier = CharacterClassifier(len(self.labels),data['state_dict']['linear.weight'].shape[1])
        self.classifier.load_state_dict(data['state_dict'])
        self.classifier.eval()
        self.food_gate = None
        if 'food_gate_state' in data:
            self.food_gate = torch.nn.Linear(self.classifier.linear.in_features,1)
            self.food_gate.load_state_dict(data['food_gate_state'])
            self.food_gate.eval()
            self.food_gate_threshold = data['food_gate_threshold']

    @torch.no_grad()
    def predict(self,text):
        cleaned = clean_product_text(text)
        batch = self.tokenizer(['query: '+cleaned],padding=True,truncation=True,max_length=96,return_tensors='pt')
        hidden = self.encoder(**batch).last_hidden_state
        mask = batch['attention_mask'].unsqueeze(-1)
        vector = torch.nn.functional.normalize((hidden*mask).sum(1)/mask.sum(1),dim=1)
        if self.semantic_center is not None:
            vector = torch.nn.functional.normalize(vector-self.semantic_center,dim=1)
        x = torch.cat([features([cleaned]),vector*self.weight],1)
        if self.lexical_feature_weight:
            x = torch.cat([x,lexical_prior([cleaned],self.labels)*self.lexical_feature_weight],1)
        logits = self.classifier(x)+lexical_prior([cleaned],self.labels)*self.lexical_strength
        probabilities = logits.softmax(1)[0]
        score,index = probabilities.max(0)
        if self.food_gate is not None:
            if self.food_gate(x).sigmoid().item()>=self.food_gate_threshold:
                food_logits = logits[0].clone()
                food_logits[self.labels.index(NONFOOD)] = -torch.inf
                index = food_logits.argmax()
            else:
                index = torch.tensor(self.labels.index(NONFOOD))
            score = probabilities[index]
        candidate = self.labels[index.item()]
        accepted = bool(normalize(cleaned)) and score.item()>=self.threshold
        return {'input':text,'candidate_label':candidate,'model_score':score.item(),
                'canonical_name':candidate if accepted and candidate!=NONFOOD else None,
                'is_food':candidate!=NONFOOD if accepted else None,'needs_review':not accepted,
                'experimental':True,'calibrated_probability':False,
                'lexical_prior_strength':self.lexical_strength,'hybrid':self.lexical_strength>0,
                'learned_head_features':self.lexical_feature_weight>0}
