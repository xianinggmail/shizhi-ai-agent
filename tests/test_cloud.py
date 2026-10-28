import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store
from cloud_app import CloudGateway


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(self.temp.name)
        self.gateway=CloudGateway(self.store,'https://trial.example','test-invitation-code-123456')
    def tearDown(self):
        self.gateway.close();self.temp.cleanup()
    def request(self,path,method='GET',payload=None,**headers):
        body=json.dumps(payload).encode() if payload is not None else b''
        env={'PATH_INFO':path,'REQUEST_METHOD':method,'HTTP_HOST':'trial.example','REMOTE_ADDR':'1.2.3.4','CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body),**headers}
        result=[]
        chunks=self.gateway(env,lambda status,hs:result.extend([int(status.split()[0]),dict(hs)]))
        return (*result,b''.join(chunks))
    def login(self):
        status,headers,_=self.request('/api/lan-login','POST',{'code':'test-invitation-code-123456'},HTTP_ORIGIN='https://trial.example')
        self.assertEqual(status,200)
        for flag in ('Secure','HttpOnly','SameSite=Strict'):self.assertIn(flag,headers['Set-Cookie'])
        return headers['Set-Cookie'].split(';')[0]
    def test_unauthenticated_routes(self):
        for route in ('/api/config','/api/books','/api/papers','/app.js','/papers.js'):
            self.assertEqual(self.request(route)[0],401)
        self.assertEqual(self.request('/healthz',HTTP_HOST='internal')[0],200)
        self.assertIn('邀请访问码'.encode(),self.request('/')[2])
        self.assertEqual(self.request('/api/lan-login','POST',{'code':'bad'})[0],401)
    def test_authenticated_get_and_write_protection(self):
        cookie=self.login()
        status,_,body=self.request('/api/config',HTTP_COOKIE=cookie)
        self.assertEqual(status,200)
        config=json.loads(body)
        self.assertTrue(config['cloud_shared']);self.assertNotIn(self.temp.name,body.decode())
        self.assertEqual(self.request('/api/books',HTTP_COOKIE=cookie)[0],200)
        self.assertEqual(self.request('/api/invalid','POST',{},HTTP_COOKIE=cookie)[0],403)
        self.assertEqual(self.request('/api/invalid','POST',{},HTTP_COOKIE=cookie,HTTP_ORIGIN='https://trial.example')[0],403)
        self.assertEqual(self.request('/api/invalid','POST',{},HTTP_COOKIE=cookie,HTTP_ORIGIN='https://trial.example',HTTP_X_STUDY_TOKEN=config['token'])[0],404)
    def test_foreign_hosts_and_spoofed_headers(self):
        for host in ('evil.example','127.0.0.1','localhost'):
            self.assertEqual(self.request('/api/config',HTTP_HOST=host)[0],403)
        self.assertEqual(self.request('/api/lan-login','POST',{'code':'test-invitation-code-123456'},HTTP_ORIGIN='https://evil.example')[0],403)
        self.assertEqual(self.request('/api/config',HTTP_X_FORWARDED_FOR='127.0.0.1')[0],401)
    def test_invalid_lengths_and_restarted_sessions(self):
        cookie=self.login()
        self.assertEqual(self.request('/api/books','POST',{},HTTP_COOKIE=cookie,HTTP_ORIGIN='https://trial.example',CONTENT_LENGTH='-1')[0],413)
        self.gateway.access.sessions.clear()
        self.assertEqual(self.request('/api/config',HTTP_COOKIE=cookie)[0],401)


if __name__=='__main__':unittest.main()
