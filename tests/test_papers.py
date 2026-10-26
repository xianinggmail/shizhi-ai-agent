import json,tempfile,time,unittest,copy,threading,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store
from deepseek_ai import DeepSeekExtractor,AIError
from paper_lab import parse_read,parse_quiz,CHUNK_CHARS,FACETS
from test_app import pdf_bytes

def response(data):return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data,ensure_ascii=False)}}],'usage':{'prompt_tokens':100,'completion_tokens':200,'total_tokens':300}}
def fixture_read(content):
    ids=[s['source_id'] for s in content['sources']]
    return {'points':[{'title':'合成论点','text':'这是一条测试观点，不是真实论文分析。','category':'argument','attribution':'author_claim','owner':'ignored','source_ids':ids[:1],'quotes':[{'source_id':ids[0],'text':content['sources'][0]['text'][:80]}]},
                      {'title':'合成理论','text':'这是被论文引用的测试理论，非老师原创。','category':'theory','attribution':'cited_theory','owner':'测试理论提出者','source_ids':ids[-1:],'quotes':[{'source_id':ids[-1],'text':content['sources'][-1]['text'][:80]}]}],'note':'合成测试材料，非真实学术结论。'}
def fixture_review(content):
    result=fixture_read(content)
    for point in result['points']:
        point.update(facets={key:'合成测试的结构化说明，不能作为真实学术观点。' for key in FACETS[point['category']]},basis_status='explicit',uncertainty='')
    result['note']='合成模型复核，仅供软件流程测试。'
    return result

def fixture_model(content):
    return fixture_read(content) if content['stage']=='read' else fixture_review(content) if content['stage']=='review' else fixture_quiz(content)

def fixture_quiz(content):
    points=content['points'];author=[p['id'] for p in points if p['attribution']=='author_claim'];theory=[p['id'] for p in points if p['attribution']=='cited_theory']
    terms=[{'title':'合成概念'+str(i),'question':'请解释合成概念'+str(i),'sections':[{'heading':'定义与边界','text':'这是合成测试答案，不能作为真实论文观点。'*10,'attribution':'cited_theory','point_ids':theory[:1]}]} for i in range(3)]
    essays=[{'title':'合成情境论述'+str(i),'scenario':'假设某校园媒体面临用户增长与内容质量之间的矛盾。这是测试设定，非真实事件。','question':'请分析这一假设情境中的矛盾、作用机制与边界。','sections':[
        {'role':role,'heading':heading,'text':'本文认为，这是AI基于合成论文观点对假设情境所作的测试分析，不是作者对真实事件的判断。'*5,'attribution':'ai_synthesis','point_ids':author+theory}
        for role,heading in [('introduction','总论点'),('argument','内容与受众的机制'),('argument','制度约束与行动选择'),('boundary','适用边界与反向讨论'),('conclusion','结论')]]} for i in range(2)]
    return {'terms':terms,'essays':essays,'note':'测试用模拟响应，非真实DeepSeek。'}
