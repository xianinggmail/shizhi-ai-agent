import copy
from dataclasses import asdict
import hashlib
import json
from unittest import TestCase
from citations import build_sources, restore_quote, parse_cards, ResponseFailure, CardFailure
from deepseek_ai import AIError, LEGACY_PROMPT_VERSION
import test_deepseek as fixture
from test_deepseek import valid_response


def row(sid='p1s1',**overrides):
    result=dict(source_id=sid,title='传播概念',question='请说明传播的条件。',answer='这是合成测试解释，不是真实模型返回的学习内容。',reason='定义为后文提供基础。',priority=1,hint='先说概念，再说条件。')
    result.update(overrides)
    return result


class CitationTests(TestCase):
    def test_source_ids_cover_pages_without_losing_or_rewriting_text(self):
        pages={2:('甲乙丙丁。'*250)+' 结尾\n',8:'这是另一页上的原文内容，用于测试页码定位。'}
        sources=build_sources(pages)
        for page,text in pages.items():
            self.assertEqual(text,''.join(s['text'] for s in sources.values() if s['page']==page))
        cards,report=parse_cards([row('p2s1')],pages,'test')
        self.assertEqual(2,cards[0].page)
        self.assertEqual(sources['p2s1']['text'],cards[0].quote)
        self.assertIn(cards[0].quote,pages[2]);self.assertEqual(1,report['accepted'])

    def test_chinese_whitespace_recovery_retains_original_quote(self):
        original='传播是人们借助符号\n交流信息的过程，意义依赖语境。'
        recovered,changed=restore_quote(original.replace('\n','  '),original)
        self.assertTrue(changed);self.assertEqual(original,recovered)
        cards,report=parse_cards([{**{k:v for k,v in row().items() if k!='source_id'},'page':'1','quote':original.replace('\n',''),'priority':'1'}],{1:original},'test')
        self.assertEqual(original,cards[0].quote)
        self.assertEqual(1,report['restored_quotes'])

    def test_no_fuzzy_matching_or_english_word_boundary_changes(self):
        for quote,text in [('概念不依赖语境，这是一个完整的理论表述。','概念依赖语境，这是一个完整的理论表述。'),('These effects are notable in this study.','These effects are not able in this study.'),('The concept，has meaning.','The concept, has meaning.')]:
            with self.subTest(quote=quote),self.assertRaises(CardFailure):restore_quote(quote,text)

    def test_partial_success_unknown_id_and_missing_field_are_separate(self):
        pages={1:'这是可以核对的合成原文，包含足够长度以验证引用。'}
        incomplete=row();incomplete.pop('answer')
        cards,report=parse_cards([row(),row('p999s1'),incomplete],pages,'test')
        self.assertEqual(1,len(cards));self.assertEqual(2,report['rejected'])
        self.assertEqual(['SOURCE_ID_UNKNOWN','FIELD_MISSING'],[x['code'] for x in report['issues']])
        self.assertEqual([2,3],[x['card'] for x in report['issues']])

    def test_unknown_page_source_conflict_and_duplicate_remain_rejected(self):
        pages={1:'这是可以核对的合成原文，包含足够长度以验证引用。'}
        cards,report=parse_cards([row(),row(page=9),row()],pages,'test')
        self.assertEqual(1,len(cards));self.assertEqual(['SOURCE_PAGE_CONFLICT','DUPLICATE_CARD'],[x['code'] for x in report['issues']])
        with self.assertRaises(ResponseFailure):parse_cards([row('p2s1')],pages,'test')


