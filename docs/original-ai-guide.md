# PaddleOCR FastAPI

한국어/영어 이미지에서 텍스트와 좌표를 추출하고, 상품 행을 식약처 가공식품 DB 및 식재료 사전과 매칭하는 로컬 CPU OCR API입니다.

## 최초 설정 (PowerShell)

```powershell
cd AI
.\setup.ps1
```

`setup.ps1`은 가상환경 생성, Python 패키지 설치, K-FIND SQLite 준비를 한 번에 수행합니다. 이미 준비된 항목은 다시 만들지 않습니다.

## 실행

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env
```

`.env`는 자동으로 읽히지 않으므로 `--env-file .env`를 지정합니다. 이미 설정한 PowerShell 환경 변수는 `.env`보다 우선합니다. 다른 장치에서 접속해야 할 때만 호스트를 `0.0.0.0`으로 변경합니다.

개발용 보조 수량 인식을 사용하려면 `.env`에서 `OCR_SECONDARY_COUNTS_ENABLED=true`, `OCR_NUMERIC_CELLS_ENABLED=true`, `OCR_HEADER_RECOVERY_ENABLED=true`로 설정하고 서버를 재시작합니다. 추가 인식 시간이 발생하며, 확인 안내가 있는 항목을 검증 없이 자동 저장하면 안 됩니다. 이 옵션들은 `/ocr`와 `/api/ocr/receipt`에 적용되며 DB 저장용 기존 엔드포인트에는 적용되지 않습니다.

- Swagger UI: http://localhost:8000/docs
- 상태 확인: `GET /health`
- 프론트엔드 영수증 분석: `POST /ocr`
- 같은 영수증 분석 API 별칭: `POST /api/ocr/receipt`
- OCR 원문·좌표 디버깅: `POST /ocr/raw`
- 이미지에서 DB 연동용 재고 후보 생성: `POST /ocr/inventory-candidates`
- 이미지에서 재고 후보를 생성하고 MySQL에 저장: `POST /ocr/inventory-candidates/save`
- 냉장고 재고와 소비기한 기반 레시피 추천: `POST /recommendations`
- 팀 계약 기반 단일 AI 레시피 추천: `POST /api/recipes/ai`

백엔드 연동용 응답 필드, DB 매핑 규칙, 보관 방식과 오류 응답은 [`docs/API_SPEC.md`](docs/API_SPEC.md)를 참고하세요.

첫 OCR 요청에서는 PP-OCRv5 모델을 내려받으므로 시간이 걸릴 수 있습니다.

```powershell
curl.exe -X POST http://localhost:8000/ocr -F "file=@sample.png"
```

프론트엔드가 호출하는 `POST /ocr`는 `file`을 받고 표준 재료명, 수량, 보관 방식, 카테고리와 권장 소비기한을 반환합니다. `/api/ocr/receipt`도 같은 응답을 제공하며, 원본 OCR 텍스트와 좌표가 필요할 때만 `/ocr/raw`를 사용합니다.

```powershell
curl.exe -X POST http://localhost:8000/api/ocr/receipt -F "file=@receipt.png"
```

`consumeByDate`는 영수증에 표시된 실제 기한을 OCR한 값이 아닙니다. 영수증 처리일을 구매일로 보고 품목 카테고리와 추천 보관 방식에 따른 MVP 규칙으로 계산한 권장 날짜이므로, 재고를 확정하기 전에 사용자가 수정할 수 있어야 합니다.

`POST /ocr/inventory-candidates`는 다음 순서로 처리합니다.

1. 신뢰도가 낮거나 중복된 OCR 결과 제거
2. 텍스트 좌표의 세로 겹침을 이용한 상품 행 분리
3. 가격, 수량, 단위, 행사 문구를 제거한 상품명 정규화
4. K-FIND 가공식품 상품명·제조사·식품유형 우선 매칭
5. 가공식품에서 찾지 못한 경우에만 `app/data/ingredients_ko.json`의 원재료 사전 매칭

음식 상품 응답 예시는 다음과 같습니다.

```json
{
  "product_name": "농심 오징어짬뽕",
  "food_type": "유탕면",
  "standard_food_name": "라면",
  "classification": "processed_food",
  "manufacturer": "농심",
  "source": "seed_product_catalog"
}
```

## K-FIND 가공식품 DB

식품의약품안전처 식품영양성분 DB의 가공식품 XLSX를 내려받아 로컬 검색 DB로 변환할 수 있습니다.

### 공식 XLSX로 초기화

K-FIND 사이트는 다운로드 전에 소속과 활용 목적을 입력해야 합니다.

1. https://various.foodsafetykorea.go.kr/nutrient/general/down/list.do 에서 `가공식품 DB` 다운로드
2. 파일을 `app/data/external/kfind_processed_foods_20260604.xlsx`에 배치
3. `.\setup.ps1` 실행

다른 위치의 XLSX를 사용할 수도 있습니다.

```powershell
.\setup.ps1 -XlsxPath path\to\가공식품.xlsx
```

### 비공개 GitHub Release로 빠르게 초기화

현재 DB를 압축하면 GitHub Release나 파일 스토리지에 올릴 배포 파일과 SHA-256이 생성됩니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.package_catalog
```

