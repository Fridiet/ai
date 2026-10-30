"""Experimental character n-gram classifier, separate from receipt production API."""
import hashlib
import re
import unicodedata
from pathlib import Path
import torch
from torch import nn

NONFOOD = '__NONFOOD__'


def clean_product_text(text):
    text = re.sub(r'\d+(?:[.,]\d+)?\s*(?:kg|ml|g|l|구|입|개|봉|단|매|팩|박스)\b', ' ', text, flags=re.I)
    return re.sub(r'^\s*\d{3}\s*', '', text)


def normalize(text):
    text = unicodedata.normalize('NFKC', text).lower()
    text = re.sub(r'^\s*\d{3}(?=\s*[^\d])', '', text)
    return re.sub(r'[^가-힣a-z0-9]', '', text)


def features(texts, dimension=4096):
    result = torch.zeros((len(texts), dimension))
    for i, text in enumerate(texts):
        value = '^' + normalize(text) + '$'
        for width in (1, 2, 3):
            for start in range(len(value) - width + 1):
                token = value[start:start + width].encode('utf-8')
                index = int.from_bytes(hashlib.blake2b(token, digest_size=8).digest(), 'little') % dimension
                result[i, index] += 1
    return torch.nn.functional.normalize(torch.log1p(result), dim=1)


class CharacterClassifier(nn.Module):
    def __init__(self, classes, dimension=4096):
        super().__init__()
        self.linear = nn.Linear(dimension, classes)

    def forward(self, x):
        return self.linear(x)


class ReceiptMLPClassifier(nn.Module):
    def __init__(self, classes, dimension=4480, hidden=128):
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(dimension,hidden),nn.ReLU(),nn.Dropout(.1),nn.Linear(hidden,classes))

    def forward(self,x):
        return self.layers(x)


class ExperimentalNormalizer:
    def __init__(self, checkpoint: Path):
        data = torch.load(checkpoint, map_location='cpu', weights_only=True)
        if data.get('format') not in ('receipt_character_classifier_v1', 'receipt_character_classifier_v2'):
            raise ValueError('Unsupported checkpoint')
        self.clean_input = data['format'].endswith('v2')
        self.labels = data['labels']
        self.dimension = data['dimension']
        self.threshold = data['threshold']
        self.model = CharacterClassifier(len(self.labels), self.dimension)
        self.model.load_state_dict(data['state_dict'])
        self.model.eval()

    @torch.no_grad()
    def predict(self, text):
        value = clean_product_text(text) if self.clean_input else text
        probs = self.model(features([value], self.dimension)).softmax(dim=1)[0]
        confidence, index = probs.max(dim=0)
        candidate = self.labels[index.item()]
        accepted = bool(normalize(text)) and confidence.item() >= self.threshold
        return {'input': text, 'candidate_label': candidate, 'model_score': round(confidence.item(), 6),
            'canonical_name': candidate if accepted and candidate != NONFOOD else None,
            'is_food': (candidate != NONFOOD) if accepted else None,
            'needs_review': not accepted, 'experimental': True,
            'note': 'model_score는 보정된 정답 확률이 아닙니다.'}
