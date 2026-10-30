"""Read-only comparison of seeded personas through the running AI endpoint.

Avoid Spring preview: it records SHOWN and would contaminate the cold-start account.
"""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
import requests
from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine
from scripts.seed_shared_taste_demo import KEY

ROOT = Path(__file__).resolve().parents[1]


def compare():
    engine = _resolve_engine(None)
    assert engine.url.host in {'localhost', '127.0.0.1', '::1'}
    now = datetime.now().replace(microsecond=0)
    rows = []
    inventory_hashes = []
    with engine.connect() as db:
        manifest = json.loads(db.execute(text('SELECT manifest_json FROM ai_demo_seed_manifest WHERE seed_key=:k'), {'k':KEY}).scalar_one())
        for account in manifest['accounts']:
            uid = account['user_id']
            login = requests.post('http://127.0.0.1:8080/api/auth/login', json={
                'email':account['email'], 'password':'local-demo-placeholder'}, timeout=20)
            login.raise_for_status()
            assert login.json()['mainFridgeId'] == manifest['shared_fridge_id']
            user = db.execute(text('SELECT main_fridge_id, health_condition, health_goal FROM user WHERE user_id=:u'), {'u':uid}).mappings().one()
            assert user['health_condition'] is None and user['health_goal'] is None
            inv = [dict(x) for x in db.execute(text('SELECT ingredient_name AS ingredientName, remaining_quantity AS remainingQuantity, unit, consume_by_date AS consumeByDate FROM inventory_batch WHERE fridge_id=:f AND status=\'ACTIVE\' ORDER BY batch_id'), {'f':user['main_fridge_id']}).mappings()]
            inv = json.loads(json.dumps(inv, default=str))
            inventory_hashes.append(hashlib.sha256(json.dumps(inv,sort_keys=True).encode()).hexdigest())
            history = [dict(x) for x in db.execute(text('SELECT recipe_key AS recipeKey, recipe_name AS recipeName, event_type AS eventType, created_at AS occurredAt FROM recipe_recommendation_feedback WHERE user_id=:u ORDER BY created_at DESC'), {'u':uid}).mappings()]
            recent = [x for x in history if x['occurredAt'] > now-timedelta(days=30)]
            if account['email']=='devnone@test.com':
                assert not history, 'Cold-start account must remain history-free'
            payload = dict(healthCondition=None, healthGoal=None, activeInventories=[i['ingredientName'] for i in inv], inventories=inv,
                           targetIngredients=[], additionalIngredients=[], excludeIngredients=[])
            responses = {}
            for label, events in [('without_history', []), ('with_history', recent)]:
                request = dict(payload, recentFeedback=json.loads(json.dumps(events,default=lambda v:v.isoformat())))
                response = requests.post('http://127.0.0.1:8000/api/recipes/ai', json=request,timeout=120)
                response.raise_for_status()
                responses[label] = response.json()
            rows.append(dict(email=account['email'], user_id=uid, fridge_id=user['main_fridge_id'],
                             history_count=len(history), recent_history_count=len(recent),
                             first_event=min((x['occurredAt'] for x in history),default=None),
                             last_event=max((x['occurredAt'] for x in history),default=None),
                             inventory_count=len(inv), **responses))
    assert len(set(inventory_hashes))==1
    assert len({r['without_history']['title'] for r in rows})==1
    assert rows[-1]['without_history']['title']==rows[-1]['with_history']['title']
    report = dict(synthetic_demo_only=True, measured_at=now.isoformat(), same_fridge=True,
                  same_inventory=True, inventory_sha256=inventory_hashes[0], same_health_settings=True,
                  history_window_days=30, results=rows,
                  scope='Real DB snapshots and AI HTTP responses. Backend login checked. No recommendation feedback written during comparison.')
    output = ROOT/'output/shared_taste_demo'
    output.mkdir(parents=True,exist_ok=True)
    (output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    lines = ['# 동일 냉장고의 행동 이력별 추천 비교', '', f'측정: {now} / 합성 데이터 시연', '',
             f"세 계정은 냉장고 {manifest['shared_fridge_id']}와 {len(inv)}종의 같은 재고를 공유한다. 건강 상태·목표는 모두 미설정이다.", '',
             '| 계정 | 전체 행동 / 최근 30일 | 이력 제거 추천 | 이력 반영 추천 | 점수 |',
             '|---|---:|---|---|---:|']
    for r in rows:
        lines.append(f"| {r['email']} | {r['history_count']} / {r['recent_history_count']} | {r['without_history']['title']} | {r['with_history']['title']} | {r['with_history']['score']:.2f} |")
    lines += ['', '## 해석과 한계', '',
              '- 비밀번호는 세 계정 모두 요청한 개발용 비밀번호로 로그인 검증했다.',
              '- 8월 1일부터 저장·조리 피드백을 합성했다. 실제 사용자의 식사 기록이나 학습 성능 증거가 아니다.',
              '- 최근 30일만 추천에 들어간다. 그 이전 8월 기록은 DB에 있으나 현재 입력에서는 제외된다.',
              '- 동일 입력에서 행동 이력만 제거한 대조군을 함께 요청했다. 점수는 추천 내부 점수이며 정확도나 만족 확률이 아니다.',
              '- 신규 계정의 점수는 상대 순위 기반이고 개인화 계정은 유사도 환산·반복 보정 기반이므로, 계정 간 점수 크기를 직접 비교하면 안 된다. 생선 계정과 신규 계정의 1위가 같다는 결과를 숨기거나 강제로 바꾸지 않았다.',
              '- Spring preview는 SHOWN을 기록하므로 비교에는 DB에서 구성한 동일 계약의 AI HTTP 요청을 사용했다. 신규 계정의 이력은 0건으로 유지했다.',
              '- 사용자가 앱에서 추천을 열면 SHOWN이 쌓여 이후 결과가 달라질 수 있다. 저장·조리 및 반복 회피 규칙도 추천에 영향을 준다.',
              '- 재고는 현시점 테스트용으로 채운 공통 재고다. 합성 조리 이력과 재고 소모 장부를 연결하지 않았으며 월말 실제 소비량 검증용 자료는 아니다.',
              '', '## 계정별 추천 근거']
    for r in rows:
        lines += ['', f"### {r['email']}", '', *['- '+s for s in r['with_history']['reasons']],
                  '', '점수 구성: `'+json.dumps(r['with_history']['scoreBreakdown'],ensure_ascii=False)+'`']
    (output/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps([dict(email=r['email'],history=r['history_count'],recent=r['recent_history_count'],
                          baseline=r['without_history']['title'],personalized=r['with_history']['title'],
                          breakdown=r['with_history']['scoreBreakdown']) for r in rows],ensure_ascii=False,indent=2))


if __name__=='__main__':
    compare()
