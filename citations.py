"""Deterministic source spans and conservative legacy quote recovery."""
import re
from extractor import ExtractedCard, validate_sources


class CardFailure(ValueError):
    def __init__(self,code,message,field=''):
        super().__init__(message)
        self.code=code
        self.field=field


class ResponseFailure(ValueError):
    def __init__(self,message,report):
        super().__init__(message)
        self.report=report


def build_sources(pages):
    sources={}
    for page,text in pages.items():
        start=0;number=1
        while start<len(text):
            end=min(start+480,len(text))
            if end<len(text):
                boundary=max(text.rfind(mark,start+160,end) for mark in ('。','！','？','\n'))
                if boundary>=start+160:end=boundary+1
            sid=f'p{page}s{number}'
            sources[sid]={'source_id':sid,'page':page,'text':text[start:end]}
            start=end;number+=1
    return sources


def _cjk(ch):
    return bool(ch) and ('\u3400'<=ch<='\u9fff' or '\uf900'<=ch<='\ufaff')


def canonical(text):
    """Normalize whitespace only; preserve English word boundaries and punctuation."""
    chars=[];positions=[];i=0
    while i<len(text):
        if text[i].isspace():
            end=i+1
            while end<len(text) and text[end].isspace():end+=1
            prev=text[i-1] if i else ''
            after=text[end] if end<len(text) else ''
            if prev and after and not (_cjk(prev) or _cjk(after)):
                chars.append(' ');positions.append((i,end))
            i=end
        else:
            chars.append(text[i]);positions.append((i,i+1));i+=1
    return ''.join(chars),positions


def restore_quote(quote,text):
    if not isinstance(quote,str) or len(quote.strip())<10 or len(quote)>3000:
        raise CardFailure('QUOTE_FORMAT','引文为空、太短、过长或格式无效。','quote')
    if quote in text:return quote,False
    needle,_=canonical(quote)
    haystack,mapping=canonical(text)
    offset=haystack.find(needle)
    if not needle or offset<0:
        raise CardFailure('QUOTE_NOT_FOUND','引文在指定页中找不到；已排除单纯空格和换行差异。','quote')
    result=text[mapping[offset][0]:mapping[offset+len(needle)-1][1]]
    # Store the original substring, never a rewritten quotation.
    return result,True


def number(value,field):
    if type(value) is int:return value
    if isinstance(value,str) and re.fullmatch(r'[0-9]+',value.strip()):return int(value.strip())
    raise CardFailure('FIELD_TYPE',{'page':'页码','priority':'学习优先级'}.get(field,field)+'必须是整数。',field)


def row_to_card(row,pages,sources,model):
    if not isinstance(row,dict):raise CardFailure('CARD_FORMAT','卡片必须是JSON对象。')
    recovered=False
    if 'source_id' in row:
        sid=row['source_id']
        if not isinstance(sid,str) or sid not in sources:
            raise CardFailure('SOURCE_ID_UNKNOWN','返回的原文编号不存在。','source_id')
        source=sources[sid]
        page=source['page'];quote=source['text']
        if 'page' in row and number(row['page'],'page')!=page:
            raise CardFailure('SOURCE_PAGE_CONFLICT','原文编号与页码不一致。','page')
        if row.get('quote'):
            quote,recovered=restore_quote(row['quote'],source['text'])
    else:
        if 'page' not in row or 'quote' not in row:
            raise CardFailure('EVIDENCE_MISSING','缺少原文编号，且没有可核验的页码和引文。','source_id')
        page=number(row['page'],'page')
        if page not in pages:raise CardFailure('PAGE_OUT_OF_RANGE','页码不在所选范围内。','page')
        quote,recovered=restore_quote(row['quote'],pages[page])
    values={}
    limits={'title':200,'question':2000,'answer':3000,'reason':1000,'hint':500}
    labels={'title':'概念名称','question':'回忆问题','answer':'核心解释','reason':'学习理由','hint':'记忆提示'}
    for field,limit in limits.items():
        value=row.get(field)
        if not isinstance(value,str) or not value.strip():
            raise CardFailure('FIELD_MISSING',labels[field]+'缺失、为空或不是文字。',field)
        if len(value)>limit:raise CardFailure('FIELD_TOO_LONG',labels[field]+'超过长度限制。',field)
        values[field]=value.strip()
    priority=number(row.get('priority'),'priority')
    if priority not in (1,2,3):raise CardFailure('PRIORITY_RANGE','学习优先级必须为1、2或3。','priority')
    card=ExtractedCard(page=page,quote=quote,mode='ai',priority=priority,model=model,**values)
    try:validate_sources([card],pages)
    except ValueError as exc:raise CardFailure('SOURCE_VALIDATION',str(exc),'quote') from None
    return card,recovered


def parse_cards(rows,pages,model):
    if not isinstance(rows,list) or len(rows)>1000:
        raise ResponseFailure('返回内容缺少有效的cards列表。',{'accepted':0,'rejected':0,'issues':[{'code':'CARDS_FORMAT','message':'cards必须为列表且不能超过1000条。'}]})
    sources=build_sources(pages);cards=[];issues=[];restored=0;seen=set()
    for index,row in enumerate(rows,1):
        if index>8:
            issues.append({'card':index,'code':'CARD_LIMIT','field':'cards','message':'超过本批8条上限，已跳过。'})
            continue
        try:
            card,recovered=row_to_card(row,pages,sources,model)
            identity=(card.page,card.title,card.quote)
            if identity in seen:raise CardFailure('DUPLICATE_CARD','重复卡片已跳过。')
            seen.add(identity);cards.append(card);restored+=int(recovered)
        except CardFailure as exc:
            issues.append({'card':index,'code':exc.code,'field':exc.field,'message':str(exc)})
    report={'accepted':len(cards),'rejected':len(issues),'restored_quotes':restored,'issues':issues,'strategy':'source-id-v2'}
    if rows and not cards:
        detail='；'.join('第'+str(x['card'])+'条：'+x['message'] for x in issues[:3])
        raise ResponseFailure('没有卡片通过核验。'+detail,report)
    return sorted(cards,key=lambda c:(c.priority,c.page)),report
