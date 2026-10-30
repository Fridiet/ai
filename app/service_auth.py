"""Legacy database endpoints are internal service operations, never public browser APIs."""
import os
import secrets
from fastapi import Header, HTTPException


def require_service_token(x_ai_service_token: str | None = Header(default=None)):
    expected = os.getenv("AI_SERVICE_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="내부 DB API 인증이 설정되지 않았습니다.")
    if not x_ai_service_token or not secrets.compare_digest(expected, x_ai_service_token):
        raise HTTPException(status_code=401, detail="내부 서비스 인증이 필요합니다.")


def require_db_writer(x_ai_db_token: str | None = Header(default=None)):
    # Legacy local API retains its contract; team adapter always requires a write key.
    if os.getenv('AI_DB_SCHEMA') != 'team':
        return
    if os.getenv('AI_DB_WRITE_ENABLED','false').lower() != 'true':
        raise HTTPException(503, 'DB 저장 기능이 비활성화돼 있습니다.')
    expected=os.getenv('AI_DB_SERVICE_TOKEN','')
    if len(expected)<32:
        raise HTTPException(503, 'DB 저장 인증이 설정되지 않았습니다.')
    if not x_ai_db_token or not secrets.compare_digest(expected,x_ai_db_token):
        raise HTTPException(401, '백엔드 DB 저장 인증이 필요합니다.')
