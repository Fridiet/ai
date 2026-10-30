# AI 요청 횟수 제한 해제 (2026-10-27)

사용자 요청으로 AWS fridiet-ai의 시연용 분당 10회·최근 24시간 200회 제한을 해제했다.
`AI_PUBLIC_REQUESTS_PER_MINUTE=0`, `AI_PUBLIC_REQUESTS_PER_DAY=0`은 해당 한도를 해제한다.
명시하지 않으면 기존 기본값 10/200이 유지된다. 인증, 업로드 크기 제한,
동시 처리 2개 제한은 유지한다. 백엔드 로그인 요청의 nginx 제한도 변경하지 않는다.
사용량 제한 해제는 OpenAI 요금이나 제공자 자체 한도를 없애지는 않는다.

검증: 게이트웨이 단위 테스트 16개 통과. 실제 서버에 잘못된 이미지 12개를 연속
전송해 요청 한도 429가 아닌 이미지 검증 400을 확인했다. 미인증 401,
외부 health 200을 확인했다. 실제 영수증 인식이나 DB 저장 테스트는 하지 않았다.

배포 전 백업(서버 root 전용):
`/var/backups/fridiet/disable-demo-quotas-20261027-140754/`
복구하려면 이 디렉터리의 `public_gateway.py`를
`/opt/fridiet-ai/app/public_gateway.py`로, `fridiet-ai.env`를
`/etc/fridiet-ai.env`로 복원하고 환경 파일 권한 600을 유지한 뒤
`sudo systemctl restart fridiet-ai`를 실행한다.
환경 파일에는 비밀 키가 있으므로 출력하거나 공유하지 않는다.