`dist/products.sqlite3.zip`을 비공개 저장소의 `kfind-2026-06-04` Release에 업로드합니다. `data_manifest.json`에는 다음과 같이 저장소, 태그, 파일명과 SHA-256만 기록합니다. 인증 토큰은 저장하지 않습니다.

```json
"github_release": {
  "repository": "euna22/fridiet",
  "tag": "kfind-2026-06-04",
  "asset": "products.sqlite3.zip"
}
```

팀원은 최초 한 번 GitHub CLI로 로그인합니다.

```powershell
gh auth login
gh auth status
.\setup.ps1
```

`setup.ps1`은 로그인된 `gh`를 이용해 비공개 Release 파일을 다운로드하고 두 SHA-256과 SQLite 무결성을 검사합니다. GitHub CLI가 없거나 저장소 권한이 없으면 설치 방법과 로그인 안내를 출력하고 중단합니다.

공개 파일 스토리지를 별도로 사용할 때만 `KFIND_DATABASE_URL`, `KFIND_ARCHIVE_SHA256`, `KFIND_DATABASE_SHA256` 환경 변수를 사용할 수 있습니다.

원본 XLSX와 생성된 SQLite 및 배포용 ZIP은 용량이 크므로 Git에서 제외됩니다. `PRODUCT_CATALOG_DB_PATH`로 다른 SQLite 경로를 지정할 수 있습니다. `MFDS_API_KEY`를 설정하면 로컬 DB에서 찾지 못한 제품을 식품안전나라 `I1250` API에서 추가로 조회합니다.

K-FIND 데이터를 활용하는 서비스와 산출물에는 출처를 `식품영양성분 데이터베이스`로 표기해야 합니다. API 매칭 결과에도 사용 버전이 `source: K-FIND:YYYY-MM-DD` 형태로 포함됩니다.

매칭 결과의 `source`는 `K-FIND:*`, `mfds:I1250`, `seed_product_catalog`, `ingredient_dictionary` 중 하나입니다. `classification`이 `processed_food`이면 완제품 분류이고, `ingredient`이면 식재료 사전 fallback입니다.

환경 변수로 `OCR_LANG`, `OCR_VERSION`, `OCR_DEVICE`, `MAX_UPLOAD_BYTES`, `PRODUCT_CATALOG_DB_PATH`, `MFDS_API_KEY`, `MFDS_TIMEOUT_SECONDS`, `INGREDIENT_DICTIONARY_PATH`를 변경할 수 있습니다.

## OCR 결과 MySQL 저장

DB 연결과 저장 구현은 `AI` 폴더 안에만 있습니다. OCR 오인식이 실제 재고를 오염시키지 않도록 결과는 `inventory_batch`가 아닌 `ocr_scan`, `ocr_scan_item`에 `PENDING_REVIEW` 상태로 먼저 저장합니다.

