import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store, AppError
from deepseek_ai import DeepSeekExtractor, AIError, NoRedirect, load_api_key
from test_app import pdf_bytes


def request_pages(payload):
    data=json.loads(payload['messages'][1]['content'])
    if 'pages' in data:return {x['page']:x['text'] for x in data['pages']}
    pages={}
    for source in data['sources']:
        pages[source['page']]=pages.get(source['page'],'')+source['text']
    return pages


def valid_response(pages, priorities=(2,1,3,1)):
    cards=[]
    for index,(page,text) in enumerate(list(pages.items())[:4]):
        cards.append(dict(page=page,title='概念'+str(page),question='请解释传播的条件。',answer='这是测试响应，不是真实模型结果。',quote=text[:80],reason='定义是理解材料的基础。',priority=priorities[index%len(priorities)],hint='先说概念，再说条件。'))
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'cards':cards},ensure_ascii=False)}}],'usage':{'prompt_tokens':120,'completion_tokens':80,'total_tokens':200}}


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work')
        self.calls=[]
        def transport(payload,key):
            self.calls.append((payload,key))
            pages=request_pages(payload)
            return valid_response(pages)
        self.ai=DeepSeekExtractor(self.tmp.name,transport=transport,key_loader=lambda:'test-only-placeholder')
        self.store=Store(self.tmp.name,extractor=self.ai)
        b=self.store.upload('test.pdf',pdf_bytes())
        self.store.pool.shutdown(wait=True)
        self.bid=b['id']
    def tearDown(self):
        self.store.job_pool.shutdown(wait=True)
        self.store.pool.shutdown(wait=True)
        self.tmp.cleanup()
    def payload(self,**extra):
        result=dict(book_id=self.bid,start=1,end=4,count=4)
        result.update(extra);return result
    def session(self,**extra):
        return self.store.session(self.store.create_session(self.payload(**extra))['id'])

    def test_real_adapter_payload_priority_cache_and_usage(self):
        s=self.session()
        self.assertEqual(1,len(self.calls))
        payload,key=self.calls[0]
        self.assertEqual('test-only-placeholder',key)
        self.assertEqual('json_object',payload['response_format']['type'])
        self.assertEqual('disabled',payload['thinking']['type'])
        self.assertEqual(6000,payload['max_tokens'])
        self.assertEqual([1,2,3,4],list(request_pages(payload)))
        self.assertEqual([1,1,2,3],[i['priority'] for i in s['items']])
        self.assertNotIn('answer',s['items'][0])
        for item in s['items']:
            self.store.update_item(s['id'],item['card_id'],{'action':'reveal'})
            self.store.update_item(s['id'],item['card_id'],{'action':'rate','rating':'mastered'})
        with self.assertRaises(AppError):self.session()
        self.assertEqual(1,len(self.calls),'No paid regeneration after all cached cards were learned')
        self.assertEqual(200,self.store.ai_status()['total_tokens'])
        self.assertEqual(1,self.store.ai_status()['calls'])

    def test_invalid_output_fails_without_silent_fallback_or_auto_retry(self):
        def wrong(payload,key):
            self.calls.append(payload)
            pages=request_pages(payload)
            r=valid_response(pages)
            data=json.loads(r['choices'][0]['message']['content'])
            for card in data['cards']:card['quote']='这是一个不存在于上传原文中的虚构引用句子。'
            r['choices'][0]['message']['content']=json.dumps(data)
            return r
        self.ai.transport=wrong
        for attempt in range(2):
            with self.assertRaises(AIError):self.session()
        self.assertEqual(1,len(self.calls))
        with self.store.db() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM cards').fetchone()[0])
        with self.assertRaises(AIError):self.session(retry_ai=True)
        self.assertEqual(2,len(self.calls))
        self.assertEqual(400,self.store.ai_status()['total_tokens'])

    def test_truncated_invalid_json_and_type_errors(self):
        pages={1:'这是一段用于测试的连续原文内容，必须真实存在于提供的页面。'}
        cases=[]
        valid=valid_response(pages)
        broken=copy.deepcopy(valid);broken['choices'][0]['finish_reason']='length';cases.append(broken)
        broken=copy.deepcopy(valid);broken['choices'][0]['message']['content']='broken';cases.append(broken)
        broken=copy.deepcopy(valid);broken['choices']=[];cases.append(broken)
        broken=copy.deepcopy(valid);broken['choices'][0]['message']['content']=json.dumps({'cards':[dict(page=True,title='x',question='q',answer='a',quote=pages[1],reason='r',priority=1,hint='h')]});cases.append(broken)
        for response in cases:
            ai=DeepSeekExtractor(self.tmp.name,key_loader=lambda:'test',transport=lambda *args:response)
            with self.subTest(response=response),self.assertRaises(AIError):ai.generate(pages)
        empty=copy.deepcopy(valid);empty['choices'][0]['message']['content']='{"cards":[]}'
        ai=DeepSeekExtractor(self.tmp.name,key_loader=lambda:'test',transport=lambda *args:empty)
        self.assertEqual([],ai.generate(pages))

    def test_preflight_limits_missing_key_and_no_unselected_pages(self):
        with self.assertRaises(AIError):self.ai.generate({1:'字'*40001})
        self.assertEqual([],self.calls)
        missing=DeepSeekExtractor(self.tmp.name,key_loader=lambda:(_ for _ in ()).throw(AIError('未配置')))
        self.store.extractor=missing
        self.assertFalse(missing.status()['ready'])
        with self.assertRaises(AIError):self.session()
        with self.store.db() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM ai_cache').fetchone()[0])
        self.store.extractor=self.ai
        s=self.session(start=2,end=3,count=3)
        self.assertEqual([2,3],list(request_pages(self.calls[0][0])))
        self.assertEqual(2,len(s['items']))

    def test_background_dedup_resume_and_delete_cache(self):
        first=self.store.start_job(self.payload())
        second=self.store.start_job(self.payload())
        self.assertEqual(first['id'],second['id'])
        self.store.job_pool.shutdown(wait=True)
        job=self.store.job(first['id'])
        self.assertEqual('ready',job['status'])
        self.assertTrue(job['session_id'])
        self.assertEqual(1,len(self.calls))
        self.store.delete(self.bid)
        with self.store.db() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM generation_jobs').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT count(*) FROM ai_cache').fetchone()[0])

    def test_usage_recorded_even_when_quote_is_bad_and_network_error_is_sanitized(self):
        class FakeOpener:
            def __init__(self,exc):self.exc=exc
            def open(self,*args,**kwargs):raise self.exc
        for exc,expected in [(HTTPError('https://api.deepseek.com',401,'bad',{},io.BytesIO(b'private body')), '密钥无效'),(HTTPError('https://api.deepseek.com',402,'bad',{},None),'余额不足'),(HTTPError('https://api.deepseek.com',429,'bad',{},None),'请求受限'),(URLError('private proxy details'),'连接失败')]:
            with patch('deepseek_ai.build_opener',return_value=FakeOpener(exc)):
                with self.assertRaises(AIError) as error:self.ai._request({},'never-print-this')
                self.assertIn(expected,str(error.exception))
                self.assertNotIn('private',str(error.exception))
                self.assertNotIn('never-print-this',str(error.exception))
        with self.assertRaises(AIError):NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_old_database_migration_and_interrupted_request_no_auto_charge(self):
        with self.store.db() as db:
            db.execute("INSERT INTO ai_cache(id,book_id,model,status,created,attempts) VALUES('test',?,'model','running',0,1)",(self.bid,))
        other=Store(self.tmp.name,extractor=self.ai)
        try:
            with other.db() as db:self.assertEqual('uncertain',db.execute("SELECT status FROM ai_cache WHERE id='test'").fetchone()[0])
            self.assertEqual(0,len(self.calls))
        finally:other.pool.shutdown(wait=True);other.job_pool.shutdown(wait=True)


    def test_legacy_schema_migrates_without_removing_cards(self):
        import sqlite3
        legacy=Path(self.tmp.name)/'legacy'
        legacy.mkdir()
        db=sqlite3.connect(legacy/'learning.sqlite3')
        db.executescript("""
        CREATE TABLE books(id TEXT PRIMARY KEY,name TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,
          processed INTEGER DEFAULT 0,total INTEGER DEFAULT 0,error TEXT DEFAULT '',warnings TEXT DEFAULT '[]',
          chapters TEXT DEFAULT '[]',created REAL NOT NULL);
        CREATE TABLE cards(id TEXT PRIMARY KEY,book_id TEXT REFERENCES books(id) ON DELETE CASCADE,
          page INTEGER,title TEXT,question TEXT,answer TEXT,quote TEXT,mode TEXT NOT NULL DEFAULT 'demo');
        INSERT INTO books(id,name,title,status,created) VALUES('legacy','book.pdf','book','ready',0);
        INSERT INTO cards(id,book_id,page,title,question,answer,quote) VALUES('old-card','legacy',1,'old','question','answer','quote');
        """)
        db.commit();db.close()
        migrated=Store(legacy,extractor=self.ai)
        try:
            with migrated.db() as db:
                row=db.execute("SELECT title,mode,source_key FROM cards WHERE id='old-card'").fetchone()
                self.assertEqual(('old','demo','demo'),tuple(row))
        finally:migrated.pool.shutdown(wait=True);migrated.job_pool.shutdown(wait=True)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_windows_encrypted_key_roundtrip(self):
        secret=Path(self.tmp.name)/'.secrets'
        secret.mkdir()
        destination=secret/'deepseek-key.dpapi'
        # Synthetic non-credential value, only tests interoperability with the setup script.
        script="$s=ConvertTo-SecureString 'synthetic-test-key-only' -AsPlainText -Force; [IO.File]::WriteAllText('"+str(destination).replace("'","''")+"',(ConvertFrom-SecureString $s))"
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',script],capture_output=True,timeout=20)
        if result.returncode!=0:
            self.skipTest('运行沙箱未加载Windows用户加密配置，DPAPI需在本机用户会话验证。')
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}):
            self.assertEqual('synthetic-test-key-only',load_api_key(self.tmp.name))
        self.assertNotIn('synthetic-test-key-only',destination.read_text())


if __name__=='__main__':unittest.main(verbosity=2)
