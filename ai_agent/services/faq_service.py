import json
import re
from typing import List, Optional

from ..config import FAQ_PATH
from ..schemas import FAQRecord


def _strip_punct(text: str) -> str:
    """Remove punctuation and normalize whitespace."""
    return re.sub(r'[^\w\s]', '', text).strip()


class FAQService:
    def __init__(self, path=FAQ_PATH):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.items: List[FAQRecord] = [FAQRecord(**item) for item in raw]

    _STOP_WORDS = frozenset({
        "а", "в", "и", "на", "не", "о", "по", "с", "у", "к", "из", "за",
        "как", "какой", "какая", "какие", "какое", "что", "это", "вы", "мы", "он", "она", "они", "вас", "ваш",
        "ваша", "ваше", "ваши", "ли", "бы", "же", "то", "да", "нет",
        "до", "от", "для", "при", "без", "под", "над", "об",
    })

    @staticmethod
    def _stem(word: str) -> str:
        """Naive Russian stemming — strip common endings for matching."""
        for suffix in ("ами", "ями", "ого", "его", "ому", "ему",
                       "ой", "ей", "ых", "их", "ом", "ем", "ым", "им",
                       "ах", "ях", "ие", "ые", "ию", "ую",
                       "ов", "ев", "ам", "ям",
                       "ки", "ка", "ке", "ку", "ок",
                       "ии", "ия", "ий",
                       "ть", "ет", "ут", "ют", "ёт",
                       "а", "о", "е", "и", "у", "ы", "й"):
            min_base = 3 if len(suffix) <= 1 else 3
            if len(word) - len(suffix) >= min_base and word.endswith(suffix):
                return word[:-len(suffix)]
        return word

    # Synonym map: user word → FAQ keyword (enables matching variant phrasings)
    _SYNONYMS: dict[str, list[str]] = {
        "доехать": ["адрес", "находится"],
        "добраться": ["адрес", "находится"],
        "проехать": ["адрес", "находится"],
        "метро": ["адрес", "находится"],
        "маршрут": ["адрес", "находится"],
        "часы": ["режим", "работа", "работаете"],
        "график": ["режим", "работа", "работаете"],
        "расписание": ["режим", "работа", "работаете"],
        "выходные": ["режим", "работа", "суббота", "воскресенье"],
        "цена": ["стоимость", "стоит", "цены"],
        "дорого": ["стоимость", "стоит", "цены"],
        "стоит": ["стоимость", "цены"],
        "оплата": ["оплатить", "способы"],
        "карта": ["оплатить", "банковской"],
        "наличные": ["оплатить"],
        "отмена": ["отменить"],
        "отменить": ["отмена"],
        "перенести": ["отменить"],
        "онлайн": ["формат", "дистанционно", "видео"],
        "удалённо": ["формат", "онлайн", "видео"],
        "дистанционно": ["формат", "онлайн", "видео"],
        "анонимно": ["конфиденциальность", "пнд"],
        "тайно": ["конфиденциальность", "анонимно"],
        "пнд": ["анонимно", "конфиденциальность"],
        "дом": ["выезд"],
        "домой": ["выезд"],
        "называется": ["клиника", "psy"],
        "название": ["клиника", "psy"],
    }

    def _expand_synonyms(self, stems: set[str]) -> set[str]:
        """Expand user query stems with synonyms for better matching."""
        expanded = set(stems)
        for word, synonyms in self._SYNONYMS.items():
            word_stem = self._stem(word)
            # Match if stem of synonym key is in query stems, or raw word is
            if word_stem in stems or word in stems:
                for syn in synonyms:
                    expanded.add(self._stem(syn))
                    expanded.add(syn)
        return expanded

    def match(self, user_question: str) -> Optional[FAQRecord]:
        if not user_question:
            return None
        q = _strip_punct(user_question.lower())
        q_words = q.split()
        q_stems = {self._stem(w) for w in q_words if len(w) > 2}
        q_expanded = self._expand_synonyms(q_stems | {w for w in q_words if len(w) > 2})

        best = None
        best_score = 0.0
        for item in self.items:
            score = 0.0
            raw_tokens = _strip_punct(item.question.lower()).split()
            faq_tokens = [t for t in raw_tokens if t not in self._STOP_WORDS and len(t) > 2]
            if not faq_tokens:
                continue
            for token in faq_tokens:
                stem = self._stem(token)
                # Check original stems, expanded synonyms, and substring
                if token in q or stem in q_expanded or token in q_expanded:
                    score += 1
            # Normalize by FAQ token count to avoid bias toward long questions
            normalized = score / len(faq_tokens) if faq_tokens else 0
            if score >= 2 and normalized > best_score:
                best_score = normalized
                best = item
            elif score >= 1 and len(faq_tokens) <= 3 and normalized > best_score:
                best_score = normalized
                best = item
        return best

    def get_all_faq_text(self) -> str:
        """Format all FAQ entries as numbered list for LLM-based matching."""
        lines = []
        for i, item in enumerate(self.items, 1):
            lines.append(f"{i}. В: {item.question}\n   О: {item.answer}")
        return "\n".join(lines)