1. [`.env.example`](.env.example)을 참고해 `DATABASE_URL`을 환경 변수로 설정합니다.
2. MySQL에서 [`migrations/001_create_ocr_tables.sql`](migrations/001_create_ocr_tables.sql), [`migrations/002_add_ocr_inventory_trace.sql`](migrations/002_add_ocr_inventory_trace.sql), [`migrations/003_create_recipe_catalog.sql`](migrations/003_create_recipe_catalog.sql), [`migrations/004_add_personalized_nutrition.sql`](migrations/004_add_personalized_nutrition.sql)을 순서대로 한 번씩 실행합니다.
3. `python -m scripts.seed_ingredient_master`로 AI 식재료 사전을 `ingredient_master`에 중복 없이 초기 적재합니다.
4. 서버 실행 후 Swagger에서 `POST /ocr/inventory-candidates/save`에 이미지, `fridge_id`, `created_by`를 입력합니다.
5. 응답의 후보를 검토한 뒤 `POST /ocr/scans/{ocr_scan_id}/confirm`으로 확정 또는 제외합니다.

```powershell
$env:DATABASE_URL="mysql+pymysql://fridiet_ai:password@localhost:3306/Fridiet?charset=utf8mb4"
.\.venv\Scripts\python.exe -m scripts.seed_ingredient_master
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

로컬에서만 `DATABASE_AUTO_CREATE=true`를 설정하면 AI 소유 테이블을 자동 생성할 수 있습니다. 공유 DB에서는 SQL 마이그레이션을 적용하는 방식을 권장합니다. 비밀번호는 코드나 `.env.example`에 실제 값으로 저장하지 않습니다.

확정 API는 `CONFIRM` 항목만 기존 `inventory_batches`에 `source_type=OCR`로 저장하고 `REJECT` 항목은 재고로 만들지 않습니다. 저장된 모든 OCR 항목에 대한 결정을 한 번에 보내야 하며, 이미 검토한 스캔을 다시 확정하면 `409`를 반환합니다.

### 개발용 데이터

로컬 `Fridiet` DB의 Spring 백엔드 테이블(`user`, `fridge`, `fridge_member`, `inventory_batch`)에 로그인 가능한 개발용 사용자, 공유 냉장고, 멤버 관계와 기본 재고를 넣으려면 다음 명령을 실행합니다. `DATABASE_URL`이 없으면 프로젝트의 Spring `application.yml`에서 MySQL 연결 설정을 읽습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_development_data
```

- 소유자: `owner@example.invalid` / `local-development-placeholder`
- 멤버: `member@example.invalid` / `local-development-placeholder`
- 초대 코드: `DEV001`
- 기본 재고: 두부, 우유, 달걀, 양파, 당근
- 소유자 건강 목표: `WEIGHT_LOSS`
- 멤버 건강 상태/목표: `DIABETES` / `DIABETES_MANAGEMENT`

스크립트는 여러 번 실행해도 동일 사용자·냉장고·재고를 중복 생성하지 않습니다. 개발 환경에서만 사용하세요.

## 소비기한 기반 레시피 추천

개인 저장 레시피인 기존 `recipes`와 구분하여, 전체 사용자가 검색·추천에 공통으로 사용하는 `recipe_catalog`, `recipe_catalog_ingredients`를 추가합니다. 적재 데이터는 [`app/data/recipes_ko.json`](app/data/recipes_ko.json)을 사용하며, 식약처 `COOKRCP01`의 공식 영양정보는 원문 값만 보존하고 출처가 검증되지 않은 자체 레시피에는 임의 영양값을 만들지 않습니다.

