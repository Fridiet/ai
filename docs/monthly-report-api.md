# 앱 월간 소비 패턴 (기록 기반 첫 버전)

추가 구현: [식재료 소비·폐기 명세](inventory-activity.md)의 `stockActivity`가 응답에 추가되었다.
아래는 초기 저장·요리 기록 지표의 명세이며, 소비량 신규 집계 범위와 DB 변경은 추가 명세를 따른다.

마이페이지 → **이번 달 소비 패턴**에서 조회한다. 웹 경로는 `/monthly-report`.
현재 달 중간 결산과 이전 달 조회를 지원한다. 로그인 만료, 로딩, 빈 기록, 실패·재시도 상태를 표시한다.

## API

`GET /api/reports/monthly?month=2026-09`

- Bearer 인증 필수. 로그인 사용자 본인 기준이며 userId/fridgeId를 입력받지 않는다.
- month 생략 시 한국 시간 현재 달. YYYY-MM 형식, 2000-01부터 현재 달까지 허용한다.
- 잘못된/미래 월: 400, 미인증: 401.
- 기존 recipe_recommendation_feedback만 조회한다. 이번 단계에 추가 DB 컬럼/테이블은 없다.
- Spring에서 직접 집계하며 AI 서버를 호출하거나 추정 수치를 생성하지 않는다.

응답 예시 (육류 합성 계정, 2026-09-10 검증 시 요리 완료 10건):

```json
{
  "month": "2026-09",
  "timezone": "Asia/Seoul",
  "partialMonth": true,
  "cookedCount": 10,
  "savedRecipeCount": 0,
  "activeDays": 10,
  "uniqueCookedRecipes": 3,
  "topRecipes": [
    {"recipeKey": "LOCAL:fridiet-009", "recipeName": "닭고기 채소볶음", "count": 6}
  ],
  "dailyActivity": [
    {"date": "2026-09-01", "cookedCount": 1, "savedCount": 0}
  ],
  "summary": "선택한 달에 10일 활동했고, 3가지 요리에 완료 기록을 남겼어요.",
  "notes": ["집계 기준 및 한계 안내"]
}
```

위 목록은 형식 설명을 위한 축약 예시다. 실제 topRecipes는 최대 5개, dailyActivity는 지난달 전체 날짜 또는 이번 달 오늘까지의 모든 날짜를 반환한다.

## 집계 정의

- 시간 범위: 한국 시간 월 1일 00:00 이상, 다음 달 1일 00:00 미만. 이번 달은 현재 시각 미만.
- cookedCount: COOKED의 레시피 고유 ID + 날짜 기준 중복 제거 건수.
- savedRecipeCount: 해당 월 SAVED에 등장하는 서로 다른 레시피 수. 수동 작성 레시피 제외.
- activeDays: 중복 제거된 저장 또는 조리 기록이 있는 날짜 수.
- uniqueCookedRecipes: COOKED에 등장하는 서로 다른 레시피 수.
- topRecipes: 요리 완료 건수 내림차순, 동률은 이름·키 순서. 원본키가 같으면 이름이 바뀌어도 같은 요리.
- 과거 키 없는 기록은 이름으로 묶는다. 키 없는 기록과 키 있는 기록 간 동일성은 추측하지 않는다.
- 과거 중복 저장은 월 내 가장 최근 기록 날짜에 1건으로 집계한다.
- 날짜별 0은 기록 없음이다. 실제 요리/섭취하지 않았음을 의미하지 않는다.
- 이 기능은 저장·조리 기록 요약이다. 실제 섭취량, 영양량, 재고 소비량, 폐기율, 지출/절약 금액은 표시하지 않는다.
- 합성 계정의 숫자는 시연 데이터이며 실제 성과가 아니다. 실제 평가/전체 서비스 통계에서는 제외해야 한다.

## 검증

Spring `MonthlyReportServiceTest`: 한국 시간 월 전환, 시작/끝 경계, 중복·동명이인 레시피,
노출 제외, 구형 이름 기록, 빈 기록, 인증, 잘못된 월, 윤년을 검사한다.

AI 폴더에서 `python -m scripts.verify_monthly_reports`를 실행하면 합성 4계정으로 로그인해
API와 실제 DB의 중복 제거 집계를 비교한다. 다른 userId 쿼리가 결과에 영향을 주지 않는지도 확인한다.
이 검증은 행동 기록을 추가하지 않는다.

다음 확장에는 소비/폐기 발생 이력과 수량·단위를 먼저 연결해야 한다. 월말 자동 알림·영양 분석·AI 코멘트는 현재 범위에 포함되지 않는다.
