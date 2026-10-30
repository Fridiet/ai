"""Transparent exact product-head evidence; not a second learned model."""
import torch
from app.ml.ocr_normalizer import normalize, clean_product_text, NONFOOD


def lexical_prior(texts, labels):
    result = torch.zeros((len(texts),len(labels)))
    keys = [normalize(label) if label != NONFOOD else '' for label in labels]
    for i,text in enumerate(texts):
        value = normalize(clean_product_text(text))
        for j,key in enumerate(keys):
            # A suffix is stronger evidence of the product head than an ingredient substring.
            # Longest matching category wins when names nest (e.g. milk / soy milk).
            if len(key)>=2 and value.endswith(key):
                result[i,j] = len(key)
        maximum = result[i].max()
        if maximum>0:
            result[i] = (result[i]==maximum).float()
    return result
