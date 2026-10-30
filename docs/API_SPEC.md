# Fridiet AI API 명세서

## 1. 문서 정보

| 항목 | 값 |
|---|---|
| API 이름 | Fridiet AI OCR·레시피 추천 API |
| API 버전 | `0.13.0` |
| 작성 기준일 | `2026-08-19` |
| 로컬 기본 URL | `http://localhost:8000` |
| Swagger UI | `http://localhost:8000/docs` |
| OpenAPI JSON | `http://localhost:8000/openapi.json` |

이 문서는 실행 중인 FastAPI의 OpenAPI 계약과 실제 처리 로직을 기준으로 작성한다.

## 2. 공통 규칙

### 2.1 인증과 사용자 식별

현재 AI API 자체에는 JWT 또는 세션 인증이 없다. `created_by`와 `user_id`를 이용해 MySQL의 사용자, 냉장고 소유자 및 활성 멤버 여부를 검증한다.

운영 환경에서는 AI 서버를 외부에 직접 공개하지 않고 백엔드 내부 API로 사용하며, 백엔드가 인증된 사용자 ID를 전달해야 한다. 클라이언트가 임의로 보낸 사용자 ID를 그대로 신뢰해서는 안 된다.

### 2.2 Content-Type

| 용도 | Content-Type |
|---|---|
| 이미지 업로드 | `multipart/form-data` |
| JSON 요청·응답 | `application/json` |

### 2.3 날짜 형식

| 값 | 형식 | 예시 |
|---|---|---|
| 날짜 | ISO 8601 `YYYY-MM-DD` | `2026-08-20` |
| 날짜·시간 | ISO 8601 | `2026-08-16T02:42:53.400122` |

### 2.4 이미지 제한

- 허용 형식: JPEG, PNG, WebP, BMP
- 기본 최대 크기: 10 MiB
- 최대 크기는 `MAX_UPLOAD_BYTES` 환경 변수로 변경할 수 있다.

### 2.5 주요 Enum

| 구분 | 값 |
|---|---|
| 보관 방식 | `REFRIGERATED`, `FROZEN`, `ROOM_TEMPERATURE`, `UNKNOWN` |
| 상품 분류 | `processed_food`, `ingredient` |
| 마스터 타입 | `INGREDIENT`, `SEASONING`, `BEVERAGE`, `ETC` |
| 매칭 방식 | `exact`, `alias`, `contains`, `fuzzy` |
| 검토 결정 | `CONFIRM`, `REJECT` |
| 식단 목표 | `GENERAL`, `WEIGHT_LOSS`, `DIABETES_MANAGEMENT`, `LOW_SODIUM` |

### 2.6 오류 응답 형식

애플리케이션 오류는 다음 형식이다.

```json
{
  "detail": "오류 설명"
}
```

요청 형식 검증 실패는 FastAPI 표준 `422` 응답을 사용한다.

```json
{
  "detail": [
    {
      "loc": ["body", "user_id"],
      "msg": "Input should be greater than 0",
      "type": "greater_than"
    }
  ]
}
```

## 3. 엔드포인트 요약

| Method | Path | 설명 | DB 변경 |
|---|---|---|---:|
| GET | `/health` | 서버 상태 확인 | N |
| POST | `/ocr` | 프론트엔드용 영수증 재고 데이터 생성 | N |
| POST | `/ocr/raw` | 원본 OCR 텍스트·좌표 추출 | N |
| POST | `/api/ocr/receipt` | 프론트엔드·Spring 연동용 영수증 재고 데이터 생성 | N |
| POST | `/ocr/inventory-candidates` | OCR 후처리 및 재고 후보 생성 | N |
| POST | `/ocr/inventory-candidates/save` | 재고 후보를 검토 대기로 저장 | Y |
| POST | `/ocr/scans/{ocr_scan_id}/confirm` | 검토 결과를 실제 재고로 확정 | Y |
| POST | `/recommendations` | 재고·소비기한·사용자 상태 기반 레시피 추천 | N |
| POST | `/api/recipes/ai` | 팀 camelCase 계약 기반 단일 레시피 추천 | N |

## 4. 서버 상태 확인

### `GET /health`

#### Response `200 OK`

```json
{
  "status": "ok"
}
```

## 5. 원본 OCR

### `POST /ocr/raw`

영수증이나 상품 이미지에서 인식한 전체 텍스트, 인식 신뢰도와 좌표를 반환한다. 상품 분리나 식재료 매칭은 수행하지 않는다.

### 5.1 Request

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---:|---:|---|
| form-data | `file` | binary | Y | OCR 처리할 이미지 |

