"""Verify live OCR specificity and recipe inventory semantics; never writes to DB."""
import json
from pathlib import Path
import requests


def main():
    base = 'http://127.0.0.1:8000'
    image = Path('C:/Users/your-user/OneDrive/바탕 화면/캡스톤/영수증사진/news-p.v1.20230216.4dde0d0e8a2d440595d9e9647501eb5d_Z1.jpg')
    response = requests.post(base+'/api/ocr/receipt', files={'file':(image.name,image.read_bytes(),'image/jpeg')}, timeout=120)
    response.raise_for_status()
    ocr = response.json()
    names = [item['name'] for item in ocr['items']]
    assert '팽이버섯' in names and '청양고추' in names, names
    response = requests.post(base+'/api/recipes/ai',json={
        'activeInventories':['팽이버섯','양파','달걀'],'targetIngredients':['표고버섯']},timeout=60)
    response.raise_for_status()
    recipe = response.json()
    assert '표고버섯' not in recipe['availableIngredients'], recipe
    assert '표고버섯' in recipe['missingIngredients'], recipe
    result = {'ocr_names':names,'review_rows':[r['rawText'] for r in ocr['reviewCandidates']],
              'recipe_title':recipe['title'],'available':recipe['availableIngredients'],'missing':recipe['missingIngredients'],
              'scoreBreakdown':recipe['scoreBreakdown'],'scope':'specificity regression, not full OCR accuracy'}
    target = Path(__file__).resolve().parents[1]/'output/ingredient_specificity/runtime_verification.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
