import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store,make_server
from lan_access import LANAccess,PairingError,private_address

class LANTests(unittest.TestCase):
    def test_private_address_only(self):
        for address in ('192.168.1.5','10.1.2.3','172.31.0.2'):
            self.assertEqual(private_address(address),address)
        for address in ('127.0.0.1','8.8.8.8','172.32.0.1','::1','invalid'):
            with self.assertRaises(ValueError):private_address(address)

    def test_expiration_and_rate_limit(self):
        now=[1.0];access=LANAccess('testing',clock=lambda:now[0])
        for _ in range(5):
            with self.assertRaises(PairingError) as error:access.login('client','wrong')
            self.assertEqual(error.exception.status,401)
        with self.assertRaises(PairingError) as error:access.login('client','testing')
        self.assertEqual(error.exception.status,429)
        now[0]+=301
        cookie='shizhi_lan='+access.login('client','testing')
        self.assertTrue(access.authorized(cookie))
        self.assertFalse(access.authorized('shizhi_lan=forged'))
        now[0]+=28800
        self.assertFalse(access.authorized(cookie))

    def test_http_pairing_and_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory)
            server=make_server(store,host='0.0.0.0',lan_address='192.168.50.2',pair_code='testing')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            port=server.server_port
            def request(path,method='GET',data=None,**headers):
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                body=json.dumps(data).encode() if data is not None else None
                conn.request(method,path,body,headers={'Host':f'192.168.50.2:{port}',**headers})
                response=conn.getresponse();result=(response.status,dict(response.getheaders()),response.read());conn.close();return result
            try:
                self.assertIn('连接电脑'.encode(),request('/')[2])
                for path in ('/api/config','/api/books','/api/papers','/app.js'):
                    self.assertEqual(request(path)[0],401)
                self.assertEqual(request('/api/config',Host=f'127.0.0.1:{port}')[0],200)
                self.assertEqual(request('/',Host=f'evil.example:{port}')[0],403)
                self.assertEqual(request('/api/lan-login','POST',{'code':'testing'},Origin='http://evil.example')[0],403)
                self.assertEqual(request('/api/lan-login','POST',{'code':'wrong'})[0],401)
                self.assertEqual(request('/api/lan-login','POST',[])[0],401)
                status,headers,_=request('/api/lan-login','POST',{'code':'testing'})
                self.assertEqual(status,200)
                cookie=headers['Set-Cookie']
                for attribute in ('HttpOnly','SameSite=Strict','Max-Age=28800'):self.assertIn(attribute,cookie)
                cookie=cookie.split(';')[0]
                status,_,body=request('/api/config',Cookie=cookie)
                config=json.loads(body);self.assertEqual(status,200);self.assertTrue(config['lan_enabled'])
                self.assertEqual(request('/api/books',Cookie=cookie)[0],200)
                self.assertEqual(request('/api/no-such-route','POST',{},Cookie=cookie)[0],403)
                self.assertEqual(request('/api/no-such-route','POST',{},Cookie=cookie,**{'X-Study-Token':config['token']})[0],404)
                self.assertEqual(request('/api/config',Cookie=cookie,Origin=f'http://localhost:{port}')[0],403)
            finally:
                server.shutdown();server.server_close();thread.join();store.pool.shutdown();store.job_pool.shutdown()

if __name__=='__main__':unittest.main()