class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work');self.calls=[];self.failure=None
        def transport(payload,key):
            content=json.loads(payload['messages'][1]['content']);self.calls.append(content)
            if self.failure:return self.failure(content)
            return response(fixture_model(content))
        self.ai=DeepSeekExtractor(Path(self.temp.name),transport=transport,key_loader=lambda:'synthetic-secret-not-real')
        self.store=Store(self.temp.name,extractor=self.ai)
    def tearDown(self):
        self.store.pool.shutdown(wait=True);self.store.job_pool.shutdown(wait=True);self.temp.cleanup()
    def book(self,name='论文.pdf'):
        b=self.store.upload(name,pdf_bytes(3))
        for _ in range(200):
            b=self.store.book(b['id'])
            if b['status'] in ('ready','failed'):break
            time.sleep(.01)
        self.assertEqual(b['status'],'ready');return b
    def request(self,books,kind='both'):
        return {'teacher':'张老师','confirmed_authorship':True,'kind':kind,'papers':[{'book_id':b['id'],'authors':'张老师、共同作者','year':'2025'} for b in books]}
    def finish(self,job):
        for _ in range(400):
            job=self.store.papers.job(job['id'])
            if job['status'] not in ('queued','running'):return job
            time.sleep(.01)
        self.fail('job did not finish')
    def test_full_multi_paper_questions_cache_drafts_and_restart(self):
        books=[self.book(str(i)+'.pdf') for i in range(3)];req=self.request(books)
        plan=self.store.papers.plan(req);self.assertEqual(plan['max_new_calls'],7);self.assertEqual(self.calls,[])
        job=self.finish(self.store.papers.start(req));self.assertEqual(job['status'],'ready',job['error'])
        self.assertEqual(len(job['result']['points']),6);self.assertEqual(len(job['result']['questions']),5)
        self.assertEqual(len(self.calls),7);self.assertTrue(all(q['length']>=150 for q in job['result']['questions']))
        for q in job['result']['questions']:
            if q['type']=='essays':
                ids={x for s in q['sections'] for x in s['point_ids']};used={p['paper_id'] for p in job['result']['points'] if p['id'] in ids};self.assertEqual(used,{b['id'] for b in books})
        again=self.store.papers.start(req);self.assertEqual(again['id'],job['id']);self.assertEqual(len(self.calls),7)
        self.store.papers.answer(job['id'],'terms1',{'action':'draft','draft':'本机草稿'});self.store.papers.answer(job['id'],'terms1',{'action':'reveal'})
        self.assertEqual(self.store.stats()['learned'],0)
        self.store.pool.shutdown(wait=True);self.store.job_pool.shutdown(wait=True);self.store=Store(self.temp.name,extractor=self.ai)
        loaded=self.store.papers.job(job['id']);self.assertTrue(loaded['answers']['terms1']['revealed']);self.assertEqual(loaded['answers']['terms1']['draft'],'本机草稿')
        self.assertEqual(self.store.papers.plan(req)['max_new_calls'],0)
    def test_invalid_count_demo_and_limits(self):
        b=self.book();req=self.request([b]);bad=copy.deepcopy(req);bad['confirmed_authorship']=False
        self.store.papers.plan(bad)
        bad=copy.deepcopy(req);bad['papers'][0]['authors']='张老师傅'
        self.store.papers.plan(bad)
        bad=copy.deepcopy(req);bad['papers']*=4
        with self.assertRaises(AIError):self.store.papers.start(bad)
        from extractor import DemoExtractor
        self.store.extractor=DemoExtractor()
        with self.assertRaises(AIError):self.store.papers.start(req)
        self.store.extractor=self.ai
        with self.store.db() as db:db.execute('UPDATE pages SET text=? WHERE book_id=? AND number=1',('字'*120001,b['id']))
        with self.assertRaises(AIError):self.store.papers.plan(req)
        self.assertEqual(self.calls,[])
    def test_no_author_year_or_confirmation_needed(self):
        book=self.book()
        req={'teacher':'张老师','kind':'both','papers':[{'book_id':book['id']}]}
        prepared=self.store.papers.prepare(req)
        self.assertEqual(prepared['papers'][0]['authors'],'')
        self.assertEqual(prepared['papers'][0]['year'],'')
        self.assertNotIn('confirmed_authorship',prepared['payload'])
        self.assertEqual(self.store.papers.plan(req)['max_new_calls'],3)
        self.assertEqual(self.calls,[])
        job=self.finish(self.store.papers.start(req))
        self.assertEqual(job['status'],'ready',job['error'])
        self.assertEqual(len(job['result']['questions']),5)
        self.assertEqual(self.calls[0]['paper']['authors'],'')
        self.assertNotEqual(self.calls[0]['paper']['authors'],req['teacher'])
    def test_old_metadata_cache_survives_removed_form(self):
        book=self.book()
        first=self.finish(self.store.papers.start(self.request([book])))
        self.assertEqual(first['status'],'ready',first['error'])
        count=len(self.calls)
        bare={'teacher':'张老师','kind':'both','papers':[{'book_id':book['id']}]}
        again=self.store.papers.start(bare)
        self.assertEqual(again['id'],first['id'])
        self.assertEqual(len(self.calls),count)
        self.assertEqual(self.store.papers.plan(bare)['max_new_calls'],0)
    def test_all_selected_text_is_chunked_without_truncation(self):
        b=self.book()
        text='中文论文段落。'*7000
        with self.store.db() as db:db.execute('UPDATE pages SET text=? WHERE book_id=? AND number=1',(text,b['id']))
        prepared=self.store.papers.prepare(self.request([b]));self.assertGreater(len(prepared['chunks']),1)
        sent=''.join(s['text'] for c in prepared['chunks'] for s in c['sources'] if s['page']==1)
        self.assertEqual(sent,text)
        self.assertTrue(all(sum(len(s['text']) for s in c['sources'])<=CHUNK_CHARS for c in prepared['chunks']))
    def test_failed_step_stops_and_requires_explicit_retry(self):
        books=[self.book('a.pdf'),self.book('b.pdf')];req=self.request(books)
        def fail(content):
            if len(self.calls)==2:raise AIError('synthetic timeout; may be charged')
            return response(fixture_model(content))
        self.failure=fail
        job=self.finish(self.store.papers.start(req));self.assertEqual(job['status'],'failed');self.assertEqual(len(self.calls),2)
        self.assertEqual(len(job['result']['points']),2)
        self.store.papers.start(req);self.assertEqual(len(self.calls),2)
        local=self.finish(self.store.papers.start({**req,'local_only':True}));self.assertEqual(local['status'],'failed');self.assertEqual(len(self.calls),2)
        self.failure=None;done=self.finish(self.store.papers.start({**req,'retry_paid':True}));self.assertEqual(done['status'],'ready',done['error']);self.assertEqual(len(self.calls),6)
    def test_free_recheck_repairs_saved_response_without_key_or_charge(self):
        b=self.book();req=self.request([b])
        self.failure=lambda content:response({'points':[{'bad':'invalid'}]})
        job=self.finish(self.store.papers.start(req));self.assertEqual(job['status'],'failed');self.assertEqual(len(self.calls),1)
        content=self.calls[0]
        with self.store.db() as db:db.execute('UPDATE paper_cache SET response=?',(json.dumps(response(fixture_read(content)),ensure_ascii=False),))
        self.ai.key_loader=lambda:(_ for _ in ()).throw(AssertionError('must not read key'))
        checked=self.finish(self.store.papers.start({**req,'local_only':True}));self.assertEqual(len(self.calls),1);self.assertEqual(checked['status'],'failed')
        self.assertIn('没有可核验',checked['error']);self.assertEqual(len(checked['result']['points']),2)
    def test_invalid_sources_are_skipped_and_wrong_attribution_rejected(self):
        b=self.book();p=self.store.papers.prepare(self.request([b]));chunk=p['chunks'][0];lookup={s['source_id']:s for s in chunk['sources']};data=fixture_read({'sources':chunk['sources']})
        data['points'].append({**data['points'][0],'source_ids':['missing']})
        data['points'].append({**data['points'][0],'category':['invalid']})
        parsed=parse_read(response(data),lookup,chunk['chunk_id']);self.assertEqual(len(parsed['points']),2);self.assertEqual(len(parsed['issues']),2)
        quiz=fixture_quiz({'points':parsed['points']});quiz['terms'][0]['sections'][0]['attribution']='author_claim'
        result=parse_quiz(response(quiz),parsed['points'],[b['id']],'terms');self.assertEqual(len(result['questions']),2);self.assertTrue(any('归属' in s for s in result['issues']))
        for q in quiz['essays']:q['sections'][0]['point_ids']=['unknown']
        with self.assertRaises(AIError):parse_quiz(response(quiz),parsed['points'],[b['id']],'essays')
    def test_multisource_essay_must_cover_every_selected_paper(self):
        books=[self.book('a.pdf'),self.book('b.pdf')];p=self.store.papers.prepare(self.request(books));points=[]
        for c in p['chunks']:points+=parse_read(response(fixture_read(c)),{s['source_id']:s for s in c['sources']},c['chunk_id'])['points']
        quiz=fixture_quiz({'points':points[:2]})
        with self.assertRaises(AIError):parse_quiz(response(quiz),points,[b['id'] for b in books],'essays')
    def test_scenario_essay_validation_and_old_quiz_cache_isolation(self):
        books=[self.book('a.pdf'),self.book('b.pdf')];req=self.request(books,'essays');prepared=self.store.papers.prepare(req);points=[]
        for c in prepared['chunks']:points+=parse_read(response(fixture_read(c)),{x['source_id']:x for x in c['sources']},c['chunk_id'])['points']
        quiz=fixture_quiz({'points':points});ids=[b['id'] for b in books]
        valid=parse_quiz(response(quiz),points,ids,'essays')['questions']
        self.assertEqual(len(valid),2);self.assertEqual(valid[0]['essay_style'],'scenario-v3');self.assertIn('假设情境',valid[0]['question'])
        for mutation in ('scenario','structure','attribution','hint','order'):
            bad=copy.deepcopy(quiz)
            for q in bad['essays']:
                if mutation=='scenario':q.pop('scenario')
                elif mutation=='structure':q['sections']=q['sections'][:2]
                elif mutation=='hint':q['question']='请结合议程设置理论，分析上述现象。'
                elif mutation=='order':q['sections'][1],q['sections'][3]=q['sections'][3],q['sections'][1]
                else:q['sections'][1]['attribution']='author_claim';q['sections'][1]['point_ids']=[p['id'] for p in points if p['attribution']=='author_claim']
            with self.assertRaises(AIError):parse_quiz(response(bad),points,ids,'essays')
        summary=copy.deepcopy(quiz)
        summary['essays'][0]['sections'][1]['text']='作者指出，'+summary['essays'][0]['sections'][1]['text']
        checked=parse_quiz(response(summary),points,ids,'essays')['questions']
        self.assertNotIn('作者指出',checked[0]['sections'][1]['text'])
        self.assertNotIn('本文认为',checked[0]['sections'][1]['text'])
        self.assertEqual(checked[0]['sections'][1]['point_ids'],quiz['essays'][0]['sections'][1]['point_ids'])
        from paper_lab import direct_answer
        self.assertEqual(direct_answer('作者主张：传播条件影响结果。本文认为，应讨论其边界。'),'传播条件影响结果。应讨论其边界。')
        self.assertEqual(direct_answer('不能把作者主张理解为普遍规律。'),'不能把作者主张理解为普遍规律。')
        # Existing old completed tasks keep their IDs, answers and read caches, but are not reused as new-style quizzes.
        from paper_lab import fingerprint,VERSION
        legacy={k:v for k,v in prepared['payload'].items() if k!='essay_style'}
        legacy_key=fingerprint({'v':VERSION,'model':self.ai.model,'request':{**legacy,'confirmed_authorship':True},'chunks':[c['key'] for c in prepared['chunks']]})
        job=self.finish(self.store.papers.start(req));self.assertEqual(job['status'],'ready',job['error']);calls=len(self.calls)
        with self.store.db() as db:
            db.execute('UPDATE paper_jobs SET fingerprint=? WHERE id=?',(legacy_key,job['id']))
            db.execute('UPDATE paper_cache SET key=? WHERE key=?',('quiz:'+legacy_key,'quiz:'+prepared['key']))
        self.store.papers.answer(job['id'],'essays1',{'action':'draft','draft':'旧题草稿保留'})
        plan=self.store.papers.plan(req);self.assertEqual(plan['cached_chunks'],2);self.assertEqual(plan['max_new_calls'],1)
        updated=self.finish(self.store.papers.start(req));self.assertNotEqual(updated['id'],job['id']);self.assertEqual(len(self.calls),calls+1)
        self.assertEqual(self.store.papers.job(job['id'])['answers']['essays1']['draft'],'旧题草稿保留')

    def test_separate_types_reuse_read_cache_and_delete_cascades(self):
        books=[self.book('a.pdf'),self.book('b.pdf')]
        first=self.finish(self.store.papers.start(self.request(books,'terms')));self.assertEqual(first['status'],'ready',first['error']);self.assertEqual(len(first['result']['questions']),3)
        second=self.finish(self.store.papers.start(self.request(books,'essays')));self.assertEqual(second['status'],'ready',second['error']);self.assertEqual(len(self.calls),6)
        self.store.papers.answer(second['id'],'essays1',{'action':'draft','draft':'keep local'})
        self.store.delete(books[1]['id'])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM paper_jobs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM paper_answers').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM paper_cache WHERE job_id IS NOT NULL').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM paper_meta').fetchone()[0],1)
        self.assertEqual(self.store.book(books[0]['id'])['status'],'ready')
    def test_learning_overview_counts_saved_practice_without_mastery(self):
        summary=self.store.stats()['paper_learning']
        self.assertEqual(summary['analyzed_papers'],0);self.assertEqual(summary['recent'],[])
        books=[self.book('a.pdf'),self.book('b.pdf')];req=self.request(books)
        first=self.finish(self.store.papers.start(req));jid=first['id']
        self.store.papers.answer(jid,'terms1',{'action':'draft','draft':'我的练习'})
        self.store.papers.answer(jid,'terms2',{'action':'draft','draft':'  '})
        self.store.papers.answer(jid,'terms1',{'action':'reveal'})
        self.store.papers.answer(jid,'essays1',{'action':'reveal'})
        summary=self.store.stats()['paper_learning']
        self.assertEqual([summary[k] for k in ('analyzed_papers','ready_batches','generated_questions','drafted_questions','viewed_answers')],[2,1,5,1,2])
        self.assertEqual(summary['recent'][0]['id'],jid);self.assertEqual(len(summary['recent'][0]['titles']),2)
        self.store.papers.start(req);self.assertEqual(self.store.stats()['paper_learning'],summary)
        self.assertEqual(self.store.stats()['learned'],0);self.assertEqual(self.store.stats()['mastered'],0)
        second=self.finish(self.store.papers.start(self.request(books,'terms')))
        summary=self.store.stats()['paper_learning'];self.assertEqual(summary['analyzed_papers'],2);self.assertEqual(summary['generated_questions'],8)
        with self.store.db() as db:db.execute("UPDATE paper_jobs SET status='failed' WHERE id=?",(second['id'],))
        summary=self.store.stats()['paper_learning'];self.assertEqual(summary['failed_batches'],1);self.assertEqual(summary['generated_questions'],5)
        self.store.delete(books[1]['id'])
        summary=self.store.stats()['paper_learning'];self.assertEqual(summary['generated_questions'],0);self.assertEqual(summary['drafted_questions'],0);self.assertEqual(summary['analyzed_papers'],0)

    def test_no_secret_stored_and_invalid_answer_rejected(self):
        job=self.finish(self.store.papers.start(self.request([self.book()])));self.assertEqual(job['status'],'ready',job['error'])
        with self.assertRaises(AIError):self.store.papers.answer(job['id'],'unknown',{'action':'reveal'})
        with self.store.db() as db:
            stored=''.join(str(tuple(r)) for r in db.execute('SELECT * FROM paper_cache'))
        self.assertNotIn('synthetic-secret-not-real',stored);self.assertEqual(self.store.ai_status()['calls'],3);self.assertEqual(self.store.ai_status()['total_tokens'],900)
    def test_delete_during_request_stops_following_calls(self):
        books=[self.book('a.pdf'),self.book('b.pdf')];entered=threading.Event();release=threading.Event()
        def block(content):entered.set();release.wait(3);return response(fixture_read(content))
        self.failure=block;job=self.store.papers.start(self.request(books));self.assertTrue(entered.wait(3));self.store.delete(books[0]['id']);release.set();self.store.job_pool.shutdown(wait=True)
        self.assertEqual(len(self.calls),1)
        with self.assertRaises(AIError):self.store.papers.job(job['id'])
if __name__=='__main__':unittest.main()
