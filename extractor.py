"""Replaceable extraction boundary. Only the offline demonstration is implemented."""
from dataclasses import dataclass
import re
from typing import Protocol


@dataclass(frozen=True)
class ExtractedCard:
    page: int
    title: str
    question: str
    answer: str
    quote: str
    mode: str = 'demo'
    reason: str = ''
    priority: int = 3
    hint: str = ''
    model: str = ''


class Extractor(Protocol):
    def generate(self, pages: dict[int, str]) -> list[ExtractedCard]: ...


class DemoExtractor:
    """Verbatim excerpt splitting, without semantic inference or AI calls."""
    def generate(self, pages):
        cards = []
        for number, text in pages.items():
            spans = re.findall(r'.{1,260}(?:[。！？；\n]|$)|.{1,260}', text, re.S)
            for index, span in enumerate(spans, 1):
                quote = span.strip()
                if len(re.sub(r'\W', '', quote)) < 20:
                    continue
                cards.append(ExtractedCard(
                    page=number, title=f'第 {number} 页 · 原文片段 {index}',
                    question=f'回忆第 {number} 页的这一段：它表达了什么观点？有哪些关键术语与关系？\n可先读过所选页再尝试复述；这是离线模板问题，不是 AI 针对概念出题。',
                    answer=quote, quote=quote))
        return cards


def validate_sources(cards, pages):
    """Reject invented page references and quotations at the storage boundary."""
    if not isinstance(cards,list) or len(cards)>1000:
        raise ValueError('知识点格式无效或数量过多。')
    for card in cards:
        if type(card.page) is not int or card.page not in pages or not isinstance(card.quote,str) or len(card.quote.strip())<10 or card.quote not in pages[card.page]:
            raise ValueError('卡片原文出处无法核对；未保存生成结果。')
        if not all(isinstance(x, str) and x.strip() for x in (card.title,card.question,card.answer)):
            raise ValueError('卡片字段不完整。')
        if len(card.answer)>3000 or len(card.quote)>3000 or len(card.title)>200 or len(card.question)>2000:
            raise ValueError('卡片内容过长。')
        if card.mode not in ('demo', 'ai'):
            raise ValueError('卡片模式不受支持。')
        if type(card.priority) is not int or card.priority not in (1,2,3):
            raise ValueError('学习优先级无效。')
        if not isinstance(card.reason,str) or not isinstance(card.hint,str) or len(card.reason)>1000 or len(card.hint)>500:
            raise ValueError('学习理由或记忆提示格式无效。')
        if card.mode=='ai' and (not card.reason.strip() or not card.hint.strip()):
            raise ValueError('AI 未提供学习理由或记忆提示。')
    return cards
