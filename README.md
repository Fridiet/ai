# 실행 매뉴얼: 소스 코드 실행 환경 및 실행 방법

## AI 서버

### 1 실행 환경

- Python 3.10
- FastAPI 0.116.1
- Uvicorn 0.35.0
- PaddlePaddle 3.2.0
- PaddleOCR 3.2.0
- PyTorch
- MySQL 연동용 SQLAlchemy 및 PyMySQL
- GPT 후처리·용기 잔량 추정용 OpenAI API

Python 패키지는 `requirements.txt` 및 `requirements-dev.txt`를 통해 설치합니다.
GPU가 없는 CPU 환경에서도 실행할 수 있습니다.
아래 명령은 Windows PowerShell 기준이며, 저장소를 내려받은 폴더명은 `ai`로 가정합니다.

### 2 환경변수 설정

`ai/.env.example`을 같은 위치에 `.env`라는 이름으로 복사합니다.
`ai` 폴더의 상위 폴더에서 다음 명령을 실행합니다.

```powershell
Copy-Item ai/.env.example ai/.env
```

생성한 `.env`에서 다음 값을 설정합니다. `<...>` 부분은 본인의 실제 설정으로 변경합니다.
예제 파일의 나머지 옵션은 필요한 기능에 맞게 유지하거나 조정합니다.

```dotenv
# DB 연결
DATABASE_URL=mysql+pymysql://<DB사용자명>:<DB비밀번호>@localhost:3306/fridiet?charset=utf8mb4
DATABASE_AUTO_CREATE=false

# 서버 간 인증
AI_SERVICE_TOKEN=<32자 이상의 서버 간 인증 토큰>

# OpenAI API
OPENAI_API_KEY=<OpenAI API 키>
OPENAI_OCR_MODEL=gpt-5.4-mini
OPENAI_CONTAINER_MODEL=gpt-5.4-mini

# 상품명 보정 및 조건부 보관 기간 제안
OCR_LLM_ENABLED=true
OCR_LLM_TIMEOUT_SECONDS=8
OCR_SHELF_LIFE_LLM_ENABLED=true

# 검증된 GPT 결과 재사용
OCR_LLM_CACHE_ENABLED=true

# OCR 모델 시작 준비 및 CPU 설정
OCR_STARTUP_WARMUP=true
OCR_CPU_THREADS=2

# 공개 게이트웨이의 동시 처리 및 대기열 설정
AI_PUBLIC_CONCURRENCY=2
AI_PUBLIC_MAX_PENDING=4
AI_PUBLIC_QUEUE_TIMEOUT_SECONDS=30

# 분당·일일 요청 횟수 제한 해제
AI_PUBLIC_REQUESTS_PER_MINUTE=0
AI_PUBLIC_REQUESTS_PER_DAY=0

# 직접 DB 저장은 기본 비활성
AI_DB_WRITE_ENABLED=false
```

`AI_SERVICE_TOKEN`은 백엔드에 설정한 값과 같아야 합니다.
DB 주소·이름은 실제 사용 중인 DB에 맞추고, 비밀번호의 특수문자는 연결 URL에 맞게 인코딩합니다.
OpenAI API 키와 서버 인증 토큰은 서버 전용이며 프론트엔드에 포함하지 않습니다.

직접 DB 저장을 활성화하려면 같은 DB와 팀 스키마의 호환성을 검증하고,
별도의 `AI_DB_SERVICE_TOKEN` 및 DB 권한을 설정해야 합니다. 기본 상태에서는 활성화하지 않습니다.

### 3 실행 방법

`ai` 폴더의 상위 폴더에서 다음 명령을 실행하여 가상환경과 의존성을 준비합니다.

```powershell
cd ai
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

상품명 매칭에 사용하는 식품 카탈로그를 준비합니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.bootstrap_data
```

로컬 개발용 서버는 다음 명령으로 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env
```

실행 후 접속 주소는 다음과 같습니다.

- 상태 확인: <http://localhost:8000/health>
- 로컬 API 문서: <http://localhost:8000/docs>

처음 실행할 때에는 OCR 모델 다운로드 및 초기 준비 시간이 발생할 수 있습니다.
`OCR_STARTUP_WARMUP=true`이면 서버 시작 시 모델을 미리 준비합니다.
`.env`는 실행 명령의 `--env-file .env`를 통해 불러옵니다.

외부에 공개할 때는 내부 앱을 직접 노출하지 말고 인증 게이트웨이와 별도의 HTTPS 프록시를 사용합니다.
게이트웨이는 아래 명령으로 실행하며 워커는 1개를 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.public_server:create_public_app --factory --host 127.0.0.1 --port 8000 --workers 1 --env-file .env
```

공개 게이트웨이는 업무 요청에 `X-AI-Service-Token` 헤더를 요구하며,
API 문서와 내부 경로는 외부에 노출하지 않습니다. 인증 헤더는 백엔드에서 전달합니다.

| 요청 | 기능 |
|---|---|
| `GET /health` | 서버 상태 확인 |
| `POST /api/ocr/receipt` | 영수증 분석 및 재고 등록 후보 생성 |
| `POST /api/recipes/ai` | 재고 기반 레시피 추천 |
| `POST /api/containers/estimate` | 용기 사진 기반 대략적인 잔량 추정 |

AI 폴더에서 다음 명령으로 테스트합니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Linux/macOS에서는 위 명령의 `.\.venv\Scripts\python.exe` 대신 `.venv/bin/python`을 사용합니다.

GPT 결과 재사용은 검증된 판단만 메모리에 최대 1시간·1,024건 보관하며,
상품·후보·모델·보관 조건이 달라지면 다시 요청합니다. 날짜가 아니라 보관 기간을 재사용합니다.
상세 내용은 [GPT 결과 캐시 문서](docs/ocr-gpt-result-cache.md)를 참고하세요.

관련 저장소: [프론트엔드](https://github.com/Fridiet/frontend) · [백엔드](https://github.com/Fridiet/backend)

> 실제 비밀 키·운영 DB 덤프·개인 영수증 이미지는 저장소에 포함하지 않습니다.
> 외부 데이터는 출처와 이용 조건을 확인하여 준비합니다.
> AI가 제안하는 보관 기간은 실제 제품 표시 소비기한이나 섭취 안전 보장이 아닙니다.
> 제품 표시와 보관 상태를 확인한 뒤 등록해야 합니다.
