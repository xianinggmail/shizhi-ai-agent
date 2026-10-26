import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store, AppError, make_server
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject


def pdf_bytes(pages=4, blank=False, encrypted=False):
    writer = PdfWriter()
    for n in range(pages):
        page = writer.add_blank_page(width=595, height=842)
        if not blank:
            font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream=DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 45 750 Td (Page {n+1}. Communication theory studies messages, audiences, and effects.) Tj 0 -25 Td (Media literacy requires evaluating sources and understanding social contexts.) Tj ET'.encode())
            page[NameObject('/Contents')]=writer._add_object(stream)
        if n in (0, 2):
            writer.add_outline_item('Chapter ' + str(n//2+1), n)
    if encrypted:
        writer.encrypt('test-password')
    output=io.BytesIO();writer.write(output)
    return output.getvalue()


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work')
        self.store=Store(self.tmp.name)

    def tearDown(self):
        self.store.pool.shutdown(wait=True)
        self.tmp.cleanup()

    def book(self, **options):
        b=self.store.upload('测试参考书.pdf',pdf_bytes(**options))
        for _ in range(200):
            b=self.store.book(b['id'])
            if b['status'] in ('ready','failed'):return b
            time.sleep(.02)
        self.fail('PDF parsing did not finish')

    def task(self,bid,**changes):
        payload={'book_id':bid,'start':1,'end':4,'count':4}
        payload.update(changes)
        return self.store.session(self.store.create_session(payload)['id'])

    def finish(self,task,ratings=None):
        for n,item in enumerate(task['items']):
            self.store.update_item(task['id'],item['card_id'],{'action':'reveal'})
            self.store.update_item(task['id'],item['card_id'],{'action':'rate','rating':(ratings or ['mastered'])[n%len(ratings or ['mastered'])]})
        return self.store.session(task['id'])

    def test_pdf_parse_bookmarks_and_verbatim_sources(self):
        b=self.book();self.assertEqual('ready',b['status']);self.assertEqual(4,b['readable_pages'])
        self.assertEqual([(1,2),(3,4)],[(c['start'],c['end']) for c in b['chapters']])
        task=self.task(b['id'],start=2,end=3,count=3)
        self.assertEqual(2,len(task['items']))
        for item in task['items']:
            self.assertIn(item['page'],(2,3))
        item=task['items'][0]
        self.assertNotIn('answer',item)
        self.store.update_item(task['id'],item['card_id'],{'action':'reveal'})
        item=self.store.session(task['id'])['items'][0]
        self.assertEqual(item['quote'],item['answer'])
        self.assertIn(item['quote'],self.store.page(b['id'],item['page'])['text'])

    def test_default_four_and_no_duplicate_new_cards(self):
        b=self.book();s=self.task(b['id']);self.assertEqual(4,len(s['items']))
        self.finish(s)
        with self.assertRaises(AppError):self.task(b['id'])

    def test_recall_guard_and_idempotent_submission(self):
        b=self.book();s=self.task(b['id']);c=s['items'][0]['card_id']
        with self.assertRaises(AppError):self.store.update_item(s['id'],c,{'action':'rate','rating':'mastered'})
        with self.assertRaises(AppError):self.store.update_item(s['id'],s['items'][1]['card_id'],{'action':'reveal'})
        self.store.update_item(s['id'],c,{'action':'reveal'})
        for _ in range(2):self.store.update_item(s['id'],c,{'action':'rate','rating':'unsure'})
        self.assertEqual(1,self.store.stats()['learned'])

    def test_draft_and_restart_persistence(self):
        b=self.book();s=self.task(b['id']);c=s['items'][0]['card_id']
        self.store.update_item(s['id'],c,{'action':'draft','draft':'中文回忆草稿'})
        other=Store(self.tmp.name)
        try:
            self.assertEqual('中文回忆草稿',other.session(s['id'])['items'][0]['draft'])
            result=other.create_session({'book_id':b['id'],'count':3,'start':2,'end':3})
            self.assertTrue(result['resumed']);self.assertEqual(s['id'],result['id'])
        finally:other.pool.shutdown(wait=True)

    def test_review_priority_tail_and_mastered_removal(self):
        b=self.book();s=self.task(b['id']);self.finish(s,['mastered','unsure','unlearned','mastered'])
        self.assertEqual(['unlearned','unsure'],[r['rating'] for r in self.store.review()])
        r=self.task(b['id'],kind='review');self.assertEqual(2,len(r['items']))
        self.assertEqual(self.store.review()[0]['id'],r['items'][0]['card_id'])
        self.finish(r);self.assertEqual(0,self.store.stats()['review'])
        self.assertEqual(4,self.store.stats()['mastered'])
        self.assertIsNotNone(self.store.session(r['id'])['completed'])

    def test_invalid_ranges_and_counts(self):
        b=self.book()
        for changes in [{'start':0},{'end':5},{'start':4,'end':2},{'count':2},{'count':6},{'start':1.5},{'count':True},{'kind':'fake'}]:
            with self.subTest(changes=changes),self.assertRaises(AppError):self.task(b['id'],**changes)

    def test_scan_encryption_and_corrupt_failure(self):
        for opts in [{'blank':True},{'encrypted':True}]:
            with self.subTest(opts=opts):self.assertEqual('failed',self.book(**opts)['status'])
        b=self.store.upload('bad.pdf',b'%PDF-1.4\ninvalid')
        for _ in range(100):
            if self.store.book(b['id'])['status']=='failed':break
            time.sleep(.02)
        self.assertEqual('failed',self.store.book(b['id'])['status'])
        with self.assertRaises(AppError):self.store.upload('fake.pdf',b'not pdf')
        with self.assertRaises(AppError):self.store.upload('fake.txt',pdf_bytes())

    def test_partial_empty_page_warning(self):
        from pypdf import PdfReader
        writer=PdfWriter();writer.append(PdfReader(io.BytesIO(pdf_bytes(1))));writer.add_blank_page(width=595,height=842)
        output=io.BytesIO();writer.write(output)
        b=self.store.upload('partial.pdf',output.getvalue())
        for _ in range(100):
            b=self.store.book(b['id'])
            if b['status']=='ready':break
            time.sleep(.02)
        self.assertEqual(1,b['readable_pages']);self.assertIn('2',b['warnings'][0])

    def test_retry_and_recovery_of_interrupted_job(self):
        b=self.book(blank=True)
        (Path(self.tmp.name)/(b['id']+'.pdf')).write_bytes(pdf_bytes())
        self.store.retry(b['id']);self.store.pool.shutdown(wait=True)
        self.assertEqual('ready',self.store.book(b['id'])['status'])
        with self.store.db() as db:db.execute("UPDATE books SET status='processing' WHERE id=?",(b['id'],))
        other=Store(self.tmp.name)
        try:self.assertIn('中断',other.book(b['id'])['error'])
        finally:other.pool.shutdown(wait=True)

    def test_cascade_delete_and_isolation(self):
        b=self.book();other=self.book();s=self.task(b['id']);self.finish(s,['unsure'])
        self.store.delete(b['id']);self.assertFalse((Path(self.tmp.name)/(b['id']+'.pdf')).exists())
        self.assertEqual(0,self.store.stats()['learned']);self.assertEqual(other['id'],self.store.books()[0]['id'])
        with self.store.db() as db:
            for table in ['cards','sessions','items','marks']:
                self.assertEqual(0,db.execute('SELECT count(*) FROM '+table).fetchone()[0])

    def test_extraction_boundary_rejects_invented_sources(self):
        from extractor import ExtractedCard, validate_sources
        for card in [ExtractedCard(99,'标题','问题','答案','原文'),ExtractedCard(1,'标题','问题','答案','不存在的引文')]:
            with self.subTest(card=card),self.assertRaises(ValueError):validate_sources([card],{1:'真实原文'})
        b=self.book()
        class BadExtractor:
            def generate(self,pages):return [ExtractedCard(999,'标题','问题','答案','不存在')]
        self.store.extractor=BadExtractor()
        with self.assertRaises(ValueError):self.task(b['id'])
        with self.store.db() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM cards').fetchone()[0])

    def test_http_security_validation_and_full_flow(self):
        server=make_server(self.store);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def request(route,data=None,headers=None):
            with urlopen(Request(base+route,data=data,headers=headers or {}),timeout=5) as response:
                return response.read()
        try:
            token=json.loads(request('/api/config'))['token']
            with self.assertRaises(HTTPError) as error:request('/api/books',b'abc')
            self.assertEqual(403,error.exception.code)
            with self.assertRaises(HTTPError) as error:request('/api/stats',headers={'Origin':'https://evil.invalid'})
            self.assertEqual(403,error.exception.code)
            with self.assertRaises(HTTPError):request('/api/stats',headers={'Host':'evil.invalid'})
            headers={'X-Study-Token':token,'X-Filename':'book.pdf'}
            b=json.loads(request('/api/books',pdf_bytes(),headers))
            self.store.pool.shutdown(wait=True)
            payload=json.dumps({'book_id':b['id'],'count':3,'start':1,'end':4}).encode()
            s=json.loads(request('/api/sessions',payload,headers))
            s=json.loads(request('/api/sessions/'+s['id']))
            self.assertEqual(3,len(s['items']));self.assertNotIn('quote',s['items'][0])
            self.assertTrue(request('/api/books/'+b['id']+'/pdf').startswith(b'%PDF-'))
            self.assertIn('拾知'.encode(),request('/'))
            with self.assertRaises(HTTPError):request('/../app.py')
            with self.assertRaises(HTTPError):request('/api/sessions',b'not json',headers)
            with self.assertRaises(HTTPError):request('/api/sessions',b'[]',headers)
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main(verbosity=2)