```powershell
curl.exe -X POST "http://localhost:8000/ocr/raw" `
  -F "file=@receipt.png"
```

### 5.2 Response `200 OK`

```json
{
  "filename": "receipt.png",
  "text": "서울우유 1L\n2,980",
  "count": 2,
  "items": [
    {
      "text": "서울우유 1L",
      "confidence": 0.98,
      "box": [[10, 20], [210, 20], [210, 50], [10, 50]]
    },
    {
      "text": "2,980",
      "confidence": 0.97,
      "box": [[240, 20], [330, 20], [330, 50], [240, 50]]
    }
  ]
}
```

`box`는 좌상단부터 OCR 엔진이 반환한 네 개의 `[x, y]` 좌표다.

### 5.3 오류

| 상태 | 조건 |
|---:|---|
| `400` | 이미지 디코딩 실패 |
| `413` | 최대 업로드 크기 초과 |
| `415` | 지원하지 않는 이미지 Content-Type |
| `422` | `file` 누락 |
| `500` | OCR 엔진 처리 실패 |

## 6. 재고 등록 후보 생성

### `POST /ocr/inventory-candidates`

OCR 결과 후처리, 상품 행 분리, 상품명 정규화, K-FIND 가공식품 또는 식재료 사전 매칭을 수행한다. DB에는 저장하지 않는다.

### 6.1 Request

| 위치 | 이름 | 타입 | 필수 | 기본값 | 범위 | 설명 |
|---|---|---:|---:|---:|---:|---|
| form-data | `file` | binary | Y | - | - | 영수증 이미지 |
| query | `min_confidence` | number | N | `0.35` | 0~1 | 사용할 OCR 최소 신뢰도 |
| query | `match_threshold` | number | N | `0.72` | 0~1 | 상품·재료 매칭 최소 점수 |

```powershell
curl.exe -X POST "http://localhost:8000/ocr/inventory-candidates?min_confidence=0.35&match_threshold=0.72" `
  -F "file=@receipt.png"
```

### 6.2 Response `200 OK`

```json
{
  "filename": "receipt.png",
  "count": 1,
  "requires_backend_mapping": true,
  "inventory_candidates": [
    {
      "row_index": 0,
      "raw_text": "서울우유 1L 2,980원",
      "recognized_product_name": "서울우유 1L",
      "normalized_product_name": "서울우유",
      "manufacturer": "서울우유협동조합",
      "food_type": "우유",
      "classification": "processed_food",
      "prices": [2980],
      "ingredient_master_candidate": {
        "ingredient_id": null,
        "ingredient_type": "BEVERAGE",
        "canonical_name": "우유",
        "default_unit": "ml",
        "alias_text": "서울우유",
        "match_confidence": 1.0,
        "match_type": "exact",
        "source": "seed_product_catalog"
      },
      "inventory_batch_candidate": {
        "ingredient_id": null,
        "consume_by_date": null,
        "remaining_quantity": 1.0,
        "unit": "l",
        "quantity_source": "PACKAGE_TEXT",
        "storage_type": "REFRIGERATED",
        "storage_source": "CANONICAL_RULE",
        "storage_confidence": 0.8,
        "status": "ACTIVE"
      },
      "warnings": [
        "INGREDIENT_ID_REQUIRES_BACKEND_MAPPING",
        "CONSUME_BY_DATE_REQUIRES_INPUT",
        "STORAGE_TYPE_IS_SUGGESTION"
      ]
    }
  ]
}
```

### 6.3 후보 필드

#### 상품 정보

| 필드 | 타입 | 설명 |
|---|---|---|
| `row_index` | integer | 상품 행 순서 |
| `raw_text` | string | 가격 등을 포함한 원본 상품 행 |
| `recognized_product_name` | string | OCR에서 상품명으로 분리한 문자열 |
| `normalized_product_name` | string | 용량·행사 문구 등을 제거한 상품명 |
| `manufacturer` | string/null | 매칭된 제조사 |
| `food_type` | string | 가공식품 유형 또는 원재료 |
| `classification` | enum | 완제품 또는 식재료 분류 |
| `prices` | integer[] | 상품 행에서 찾은 가격 후보 |

#### `ingredient_master_candidate`

| 필드 | 타입 | 설명 |
|---|---|---|
| `ingredient_id` | null | 후보 API에서는 항상 `null` |
| `ingredient_type` | enum | 표준 마스터 유형 후보 |
| `canonical_name` | string | 표준화된 식품·재료명 |
| `default_unit` | string | 기본 단위 후보 |
| `alias_text` | string/null | 상품명 또는 동의어 후보 |
| `match_confidence` | number | 0~1 매칭 점수 |
| `match_type` | enum | 매칭 방식 |
| `source` | string | K-FIND, 식약처 API, 로컬 사전 등의 출처 |