마이그레이션 적용 후 레시피를 적재합니다. 스크립트는 `(source, source_recipe_id)`를 기준으로 갱신하므로 여러 번 실행해도 레시피가 중복되지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_recipe_catalog
```

추천 점수는 100점 만점으로 계산합니다.

| 기준 | 배점 | 계산 방식 |
|---|---:|---|
| 소비기한 임박도 | 40 | 가장 임박한 재료 70% + 사용 재료 평균 30% |
| 필수 재료 보유율 | 35 | 보유 필수 재료 수 / 전체 필수 재료 수 |
| 재고 활용도 | 15 | 사용할 수 있는 서로 다른 재료 3개 이상이면 만점 |
| 메뉴 다양성 | 10 | 최근 7일 동일 메뉴 0점, 8~30일 5점, 그 외 10점 |
| 부족 필수 재료 | 감점 | 부족한 필수 재료 1개당 10점 감점 |

소비기한이 지난 재고는 음식 안전을 위해 추천 계산에서 제외하며 `excluded_expired_inventory_count`로 건수를 반환합니다. 소비기한이 없는 재고는 사용할 수 있는 재고로 보되 임박도 점수는 주지 않습니다.

```powershell
curl.exe -X POST "http://localhost:8000/recommendations" `
  -H "Content-Type: application/json" `
  -d '{"fridge_id":2,"user_id":2,"limit":5,"max_missing_required":1}'
```

### 사용자 상태 반영

`user_diet_profiles`에 사용자별 식단 목표와 영양 범위를 저장하고, `user_excluded_ingredients`에는 알레르기·기피·종교·의료 사유로 제외할 표준 재료를 저장합니다. 별도 AI API를 추가하지 않고 기존 `POST /recommendations`가 `user_id`로 프로필을 조회합니다.

- 프로필이 없거나 `GENERAL`: 기존 소비기한·재고 점수를 그대로 사용
- `WEIGHT_LOSS`, `DIABETES_MANAGEMENT`, `LOW_SODIUM`: 영양정보가 검증된 레시피만 대상으로 `기존 점수 70% + 영양 적합도 30%` 적용
- 제외 재료가 필수 또는 선택 재료에 있으면 추천에서 제거
- 고정된 질환별 수치를 코드에 넣지 않고, 사용자가 설정한 열량·탄수화물·단백질·나트륨 범위와 비교

현재 데이터셋은 Fridiet 자체 레시피 20개와 식품의약품안전처 `COOKRCP01` 레시피 941개를 합친 961개 레시피로 구성됩니다. 메뉴명·재료·조리 순서·영양 정보를 네트워크 없이 추천과 모델 학습에 사용합니다. 체크인된 데이터셋은 수량이 명시된 원본 재료 10,798개 구간의 표준명 매핑률이 100%이며, 같은 레시피 안에서는 표준 재료가 중복되지 않습니다. 원문 재료 파싱률이 70% 미만이거나 실제 정량 재료로 확인되는 제목 핵심 재료가 누락된 레시피는 갱신 단계에서 격리합니다.

### 식약처 레시피 데이터셋 갱신

`scripts/import_mfds_recipes.py`는 `COOKRCP01` 데이터를 페이지 단위로 내려받아 현재 `app/data/recipes_ko.json`에 병합합니다. 동일한 `source_recipe_id`는 갱신하고 Fridiet 자체 레시피는 유지합니다. 열량·탄수화물·단백질·지방·나트륨은 API 원문 값만 저장하며 빈 값을 추정하지 않습니다.

재료는 `ingredients_ko.json`의 표준명·별칭과 일치하고 공식 원문에 수량과 단위가 명시된 경우만 저장합니다. `소금 약간`처럼 수치가 없는 항목이나 마스터에 없는 항목은 임의 변환하지 않으며, 추천에 쓸 수 있는 필수 재료 또는 조리 순서가 없는 레시피는 제외합니다. API가 선택 재료를 구분하지 않으므로 조미료와 일반 고명은 Fridiet 추천 정책상 `optional`로 변환합니다. 레시피명에서 확인되는 식재료는 `primary`로 표시하고, 제목에 식재료가 없으면 식약처 원본의 첫 필수 재료를 주재료로 사용합니다.

전체 원본을 받은 뒤에는 재료 사전 동기화와 재파싱을 순서대로 실행합니다. 동기화 스크립트는 기존 표준명을 유지하면서 부위명·오타·표기 변형을 별칭으로 연결하고, 실제로 새로운 식재료만 표준 항목으로 추가합니다. `--dry-run`으로 추가·별칭·미매칭 건수를 먼저 확인할 수 있습니다.

```powershell
python -m scripts.sync_recipe_ingredient_dictionary --dry-run
python -m scripts.sync_recipe_ingredient_dictionary
python -m scripts.reparse_recipe_ingredients
```

먼저 인증키 없이 공식 샘플 5건의 변환 결과를 확인할 수 있습니다. `--dry-run`은 파일을 바꾸지 않습니다.

```powershell
cd AI
python -m scripts.import_mfds_recipes --sample --dry-run
```

전체 데이터는 식품안전나라에서 OpenAPI 이용 신청 후 발급된 키를 환경 변수로 넣어 갱신합니다. 인증키는 `.env`나 Git에 커밋하지 않습니다.

```powershell
cd AI
$env:MFDS_API_KEY="발급받은_인증키"
python -m scripts.import_mfds_recipes
```

개발 중 일부만 별도 파일로 검증하려면 다음처럼 실행합니다.

```powershell
python -m scripts.import_mfds_recipes `
  --limit 100 `
  --output output/recipes_ko.preview.json
