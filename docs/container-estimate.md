# 용기 사진 잔량 추정 API

`POST /api/containers/estimate` (AI 서버 8000)

사진 한 장에서 **용기 전체 내부 용적 대비 내용물이 차지하는 비율**을 대략 추정한다.
처음 구매량 대비 비율, 무게, 정확한 계측값이 아니다. 재고 DB를 조회하거나 변경하지 않는다.

## 설정

AI/.env에 OPENAI_API_KEY를 설정하고 서버를 재시작한다. 키는 Git에 올리지 않는다.
OPENAI_CONTAINER_MODEL 기본값은 gpt-5.4-mini이며 계정에서 사용 가능한 이미지 및
Structured Outputs 지원 모델로 변경 가능하다.
AI_SERVICE_TOKEN은 32자 이상 서버 간 비밀값이며 X-AI-Service-Token 헤더로 전달한다.
프론트에 이 키들을 넣지 않는다. Spring 프록시/촬영 화면 연동은 이번 API 범위 밖이다.

```powershell
curl.exe -X POST http://localhost:8000/api/containers/estimate `
  -H "X-AI-Service-Token: $env:AI_SERVICE_TOKEN" `
  -F "file=@C:/photos/container.jpg"
```

multipart/form-data의 file 필드에 JPEG/PNG/WebP 한 장을 전송한다.
10MB·2천만 픽셀 이하, 애니메이션 불가. 서버에서 EXIF 방향을 적용하고
1536px 이내 JPEG로 재인코딩하여 메타데이터를 제외한 이미지를 OpenAI로 전송한다.
로컬 원본 저장 없음. Responses 요청은 store=false이며 OpenAI의 별도 데이터 처리
정책까지 무보관을 보장한다는 의미는 아니다. 실제 호출은 API 사용료가 발생한다.

## 응답 예시 (설명용이며 실제 사진 측정값 아님)

```json
{
  "status": "estimated",
  "remainingPercent": 45,
  "lowerPercent": 35,
  "upperPercent": 55,
  "reason": "내용물 높이와 용기 모양을 고려하면 절반보다 조금 적어 보입니다.",
  "requiresConfirmation": true,
  "basis": "container_volume"
}
```

5% 단위 추정. lower/upper는 모델이 제시한 대략적인 범위이며 통계적 신뢰구간이 아니다.
불투명·가려짐·잘림·여러 용기·용기 없음 등 판단 불가 시 status=unable_to_estimate,
세 percent 필드는 null. 빈 용기 0%와 판별 불가 null은 구별한다.
사용자는 반드시 확인 후 별도의 기존 재고 수정 API를 사용해야 한다.

오류: 400 손상 이미지, 401 서비스 인증 실패, 413 크기 초과, 415 형식 미지원,
422 필수 입력 누락, 502 OpenAI 연결/응답 실패, 503 키 미설정·사용량 제한,
504 45초 네트워크 타임아웃. 외부 오류 내용/키/사진은 응답에 노출하지 않는다.
자동 재시도는 하지 않는다. 인터넷 공개 시 Spring 인증·사용자별 호출 제한을 앞에 둔다.

## 검증

`python -m pytest tests/test_container_estimate.py -q`

모의 OpenAI 응답으로 HTTP 계약·범위·오류·인증을 검증한다. 이는 시각 정확도 검증이 아니다.
실측 0/25/50/75/100% 투명 용기 사진과 불투명/다중 용기 사진으로 별도 실제 평가가 필요하다.

설정 후 서버를 재시작하고 AI 폴더에서 실제 호출을 검증할 수 있다 (유료 API 호출):

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_container_estimate "C:/photos/container.jpg"
```

이 명령은 서버 간 토큰을 .env에서 읽으므로 명령줄에 비밀값을 붙여넣을 필요가 없다.

### 2026-09-29 검증 상태

- gpt-5.4-mini로 실제 실행 중인 AI API를 통해 OpenAI 호출 성공(HTTP 200).
- 프로젝트 로고 이미지는 unable_to_estimate, percent 3개 필드 null,
  requiresConfirmation=true로 반환되어 비대상 이미지 거절 경로를 확인했다.
- tests/test_container_estimate.py와 tests/test_api.py 합계 31개 통과.
- 사용자가 제공한 `ChatGPT 이미지 2026년 9월 29일 오전 12_57_38.png`로
  실행 중인 API 호출 성공(HTTP 200): status=estimated, remainingPercent=30,
  lowerPercent=25, upperPercent=35, requiresConfirmation=true,
  basis=container_volume. 용기 이미지 → OpenAI → 수치 JSON 반환 경로를 확인했다.
- 해당 이미지의 실측 용적 정답은 없으므로 30%의 정확도나 일반 사진에 대한 성능은
  검증하지 않았다. 로고 응답 성공이나 모의 응답 테스트 역시 정확도의 증거가 아니다.

공식 문서: https://developers.openai.com/api/docs/guides/images-vision
https://developers.openai.com/api/docs/guides/structured-outputs
