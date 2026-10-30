"""Run the experimental OCR product classifier locally without starting/changing an API."""
import argparse
import json
from pathlib import Path
import torch
from app.ml.choice_ocr_normalizer import ChoiceOCRNormalizer,ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,default=ROOT/'output/ocr_normalizer_experiment_v25/model.json')
    parser.add_argument('--text',action='append',required=True,help='OCR product text; may be repeated')
    args = parser.parse_args()
    torch.set_num_threads(2)
    model = ChoiceOCRNormalizer(args.model)
    print(json.dumps([model.predict(text) for text in args.text],ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
