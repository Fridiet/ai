# Fridiet AI

Python / FastAPI 기반 영수증 OCR, 식재료 정규화, 레시피 추천 및 용기 사진 잔량 추정 서버입니다.
PaddleOCR를 유지하고 GPT 상품명 보정·조건부 보관 기간 제안을 선택적으로 사용합니다.

## 설정 및 실행

Python 3.10 환경을 권장합니다. `.env.example`을 `.env`로 복사하여 서버 전용 값을 설정합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m scripts.bootstrap_data
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env
```

Linux/macOS에서는 `.venv/bin/python`을 사용합니다.
`setup.ps1`로 의존성과 식품 DB를 준비할 수도 있습니다. PaddleOCR 모델은 최초 준비 시
내려받습니다. `OCR_STARTUP_WARMUP=true`이면 서버 시작 때 준비합니다.

외부 공개 시 내부 앱을 직접 노출하지 말고 인증 게이트웨이와 HTTPS를 사용하세요.
`AI_SERVICE_TOKEN`에 본인이 생성한 충분히 긴 값을 설정한 뒤 실행합니다.

```bash
python -m uvicorn app.public_server:create_public_app --factory --host 127.0.0.1 --port 8000 --workers 1 --env-file .env
```

업무 POST 요청은 `X-AI-Service-Token`이 필요합니다. `GET /health`는 비인증 상태 확인이며,
문서·내부 경로는 공개하지 않습니다. 인증 헤더는 앱이 아니라 백엔드에서 전달합니다.
직접 DB 저장 기능은 기본 비활성이고 별도 DB 쓰기 토큰과 스키마 검증이 필요합니다.

## 주요 API와 결과 재사용

- `POST /ocr`, `/api/ocr/receipt`: 영수증 분석과 등록 준비
- `POST /api/recipes/ai`: 재고 기반 레시피 추천
- `POST /api/containers/estimate`: 사진 기반 대략적인 잔량 추정

소비기한 제안은 실제 제품 표시 기한이나 섭취 안전 보장이 아닙니다.
사용자는 보관 상태와 제품 표시를 확인한 뒤 등록해야 합니다.

`OCR_LLM_CACHE_ENABLED=true`이면 검증된 결과를 메모리에서 1시간, 최대 1,024건 재사용합니다.
상품/후보/모델/보관 상태가 다르면 다시 요청하며 적중 결과도 기존 검증을 거칩니다.
날짜가 아니라 보관 기간을 재사용합니다. [캐시 검증 기록](docs/ocr-gpt-result-cache.md)을 참고하세요.

## 테스트와 데이터

```bash
python -m pytest -q
```

개인 영수증 이미지·운영 DB 덤프·대형 외부 식품 DB·학습 산출물·서버 접속 자료는 제외했습니다.
`data_manifest.json` 및 `scripts.bootstrap_data`로 해시 검증된 K-FIND 카탈로그를 준비합니다.
각 원본 데이터의 출처·재배포 조건은 별도로 확인하세요. 이미지 평가에는 사용 권한을 가진 자료와
사람이 검수한 정답이 필요하며 테스트 통과가 전체 OCR 정확도를 의미하지 않습니다.
합성 시드의 예시 비밀번호는 운영용이 아닙니다. 운영 DB에 시드 스크립트를 실행하지 마세요.
실제 서버 운영 문서·SSH 계정·주소·비밀 값은 포함하지 않습니다.

## 관련 저장소

- [Frontend](https://github.com/Fridiet/frontend)
- [Backend](https://github.com/Fridiet/backend)
- [AI](https://github.com/Fridiet/ai)

기존 통합 프로젝트 [euna22/fridiet](https://github.com/euna22/fridiet)의 현재 작업본에서 분리했습니다.
새 이력으로 시작하며 기존 운영 서버의 주소나 배포 상태는 변경하지 않습니다.
