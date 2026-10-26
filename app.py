"""Local learning assistant with optional paid DeepSeek generation."""
import argparse
from lan_access import LANAccess,PairingError,private_address
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

from pypdf import PdfReader
from extractor import DemoExtractor, ExtractedCard, validate_sources
from deepseek_ai import DeepSeekExtractor, AIError, PROMPT_VERSION, LEGACY_PROMPT_VERSION
from dataclasses import asdict
from paper_lab import PaperLab, VERSION as PAPER_VERSION

ROOT = Path(__file__).resolve().parent
SERVER_BUILD = hashlib.sha256(b'\0'.join((ROOT / name).read_bytes() for name in
    ('app.py', 'paper_lab.py', 'deepseek_ai.py', 'citations.py', 'extractor.py', 'lan_access.py'))).hexdigest()
MAX_UPLOAD = 30 * 1024 * 1024
MAX_PAGES = 1000


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Store:
    def __init__(self, directory, extractor=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / 'learning.sqlite3'
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.lock = threading.RLock()
        self.extractor = extractor or DemoExtractor()
        self.job_pool = ThreadPoolExecutor(max_workers=1)
        self.extraction_lock = threading.Lock()
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS books(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, title TEXT NOT NULL,
              status TEXT NOT NULL, processed INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
              error TEXT DEFAULT '', warnings TEXT DEFAULT '[]', chapters TEXT DEFAULT '[]',
              created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pages(
              book_id TEXT REFERENCES books(id) ON DELETE CASCADE,
              number INTEGER, text TEXT NOT NULL, PRIMARY KEY(book_id,number));
            CREATE TABLE IF NOT EXISTS cards(
              id TEXT PRIMARY KEY, book_id TEXT REFERENCES books(id) ON DELETE CASCADE,
              page INTEGER, title TEXT, question TEXT, answer TEXT, quote TEXT,
              mode TEXT NOT NULL DEFAULT 'demo');
            CREATE TABLE IF NOT EXISTS marks(
              card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
              rating TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
              id TEXT PRIMARY KEY, book_id TEXT REFERENCES books(id) ON DELETE CASCADE,
              kind TEXT, start_page INTEGER, end_page INTEGER,
              created REAL, completed REAL);
            CREATE TABLE IF NOT EXISTS items(
              session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
              card_id TEXT REFERENCES cards(id) ON DELETE CASCADE,
              position INTEGER, draft TEXT DEFAULT '', revealed INTEGER DEFAULT 0,
              rating TEXT, PRIMARY KEY(session_id,card_id));
            ''')

            existing={r[1] for r in db.execute('PRAGMA table_info(cards)')}
            for column,sql in {
                'reason':"TEXT NOT NULL DEFAULT ''",'priority':"INTEGER NOT NULL DEFAULT 3",
                'hint':"TEXT NOT NULL DEFAULT ''",'model':"TEXT NOT NULL DEFAULT ''",
                'source_key':"TEXT NOT NULL DEFAULT 'demo'"
            }.items():
                if column not in existing:db.execute('ALTER TABLE cards ADD COLUMN '+column+' '+sql)
            db.executescript("""
            CREATE TABLE IF NOT EXISTS ai_cache(
              id TEXT PRIMARY KEY,book_id TEXT REFERENCES books(id) ON DELETE CASCADE,
              model TEXT,status TEXT,cards TEXT NOT NULL DEFAULT '[]',
              usage TEXT NOT NULL DEFAULT '{}',error TEXT DEFAULT '',created REAL,attempts INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS generation_jobs(
              id TEXT PRIMARY KEY,book_id TEXT REFERENCES books(id) ON DELETE CASCADE,
              payload TEXT,status TEXT,message TEXT DEFAULT '',session_id TEXT,
              error TEXT DEFAULT '',created REAL);
            """)
            for table,fields in {
                'ai_cache':{'response':"TEXT NOT NULL DEFAULT ''",'diagnostics':"TEXT NOT NULL DEFAULT '{}'",'prompt_version':"TEXT NOT NULL DEFAULT ''"},
                'generation_jobs':{'diagnostics':"TEXT NOT NULL DEFAULT '{}'",'can_recheck':"INTEGER NOT NULL DEFAULT 0"}
            }.items():
                columns={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
                for column,definition in fields.items():
                    if column not in columns:db.execute('ALTER TABLE '+table+' ADD COLUMN '+column+' '+definition)
            db.execute("UPDATE ai_cache SET status='uncertain',error='上次调用中断，可能已计费。' WHERE status='running'")
            db.execute("UPDATE generation_jobs SET status='failed',error='上次生成被中断，可能已计费；请确认后重试。' WHERE status IN ('queued','running')")
            db.execute("UPDATE books SET status='failed',error='上次解析被中断，请重试。' WHERE status IN ('queued','processing')")

        self.papers = PaperLab(self)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def book(self, bid):
        with self.db() as db:
            row = db.execute('SELECT * FROM books WHERE id=?', (bid,)).fetchone()
            if not row:
                raise AppError('参考书不存在。', 404)
            result = dict(row)
            for key in ('warnings', 'chapters'):
                result[key] = json.loads(result[key])
            result['readable_pages'] = db.execute("SELECT count(*) FROM pages WHERE book_id=? AND length(trim(text))>0", (bid,)).fetchone()[0]
            result['learned'] = db.execute('SELECT count(*) FROM marks m JOIN cards c ON c.id=m.card_id WHERE c.book_id=?', (bid,)).fetchone()[0]
            return result

    def books(self):
        with self.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM books ORDER BY created DESC')]
        return [self.book(bid) for bid in ids]

    def upload(self, name, content):
        if not name.lower().endswith('.pdf') or not content.startswith(b'%PDF-'):
            raise AppError('请上传有效的 PDF 文件。仅支持文字版 PDF。')
        if len(content) > MAX_UPLOAD:
            raise AppError('文件超过 30 MB，请拆分后上传。', 413)
        bid = uuid.uuid4().hex
        name = name.replace('\\', '/').rsplit('/', 1)[-1][:160]
        with self.lock:
            (self.directory / (bid + '.pdf')).write_bytes(content)
            with self.db() as db:
                db.execute('INSERT INTO books(id,name,title,status,created) VALUES(?,?,?,?,?)', (bid, name, name[:-4], 'queued', time.time()))
            self.pool.submit(self.parse, bid)
        return self.book(bid)

    def retry(self, bid):
        with self.lock:
            if self.book(bid)['status'] != 'failed':
                raise AppError('只能重试处理失败的文件。')
            with self.db() as db:
                db.execute("UPDATE books SET status='queued',processed=0,error='' WHERE id=?", (bid,))
            self.pool.submit(self.parse, bid)

    def parse(self, bid):
        try:
            with self.db() as db:
                if not db.execute('SELECT id FROM books WHERE id=?', (bid,)).fetchone():
                    return
                db.execute("UPDATE books SET status='processing' WHERE id=?", (bid,))
            reader = PdfReader(self.directory / (bid + '.pdf'), strict=False)
            if reader.is_encrypted:
                raise AppError('此 PDF 已加密，请上传已解密且可提取文字的版本。')
            total = len(reader.pages)
            if not 1 <= total <= MAX_PAGES:
                raise AppError('仅支持 1—1000 页的 PDF，请拆分后重试。')
            with self.db() as db:
                db.execute('UPDATE books SET total=? WHERE id=?', (total, bid))
            pages, empty, failed = [], [], []
            length = 0
            for number, page in enumerate(reader.pages, 1):
                try:
                    txt = page.extract_text() or ''
                    txt = txt.replace('\x00', '').strip()
                except Exception:
                    txt = ''
                    failed.append(number)
                if not txt:
                    empty.append(number)
                length += len(txt)
                if length > 6_000_000:
                    raise AppError('提取文字超过 600 万字，请拆分文件。')
                pages.append((bid, number, txt))
                with self.db() as db:
                    if not db.execute('SELECT id FROM books WHERE id=?', (bid,)).fetchone():
                        return
                    db.execute('UPDATE books SET processed=? WHERE id=?', (number, bid))
            if len(empty) == total:
                raise AppError('所有页面均未提取到文字；扫描版需要先在其他工具中进行 OCR。')
            chapters = []
            def walk(outline):
                for item in outline:
                    if isinstance(item, list):
                        walk(item)
                    else:
                        p = reader.get_destination_page_number(item)
                        if p is not None and 0 <= p < total:
                            chapters.append({'title': str(item.title)[:200], 'start': p + 1})
            try:
                walk(reader.outline)
            except Exception:
                chapters = []
            chapters.sort(key=lambda x: x['start'])
            for i, chapter in enumerate(chapters):
                next_start = next((x['start'] for x in chapters[i+1:] if x['start'] > chapter['start']), total + 1)
                chapter['end'] = next_start - 1
            warnings = []
            if empty:
                warnings.append('未提取到文字的 PDF 页：' + '、'.join(map(str, empty)))
            if failed:
                warnings.append('其中解析出错页：' + '、'.join(map(str, failed)))
            with self.lock, self.db() as db:
                if not db.execute('SELECT id FROM books WHERE id=?', (bid,)).fetchone():
                    return
                db.execute('DELETE FROM pages WHERE book_id=?', (bid,))
                db.executemany('INSERT INTO pages VALUES(?,?,?)', pages)
                db.execute("UPDATE books SET status='ready',chapters=?,warnings=?,error='' WHERE id=?", (json.dumps(chapters, ensure_ascii=False), json.dumps(warnings, ensure_ascii=False), bid))
        except Exception as exc:
            message = str(exc) if isinstance(exc, AppError) else 'PDF 解析失败，文件可能损坏；请检查后重试或重新上传。'
            with self.db() as db:
                db.execute("UPDATE books SET status='failed',error=? WHERE id=?", (message, bid))

    def delete(self, bid):
        with self.lock:
            self.book(bid)
            with self.db() as db:
                self.papers.delete_book(db, bid)
                db.execute('DELETE FROM books WHERE id=?', (bid,))
            (self.directory / (bid + '.pdf')).unlink(missing_ok=True)

    def page(self, bid, number):
        self.book(bid)
        with self.db() as db:
            row = db.execute('SELECT text FROM pages WHERE book_id=? AND number=?', (bid, number)).fetchone()
        if row is None:
            raise AppError('此页面尚未解析或页码不存在。', 404)
        return {'page': number, 'text': row[0]}

    def stats(self):
        with self.db() as db:
            return {
                'paper_learning': self.papers.learning_stats(),
                'learned': db.execute('SELECT count(*) FROM marks').fetchone()[0],
                'mastered': db.execute("SELECT count(*) FROM marks WHERE rating='mastered'").fetchone()[0],
                'review': db.execute("SELECT count(*) FROM marks WHERE rating IN ('unsure','unlearned')").fetchone()[0],
                'active': [dict(r) for r in db.execute('SELECT s.*, b.title, (SELECT count(*) FROM items i WHERE i.session_id=s.id AND rating IS NOT NULL) AS done, (SELECT count(*) FROM items i WHERE i.session_id=s.id) AS total FROM sessions s JOIN books b ON b.id=s.book_id WHERE completed IS NULL ORDER BY created DESC')]
            }

    def review(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT c.id,c.book_id,c.title,c.page,b.title AS book_title,m.rating FROM marks m JOIN cards c ON c.id=m.card_id JOIN books b ON b.id=c.book_id WHERE m.rating IN ('unsure','unlearned') ORDER BY CASE m.rating WHEN 'unlearned' THEN 0 ELSE 1 END,m.updated,c.id")]


    def ai_status(self):
        if isinstance(self.extractor, DeepSeekExtractor):
            result = self.extractor.status()
        else:
            result = {'mode':'demo','model':'','ready':True,'error':'','fee_limit':None}
        with self.db() as db:
            usage = [json.loads(r[0]) for r in db.execute("SELECT usage FROM ai_cache WHERE usage!='{}'")]
            result['calls'] = db.execute('SELECT coalesce(sum(attempts),0) FROM ai_cache').fetchone()[0]
        result['total_tokens'] = sum(r.get('total_tokens',0) for r in usage)
        result['generation_version']=PROMPT_VERSION
        result['local_recheck']=True
        paper_usage=self.papers.usage()
        result['calls']+=paper_usage['calls']
        result['total_tokens']+=paper_usage['tokens']
        result['paper_lab_version']=PAPER_VERSION
        result['paper_authorship_required']=False
        result['paper_essay_style']='scenario-v3'
        result['paper_read_style']='grounded-v2'
        result['paper_review_style']='detail-v1'
        result['paper_completion_supported']=True
        result['server_build']=SERVER_BUILD
        return result

    def job(self,jid):
        with self.db() as db:
            row=db.execute('SELECT id,book_id,status,message,session_id,error,diagnostics,can_recheck,payload FROM generation_jobs WHERE id=?',(jid,)).fetchone()
        if row is None:raise AppError('生成任务不存在。',404)
        result=dict(row)
        result['diagnostics']=json.loads(result['diagnostics'])
        result['can_recheck']=bool(result['can_recheck'])
        payload=json.loads(result.pop('payload'))
        result['request']={key:payload[key] for key in ('book_id','kind','count','start','end') if key in payload}
        return result

    def start_job(self,payload):
        bid=str(payload.get('book_id',''))
        self.book(bid)
        with self.lock,self.db() as db:
            old=db.execute("SELECT id FROM generation_jobs WHERE book_id=? AND status IN ('queued','running') ORDER BY created DESC LIMIT 1",(bid,)).fetchone()
            if old:return {'id':old[0]}
            jid=uuid.uuid4().hex
            db.execute("INSERT INTO generation_jobs(id,book_id,payload,status,message,created) VALUES(?,?,?,'queued','排队等待生成',?)",(jid,bid,json.dumps(payload),time.time()))
        self.job_pool.submit(self.run_job,jid,payload)
        return {'id':jid}

    def run_job(self,jid,payload):
        try:
            with self.db() as db:
                if not db.execute('SELECT 1 FROM generation_jobs WHERE id=?',(jid,)).fetchone():return
                db.execute("UPDATE generation_jobs SET status='running',message='正在提炼重点并核对原文；请勿重复提交' WHERE id=?",(jid,))
            session=self.create_session(payload)
            report=self.session(session['id']).get('generation_report',{})
            with self.db() as db:
                db.execute("UPDATE generation_jobs SET status='ready',message='已完成',session_id=?,diagnostics=? WHERE id=?",(session['id'],json.dumps(report,ensure_ascii=False),jid))
        except Exception as exc:
            error=str(exc) if isinstance(exc,(AIError,AppError)) else '生成失败；已有进度保留。请检查服务后重试。'
            with self.db() as db:db.execute("UPDATE generation_jobs SET status='failed',error=?,message='生成未完成',diagnostics=?,can_recheck=? WHERE id=?",(error,json.dumps(getattr(exc,'report',{}),ensure_ascii=False),int(getattr(exc,'can_recheck',False)),jid))

    def build_cards(self,bid,first,last,retry_ai=False,local_only=False):
        with self.extraction_lock:
            with self.db() as db:
                rows=db.execute('SELECT number,text FROM pages WHERE book_id=? AND number BETWEEN ? AND ? ORDER BY number',(bid,first,last)).fetchall()
            pages={r['number']:r['text'] for r in rows}
            is_ai=isinstance(self.extractor,DeepSeekExtractor)
            if not any(text.strip() for text in pages.values()):
                raise AppError('所选页面没有可提取文字，请调整范围；尚未调用模型。',422)
            source_key='demo'
            if is_ai:
                if sum(map(len,pages.values()))>40000:
                    raise AIError('所选文字超过4万字符，请缩小范围；尚未发起请求。')
                def fingerprint(version):
                    return hashlib.sha256(json.dumps([bid,self.extractor.model,version,pages],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                source_key=fingerprint(PROMPT_VERSION)
                with self.db() as db:
                    cache=db.execute('SELECT * FROM ai_cache WHERE id=?',(source_key,)).fetchone()
                    if cache is None:
                        legacy_key=fingerprint(LEGACY_PROMPT_VERSION)
                        cache=db.execute('SELECT * FROM ai_cache WHERE id=?',(legacy_key,)).fetchone()
                        if cache is not None:source_key=legacy_key
                def save_report(report):
                    with self.db() as db:
                        db.execute('UPDATE ai_cache SET diagnostics=? WHERE id=?',(json.dumps(report,ensure_ascii=False),source_key))
                def save_ready(cards,report):
                    with self.db() as db:
                        if not db.execute('SELECT 1 FROM books WHERE id=?',(bid,)).fetchone():
                            raise AppError('材料已删除，生成结果已丢弃。',404)
                        db.execute("UPDATE ai_cache SET status='ready',cards=?,diagnostics=?,error='' WHERE id=?",(json.dumps([asdict(c) for c in cards],ensure_ascii=False),json.dumps(report,ensure_ascii=False),source_key))
                cards=None
                if cache and cache['status']=='ready' and not local_only:
                    cards=[ExtractedCard(**x) for x in json.loads(cache['cards'])]
                elif cache and cache['response']:
                    try:
                        cards,report=self.extractor.parse_response(json.loads(cache['response']),pages)
                        report['rechecked_locally']=True
                        save_ready(cards,report)
                    except AIError as exc:
                        save_report(exc.report)
                        if local_only or not retry_ai:raise
                if cards is None:
                    if local_only:
                        raise AIError('没有可重新核验的本地响应（旧版本未保留失败内容）。本次未调用模型；如需新结果，请明确选择重新请求。')
                    if cache and not retry_ai:
                        raise AIError('该范围上次未完成，旧版没有保留响应，无法免费恢复。若要重新调用，请点击“重新请求DeepSeek”，可能再次计费。',json.loads(cache['diagnostics']),bool(cache['response']))
                    if not self.extractor.key_loader():raise AIError('密钥为空，请重新配置。')
                    with self.db() as db:
                        db.execute("INSERT INTO ai_cache(id,book_id,model,status,created,attempts,prompt_version) VALUES(?,?,?,'running',?,1,?) ON CONFLICT(id) DO UPDATE SET status='running',error='',attempts=attempts+1,prompt_version=excluded.prompt_version",(source_key,bid,self.extractor.model,time.time(),PROMPT_VERSION))
                    def response_saved(response):
                        with self.db() as db:
                            db.execute('UPDATE ai_cache SET response=? WHERE id=?',(json.dumps(response,ensure_ascii=False),source_key))
                    def usage_saved(usage):
                        with self.db() as db:
                            old=db.execute('SELECT usage FROM ai_cache WHERE id=?',(source_key,)).fetchone()
                            if old:
                                aggregate=json.loads(old[0])
                                for key,value in usage.items():aggregate[key]=aggregate.get(key,0)+value
                                db.execute('UPDATE ai_cache SET usage=? WHERE id=?',(json.dumps(aggregate),source_key))
                    try:
                        cards=self.extractor.generate(pages,on_usage=usage_saved,on_response=response_saved,on_report=save_report)
                        with self.db() as db:
                            row=db.execute('SELECT diagnostics FROM ai_cache WHERE id=?',(source_key,)).fetchone()
                        save_ready(cards,json.loads(row[0]) if row else {})
                    except Exception as exc:
                        error=str(exc) if isinstance(exc,(AIError,AppError)) else '模型处理失败。'
                        with self.db() as db:
                            db.execute("UPDATE ai_cache SET status='failed',error=? WHERE id=?",(error,source_key))
                        raise
            else:
                cards=self.extractor.generate(pages)
            validate_sources(cards,pages)
            with self.lock,self.db() as db:
                if not db.execute('SELECT id FROM books WHERE id=?',(bid,)).fetchone():raise AppError('材料已删除。',404)
                for card in cards:
                    identity=f'{bid}:{card.page}:{card.quote}' if not is_ai else f'{source_key}:{card.page}:{card.title}:{card.quote}'
                    cid=hashlib.sha256(identity.encode()).hexdigest()[:32]
                    db.execute('INSERT OR IGNORE INTO cards(id,book_id,page,title,question,answer,quote,mode,reason,priority,hint,model,source_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(cid,bid,card.page,card.title,card.question,card.answer,card.quote,card.mode,card.reason,card.priority,card.hint,card.model,source_key))
            return source_key

    def create_session(self,payload):
        bid=str(payload.get('book_id',''))
        book=self.book(bid)
        if book['status']!='ready':raise AppError('请等待参考书解析完成。')
        kind=payload.get('kind','new')
        if kind not in ('new','review'):raise AppError('任务类型无效。')
        count=integer(payload.get('count',4),'知识点数量')
        if count not in (3,4,5):raise AppError('每次请选择3—5个知识点。')
        first=integer(payload.get('start',1),'起始页')
        last=integer(payload.get('end',book['total']),'结束页')
        if kind=='new' and not 1<=first<=last<=book['total']:raise AppError('页码范围无效，请使用PDF物理页码。')
        if kind=='new' and last-first+1>80:raise AppError('每次最多选择80页，请缩小范围。')
        def active_session(db):
            return db.execute('SELECT id FROM sessions WHERE book_id=? AND completed IS NULL ORDER BY created DESC LIMIT 1',(bid,)).fetchone()
        with self.db() as db:
            active=active_session(db)
            if active:return {'id':active[0],'resumed':True}
        source_key=self.build_cards(bid,first,last,payload.get('retry_ai') is True,payload.get('local_only') is True) if kind=='new' else ''
        with self.lock,self.db() as db:
            active=active_session(db)
            if active:return {'id':active[0],'resumed':True}
            if kind=='new':
                cards=db.execute('SELECT c.id FROM cards c LEFT JOIN marks m ON m.card_id=c.id WHERE c.book_id=? AND c.page BETWEEN ? AND ? AND c.source_key=? AND m.card_id IS NULL ORDER BY c.priority,c.page,c.rowid LIMIT ?',(bid,first,last,source_key,count)).fetchall()
            else:
                cards=db.execute("SELECT c.id FROM cards c JOIN marks m ON m.card_id=c.id WHERE c.book_id=? AND m.rating IN ('unsure','unlearned') ORDER BY CASE m.rating WHEN 'unlearned' THEN 0 ELSE 1 END,m.updated,c.id LIMIT ?",(bid,count)).fetchall()
            if not cards:
                raise AppError('没有待复习内容。' if kind=='review' else '该范围没有新的可学习知识点（可能已学完、文字太少或模型未找到可核验内容）。请调整范围或进入复习。',422)
            sid=uuid.uuid4().hex
            db.execute('INSERT INTO sessions(id,book_id,kind,start_page,end_page,created) VALUES(?,?,?,?,?,?)',(sid,bid,kind,first,last,time.time()))
            db.executemany('INSERT INTO items(session_id,card_id,position) VALUES(?,?,?)',[(sid,r[0],n) for n,r in enumerate(cards)])
            return {'id':sid,'resumed':False}

    def session(self, sid):
        with self.db() as db:
            row = db.execute('SELECT s.*,b.title AS book_title FROM sessions s JOIN books b ON b.id=s.book_id WHERE s.id=?', (sid,)).fetchone()
            if row is None:
                raise AppError('学习任务不存在。', 404)
            result = dict(row)
            result['items'] = []
            for item in db.execute('SELECT i.*,c.title,c.question,c.answer,c.quote,c.page,c.mode,c.reason,c.priority,c.hint,c.model FROM items i JOIN cards c ON c.id=i.card_id WHERE i.session_id=? ORDER BY position', (sid,)):
                item = dict(item)
                if not item['revealed']:
                    item.pop('answer')
                    item.pop('quote')
                result['items'].append(item)
            report=db.execute('SELECT a.diagnostics FROM items i JOIN cards c ON c.id=i.card_id JOIN ai_cache a ON a.id=c.source_key WHERE i.session_id=? ORDER BY i.position LIMIT 1',(sid,)).fetchone()
            result['generation_report']=json.loads(report[0]) if report else {}
            return result

    def update_item(self, sid, cid, payload):
        with self.lock, self.db() as db:
            item = db.execute('SELECT * FROM items WHERE session_id=? AND card_id=?', (sid, cid)).fetchone()
            if item is None:
                raise AppError('学习卡片不存在。', 404)
            if item['rating'] is not None:
                return {'saved': True}
            pending = db.execute('SELECT card_id FROM items WHERE session_id=? AND rating IS NULL ORDER BY position LIMIT 1', (sid,)).fetchone()
            if pending and pending[0] != cid:
                raise AppError('请先完成当前卡片。', 409)
            action = payload.get('action')
            if action == 'draft':
                draft = payload.get('draft', '')
                if not isinstance(draft, str) or len(draft) > 6000:
                    raise AppError('回答最多 6000 字。')
                db.execute('UPDATE items SET draft=? WHERE session_id=? AND card_id=?', (draft, sid, cid))
            elif action == 'reveal':
                db.execute('UPDATE items SET revealed=1 WHERE session_id=? AND card_id=?', (sid, cid))
            elif action == 'rate':
                rating = payload.get('rating')
                if rating not in ('mastered', 'unsure', 'unlearned'):
                    raise AppError('掌握状态无效。')
                if not item['revealed']:
                    raise AppError('请先核对答案，再标记掌握情况。', 409)
                db.execute('UPDATE items SET rating=? WHERE session_id=? AND card_id=?', (rating, sid, cid))
                db.execute('INSERT INTO marks(card_id,rating,updated) VALUES(?,?,?) ON CONFLICT(card_id) DO UPDATE SET rating=excluded.rating,updated=excluded.updated', (cid, rating, time.time()))
                if not db.execute('SELECT 1 FROM items WHERE session_id=? AND rating IS NULL', (sid,)).fetchone():
                    db.execute('UPDATE sessions SET completed=? WHERE id=?', (time.time(), sid))
            else:
                raise AppError('操作无效。')
        return {'saved': True}


def integer(value, label):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise AppError(label + '必须是整数。')
    return int(value)


def make_server(store, port=0, host='127.0.0.1', lan_address=None, pair_code=None):
    if host not in ('127.0.0.1','0.0.0.0'):raise ValueError('不支持该监听地址。')
    if host=='0.0.0.0' and not lan_address:raise ValueError('开启手机访问必须指定局域网地址。')
    lan_address=private_address(lan_address) if lan_address else None
    access=LANAccess(pair_code) if lan_address else None
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Shizhi/0.1'

        def log_message(self, fmt, *args):
            pass  # Avoid recording file names, answers, or user data in logs.

        def output(self, data, status=200, kind='application/json; charset=utf-8', cookie=None):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            if cookie:self.send_header('Set-Cookie',cookie)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def guard(self, write=False, login=False):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if lan_address:allowed.add(f'{lan_address}:{self.server.server_port}')
            if self.headers.get('Host') not in allowed:
                raise AppError('访问地址不在允许列表中。', 403)
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host',''):
                raise AppError('不允许跨站请求。', 403)
            local=self.client_address[0]=='127.0.0.1' and self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if access and not local and not login and not access.authorized(self.headers.get('Cookie')):raise AppError('请先在手机上输入电脑窗口显示的访问码。',401)
            if write and not login and not secrets.compare_digest(self.headers.get('X-Study-Token', ''), token):
                raise AppError('页面安全令牌已过期，请刷新页面。', 403)

        def do_GET(self):
            try:
                route = urlsplit(self.path).path
                self.guard(login=bool(access and route in ('/','/lan-login.js','/style.css')))
                local=self.client_address[0]=='127.0.0.1' and self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
                if access and route=='/' and not local and not access.authorized(self.headers.get('Cookie')):
                    return self.output((ROOT/'static'/'lan-login.html').read_bytes(),kind='text/html; charset=utf-8')
                if route == '/api/config':
                    return self.output({'token':token,'data_dir':str(store.directory),'lan_enabled':bool(access),'lan_address':lan_address,**store.ai_status()})
                if route == '/api/jobs':
                    with store.db() as db:
                        jobs=[dict(r) for r in db.execute("SELECT j.id,j.status,j.message,b.title FROM generation_jobs j JOIN books b ON b.id=j.book_id WHERE j.status IN ('queued','running') ORDER BY j.created")]
                    return self.output(jobs)
                match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})',route)
                if match:return self.output(store.job(match[1]))
                if route == '/api/papers':return self.output(store.papers.metadata())
                if route == '/api/paper-jobs':return self.output(store.papers.jobs())
                match=re.fullmatch(r'/api/paper-jobs/([a-f0-9]{32})',route)
                if match:return self.output(store.papers.job(match[1]))
                if route == '/api/books':
                    return self.output(store.books())
                if route == '/api/stats':
                    return self.output(store.stats())
                if route == '/api/review':
                    return self.output(store.review())
                m = re.fullmatch(r'/api/sessions/([a-f0-9]{32})', route)
                if m:
                    return self.output(store.session(m[1]))
                m = re.fullmatch(r'/api/books/([a-f0-9]{32})/pages/(\d+)', route)
                if m:
                    return self.output(store.page(m[1], int(m[2])))
                m = re.fullmatch(r'/api/books/([a-f0-9]{32})/pdf', route)
                if m:
                    store.book(m[1])
                    return self.output((store.directory / (m[1]+'.pdf')).read_bytes(), kind='application/pdf')
                files = {'/lan-login.js': ('lan-login.js','text/javascript; charset=utf-8'), '/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/papers.js': ('papers.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
                if route in files:
                    name, mime = files[route]
                    return self.output((ROOT / 'static' / name).read_bytes(), kind=mime)
                raise AppError('页面不存在。', 404)
            except AIError as exc:
                self.output({'error':str(exc)},422)
            except AppError as exc:
                self.output({'error': str(exc)}, exc.status)
            except Exception:
                self.output({'error': '读取失败，请刷新后重试。'}, 500)

        def do_POST(self):
            try:
                route = urlsplit(self.path).path
                self.guard(write=True,login=bool(access and route=='/api/lan-login'))
                if access and route=='/api/lan-login':
                    size=integer(self.headers.get('Content-Length','0'),'请求长度')
                    if not 0<size<=1024:raise AppError('访问码请求无效。',413)
                    self.connection.settimeout(10)
                    try:payload=json.loads(self.rfile.read(size));session=access.login(self.client_address[0],payload.get('code') if isinstance(payload,dict) else None)
                    except (ValueError,UnicodeDecodeError) as exc:
                        if isinstance(exc,PairingError):raise AppError(str(exc),exc.status) from None
                        raise AppError('请求格式无效。',400) from None
                    return self.output({'ok':True},cookie='shizhi_lan='+session+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
                size = integer(self.headers.get('Content-Length', '0'), '请求长度')
                if size > (MAX_UPLOAD if route == '/api/books' else 50000):
                    self.close_connection = True
                    raise AppError('请求过大。PDF 最大 30 MB。', 413)
                self.connection.settimeout(30)
                raw = self.rfile.read(size)
                if route == '/api/books':
                    name = unquote(self.headers.get('X-Filename', 'reference.pdf'))
                    return self.output(store.upload(name, raw), 202)
                try:
                    payload = json.loads(raw or b'{}')
                except (ValueError, UnicodeDecodeError):
                    raise AppError('请求数据不是有效 JSON。')
                if not isinstance(payload, dict):
                    raise AppError('请求格式无效。')
                if route == '/api/papers/plan':return self.output(store.papers.plan(payload))
                if route == '/api/paper-jobs':return self.output(store.papers.start(payload),202)
                match=re.fullmatch(r'/api/paper-jobs/([a-f0-9]{32})/answers/(terms[1-3]|essays[1-2])',route)
                if match:return self.output(store.papers.answer(match[1],match[2],payload))
                if route == '/api/jobs':
                    return self.output(store.start_job(payload),202)
                if route == '/api/sessions':
                    return self.output(store.create_session(payload), 201)
                m = re.fullmatch(r'/api/books/([a-f0-9]{32})/(delete|retry)', route)
                if m:
                    if m[2] == 'delete':
                        store.delete(m[1])
                    else:
                        store.retry(m[1])
                    return self.output({'ok': True})
                m = re.fullmatch(r'/api/sessions/([a-f0-9]{32})/items/([a-f0-9]{32})', route)
                if m:
                    return self.output(store.update_item(m[1], m[2], payload))
                raise AppError('操作不存在。', 404)
            except AIError as exc:
                self.output({'error':str(exc)},422)
            except AppError as exc:
                self.output({'error': str(exc)}, exc.status)
            except Exception:
                self.output({'error': '保存失败，请重试；已有进度会保留。'}, 500)

    server=ThreadingHTTPServer((host,port),Handler)
    server.lan_access=access
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', default=os.environ.get('STUDY_DATA_DIR', str(ROOT / 'data')))
    parser.add_argument('--demo',action='store_true',help='仅使用离线演示，不调用AI')
    parser.add_argument('--open-browser',action='store_true')
    parser.add_argument('--lan-ip',help='开启手机访问，指定电脑的专用局域网IPv4地址')
    args = parser.parse_args()
    store = Store(args.data_dir,extractor=DemoExtractor() if args.demo else DeepSeekExtractor(ROOT))
    server = make_server(store,args.port,host='0.0.0.0' if args.lan_ip else '127.0.0.1',lan_address=args.lan_ip)
    print(f'新传学习助手：http://127.0.0.1:{server.server_port}', flush=True)
    if server.lan_access:
        print(f'手机访问地址：http://{args.lan_ip}:{server.server_port}/',flush=True)
        print('手机访问码：'+server.lan_access.code,flush=True)
        print('电脑与手机须在同一Wi-Fi；访问码在重启后更换，会话8小时有效。仅用于可信专用网络。',flush=True)
    if args.open_browser:
        def open_when_ready():
            import webbrowser
            from urllib.request import urlopen
            url=f'http://127.0.0.1:{server.server_port}/'
            for _ in range(50):
                try:
                    with urlopen(url,timeout=1) as response:
                        if response.status==200:
                            webbrowser.open(url)
                            return
                except OSError:
                    time.sleep(.1)
        threading.Thread(target=open_when_ready,daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.pool.shutdown(wait=True)
        store.job_pool.shutdown(wait=True)


if __name__ == '__main__':
    main()