#### `inventory_batch_candidate`

| 필드 | 타입 | 설명 |
|---|---|---|
| `ingredient_id` | null | 저장 단계에서 기존 마스터와 연결 |
| `consume_by_date` | date/null | 영수증에서 알 수 없어 기본 `null` |
| `remaining_quantity` | number | 포장 수량 또는 기본값 `1.0` |
| `unit` | string | OCR 단위 또는 기본 단위 |
| `quantity_source` | enum | `PACKAGE_TEXT` 또는 `DEFAULT` |
| `storage_type` | enum | 추천 보관 방식 |
| `storage_source` | enum | 영수증 문구, 표준명 규칙 또는 미확인 |
| `storage_confidence` | number | 0~1 보관 방식 신뢰도 |
| `status` | string | 항상 `ACTIVE` |

### 6.4 경고 코드

| 코드 | 의미 | 권장 처리 |
|---|---|---|
| `INGREDIENT_ID_REQUIRES_BACKEND_MAPPING` | 마스터 ID 미연결 | 저장 또는 확정 단계에서 마스터 조회 |
| `CONSUME_BY_DATE_REQUIRES_INPUT` | 소비기한을 알 수 없음 | 사용자 입력 받기 |
| `QUANTITY_DEFAULTED_TO_ONE` | 포장 수량을 찾지 못함 | 수량 확인 UI 표시 |
| `STORAGE_TYPE_IS_SUGGESTION` | 규칙으로 보관 방식 추정 | 사용자 수정 허용 |
| `STORAGE_TYPE_REQUIRES_CONFIRMATION` | 보관 방식 미확인 | 사용자 선택 받기 |

### 6.5 팀 연동용 영수증 분석

#### `POST /ocr`, `POST /api/ocr/receipt`

프론트엔드 또는 Spring 백엔드가 보낸 영수증 이미지를 분석해 재고 등록 화면에서 바로 사용할 수 있는 간소화된 JSON을 반환한다. 현재 병합된 프론트엔드는 `/ocr`를 호출하며 `/api/ocr/receipt`는 같은 기능의 별칭이다. DB에는 저장하지 않는다.

#### Request

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---:|---:|---|
| form-data | `file` | binary | Y | JPEG, PNG, WebP 또는 BMP 영수증 이미지 |

```powershell
curl.exe -X POST "http://localhost:8000/api/ocr/receipt" `
  -F "file=@receipt.png"
