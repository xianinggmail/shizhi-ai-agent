"""Synthetic evidence/structure/cache regressions; not an academic-quality benchmark."""
import copy,json,unittest
import test_papers as fixtures
from paper_lab import parse_read,READ_STYLE
from deepseek_ai import AIError

class QualityTests(unittest.TestCase):
    setUp=fixtures.PaperTests.setUp
    tearDown=fixtures.PaperTests.tearDown
    book=fixtures.PaperTests.book
    request=fixtures.PaperTests.request
    finish=fixtures.PaperTests.finish
    def prepared(self):
        book=self.book();prepared=self.store.papers.prepare(self.request([book]));chunk=prepared['chunks'][0]
        return book,prepared,chunk,{s['source_id']:s for s in chunk['sources']}
    def test_forged_quote_and_missing_quote_rejected(self):
        _,_,chunk,lookup=self.prepared()
        for mutation in ('invented','missing','wrong_id'):
            data=fixtures.fixture_read(chunk)
            for point in data['points']:
                if mutation=='invented':point['quotes'][0]['text']='这是原文没有出现的捏造引文，不能被接受。'
                elif mutation=='missing':point['quotes']=[]
                else:point['quotes'][0]['source_id']='unknown'
            with self.assertRaises(AIError):parse_read(fixtures.response(data),lookup,'test',strict=True)
    def test_each_source_needs_quote_and_cannot_cross_papers(self):
        _,_,chunk,lookup=self.prepared();data=fixtures.fixture_read(chunk)
        for point in data['points']:point['source_ids']=list(lookup)
        with self.assertRaises(AIError):parse_read(fixtures.response(data),lookup,'test',strict=True)
        other=copy.deepcopy(next(iter(lookup.values())));other.update(source_id='other',book_id='different-paper');lookup['other']=other
        for point in data['points']:
            point['source_ids']=[chunk['sources'][0]['source_id'],'other'];point['quotes']=[{'source_id':sid,'text':lookup[sid]['text'][:80]} for sid in point['source_ids']]
        with self.assertRaises(AIError):parse_read(fixtures.response(data),lookup,'test',strict=True)
    def test_review_requires_category_specific_facets_and_uncertainty_reason(self):
        _,_,chunk,lookup=self.prepared()
        for mutation in ('facets','status','reason'):
            data=fixtures.fixture_review(chunk)
            for point in data['points']:
                if mutation=='facets':point['facets']={'其他维度':'不匹配'}
                elif mutation=='status':point['basis_status']='100%正确'
                else:point.update(basis_status='uncertain',uncertainty='')
            with self.assertRaises(AIError):parse_read(fixtures.response(data),lookup,'test',strict=True,final=True)
    def test_exact_duplicate_merge_preserves_original_quotes(self):
        _,_,chunk,lookup=self.prepared();data=fixtures.fixture_review(chunk)
        data['points'].append(copy.deepcopy(data['points'][0]))
        parsed=parse_read(fixtures.response(data),lookup,'test',strict=True,final=True)
        self.assertEqual(len(parsed['points']),2);self.assertTrue(any('重复' in issue for issue in parsed['issues']))
        for point in parsed['points']:
            for quote in point['quotes']:self.assertIn(quote['text'],lookup[quote['source_id']]['text'])
    def test_review_gets_whole_paper_and_remains_separate_between_papers(self):
        books=[self.book('a.pdf'),self.book('b.pdf')]
        with self.store.db() as db:db.execute('UPDATE pages SET text=? WHERE book_id=? AND number=1',('这是用于分段覆盖检查的合成文本。'*1600,books[0]['id']))
        req=self.request(books);prepared=self.store.papers.prepare(req)
        result=self.finish(self.store.papers.start(req));self.assertEqual(result['status'],'ready',result['error'])
        reviews=[call for call in self.calls if call['stage']=='review'];self.assertEqual(len(reviews),2)
        for call in reviews:
            bid=call['paper']['book_id'];expected=[s for s in prepared['sources'].values() if s['book_id']==bid]
            self.assertEqual(call['sources'],expected)
            self.assertTrue(all(point['paper_id']==bid for point in call['candidates']))
        self.assertEqual(result['result']['analysis_stage'],'reviewed');self.assertEqual(result['result']['analysis_style'],READ_STYLE)
        for point in result['result']['points']:self.assertIn('facets',point);self.assertIn('quotes',point)
    def test_uncertain_points_do_not_enter_quiz(self):
        def transport(content):
            data=fixtures.fixture_model(content)
            if content['stage']=='review':data['points'][0].update(basis_status='uncertain',uncertainty='合成待核对原因：上下文存在限制。')
            return fixtures.response(data)
        self.failure=transport
        result=self.finish(self.store.papers.start(self.request([self.book()])))
        self.assertEqual(result['status'],'ready',result['error'])
        uncertain=[p['id'] for p in result['result']['points'] if p['basis_status']=='uncertain'];self.assertEqual(len(uncertain),1)
        sent=next(call for call in self.calls if call['stage']=='quiz')['points'];self.assertFalse(set(uncertain)&{p['id'] for p in sent})
    def test_all_uncertain_stops_before_paid_quiz(self):
        def transport(content):
            data=fixtures.fixture_model(content)
            if content['stage']=='review':
                for point in data['points']:point.update(basis_status='uncertain',uncertainty='合成测试：没有确定依据。')
            return fixtures.response(data)
        self.failure=transport;result=self.finish(self.store.papers.start(self.request([self.book()])))
        self.assertEqual(result['status'],'failed');self.assertEqual([c['stage'] for c in self.calls],['read','review'])
        self.assertEqual(result['result']['analysis_stage'],'reviewed');self.assertIn('待核对',result['error'])
    def test_review_failure_preserves_candidates_and_free_check_makes_no_call(self):
        def transport(content):
            if content['stage']=='review':raise AIError('合成复核请求中断；可能计费。')
            return fixtures.response(fixtures.fixture_model(content))
        self.failure=transport;req=self.request([self.book()]);result=self.finish(self.store.papers.start(req))
        self.assertEqual(result['status'],'failed');self.assertEqual(result['result']['analysis_stage'],'candidates')
        self.assertEqual(len(result['result']['points']),2);self.assertEqual(self.store.stats()['paper_learning']['analyzed_papers'],0);calls=len(self.calls)
        result=self.finish(self.store.papers.start({**req,'local_only':True}));self.assertEqual(len(self.calls),calls)
        self.assertEqual(result['status'],'failed')
    def test_invalid_json_backslash_is_preserved_without_touching_valid_escapes(self):
        from paper_lab import unpack
        value='表达'+chr(92)+'路径；换行\n保留引号"及合法反斜杠'+chr(92)
        payload=fixtures.response({'note':value})
        payload['choices'][0]['message']['content']=payload['choices'][0]['message']['content'].replace(chr(92)*2+'路径',chr(92)+'路径')
        parsed=unpack(payload);self.assertEqual(parsed['note'],value);self.assertIn('_local_json_recovery',parsed)
        self.assertNotIn('_local_json_recovery',unpack(fixtures.response({'note':value})))
    def test_free_recheck_recovers_cached_invalid_escape_without_key_or_api(self):
        req=self.request([self.book()]);first=self.finish(self.store.papers.start(req));calls=len(self.calls)
        content=self.calls[0];data=fixtures.fixture_read(content);data['note']='保留字面反斜杠'+chr(92)+'中文'
        broken=fixtures.response(data);broken['choices'][0]['message']['content']=broken['choices'][0]['message']['content'].replace(chr(92)*2+'中文',chr(92)+'中文')
        with self.store.db() as db:
            db.execute("UPDATE paper_cache SET status='failed',response=?,result='{}' WHERE key LIKE 'read:%'",(json.dumps(broken,ensure_ascii=False),))
            db.execute("UPDATE paper_jobs SET status='failed' WHERE id=?",(first['id'],))
        self.ai.key_loader=lambda:(_ for _ in ()).throw(AssertionError('free recovery must not access credentials'))
        restored=self.finish(self.store.papers.start({**req,'local_only':True}))
        self.assertEqual(restored['status'],'ready',restored['error']);self.assertEqual(len(self.calls),calls)
        self.assertTrue(any('非法反斜杠' in note for note in restored['result']['notes']))

    def test_json_recovery_does_not_guess_missing_braces_or_unicode(self):
        from paper_lab import unpack
        for bad in ('{"points":[', '{"note":"'+chr(92)+'uXX01"}', '{"note":"line\nline"}'):
            response={'choices':[{'finish_reason':'stop','message':{'content':bad}}]}
            with self.assertRaises(AIError):unpack(response)
    def test_repaired_json_still_requires_real_quotes(self):
        _,_,chunk,lookup=self.prepared();data=fixtures.fixture_review(chunk)
        data['note']='原文包含'+chr(92)+'中文'
        for point in data['points']:point['quotes'][0]['text']='这段引文不在原文中，不能通过来源检查。'
        payload=fixtures.response(data);payload['choices'][0]['message']['content']=payload['choices'][0]['message']['content'].replace(chr(92)*2+'中文',chr(92)+'中文')
        with self.assertRaises(AIError):parse_read(payload,lookup,'test',strict=True,final=True)

    def partial_quiz(self,content):
        data=fixtures.fixture_model(content)
        if content['stage']=='quiz' and content['request']['kind']=='both':
            first_paper=content['points'][0]['paper_id'];allowed={p['id'] for p in content['points'] if p['paper_id']==first_paper}
            for question in data['essays']:
                for section in question['sections']:section['point_ids']=[pid for pid in section['point_ids'] if pid in allowed]
            data['terms'][2]['sections'][0]['attribution']='author_claim'
        return fixtures.response(data)
    def test_partial_quiz_can_supplement_essays_without_losing_terms_or_drafts(self):
        books=[self.book('a.pdf'),self.book('b.pdf')];req=self.request(books);self.failure=self.partial_quiz
        first=self.finish(self.store.papers.start(req));self.assertEqual(first['status'],'ready',first['error']);self.assertEqual(len(first['result']['questions']),2)
        self.assertEqual(first['missing_types'],['essays']);self.assertTrue(first['can_complete_missing'])
        self.assertTrue(any('未融合所有' in issue for issue in first['result']['issues']))
        jid=first['id'];self.store.papers.answer(jid,'terms1',{'action':'draft','draft':'保留已有答案草稿'});self.store.papers.answer(jid,'terms1',{'action':'reveal'})
        calls=len(self.calls);self.store.papers.start(req);self.assertEqual(len(self.calls),calls)
        with self.assertRaises(AIError):self.store.papers.start({**req,'complete_missing':True})
        local=self.finish(self.store.papers.start({**req,'complete_missing':True,'local_only':True}));self.assertEqual(len(self.calls),calls);self.assertEqual(local['status'],'failed')
        self.assertEqual(local['result']['questions'],first['result']['questions'])
        second=self.finish(self.store.papers.start({**req,'retry_paid':True,'complete_missing':True}))
        self.assertEqual(second['status'],'ready',second['error']);self.assertEqual(second['id'],jid)
        self.assertEqual(len(self.calls),calls+1);self.assertEqual(self.calls[-1]['request']['kind'],'essays')
        self.assertEqual(len([q for q in second['result']['questions'] if q['type']=='essays']),2)
        self.assertEqual([q for q in second['result']['questions'] if q['type']=='terms'],first['result']['questions'])
        self.assertEqual(second['answers']['terms1']['draft'],'保留已有答案草稿');self.assertTrue(second['answers']['terms1']['revealed']);self.assertFalse(second['can_complete_missing'])
    def test_failed_supplement_keeps_saved_work_and_can_retry_only_missing_type(self):
        req=self.request([self.book('a.pdf'),self.book('b.pdf')]);self.failure=self.partial_quiz
        first=self.finish(self.store.papers.start(req));self.store.papers.answer(first['id'],'terms1',{'action':'draft','draft':'不要丢失'})
        def failed(content):
            if content['stage']=='quiz' and content['request']['kind']=='essays':raise AIError('合成的出题请求失败')
            return self.partial_quiz(content)
        self.failure=failed;calls=len(self.calls)
        second=self.finish(self.store.papers.start({**req,'complete_missing':True,'retry_paid':True}));self.assertEqual(second['status'],'failed');self.assertEqual(len(self.calls),calls+1)
        self.assertEqual(second['result']['questions'],first['result']['questions']);self.assertEqual(second['answers']['terms1']['draft'],'不要丢失')
        self.failure=self.partial_quiz
        third=self.finish(self.store.papers.start({**req,'retry_paid':True}));self.assertEqual(third['status'],'ready',third['error']);self.assertEqual(len(self.calls),calls+2)
        self.assertEqual(len(third['result']['questions']),4)
    def test_supplement_does_not_create_new_task_or_repeat_complete_types(self):
        req=self.request([self.book()])
        with self.assertRaises(AIError):self.store.papers.start({**req,'complete_missing':True,'retry_paid':True})
        self.assertEqual(self.calls,[])
        first=self.finish(self.store.papers.start(req));calls=len(self.calls)
        second=self.store.papers.start({**req,'complete_missing':True,'retry_paid':True});self.assertEqual(second['id'],first['id']);self.assertEqual(len(self.calls),calls)

    def test_cached_items_envelope_and_quote_source_ids_are_recoverable(self):
        _,_,chunk,lookup=self.prepared();data=fixtures.fixture_review(chunk)
        for row in data['points']:row.pop('source_ids');row['id']='model-supplied-id'
        wrapped={'type':'review','items':data['points']}
        result=parse_read(fixtures.response(wrapped),lookup,'server-id',strict=True,final=True)
        self.assertEqual(len(result['points']),2)
        self.assertTrue(all(p['id'].startswith('server-id') for p in result['points']))
        for point in result['points']:
            for quote in point['quotes']:self.assertIn(quote['text'],lookup[quote['source_id']]['text'])
    def test_envelope_recovery_does_not_bypass_evidence_checks(self):
        _,_,chunk,lookup=self.prepared()
        for mutation in ('invented_quote','unknown_source','missing_quotes','explicit_bad_ids','ambiguous_root'):
            data=fixtures.fixture_review(chunk)
            for row in data['points']:
                row.pop('source_ids')
                if mutation=='invented_quote':row['quotes'][0]['text']='这段原文根本不存在，不能用作概括的依据。'
                elif mutation=='unknown_source':row['quotes'][0]['source_id']='unknown'
                elif mutation=='missing_quotes':row['quotes']=[]
                elif mutation=='explicit_bad_ids':row['source_ids']=['unknown']
            wrapped={'type':'review','items':data['points']}
            if mutation=='ambiguous_root':wrapped['points']='invalid'
            with self.assertRaises(AIError):parse_read(fixtures.response(wrapped),lookup,'test',strict=True,final=True)

    def test_detail_profile_change_reuses_extraction_but_not_old_review_or_quiz(self):
        req=self.request([self.book()]);first=self.finish(self.store.papers.start(req));self.assertEqual(first['status'],'ready',first['error'])
        with self.store.db() as db:
            db.execute("UPDATE paper_jobs SET fingerprint='previous:'||fingerprint WHERE id=?",(first['id'],))
            db.execute("UPDATE paper_cache SET key='previous:'||key WHERE key LIKE 'review:%' OR key LIKE 'quiz:%'")
        plan=self.store.papers.plan(req);self.assertEqual(plan['cached_chunks'],1);self.assertEqual(plan['cached_reviews'],0);self.assertEqual(plan['max_new_calls'],2)
        calls=len(self.calls);second=self.finish(self.store.papers.start(req));self.assertEqual(second['status'],'ready',second['error'])
        self.assertEqual([c['stage'] for c in self.calls[calls:]],['review','quiz'])
        self.assertEqual(second['result']['review_style'],'detail-v1');self.assertNotEqual(first['id'],second['id'])

    def test_old_cached_tasks_and_drafts_preserved_but_not_reused_as_reviewed(self):
        req=self.request([self.book()]);first=self.finish(self.store.papers.start(req));jid=first['id'];self.store.papers.answer(jid,'terms1',{'action':'draft','draft':'旧草稿保留'})
        with self.store.db() as db:
            db.execute("UPDATE paper_jobs SET fingerprint='legacy:'||fingerprint WHERE id=?",(jid,))
            db.execute("UPDATE paper_cache SET key='legacy:'||key")
        plan=self.store.papers.plan(req);self.assertEqual(plan['max_new_calls'],3);self.assertEqual(plan['cached_reviews'],0)
        second=self.finish(self.store.papers.start(req));self.assertNotEqual(first['id'],second['id']);self.assertEqual(len(self.calls),6)
        self.assertEqual(self.store.papers.job(jid)['answers']['terms1']['draft'],'旧草稿保留')

if __name__=='__main__':unittest.main()