```

팀 연동용 `POST /api/recipes/ai`는 Spring의 `AiRecipeClientDto.Request`와 같은 camelCase 요청을 그대로 사용합니다. Spring이 전달한 `activeInventories`와 추가 재료를 가용 재료에 포함하며 제외 재료가 들어간 레시피는 후보에서 제거합니다. `healthCondition`과 `healthGoal`은 당뇨·체중 감량·저염 목표의 영양 적합도에 반영합니다. 이 API는 Spring이 이미 조회한 재고명을 사용하므로 AI 서버가 사용자·냉장고 DB를 다시 조회하지 않습니다.

```powershell
curl.exe -X POST http://localhost:8000/api/recipes/ai `
  -H "Content-Type: application/json" `
  -d '{"healthCondition":"당뇨","healthGoal":"혈당 관리","activeInventories":["소고기","배추","된장"],"additionalIngredients":["버터","파프리카"],"excludeIngredients":["설탕","마요네즈"]}'
```

- 공공데이터포털: https://www.data.go.kr/data/15060073/openapi.do
- 식품안전나라 API 상세: https://www.foodsafetykorea.go.kr/api/newDatasetDetail.do?svc_no=COOKRCP01

### Two-Tower 학습 추천 모델

`POST /api/recipes/ai`는 PyTorch로 학습된 `two_tower_pytorch_v1` 모델이 있으면 모델 점수를 추천 순위의 주 점수로 사용합니다. 사용자 냉장고 재료, 재료별 소비기한 긴급도와 건강 목표를 Query Tower에서 64차원 벡터로 변환하고, 레시피 재료·영양·분류·조리 시간과 레시피 ID를 Recipe Tower에서 같은 크기의 벡터로 변환합니다. 모델 파일이 없거나 불러오지 못하면 기존 규칙 기반 점수로 자동 전환합니다.

학습 context는 `recipes_ko.json`에서 결정적으로 생성하므로 Git에 별도 학습 데이터 파일을 커밋하지 않아도 재현할 수 있습니다. 기본 생성기는 961개 레시피마다 32개 context를 만들어 총 30,752개의 합성 샘플을 구성합니다. 재료 부족, 일반 재고, 재료 풍부, 소비기한 임박 시나리오와 일반·체중 감량·혈당 관리·저염 목표를 조합하며, 재료 구성이 비슷한 레시피 4개를 hard negative로 학습합니다. 합성 냉장고에는 해당 레시피의 `primary` 재료가 항상 포함됩니다.

각 샘플에는 `source=constraint_persona_v2`가 기록됩니다. 합성 데이터는 서비스 초기의 cold start 사전학습용이며 실제 사용자 개인화 성능을 증명하는 데이터는 아닙니다. 실제 추천 로그가 쌓이면 별도의 시간순 테스트 데이터로 최종 검증해야 합니다.