```

#### Response `200 OK`

```json
{
  "items": [
    {
      "name": "당근",
      "quantity": 2,
      "unit": "개",
      "consumeByDate": "2026-08-25",
      "locationCategory": "refrigerated",
      "category": "vegetable"
    }
  ]
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `name` | string | 상품명을 정규화한 표준 식품·재료명 |
| `quantity` | number | 등록 초안의 총량. 포장당 용량과 구매 개수가 확인되면 곱한 값이며, 개수만 확인되면 구매 개수. 미확정 기본값은 확정 수량이 아니므로 `reviewNotes`를 확인 |
| `unit` | string | OCR에서 인식한 단위, 찾지 못하면 `개` |
| `consumeByDate` | date 또는 null | 알려진 보관 방식에 대한 추정 기한. 보관 방식이 불명확하면 null이며 사용자 입력 필요 |
| `locationCategory` | enum | `refrigerated`, `frozen`, `room_temperature`, `unknown` |
| `category` | enum | `vegetable`, `fruit`, `meat`, `seafood`, `dairy`, `egg`, `grain`, `seasoning`, `beverage`, `processed_food`, `etc` |
| `rawText` | string 또는 null | 해당 상품의 OCR 원문 |
| `reviewNotes` | string[] | 수량 근거, 포장 용량 및 보관·기한 확인 안내 |

`consumeByDate`는 포장지에 표시된 실제 소비기한을 읽은 값이 아니다. 영수증 처리일을 구매일로 간주하고 품목 카테고리와 추천 보관 방식별 보수적인 보관 일수를 더한 추정값이다. 예를 들어 냉장 채소는 기본 7일, 냉장 육류는 3일, 냉장 수산물은 2일을 적용한다. 프론트엔드는 저장 전 확인·수정 화면을 제공해야 한다.

**2026-09-12 계약 확장:** `locationCategory`가 `unknown`이면 `consumeByDate`는 `null`이다. 이 날짜를 오늘 날짜나 임의 기한으로 대체하여 저장하지 않는다. 현재 프론트는 null을 빈 입력칸으로 표시하고 날짜 입력 전 등록을 막으며, 재고 DB의 필수 날짜 제약은 그대로다. 알려진 보관 방식의 추정 날짜도 제품 표시를 확인해야 한다. 실제 구매일·개봉 여부·제품의 실제 기한을 OCR이 검증한 것은 아니다.

미분류 상품은 최상위 `reviewCandidates` 배열에 `{ "rawText": "...", "purchaseCount": null, "reason": "확인 안내" }` 형태로 반환할 수 있다. `purchaseCount`는 확인된 구매 개수 또는 null이다. `items`에 들어갔다고 모든 필드가 확정된 것은 아니므로 `reviewNotes`, `unknown`, null을 함께 처리한다.

보관·날짜가 미확정인 정상 응답 예시:

```json
{
  "items": [{
    "name": "제로사이다",
    "quantity": 1,
    "unit": "개",
    "consumeByDate": null,
    "locationCategory": "unknown",
    "category": "beverage",
    "rawText": "칠성사이다제로P500 1 2,200",
    "reviewNotes": ["제품 표시를 확인하고 보관 위치와 소비기한을 입력해주세요."]
  }],
  "reviewCandidates": []
}
```

상세 재고 후보의 `quantity_source=VERIFIED_RECEIPT_TOTALS`는 표시된 구매·증정 수량과 금액을 전체 합계와 대조했다는 뜻이다. 모델 신뢰도나 포장 용량을 검증했다는 뜻은 아니다.

오류 상태는 원본 OCR과 동일하게 `400`, `413`, `415`, `422`, `500`을 사용한다.

## 7. 재고 후보 저장

### `POST /ocr/inventory-candidates/save`

후보 생성 과정을 수행한 뒤 `ocr_scan`, `ocr_scan_item`에 `PENDING_REVIEW` 상태로 저장한다. 이 단계에서는 `inventory_batches`를 생성하지 않는다.

### 7.1 Request

| 위치 | 이름 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---:|---:|---:|---|
| form-data | `file` | binary | Y | - | 영수증 이미지 |
| form-data | `fridge_id` | integer | Y | - | 대상 냉장고 ID, 1 이상 |
| form-data | `created_by` | integer | Y | - | 요청 사용자 ID, 1 이상 |
| query | `min_confidence` | number | N | `0.35` | OCR 최소 신뢰도 |
| query | `match_threshold` | number | N | `0.72` | 매칭 최소 점수 |

```powershell
curl.exe -X POST "http://localhost:8000/ocr/inventory-candidates/save" `
  -F "file=@receipt.png" `
  -F "fridge_id=2" `
  -F "created_by=2"
```

### 7.2 Response `201 Created`

```json
{
  "ocr_scan_id": 10,
  "status": "PENDING_REVIEW",
  "item_count": 1,
  "mapped_item_count": 1,
  "requires_review_count": 1,
  "items": [
    {
      "ocr_scan_item_id": 20,
      "row_index": 0,
      "ingredient_id": 48,
      "canonical_name": "두부",
      "review_status": "PENDING_REVIEW"
    }
  ],
  "inventory_candidates": []
}
```

실제 `inventory_candidates`에는 6절의 응답과 같은 전체 후보 객체가 포함된다. 위 예시는 저장 관련 필드에 집중하기 위해 배열을 축약했다.

저장 시 `canonical_name`으로 `ingredient_master`, `alias_text`로 `ingredient_alias`를 조회한다. 정확히 연결되는 마스터가 없으면 `ingredient_id`는 `null`로 저장되며 자동으로 새 마스터를 만들지 않는다.

### 7.3 오류

| 상태 | 조건 |
|---:|---|
| `400`, `413`, `415` | 이미지 처리 오류 |
| `403` | 사용자 없음, 냉장고 없음 또는 접근 권한 없음 |
| `422` | form/query 검증 실패 |
| `503` | `DATABASE_URL` 미설정 또는 OCR 저장 스키마 미적용 |
| `500` | OCR 결과 DB 저장 실패 |

## 8. OCR 후보 재고 확정

### `POST /ocr/scans/{ocr_scan_id}/confirm`

저장된 모든 OCR 항목에 대해 `CONFIRM` 또는 `REJECT` 결정을 한 번에 전달한다. `CONFIRM` 항목만 실제 `inventory_batches`에 생성한다.

### 8.1 Request

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---:|---:|---|
| path | `ocr_scan_id` | integer | Y | 저장 API가 반환한 스캔 ID |
| body | `created_by` | integer | Y | 최초 OCR 요청자와 같은 사용자 ID |
| body | `items` | array | Y | 한 개 이상의 전체 항목 결정 |

```json
{
  "created_by": 2,
  "items": [
    {
      "ocr_scan_item_id": 20,
      "decision": "CONFIRM",
      "ingredient_id": 48,
      "remaining_quantity": 300,
      "unit": "g",
      "consume_by_date": "2026-08-20",
      "storage_type": "REFRIGERATED"
    },
    {
      "ocr_scan_item_id": 21,
      "decision": "REJECT"
    }
  ]
}
```

`CONFIRM` 항목에는 `ingredient_id`, `remaining_quantity`, `unit`, `storage_type`이 필요하다. `consume_by_date`는 선택값이다. `REJECT` 항목에는 항목 ID와 결정만 필요하다.

### 8.2 Response `200 OK`

```json
{
  "ocr_scan_id": 10,
  "status": "REVIEWED",
  "created_batch_count": 1,
  "rejected_item_count": 1,
  "batches": [
    {
      "batch_id": 30,
      "ocr_scan_item_id": 20,
      "ingredient_id": 48,
      "remaining_quantity": 300.0,
      "unit": "g",
      "consume_by_date": "2026-08-20",
      "storage_type": "REFRIGERATED"
    }
  ]
}
```

생성되는 재고는 `status=ACTIVE`, `source_type=OCR`, `source_ocr_item_id=ocr_scan_item_id`로 저장된다. 동일 OCR 항목은 UNIQUE 제약으로 중복 재고 생성이 차단된다.

### 8.3 검증 규칙

- 저장된 모든 OCR 항목에 대한 결정을 한 요청에 포함해야 한다.
- 같은 `ocr_scan_item_id`를 중복해서 보낼 수 없다.
- 최초 OCR 요청자와 `created_by`가 같아야 한다.
- `CONFIRM`의 `ingredient_id`는 실제 `ingredient_master`에 존재해야 한다.
- 한 번 `REVIEWED`가 된 스캔은 다시 확정할 수 없다.
- 하나라도 실패하면 트랜잭션 전체가 롤백된다.

### 8.4 오류

| 상태 | 조건 |
|---:|---|
| `400` | 요청자 불일치, 전체 결정 누락, 중복 항목, 필수값 누락, 잘못된 재료 ID |
| `404` | 존재하지 않는 `ocr_scan_id` |
| `409` | 이미 검토 완료된 스캔 |
| `422` | JSON 스키마 검증 실패 |
| `503` | DB 설정 또는 필수 테이블·컬럼 누락 |
| `500` | 재고 확정 처리 실패 |

## 9. 사용자 상태 기반 레시피 추천

### `POST /recommendations`

냉장고의 활성 재고, 소비기한, 필수 재료 보유율, 최근 메뉴와 사용자 식단 프로필을 기준으로 레시피를 추천한다. 조회 전용이며 재고를 차감하거나 개인 레시피를 저장하지 않는다.

### 9.1 Request

```json
{
  "fridge_id": 2,
  "user_id": 3,
  "limit": 5,
  "max_missing_required": 1
}
```

| 필드 | 타입 | 필수 | 기본값 | 범위 | 설명 |
|---|---:|---:|---:|---:|---|
| `fridge_id` | integer | Y | - | 1 이상 | 추천 대상 냉장고 |
| `user_id` | integer | Y | - | 1 이상 | 요청자 및 식단 프로필 사용자 |
| `limit` | integer | N | `5` | 1~10 | 최대 추천 개수 |
| `max_missing_required` | integer | N | `1` | 0~5 | 허용할 부족 필수 재료 수 |

```powershell
curl.exe -X POST "http://localhost:8000/recommendations" `
  -H "Content-Type: application/json" `
  -d '{"fridge_id":2,"user_id":3,"limit":5,"max_missing_required":1}'
```

### 9.2 Response `200 OK`

다음은 개발용 당뇨 관리 프로필로 실제 호출한 응답 예시다.

```json
{
  "fridge_id": 2,
  "user_id": 3,
  "generated_at": "2026-08-16T02:42:53.400122",
  "active_inventory_count": 5,
  "excluded_expired_inventory_count": 0,
  "applied_profile": {
    "goal": "DIABETES_MANAGEMENT",
    "conditions": ["DIABETES"],
    "calorie_min": 150.0,
    "calorie_max": 450.0,
    "carbohydrate_min_g": null,
    "carbohydrate_max_g": 30.0,
    "protein_min_g": 10.0,
    "sodium_max_mg": 600.0
  },
  "warnings": [
    "영양 적합도는 설정값과 데이터셋 영양정보를 비교한 참고 정보이며 의료 진단이나 치료를 대체하지 않습니다."
  ],
  "score_policy": {
    "expiry_urgency": 40.0,
    "inventory_coverage": 35.0,
    "stock_utilization": 15.0,
    "menu_diversity": 10.0,
    "missing_required_penalty_per_item": 10.0,
    "personalized_inventory_weight": 0.7,
    "personalized_nutrition_weight": 0.3
  },
  "recommendations": [
    {
      "recipe_id": 21,
      "recipe_name": "새우 두부 계란찜",
      "cook_time": null,
      "category": "반찬",
      "description": "식품의약품안전처 조리식품 레시피 DB에서 제공하는 찜 요리",
      "steps": [
        "손질된 새우를 끓는 물에 데쳐 건진다.",
        "두부, 달걀과 나머지 재료를 섞어 그릇에 담는다.",
        "시금치를 올리고 찜기에서 약 10분간 찐다."
      ],
      "source": "MFDS_COOKRCP01",
      "nutrition": {
        "serving_size_g": null,
        "calories_kcal": 220.0,
        "carbohydrate_g": 3.0,
        "protein_g": 14.0,
        "fat_g": 17.0,
        "sodium_mg": 99.0,
        "source": "MFDS_COOKRCP01"
      },
      "health_reasons": [
        "열량 220kcal가 설정 범위에 포함됩니다",
        "탄수화물 3g이 설정 범위에 포함됩니다",
        "단백질 14g이 설정 최솟값을 충족합니다",
        "나트륨 99mg이 설정 상한 이내입니다"
      ],
      "total_score": 75.38,
      "score_breakdown": {
        "expiry_urgency": 31.5,
        "inventory_coverage": 23.33,
        "stock_utilization": 10.0,
        "menu_diversity": 10.0,
        "missing_required_penalty": 10.0,
        "base_inventory_score": 64.83,
        "nutrition_fit_score": 100.0
      },
      "coverage_ratio": 0.6667,
      "available_ingredients": ["두부", "달걀"],
      "missing_ingredients": ["새우"],
      "expiring_ingredients": [
        {
          "batch_id": 2,
          "ingredient_id": 48,
          "canonical_name": "두부",
          "consume_by_date": "2026-08-18",
          "days_remaining": 2
        }
      ],
      "used_batch_ids": [2, 4],
      "reason": "두부의 소비기한이 2일 남았습니다. 필수 재료 3개 중 2개를 보유하고 있습니다. 추가로 필요한 재료는 새우입니다."
    }
  ]
}
```

### 9.3 응답 필드

#### 최상위

| 필드 | 타입 | 설명 |
|---|---|---|
| `fridge_id` | integer | 추천 대상 냉장고 ID |
| `user_id` | integer | 적용 사용자 ID |
| `generated_at` | datetime | 추천 생성 시각 |
| `active_inventory_count` | integer | 수량이 남은 활성 재고 수, 만료 재고 포함 |
| `excluded_expired_inventory_count` | integer | 추천 계산에서 제외한 만료 재고 수 |
| `applied_profile` | object/null | 활성 식단 프로필, 없으면 `null` |
| `warnings` | string[] | 의료정보 등 사용자 안내 |
| `score_policy` | object | 적용된 점수 배점과 결합 가중치 |
| `recommendations` | array | 점수 내림차순 추천 결과 |

#### 추천 레시피

| 필드 | 타입 | 설명 |
|---|---|---|
| `recipe_id` | integer | `recipe_catalog.recipe_catalog_id` |
| `recipe_name` | string | 레시피명 |
| `cook_time` | integer/null | 조리 시간(분) |
| `category` | string/null | 반찬, 국, 메인 등의 분류 |
| `steps` | string[] | 조리 순서 |
| `source` | string | 레시피 출처 |
| `nutrition` | object/null | 출처가 확인된 1회 제공량 영양정보 |
| `health_reasons` | string[] | 사용자 목표와 영양값의 비교 결과 |
| `total_score` | number | 최종 추천 점수, 0~100 |
| `score_breakdown` | object | 항목별 점수 |
| `coverage_ratio` | number | 필수 재료 보유 비율, 0~1 |
| `available_ingredients` | string[] | 보유한 필수·선택 재료 |
| `missing_ingredients` | string[] | 부족한 필수 재료 |
| `expiring_ingredients` | array | 7일 이내 소비기한 재료 |
| `used_batch_ids` | integer[] | 우선 사용할 재고 배치 ID |
| `reason` | string | 재고와 소비기한 기반 추천 설명 |

### 9.4 기본 재고 점수

| 항목 | 최대 점수 | 계산 |
|---|---:|---|
| 소비기한 임박도 | 40 | 가장 임박한 사용 재료 70% + 사용 재료 평균 30% |
| 필수 재료 보유율 | 35 | 보유 필수 재료 수 / 전체 필수 재료 수 |
| 재고 활용도 | 15 | 사용할 수 있는 서로 다른 재료 수 / 3, 최대 1 |
| 메뉴 다양성 | 10 | 최근 7일 동일 메뉴 0, 8~30일 5, 그 외 10 |
| 부족 필수 재료 | 감점 | 1개당 10점 |

소비기한 임박도 계수는 다음과 같다.

| 남은 기간 | 계수 |
|---:|---:|
| 오늘 | 1.00 |
| 1일 | 0.95 |
| 2일 | 0.90 |
| 3일 | 0.80 |
| 4일 | 0.65 |
| 5일 | 0.55 |
| 6일 | 0.45 |
| 7일 | 0.35 |
| 8~14일 | 0.15 |
| 15일 이상 또는 날짜 없음 | 0.00 |
| 이미 만료 | 추천 계산에서 제외 |

### 9.5 사용자 상태 적용

`user_diet_profiles.goal`이 없거나 `GENERAL`이면 기본 재고 점수를 그대로 `total_score`로 사용한다.

`WEIGHT_LOSS`, `DIABETES_MANAGEMENT`, `LOW_SODIUM`이면 다음 규칙을 적용한다.

1. `nutrition_source`가 있는 레시피만 후보로 사용한다.
2. 열량·탄수화물은 사용자 최소·최대 범위와 비교한다.
3. 단백질은 사용자 최솟값, 나트륨은 사용자 최댓값과 비교한다.
4. 각 비교 결과의 평균을 `nutrition_fit_score` 0~100으로 계산한다.
5. `total_score = base_inventory_score × 0.7 + nutrition_fit_score × 0.3`을 적용한다.
6. `user_excluded_ingredients`에 등록된 재료가 필수 또는 선택 재료에 하나라도 있으면 레시피를 제외한다.

질환명만으로 고정된 영양 기준을 생성하지 않는다. 사용자 또는 의료 전문가가 정한 프로필 범위만 계산에 사용한다.

식단 프로필 등록·수정 API는 현재 AI 서버에 없다. 백엔드가 `user_diet_profiles`와 `user_excluded_ingredients`를 관리해야 한다.

### 9.6 후보 필터링 규칙

- 활성 상태이고 남은 수량이 0보다 큰 재고만 사용한다.
- 소비기한이 지난 재고는 사용할 수 없는 재고로 제외한다.
- 필수 재료를 하나도 보유하지 않은 레시피는 추천하지 않는다.
- 부족 필수 재료 수가 `max_missing_required`보다 크면 추천하지 않는다.
- 같은 재료의 여러 배치 중 소비기한이 가장 빠른 배치를 `used_batch_ids`에 우선 포함한다.

### 9.7 오류

| 상태 | 조건 |
|---:|---|
| `403` | 사용자 없음, 냉장고 없음 또는 접근 권한 없음 |
| `422` | JSON 필드 누락·범위 오류 |
| `503` | DB 미설정, 추천 테이블 또는 영양 컬럼 누락 |
| `500` | 추천 계산 실패 |

### 9.8 팀 연동용 AI 레시피

#### `POST /api/recipes/ai`

Spring의 `AiRecipeClientDto.Request`와 동일한 camelCase 요청을 받아 가장 적합한 레시피 한 건을 반환한다. 사용자·냉장고 권한과 활성 재고 조회는 Spring이 담당하며 AI 서버에는 표준화된 재고명인 `activeInventories`를 전달한다.

#### Request

```json
{
  "healthCondition": "당뇨",
  "healthGoal": "혈당 관리",
  "activeInventories": ["소고기", "배추", "된장"],
  "additionalIngredients": ["버터", "파프리카"],
  "excludeIngredients": ["설탕", "마요네즈"]
}
```

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---:|---|---|
| `healthCondition` | string/null | N | `null` | 당뇨, 고혈압 등의 건강 상태 |
| `healthGoal` | string/null | N | `null` | 혈당 관리, 체중 감량, 저염 등의 목표 |
| `activeInventories` | string[] | N | `[]` | Spring이 조회한 냉장고의 활성 재고 표준명 |
| `additionalIngredients` | string[] | N | `[]` | 재고 외에 사용할 수 있는 추가 재료 |
| `excludeIngredients` | string[] | N | `[]` | 알레르기·기피 등으로 반드시 제외할 재료 |

#### Response `200 OK`

```json
{
  "title": "소고기 배추 된장국",
  "description": "시원한 배추와 소고기가 어우러진 국 요리입니다.",
  "cookingTime": 25,
  "category": "국/찌개",
  "ingredients": [
    {
      "nameText": "소고기",
      "quantity": 150.0,
      "unit": "g",
      "optional": false
    }
  ],
  "steps": [
    {
      "stepNumber": 1,
      "description": "소고기와 채소를 손질합니다."
    }
  ]
}
```

선정 점수는 필수 재료 보유율 70점, 재고 활용도 10점, 건강 목표 적합도 20점으로 계산한다. 요청의 제외 재료가 필수 또는 선택 재료에 포함된 레시피는 점수와 관계없이 제거한다. 건강 상태와 목표는 현재 당뇨·혈당 관리, 다이어트·체중 감량, 고혈압·저염 키워드를 지원한다. 현재 Spring DTO에는 소비기한이 포함되지 않으므로 이 연동 API의 점수에는 소비기한 임박도를 적용하지 않는다.

이 API는 검증된 로컬 레시피 데이터셋에서 한 건을 선정하는 결정론적 추천 API다. 외부 LLM을 호출해 새로운 조리법을 임의 생성하지 않으므로 API 키 없이 재현 가능하게 동작한다.

| 상태 | 조건 |
|---:|---|
| `404` | 제외 조건을 만족하는 레시피 없음 |
| `422` | 필수 필드 누락 또는 잘못된 타입·범위 |
| `500` | 레시피 선정 처리 실패 |

## 10. DB 연동 책임

### 10.1 OCR 등록 흐름

```text
이미지
  → OCR 및 상품 행 분리
  → 재고 후보 생성
  → ocr_scan / ocr_scan_item (PENDING_REVIEW)
  → 사용자 검토
  → CONFIRM 항목만 inventory_batches 생성
```

### 10.2 추천 조회 흐름

```text
user_id / fridge_id
  → 사용자와 냉장고 접근 권한 확인
  → inventory_batches 활성 재고 조회
  → user_diet_profiles 및 제외 재료 조회
  → recipe_catalog 및 영양정보 조회
  → 필터링·점수 계산
  → 추천 JSON 반환
```

### 10.3 주요 테이블

| 테이블 | 역할 |
|---|---|
| `ingredient_master` | 표준 식재료 ID와 명칭 |
| `ingredient_alias` | 상품명·동의어와 표준 재료 연결 |
| `inventory_batch` | 팀 Spring 백엔드의 우선 재고 테이블 |
| `inventory_batches` | 냉장고의 실제 재고 |
| `ocr_scan` | OCR 실행 단위 및 검토 상태 |
| `ocr_scan_item` | OCR 상품 행별 후보와 검토 결과 |
| `recipe_catalog` | 공용 추천 레시피와 영양정보 |
| `recipe_catalog_ingredients` | 레시피와 표준 재료 연결 |
| `user_diet_profiles` | 사용자별 식단 목표와 영양 범위 |
| `user_excluded_ingredients` | 알레르기·기피 등 제외 재료 |

## 11. HTTP 상태 코드 요약

| 상태 | 의미 |
|---:|---|
| `200` | 조회·처리 성공 |
| `201` | OCR 후보 저장 성공 |
| `400` | 이미지 또는 재고 확정 비즈니스 검증 실패 |
| `403` | 사용자·냉장고 접근 불가 |
| `404` | OCR 스캔 또는 추천 가능한 레시피 없음 |
| `409` | 이미 검토한 OCR 스캔 |
| `413` | 이미지 크기 초과 |
| `415` | 이미지 형식 미지원 |
| `422` | FastAPI 요청 스키마 검증 실패 |
| `500` | OCR·DB 저장·추천 내부 처리 실패 |
| `503` | DB 연결 설정 또는 마이그레이션 누락 |

## 12. 변경 이력

| 버전 | 내용 |
|---|---|
| `0.13.0` | 병합된 프론트 `/ocr` 및 Spring `activeInventories` 계약에 맞춰 연동 API 재구성 |
| `0.12.0` | 팀 camelCase 계약의 `/api/recipes/ai`, 단수형 Spring 재고 테이블 우선 조회 추가 |
| `0.11.0` | 팀 연동용 `/api/ocr/receipt`와 권장 소비기한·카테고리 변환 추가 |
| `0.10.0` | 사용자 식단 프로필, 제외 재료, 레시피 영양정보와 개인화 추천 추가 |
| `0.9.0` | 소비기한·재고 기반 레시피 추천 API 추가 |
| `0.8.0` | OCR 후보 저장 및 재고 확정 API 추가 |
