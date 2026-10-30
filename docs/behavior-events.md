# 사용자 행동 기록 — 1단계

목표는 사용자별 개인화 및 향후 앱의 이번 달 소비 패턴 화면에 사용할 기록의 신뢰성을 높이는 것이다.
이번 변경에는 월말 결산 API·화면 자체나 모델 재학습이 포함되지 않는다.

## 변경된 계약

레시피 고유 ID `recipeKey`는 `원본출처:원본레시피ID` 형식이다. 예: `LOCAL:fridiet-009`.
사용자의 저장 레시피 DB PK인 `recipeId`와 구분한다. 레시피명이 같거나 바뀌어도 같은 원본을 식별한다.

| API | 동작 |
|---|---|
| POST /api/recipes/ai/preview | 응답에 recipeKey 포함, SHOWN 기록 생성 |
| POST /api/recipes/ai/save | 기존 저장 본문에 recipeKey 전달, 저장 성공 시 SAVED 기록. 같은 사용자·키는 기존 recipeId 반환 |
| POST /api/recipes/ai | AI 추천 후 저장하는 기존 경로도 recipeKey 보존·중복 저장 방지 |
| POST /api/recipes/ai/feedback | recipeKey, recipeName, fridgeId, eventType 전달. COOKED/DISMISSED만 허용, 성공 204 |

피드백 요청 예시:

```json
{
  "recipeKey": "LOCAL:fridiet-009",
  "recipeName": "닭고기 채소볶음",
  "fridgeId": 7,
  "eventType": "COOKED"
}
```

ID는 예시를 하드코딩하지 않고 실제 추천 응답에서 그대로 전달한다.
로그인 사용자에게 추천된 적 없는 recipeKey는 400, 소유하거나 ACCEPTED 상태로 참여하지 않은 냉장고는 403이다.
SAVED를 피드백 API로 임의 전송하면 400이다. 저장이 성공한 트랜잭션에서만 자동 생성한다.

구형 클라이언트·이력 호환 때문에 recipeKey는 현재 nullable이다. 없는 이력은 유일하게 매칭되는 이름만 AI 개인화에 사용한다.
공급된 키가 잘못됐을 때는 이름으로 되돌아가 추측하지 않는다.
구형 클라이언트의 키 없는 저장 요청은 저장 레시피 행의 중복 방지를 보장하지 않으므로 최신 프론트 사용이 필요하다.

## 중복 기준 및 개인화 조회

- SAVED: 사용자·원본 레시피당 한 번. 재전송 시 새 저장 레시피를 만들지 않는다.
- COOKED / DISMISSED: 사용자·원본 레시피·한국 날짜당 한 번.
- SHA-256 event_key의 DB 유니크 제약과 사용자 행 잠금으로 동시 요청도 방어한다.
- SHOWN은 노출/다양성 처리용이며 긍정 취향으로 학습·집계하지 않는다.
- AI 요청은 최근 30일 SAVED/COOKED 전체와 최근 50개 행동을 합치고 동일 행은 중복 제거한다.
- 따라서 노출을 반복해도 30일 안의 긍정 이력이 최근 50건에서 밀려나 사라지지 않는다.
- DB의 과거 이력은 삭제하지 않는다. 30일은 AI 추천 입력 범위이며 월별 집계 보존 기한이 아니다.
- 같은 냉장고를 공유하더라도 취향 이력 조회 기준은 로그인 user_id이다.

## DB 변경 및 기존 데이터

- recipe.recipe_key: VARCHAR(160), NULL 허용.
- recipe_recommendation_feedback.recipe_key: VARCHAR(160), NULL 허용.
- recipe_recommendation_feedback.event_key: VARCHAR(64), NULL 허용, UNIQUE.

현재 개발 환경은 Spring의 스키마 update 설정으로 재시작 시 추가되었다.
운영 환경에서는 동일 변경을 별도 DB 마이그레이션으로 적용해야 하며 update 설정에 의존하지 않는다.

AI 폴더에서 `python -m scripts.backfill_recipe_keys`로 기존 자료 연결 예정 건수를 확인한다.
`--apply`를 붙이면 로컬 DB의 유일하게 식별되는 AI 저장 레시피 및 행동 기록만 갱신한다.
반복 실행 가능하고 이름·원본 행을 삭제하거나 변경하지 않는다. 이름이 모호하면 NULL로 남긴다.
2026-09-10 적용: 저장 레시피 24건, 행동 기록 443건 연결. 각각 1건, 80건은 불명확하여 보존.
기존 event_key=NULL 이력은 소급 삭제/중복 정리하지 않았으며 신규 중복 방지 기준을 과거 전체에 보장하지 않는다.

## 검증

- AI 전체 테스트와 프론트 TypeScript 검사.
- Spring 테스트: 키 전달, 최근 50건 밖의 저장 이력 전달, 조리 재전송, 저장 재전송, 허용하지 않는 이벤트/키 거부.
- `python -m scripts.verify_behavior_events`: 합성 육류 사용자에게 저장 4건·조리 4건 동시 요청.
  실제 MySQL에서 동일 recipeId 및 당일 조리 기록 1건 확인. 합성 계정에 테스트 기록을 남기며 manifest에 식별자를 기록한다.
- `python -m scripts.compare_personalization_accounts`: 동일 냉장고에서 기존 3계정과 신규 계정 비교.
  현재 추천 응답과 동일 입력의 로컬 AI 계산을 대조한다. 테스트 중 노출 이벤트가 추가되므로 화면 결과는 실행마다 바뀔 수 있다.

## 다음 단계: 앱 내 이번 달 소비 패턴

월별 저장·조리 기록 API와 앱 화면의 첫 버전 구현은 [월간 API 명세](monthly-report-api.md)에 기록했다.

이번 단계의 COOKED는 사용자가 누른 **요리 완료 기록**이다. 실제 섭취 횟수·양이나 재고 소비량의 증거는 아니다.
동일 요리를 하루 두 번 해도 현재는 1건이므로 화면에 “식사 횟수”로 표시하면 안 된다.

우선 월별 요리 완료 기록 수, 자주 만든 요리, 저장한 레시피, 활동한 날짜를 표시할 수 있다.
사용자 행동과 공유 냉장고 재고 통계를 구분하고 한국 시간 월 시작 이상·다음 달 시작 미만으로 집계해야 한다.
식재료 소비량·폐기율·절약 금액은 각각 소비/폐기 이력, 기준 수량/단위 및 가격 데이터가 있어야 하며 현재 클릭 기록으로 추정하지 않는다.
실제 한 달 섭취 열량·당류 등도 섭취량·인분 정보가 없으면 확정값으로 제공하지 않는다.
합성/검증 사용자는 실제 사용자 성과 지표에서 제외해야 한다.