```powershell
cd AI

.\.venv\Scripts\python.exe -m scripts.build_training_data

.\.venv\Scripts\python.exe -m scripts.train_two_tower `
  --epochs 200 `
  --batch-size 64 `
  --patience 200 `
  --hard-negative-weight 0.5 `
  --seed 42

.\.venv\Scripts\python.exe -m scripts.evaluate_two_tower --split test
```

기본 모델 평가 결과는 다음과 같습니다.

검증·테스트에는 네 가지 냉장고 시나리오가 각각 961개씩 균등하게 포함됩니다.

| 모델 | Recall@1 | Recall@5 | Recall@10 | NDCG@5 | MRR |
|---|---:|---:|---:|---:|---:|
| 재료 겹침 baseline | 0.6733 | 0.9251 | 0.9649 | 0.8154 | 0.7855 |
| PyTorch Two-Tower v1 | 0.8010 | 0.9378 | 0.9654 | 0.8779 | 0.8631 |

학습 결과는 `app/models/two_tower_pytorch_v1.pt`, 특징 vocabulary와 모델 설정은 `two_tower_pytorch_v1.metadata.json`, 평가 결과는 `two_tower_pytorch_v1.metrics.json`에 저장됩니다. API 응답의 `scoreBreakdown.two_tower_score`와 `scoreBreakdown.two_tower_similarity`에서 모델 적용 여부와 점수를 확인할 수 있습니다.

일시적으로 기존 규칙 추천만 검증하려면 AI 서버 실행 전에 다음 환경 변수를 설정합니다.

```powershell
$env:TWO_TOWER_ENABLED="false"
```

## 테스트

```powershell
pytest -q
```

### 영수증 OCR 정확도 평가

`scripts/evaluate_receipts.py`는 하나의 정답 데이터셋으로 OCR 상품명 재현율, 상품 행 재현율, 식재료 선별 F1, 표준명 정확도를 각각 계산합니다. 기본 샘플은 PaddleOCR 결과를 고정한 후처리 회귀 테스트이며 실제 이미지 데이터셋을 지정하면 PaddleOCR부터 전체 흐름을 평가합니다.

```powershell
cd AI
python -m scripts.evaluate_receipts
```

상세 JSON 결과는 기본적으로 `output/receipt_evaluation.json`에 저장됩니다. 실제 영수증 데이터셋은 다음처럼 실행합니다.

```powershell
python -m scripts.evaluate_receipts `
  --dataset evaluation/receipt_ground_truth.local.json `
  --min-ocr-text-recall 0.90 `
  --min-product-row-recall 0.90 `
  --min-food-selection-f1 0.90 `
  --min-standard-name-accuracy 0.90
```

지정한 최소 기준보다 낮으면 종료 코드 `1`을 반환하므로 CI 회귀 검사에도 사용할 수 있습니다. 데이터 작성 규칙과 지표 정의는 `evaluation/README.md`에 정리되어 있습니다.

### OCR → 개인화 추천 통합 스모크 테스트

세 서버를 실행한 상태에서 실제 영수증을 OCR하고, 표준화된 재료명을 Spring 추천 API의 임시 재료로 전달해 로그인 사용자의 DB 건강 프로필까지 반영되는지 한 번에 확인할 수 있습니다. JWT는 명령행 인자로 남기지 않고 환경 변수로만 전달합니다.

기본 실행은 재고 DB를 변경하지 않습니다.

```powershell
cd AI
$env:FRIDIET_AUTH_TOKEN="로그인_JWT"
python -m scripts.e2e_ocr_recommendation `
  --image "C:\path\to\receipt.jpg" `
  --fridge-id 1
```

건강 목표가 설정된 테스트 계정에서 영양 적합도까지 필수로 검사하려면 `--require-personalization`을 추가합니다. OCR 결과를 실제 재고로 저장하는 검증은 결과의 재료명·소비기한·보관 위치를 확인한 뒤에만 `--register`를 붙여 실행합니다.

```powershell
python -m scripts.e2e_ocr_recommendation `
  --image "C:\path\to\receipt.jpg" `
  --fridge-id 1 `
  --register
```
