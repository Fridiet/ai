"""Use uvicorn app.public_server:create_public_app --factory --workers 1."""
import os
from .public_gateway import PublicGateway


def create_public_app():
    token = os.getenv('AI_SERVICE_TOKEN', '')
    if len(token) < 32:
        raise RuntimeError('AI_SERVICE_TOKEN must have at least 32 characters')
    from .main import app
    db_token=None
    if os.getenv('AI_DB_WRITE_ENABLED','false').lower()=='true':
        if os.getenv('AI_DB_SCHEMA')!='team':
            raise RuntimeError('Public DB writes require the validated team schema')
        from .database import get_engine
        from .team_ocr_database import require_schema
        require_schema(get_engine())
        db_token=os.getenv('AI_DB_SERVICE_TOKEN','')
    return PublicGateway(app, token,
        per_minute=int(os.getenv('AI_PUBLIC_REQUESTS_PER_MINUTE','10')),
        per_day=int(os.getenv('AI_PUBLIC_REQUESTS_PER_DAY','200')),
        concurrency=int(os.getenv('AI_PUBLIC_CONCURRENCY','2')),db_token=db_token,
        queue_timeout=float(os.getenv('AI_PUBLIC_QUEUE_TIMEOUT_SECONDS','30')),
        max_pending=int(os.getenv('AI_PUBLIC_MAX_PENDING','4')))
