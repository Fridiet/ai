# OCR 통합 테스트 — 2026-09-12

사용자 승인 후 프론트·백엔드 수정과 로컬 DB 쓰기를 진행했다. 기존 OCR 마무리 문서의 AI-only 범위는 그 이전 작업에 대한 기록이다.

## 호출 경로

- 프론트 `receiptApi.ts`: 로그인 토큰으로 백엔드 `POST /api/ocr/receipt`에 multipart `file` 전송. AI 주소로 직접 요청하지 않는다.
- 백엔드 `ReceiptOcrController`: 인증 필수, JPEG/PNG/WebP 및 10MB 제한. `ai.base-url`의 `/api/ocr/receipt`로 전달한다. 사용자 토큰은 AI에 전달하지 않는다.
- 분석은 재고를 저장하지 않는다. 사용자가 확인한 항목만 기존 `POST /api/inventories`로 저장한다. `reviewCandidates`와 `reviewNotes`는 분석 응답에서 유지한다.
- Spring 파일 제한 10MB, 전체 multipart 요청 제한 11MB. application.example.yml과 로컬 application.yml에 반영했다.

## 실제 검증

- 프론트 TypeScript 검사 통과, 백엔드 Gradle test 통과. 새 컨트롤러 테스트는 비로그인·빈 파일·잘못된 형식 거부, 확인 후보 보존, 내부 오류 비노출을 확인한다.
- 테스트 계정 `receipt.integration.20260912@example.test`의 사용자 ID 9, 기본 냉장고 ID 10을 회원가입 API로 생성했다.
- IMG00131 PNG를 인증된 백엔드로 전달해 7개 항목과 확인 후보 5개를 수신했다. 체다치즈 907g을 기존 재고 등록 API로 저장했고 배치 ID 164, ingredientMasterId 668로 다시 조회했다.
- 테스트 재고의 소비기한 2026-09-19와 냉장 보관은 개발용으로 지정한 값이다. 영수증에서 읽은 소비기한이나 안전성을 보장하는 값이 아니다.
- 브라우저에서 계정 로그인 및 냉장고의 체다치즈 907g 표시를 확인했다. 이후 화면에서 별도 이마트 영수증의 양파·대파 등 OCR 결과와 경고가 표시되는 것을 확인했다. 모든 항목의 등록 버튼을 누른 것은 아니다.
- 기존 데이터 삭제나 스키마 마이그레이션 없이 진행했다. 실행 중 백엔드는 `SPRING_JPA_HIBERNATE_DDL_AUTO=none`으로 시작했다. 커밋·푸시하지 않았다.

## 사용자 테스트

http://localhost:8081 에 접속해 로그인 후 냉장고 → 재료 추가 → 영수증으로 입력을 사용한다. 결과에서 이름·단위·수량·보관 위치·소비기한을 확인하고 필요한 항목만 등록한다. AI 문서는 http://localhost:8000/docs 에서 볼 수 있다.
