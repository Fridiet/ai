# 영수증 OCR 정확도 평가 데이터

`receipt_ground_truth.sample.json`은 이미지 없이 OCR 후처리 회귀 테스트를 실행할 수 있는 작은 샘플입니다. 실제 PaddleOCR 정확도까지 평가하려면 영수증 이미지와 정답 데이터셋을 별도로 준비합니다.

## 데이터 작성 원칙

- 케이스마다 `image_path` 또는 `ocr_items` 중 하나만 사용합니다.
- `image_path`는 데이터셋 JSON 파일이 있는 폴더를 기준으로 작성할 수 있습니다.
- `expected_items`에는 영수증의 모든 상품 행을 기록합니다. 일부 행을 누락하면 정상 예측도 오탐으로 계산됩니다.
- `source_text`는 영수증에 인쇄된 상품명 부분을 그대로 기록합니다.
- 재고 등록 대상으로 선택해야 하면 `include: true`와 `canonical_name`을 기록합니다.
- 비식품이나 재고 후보에서 제외할 행은 `include: false`, `canonical_name: null`로 기록합니다.
- 같은 상품이 두 번 인쇄되어 있으면 정답에도 두 번 기록합니다.

형식은 `receipt_dataset.schema.json`에서 확인할 수 있습니다.

```json
{
  "version": 1,
  "cases": [
    {
      "id": "receipt-001",
      "image_path": "images/local/receipt-001.jpg",
      "expected_items": [
        {
          "source_text": "파머스픽 청양고추(봉)",
          "include": true,
          "canonical_name": "고추"
        },
        {
          "source_text": "미장센 퍼펙트 샴푸680",
          "include": false,
          "canonical_name": null
        }
      ]
    }
  ]
}
```

실제 영수증에는 개인정보가 있을 수 있으므로 로컬 이미지는 `evaluation/images/local/`, 정답은 `evaluation/receipt_ground_truth.local.json`에 두는 것을 권장합니다. 두 경로는 Git에서 제외됩니다. 공유할 데이터는 개인정보를 가리고 저작권과 이용 조건을 확인한 뒤 별도로 선별합니다.

## 지표

- `ocr_text_recall`: 정답 상품명이 OCR 행에서 발견된 비율
- `product_row_recall`: OCR 결과가 상품 행으로 후처리된 비율
- `food_selection_precision/recall/f1`: 재고 등록 대상 선별 성능
- `standard_name_accuracy`: 선택된 정답 상품 중 표준명이 정확한 비율
- `end_to_end_standard_name_recall`: 전체 등록 대상 중 OCR부터 표준명까지 성공한 비율

최소 30개 이상의 서로 다른 영수증을 모은 뒤 점수를 기준선으로 사용하는 것이 좋습니다. 매장, 촬영 각도, 해상도, 구겨짐, 긴 상품명과 비식품 포함 여부가 한쪽에 치우치지 않도록 구성합니다.
