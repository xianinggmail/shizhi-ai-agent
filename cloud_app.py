"""Authenticated HTTPS gateway for a single, invited shared trial workspace.

The original application remains bound to loopback. Run exactly one WSGI worker.
"""
import atexit
import http.client
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from app import Store, make_server, MAX_UPLOAD, ROOT
from deepseek_ai import DeepSeekExtractor
from lan_access import LANAccess, PairingError


class CloudGateway:
    def __init__(self, store, origin, access_code):
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('PUBLIC_ORIGIN must be an HTTPS origin without a path.')
        if not isinstance(access_code, str) or len(access_code) < 20:
            raise ValueError('SHARE_ACCESS_CODE must contain at least 20 characters.')
        self.origin = origin.rstrip('/')
        self.host = parsed.netloc
        self.access = LANAccess(access_code)
        self.store = store
        self.backend = make_server(store)
        self.thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.backend.shutdown()
        self.backend.server_close()
        self.thread.join()
        self.store.pool.shutdown(wait=True)
        self.store.job_pool.shutdown(wait=True)

    def __call__(self, env, start_response):
        def reply(status, data, mime='application/json; charset=utf-8', extra=()):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            headers = [('Content-Type', mime), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'),
                       ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                       ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"), *extra]
            start_response(str(status)+' '+http.client.responses[status], headers)
            return [body]

        path = env.get('PATH_INFO', '/')
        method = env.get('REQUEST_METHOD', 'GET')
        # Health checks reveal no app data and work before the external domain is routed.
        if path == '/healthz' and method == 'GET':
            return reply(200, {'ok': True})
        if env.get('HTTP_HOST') != self.host:
            return reply(403, {'error': '访问地址不在允许列表中。'})
        if env.get('HTTP_ORIGIN') not in (None, '', self.origin):
            return reply(403, {'error': '不允许跨站请求。'})
        if method not in ('GET', 'POST'):
            return reply(405, {'error': '不支持此操作。'})
        authenticated = self.access.authorized(env.get('HTTP_COOKIE'))
        if not authenticated and method == 'GET':
            if path == '/':
                return reply(200, (ROOT/'static/cloud-login.html').read_bytes(), 'text/html; charset=utf-8')
            if path in ('/style.css', '/lan-login.js'):
                mime = 'text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8'
                return reply(200, (ROOT/'static'/path[1:]).read_bytes(), mime)
        if method == 'POST' and path == '/api/lan-login':
            try:
                size = int(env.get('CONTENT_LENGTH') or '0')
                if not 0 < size <= 1024:
                    return reply(413, {'error': '访问码请求过大或为空。'})
                payload = json.loads(env['wsgi.input'].read(size))
                # Ignore client-supplied forwarding headers for rate limiting.
                session = self.access.login(env.get('REMOTE_ADDR', 'unknown'), payload.get('code') if isinstance(payload, dict) else None)
            except PairingError as exc:
                return reply(exc.status, {'error': '访问码错误或尝试过于频繁，请稍后再试。'})
            except (ValueError, UnicodeError):
                return reply(400, {'error': '请求格式无效。'})
            return reply(200, {'ok': True}, extra=[('Set-Cookie', 'shizhi_lan='+session+'; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')])
        if not authenticated:
            return reply(401, {'error': '请先输入邀请访问码。'})
        if method == 'POST' and env.get('HTTP_ORIGIN') != self.origin:
            return reply(403, {'error': '写入请求缺少正确来源。'})
        try:
            size = int(env.get('CONTENT_LENGTH') or '0')
            limit = MAX_UPLOAD if path == '/api/books' else 50000
            if not 0 <= size <= limit:
                return reply(413, {'error': '请求过大。PDF最大30MB。'})
            body = env['wsgi.input'].read(size) if size else None
            headers = {name: env[key] for name, key in [('Content-Type', 'CONTENT_TYPE'), ('X-Filename', 'HTTP_X_FILENAME'), ('X-Study-Token', 'HTTP_X_STUDY_TOKEN')] if key in env}
            if method == 'POST':
                headers['Origin'] = 'http://127.0.0.1:'+str(self.backend.server_port)
            conn = http.client.HTTPConnection('127.0.0.1', self.backend.server_port, timeout=120)
            try:
                query = env.get('QUERY_STRING', '')
                conn.request(method, path+('?' + query if query else ''), body, headers)
                response = conn.getresponse()
                result = response.read()
                status, mime = response.status, response.getheader('Content-Type')
            finally:
                conn.close()
            if path == '/api/config' and status == 200:
                config = json.loads(result)
                config['data_dir'] = '云端独立体验空间'
                config['cloud_shared'] = True
                result = json.dumps(config, ensure_ascii=False).encode()
            return reply(status, result, mime or 'application/json; charset=utf-8')
        except (OSError, ValueError):
            return reply(502, {'error': '服务暂不可用，请稍后重试。'})


_application = None
_lock = threading.Lock()


def application(env, start_response):
    global _application
    with _lock:
        if _application is None:
            origin = os.environ.get('PUBLIC_ORIGIN') or ('https://'+os.environ['RENDER_EXTERNAL_HOSTNAME'])
            # Require explicit cloud storage; never reuse the owner's local data.
            store = Store(os.environ['STUDY_DATA_DIR'], extractor=DeepSeekExtractor(ROOT))
            _application = CloudGateway(store, origin, os.environ['SHARE_ACCESS_CODE'])
            atexit.register(_application.close)
    return _application(env, start_response)
