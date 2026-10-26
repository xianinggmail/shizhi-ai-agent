"""Native PowerShell startup checks: never touch credentials or stop user services."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'work')
        self.project=Path(self.temp.name)
        self.files=('app.py','paper_lab.py','deepseek_ai.py','citations.py','extractor.py','lan_access.py')
        for name in self.files+('local-start.ps1','startup-check.py','本机启动并配置.cmd'):
            shutil.copy2(ROOT/name,self.project/name)
        self.build=hashlib.sha256(b'\0'.join((self.project/n).read_bytes() for n in self.files)).hexdigest()
        self.server=None
    def tearDown(self):
        if self.server:
            self.server.shutdown();self.server.server_close();self.thread.join()
        self.temp.cleanup()
    def service(self,status):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body=json.dumps(status).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        return self.server.server_port
    def run_check(self,port):
        return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.project/'local-start.ps1'),'-Port',str(port),'-CheckOnly','-NoBrowser'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
    def test_current_server_reused_without_process_or_secret_access(self):
        port=self.service({'data_dir':str(self.project/'data'),'server_build':self.build,'ready':True})
        result=self.run_check(port)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.project/'.secrets').exists())
        source=(self.project/'local-start.ps1').read_text(encoding='utf-8-sig')
        self.assertNotIn('Get-CimInstance',source);self.assertNotIn('Stop-Process',source)
        self.assertNotIn('\x0b',(self.project/'本机启动并配置.cmd').read_text(encoding='utf-8'))
    def test_old_server_kept_alive_with_explicit_close_instruction(self):
        port=self.service({'data_dir':str(self.project/'data'),'ready':True})
        result=self.run_check(port)
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertIsNotNone(self.server.socket.fileno())
        self.assertFalse((self.project/'.secrets').exists())
    def test_other_project_never_stopped_or_replaced(self):
        port=self.service({'data_dir':str(self.project/'other-data'),'server_build':self.build,'ready':True})
        self.assertEqual(self.run_check(port).returncode,1)
        self.assertFalse((self.project/'data').exists())
    def test_no_listener_check_does_not_prompt_or_start_database(self):
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        result=self.run_check(port)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.project/'data').exists())
        self.assertFalse((self.project/'.secrets').exists())

if __name__=='__main__':unittest.main()
