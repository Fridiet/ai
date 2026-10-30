# 식재료 소비·폐기 및 월간 집계

## 사용자 흐름

- 재료 상세 → 삭제 → **다 먹었어요 / 버렸어요**: 남아 있는 전체 수량을 소진/폐기로 서버에 기록.
- 캘린더 식사 등록 → 사용 재료·양 입력: 부분 소비를 기록하고 잔량을 차감.
- 마이페이지 → 이번 달 소비 패턴 → 식재료 소비·폐기: 로그인 사용자의 기록을 이름·동일 단위별로 표시.
- 브라우저에서도 사유 선택이 작동하도록 네이티브 Alert 대신 화면 안 선택 영역을 사용한다.
- 기존 기기 내 활동 장부는 서버로 자동 이관하지 않는다. 같은 이력을 두 번 집계하지 않기 위해서다.

## API 및 검증

`DELETE /api/inventories/{batchId}?reason=CONSUMED|DISCARDED`

기존 DELETE 경로를 유지하지만 실제 행 삭제 대신 상태 전환한다. 사유를 생략한 구형 요청은 REMOVED로 숨기며 소비/폐기로 추측하지 않는다.
Bearer 사용자 본인 또는 ACCEPTED 공유 멤버만 가능하다. 잔량은 0으로 전환하며 이전 수량·단위·이름은 이력에 보존한다.
동일 사유 재시도는 204, 이미 처리한 재고에 다른 사유를 보내면 409. 배치 행 잠금으로 동시 처리를 직렬화한다.
EXPIRED는 실제 폐기가 아니며 자동 폐기 통계로 잡지 않는다.

`POST /api/calendar/meals`

기존 본문에 선택적 `requestId`(8~64자 영숫자/하이픈)를 추가했다. 최신 프론트는 하나의 입력창에서 재시도할 때 같은 ID를 보낸다.
같은 로그인 사용자·requestId는 첫 저장 결과를 반환하고 재고를 재차 차감하지 않는다. 새 식사는 새 ID를 사용한다.
구형 클라이언트가 ID 없이 보내면 요청 재시도 중복 방지는 보장하지 않는다.
사용자 행 잠금·고유 request_key·재고 배치 잠금으로 동시 요청을 제어한다.

- 냉장고 참여 권한, 선택한 배치의 냉장고 일치, 아직 처리되지 않은 재고인지 확인.
- 수량은 유한한 양수이며 잔량 이하. 단위·단위 유형 일치 필수; kg/g 등을 자동 변환하지 않는다.
- 동일 배치 중복 지정·미래 식사 시각 거부.
- CUSTOM은 숫자 quantityUsed를 보내지 않고 사용 후 상태 customText 입력. 소진 시 `0`.
- 검증 실패는 식사·소비 기록·잔량 모두 저장하지 않는다. 성공은 동일 트랜잭션에서 처리.
- 식사 조회 GET도 쿼리 userId 대신 인증된 사용자 기준으로 반환.
- 단순 잔량 수정은 소비로 추정하지 않는다. 실제 소비 통계에는 식사 사용량 또는 명시적 소진을 입력해야 한다.

## DB 변경

- 신규 `inventory_activity`: id, user_id, fridge_id, batch_id, ingredient_name, event_type,
  unit_type, quantity(nullable), unit, custom_text, occurred_at. 이름/수량/단위를 당시 스냅샷으로 보존한다.
  user_id + occurred_at 인덱스를 사용하며 출처 ID는 값으로 보존한다.
- calendar.request_key VARCHAR(100) nullable UNIQUE: 식사 등록 재시도 식별.
- inventory_batch.remaining_quantity, meal_consumption.quantity_used: DOUBLE NULL 허용으로 변경.
  CUSTOM의 알 수 없는 양을 0이나 1로 꾸미지 않는다.
- inventory_batch.status에 DISCARDED / REMOVED 사용. 목록에서 소진·폐기·제외 상태를 숨긴다.
- 만료 스케줄러는 ACTIVE만 EXPIRED로 바꾼다. 소진·폐기 상태를 덮어쓰지 않는다.

현재 로컬은 Spring 재시작으로 신규 테이블/컬럼이 추가되었다. 기존 NOT NULL 변경은 Hibernate update가 적용하지 않아 별도 수행했다.
AI 폴더에서 `python -m scripts.migrate_inventory_activity`로 예정 SQL 확인, `--apply`로 로컬 적용한다.
스크립트는 실제 SQL 타입을 보존하며 두 컬럼의 NULL 제약만 변경한다. MySQL DDL은 자동 커밋이다.
운영 배포에는 별도 검토된 마이그레이션이 필요하다.

## 월간 응답 추가

`GET /api/reports/monthly`의 `stockActivity` 배열:

```json
[{"ingredientName":"당근","unit":"g","consumedQuantity":40,"discardedQuantity":60,"consumedRecords":1,"discardedRecords":1}]
```

같은 이름·동일 단위 QUANTITY만 합산한다. g/kg/개를 서로 합치지 않는다.
PERCENT는 서로 다른 원래 양에 대한 비율이므로 합산하지 않고 CUSTOM·단위 미상과 함께 건수만 표시한다.
이때 quantity 값은 NULL이며 unit은 `수량 미집계`이다.
이는 사용자가 기록한 양이지 측정된 실제 섭취량은 아니다. 폐기율·영양량·비용 분석은 포함하지 않는다.
소비 시각은 입력한 식사 시각, 전체 소진/폐기는 처리 시각(Asia/Seoul) 기준이다.
기능 도입 전 삭제/소비 이력은 불확실해 소급 변환하지 않는다.

## 검증 및 테스트 데이터

관련 Spring 테스트 21개 및 프론트 타입 검사 통과.
`python -m scripts.verify_inventory_activity`는 합성 육류 계정에 `[검증]소비폐기-*` 배치를 생성한다.
100g 중 40g 소비·60g 폐기, 과다/음수/다른 단위 거부, 식사/폐기 4개 동시 요청 각 1회 반영,
동일 이름의 g/개 분리, CUSTOM 숫자 NULL 저장·월간 건수 표시를 실제 API/DB에서 확인한다.
테스트가 끝난 재고는 모두 소진·폐기 상태로 보존한다. 합성 데이터는 실제 사용자 평가에서 제외한다.
첫 검사 실패 시 남았던 배치 154/155는 각각 UI 폐기/단순 제외로 정리했다. 실제 사용자 재고는 변경하지 않았다.