class RecoveryTests(TestCase):
    setUp=fixture.DeepSeekTests.setUp
    tearDown=fixture.DeepSeekTests.tearDown
    payload=fixture.DeepSeekTests.payload
    session=fixture.DeepSeekTests.session

    def pages(self):
        with self.store.db() as db:return {r['number']:r['text'] for r in db.execute('SELECT number,text FROM pages WHERE book_id=? ORDER BY number',(self.bid,))}

    def insert_legacy(self,status,response=None,cards=None):
        key=hashlib.sha256(json.dumps([self.bid,self.ai.model,LEGACY_PROMPT_VERSION,self.pages()],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        with self.store.db() as db:
            db.execute('INSERT INTO ai_cache(id,book_id,model,status,cards,response,created,attempts,usage) VALUES(?,?,?,?,?,?,0,1,?)',(key,self.bid,self.ai.model,status,json.dumps([asdict(c) for c in (cards or [])],ensure_ascii=False),json.dumps(response,ensure_ascii=False) if response else '',json.dumps({'total_tokens':200})))
        return key

    def test_local_recheck_repairs_saved_legacy_response_without_key_or_charge(self):
        response=valid_response(self.pages())
        content=json.loads(response['choices'][0]['message']['content'])
        for card in content['cards']:card['priority']=str(card['priority'])
        response['choices'][0]['message']['content']=json.dumps(content)
        self.insert_legacy('failed',response)
        self.ai.key_loader=lambda:(_ for _ in ()).throw(AssertionError('Must not read credentials for local recovery'))
        session=self.session(local_only=True)
        self.assertEqual(4,len(session['items']));self.assertEqual([],self.calls)
        self.assertTrue(session['generation_report']['rechecked_locally'])
        with self.store.db() as db:self.assertEqual(1,db.execute('SELECT attempts FROM ai_cache').fetchone()[0])

    def test_missing_old_response_cannot_be_recovered_or_silently_recharged(self):
        self.insert_legacy('failed')
        for args in ({},{'local_only':True}):
            with self.subTest(args=args),self.assertRaises(AIError):self.session(**args)
        self.assertEqual([],self.calls)
        with self.store.db() as db:self.assertEqual(1,db.execute('SELECT attempts FROM ai_cache').fetchone()[0])

    def test_prompt_change_reuses_successful_old_cache_and_progress(self):
        cards,_=self.ai.parse_response(valid_response(self.pages()),self.pages())
        self.insert_legacy('ready',cards=cards)
        self.ai.key_loader=lambda:(_ for _ in ()).throw(AssertionError('Cached cards need no key'))
        session=self.session()
        self.assertEqual(4,len(session['items']));self.assertEqual([],self.calls)

    def test_failure_snapshot_and_free_recheck_keep_attempt_count(self):
        def wrong(payload,key):
            self.calls.append(payload)
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'cards':[row('missing')]})}}], 'usage':{'total_tokens':20},'headers':{'Authorization':'sensitive-test-value'},'api_key':'sensitive-test-value'}
        self.ai.transport=wrong
        with self.assertRaises(AIError) as caught:self.session()
        self.assertTrue(caught.exception.can_recheck)
        self.assertEqual('SOURCE_ID_UNKNOWN',caught.exception.report['issues'][0]['code'])
        with self.store.db() as db:
            cache=db.execute('SELECT * FROM ai_cache').fetchone()
            self.assertNotIn('sensitive-test-value',cache['response']);self.assertTrue(cache['response'])
            self.assertEqual(1,cache['attempts'])
        with self.assertRaises(AIError):self.session(local_only=True)
        self.assertEqual(1,len(self.calls))

    def test_partial_success_is_saved_with_report_and_no_retries(self):
        def mixed(payload,key):
            self.calls.append(payload)
            sid=json.loads(payload['messages'][1]['content'])['sources'][0]['source_id']
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'cards':[row(sid),row('missing')]})}}],'usage':{'total_tokens':30}}
        self.ai.transport=mixed
        session=self.session()
        self.assertEqual(1,len(session['items']));self.assertEqual(1,session['generation_report']['rejected'])
        self.assertEqual(1,len(self.calls))
        with self.store.db() as db:self.assertEqual('ready',db.execute('SELECT status FROM ai_cache').fetchone()[0])

    def test_json_fence_and_structural_failure_are_diagnosable(self):
        response=valid_response(self.pages())
        response['choices'][0]['message']['content']='```json\n'+response['choices'][0]['message']['content']+'\n```'
        cards,report=self.ai.parse_response(response,self.pages())
        self.assertEqual(4,len(cards))
        for response,code in [({'choices':[]},'CHOICES_MISSING'),({'choices':[{'finish_reason':'stop','message':{'content':'{'}}]},'JSON_INVALID')]:
            with self.subTest(code=code),self.assertRaises(AIError) as caught:self.ai.parse_response(response,self.pages())
            self.assertEqual(code,caught.exception.report['issues'][0]['code'])

    def test_blank_range_does_not_create_api_attempt(self):
        with self.store.db() as db:db.execute("UPDATE pages SET text='' WHERE book_id=? AND number=1",(self.bid,))
        from app import AppError
        with self.assertRaises(AppError):self.session(start=1,end=1)
        self.assertEqual([],self.calls)
        with self.store.db() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM ai_cache').fetchone()[0])
