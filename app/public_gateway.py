"""Small fail-closed, authenticated edge for PC demo and future cloud hosting."""
import asyncio
import secrets
import time
import re
from collections import deque
from starlette.responses import JSONResponse

POST_PATHS = {'/ocr', '/api/ocr/receipt', '/api/recipes/ai', '/api/containers/estimate'}


class PublicGateway:
    def __init__(self, application, token, per_minute=10, per_day=200, concurrency=2, db_token=None,
                 queue_timeout=30, max_pending=4):
        if len(token) < 32 or per_minute < 0 or per_day < 0 or concurrency < 1:
            raise ValueError('Authentication, nonnegative request limits and positive concurrency are required')
        self.application, self.token = application, token
        if db_token is not None and (len(db_token)<32 or secrets.compare_digest(db_token,token)):
            raise ValueError('Strong DB writer token required')
        self.db_token=db_token
        self.per_minute, self.per_day = per_minute, per_day
        self.calls = deque()
        self.slots = asyncio.Semaphore(concurrency)
        if queue_timeout <= 0 or max_pending < 0:
            raise ValueError('Positive queue timeout and nonnegative pending capacity required')
        self.queue_timeout, self.max_pending = queue_timeout, max_pending
        self.capacity = concurrency + max_pending
        self.pending = 0

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            return await self.application(scope, receive, send)
        if scope['type'] != 'http':
            return await send({'type': 'websocket.close', 'code': 1008})
        path, method = scope['path'], scope['method']

        async def reject(code, message):
            await JSONResponse({'detail': message}, status_code=code)(scope, receive, send)

        if path == '/health' and method == 'GET':
            return await JSONResponse({'status': 'ok'})(scope, receive, send)
        db_path=path=='/ocr/inventory-candidates/save' or bool(re.fullmatch(r'/ocr/scans/[1-9]\d{0,18}/confirm',path))
        if path not in POST_PATHS and not (db_path and self.db_token):
            return await reject(404, 'Not found')
        if method != 'POST':
            return await reject(405, 'Method not allowed')
        tokens = [v for k,v in scope['headers'] if k.lower() == b'x-ai-service-token']
        if len(tokens) != 1 or not secrets.compare_digest(tokens[0], self.token.encode()):
            return await reject(401, 'AI service authentication required')
        if db_path:
            keys=[v for k,v in scope['headers'] if k.lower()==b'x-ai-db-token']
            if len(keys)!=1 or not secrets.compare_digest(keys[0],self.db_token.encode()):
                return await reject(401, 'Backend DB writer authentication required')
        now = time.monotonic()
        while self.calls and self.calls[0] <= now-86400:
            self.calls.popleft()
        if ((self.per_day and len(self.calls) >= self.per_day)
                or (self.per_minute and sum(t > now-60 for t in self.calls) >= self.per_minute)):
            return await reject(429, 'Demo request limit reached; retry later')
        limit = 256*1024 if path == '/api/recipes/ai' or path.endswith('/confirm') else 11*1024*1024
        lengths = [v for k,v in scope['headers'] if k.lower() == b'content-length']
        if lengths:
            try:
                if len(lengths) != 1 or int(lengths[0]) < 0:
                    raise ValueError()
                if int(lengths[0]) > limit:
                    return await reject(413, 'Request too large')
            except ValueError:
                return await reject(400, 'Invalid content length')
        # Bound waiters as well as active work; never create an unbounded OCR backlog.
        if self.pending >= self.capacity:
            return await reject(429, 'AI server busy; retry later')
        self.pending += 1
        acquired = False
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=self.queue_timeout)
            acquired = True
        except asyncio.TimeoutError:
            return await reject(429, 'AI server busy; retry later')
        finally:
            if not acquired:
                self.pending -= 1
        try:
            if self.per_minute or self.per_day:
                self.calls.append(now)
            async def read_body():
                chunks, size = [], 0
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return None
                    chunk = message.get('body', b'')
                    size += len(chunk)
                    if size > limit:
                        raise OverflowError()
                    chunks.append(chunk)
                    if not message.get('more_body', False):
                        return b''.join(chunks)
            try:
                body = await asyncio.wait_for(read_body(), timeout=15)
            except OverflowError:
                return await reject(413, 'Request too large')
            except asyncio.TimeoutError:
                return await reject(408, 'Upload timed out')
            if body is None:
                return
            consumed = False
            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {'type':'http.request', 'body':body, 'more_body':False}
                return await receive()
            await self.application(scope, replay, send)
        finally:
            self.slots.release()
            self.pending -= 1
