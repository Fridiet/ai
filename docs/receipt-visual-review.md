# 로컬 영수증 156장 시각 검수

사용자가 제공한 156장 전체를 AI가 원본 이미지와 대조해 상품 행을 기록했습니다.
결과는 `output/local_receipt_review/assistant_annotation.html`에서 추가 입력 없이 볼 수 있습니다.
기존 `annotation.html`, `annotation.json`, `review.json`은 보존했습니다.

## 산출물

- `assistant_annotations.json`: 156장, 상품 행 681개. 원문, 분류 제안, 식품 여부, 구매 수량, 단위, 메모.
- `assistant_unresolved.json`: 식품 여부/구체 품목/수량을 확정하지 못한 31개 행.
- `assistant_summary.json`: 식품 408행, 비식품 250행, 식품 여부 불명 23행. 매장 유형은 마트 83장, 편의점 15장, 기타 58장.
- `visual_*.json`: 원본 대조 시 기록한 분할 주석. 자동 OCR 예측을 정답으로 복사한 파일이 아닙니다.

`is_food=true`는 신선 재료라는 뜻이 아닙니다. 빵·과자·외식 메뉴도 식품이며, 재고 등록 대상 필터는 별도로 적용해야 합니다.
수량은 영수증 구매 단위이며 포장 내부 수량과 구분합니다. 음수는 반품입니다.
일반적인 구매 묶음에 사용한 `개`는 달걀·과일의 실제 낱개 수를 뜻하지 않습니다.

## 재생성 및 검증

AI 폴더에서:

```powershell
.\.venv\Scripts\python.exe -m scripts.assemble_receipt_visual_review
.\.venv\Scripts\python.exe -m pytest tests/test_receipt_visual_review.py tests/test_receipt_image_preparation.py -q
```

조립 스크립트는 누락/중복 파일, 이미지 SHA-256, 필드 형식을 검사합니다.
검수 파일은 로컬 output에만 저장됩니다. 원본 개인정보가 포함된 이미지와 주석은 공개 저장소에 올리지 마세요.

## 학습과 평가 주의

`assistant_review_complete=true`와 `human_reviewed=false`를 구분합니다.
AI가 확인한 주석도 오류 가능성이 있으므로 사람 검증 정답이라고 보고하지 않습니다.
불명확한 행은 학습 정답에서 제외해야 합니다. 기존 학습 승인 절차를 우회하지 않도록 `reviewed=false`, `training_eligible=false`를 유지합니다.
현재 모델 재학습, DB 등록, 운영 모델 교체는 실행하지 않았습니다.
이미 개발 과정에서 열람한 원래 test 이미지는 독립 테스트셋으로 성능을 주장하는 데 사용하지 않습니다.
동일 거래로 관찰된 IMG00358/IMG00378은 같은 duplicate_group으로 묶었습니다. 그 외 중복 거래를 완전히 배제했다는 뜻은 아닙니다.
