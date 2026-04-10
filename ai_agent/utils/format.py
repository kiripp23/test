"""Human-friendly formatting for voice output."""

import re

_MONTHS_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

_DAY_ORDINAL = {
    1: "первого", 2: "второго", 3: "третьего", 4: "четвёртого",
    5: "пятого", 6: "шестого", 7: "седьмого", 8: "восьмого",
    9: "девятого", 10: "десятого", 11: "одиннадцатого", 12: "двенадцатого",
    13: "тринадцатого", 14: "четырнадцатого", 15: "пятнадцатого",
    16: "шестнадцатого", 17: "семнадцатого", 18: "восемнадцатого",
    19: "девятнадцатого", 20: "двадцатого", 21: "двадцать первого",
    22: "двадцать второго", 23: "двадцать третьего", 24: "двадцать четвёртого",
    25: "двадцать пятого", 26: "двадцать шестого", 27: "двадцать седьмого",
    28: "двадцать восьмого", 29: "двадцать девятого", 30: "тридцатого",
    31: "тридцать первого",
}

_HOUR_WORDS = {
    0: "ноль", 1: "час", 2: "два", 3: "три", 4: "четыре",
    5: "пять", 6: "шесть", 7: "семь", 8: "восемь", 9: "девять",
    10: "десять", 11: "одиннадцать", 12: "двенадцать", 13: "тринадцать",
    14: "четырнадцать", 15: "пятнадцать", 16: "шестнадцать",
    17: "семнадцать", 18: "восемнадцать", 19: "девятнадцать",
    20: "двадцать", 21: "двадцать один", 22: "двадцать два",
    23: "двадцать три",
}

_MINUTE_WORDS = {
    0: "", 10: "десять", 15: "пятнадцать", 20: "двадцать",
    30: "тридцать", 40: "сорок", 45: "сорок пять", 50: "пятьдесят",
}


def format_slot(slot: str) -> str:
    """Format '2026-04-06 10:00' → 'шестого апреля в десять утра'.

    Falls back to original string if parsing fails.
    """
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})", slot)
    if not m:
        return slot

    day = int(m.group(3))
    month = int(m.group(2))
    hour = int(m.group(4))
    minute = int(m.group(5))

    day_str = _DAY_ORDINAL.get(day, str(day))
    month_str = _MONTHS_GENITIVE.get(month, str(month))
    hour_str = _HOUR_WORDS.get(hour, str(hour))

    if minute == 0:
        if 9 <= hour <= 11:
            time_str = f"{hour_str} утра"
        elif 12 <= hour <= 17:
            time_str = f"{hour_str} часов"
        else:
            time_str = f"{hour_str} ноль-ноль"
    else:
        min_str = _MINUTE_WORDS.get(minute, str(minute))
        time_str = f"{hour_str} {min_str}"

    return f"{day_str} {month_str} в {time_str}"


# Simple genitive case for Russian names (covers most patterns)
_GENITIVE_ENDINGS = [
    # Feminine surnames (-ова → -овой, -ева → -евой, -ина → -иной)
    (r"ова$", "овой"), (r"ева$", "евой"), (r"ёва$", "ёвой"), (r"ина$", "иной"),
    # Masculine surnames (-ов → -ова, -ев → -ева)
    (r"ов$", "ова"), (r"ев$", "ева"), (r"ёв$", "ёва"), (r"ин$", "ина"),
    # Adjective-style surnames (Лазебный → Лазебного, Чайковский → Чайковского)
    (r"ский$", "ского"), (r"ной$", "ного"), (r"ный$", "ного"),
    (r"ской$", "ского"), (r"кий$", "кого"), (r"ая$", "ой"),
    (r"й$", "я"), (r"ь$", "я"),
    (r"ич$", "ича"), (r"вна$", "вны"),
    (r"ия$", "ии"),  # Виктория → Виктории
    (r"на$", "ны"),  # Светлана → Светланы
    # Masculine names ending in consonant (Олег → Олега, Пётр → Петра)
    (r"г$", "га"), (r"р$", "ра"), (r"д$", "да"), (r"л$", "ла"),
    (r"н$", "на"), (r"м$", "ма"), (r"т$", "та"), (r"с$", "са"),
    (r"к$", "ка"), (r"п$", "па"),
]

_DATIVE_ENDINGS = [
    # Feminine surnames (-ова → -овой, -ева → -евой, -ина → -иной)
    (r"ова$", "овой"), (r"ева$", "евой"), (r"ёва$", "ёвой"), (r"ина$", "иной"),
    # Masculine surnames (-ов → -ову, -ев → -еву)
    (r"ов$", "ову"), (r"ев$", "еву"), (r"ёв$", "ёву"), (r"ин$", "ину"),
    # Adjective-style surnames (Лазебный → Лазебному, Чайковский → Чайковскому)
    (r"ский$", "скому"), (r"ной$", "ному"), (r"ный$", "ному"),
    (r"ской$", "скому"), (r"кий$", "кому"), (r"ая$", "ой"),
    (r"й$", "ю"), (r"ь$", "ю"),
    (r"ич$", "ичу"), (r"вна$", "вне"),
    (r"ия$", "ии"),  # Виктория → Виктории
    (r"на$", "не"),  # Светлана → Светлане
    (r"г$", "гу"), (r"р$", "ру"), (r"д$", "ду"), (r"л$", "лу"),
    (r"н$", "ну"), (r"м$", "му"), (r"т$", "ту"), (r"с$", "су"),
    (r"к$", "ку"), (r"п$", "пу"),
]


def _decline_word(word: str, endings: list) -> str:
    for pattern, replacement in endings:
        if re.search(pattern, word):
            return re.sub(pattern, replacement, word)
    return word


def decline_name(full_name: str, case: str = "genitive") -> str:
    """Decline a Russian full name (ФИО) into genitive or dative case.

    'Хайретдинов Олег Замильевич' → 'Хайретдинова Олега Замильевича' (genitive)
    """
    endings = _GENITIVE_ENDINGS if case == "genitive" else _DATIVE_ENDINGS
    parts = full_name.split()
    return " ".join(_decline_word(p, endings) for p in parts)


# --- Genitive case for common nouns / adjective phrases (diseases, etc.) ---

_NOUN_GENITIVE = [
    # Neuter -ство → -ства (расстройство → расстройства)
    (r"ство$", "ства"),
    # Neuter -ние → -ния (нарушение → нарушения)
    (r"ние$", "ния"),
    # Neuter -ие → -ия (заболевание → заболевания) — after more specific -ние
    (r"ие$", "ия"),
    # Feminine -ка → -ки (диагностика → диагностики)
    (r"ка$", "ки"),
    # Feminine -а → -ы (травма → травмы)
    (r"а$", "ы"),
]

_ADJ_GENITIVE = [
    # -ое/-ее → -ого/-его (невротическое → невротического, пищевое → пищевого)
    (r"ое$", "ого"),
    (r"ее$", "его"),
    # -ий/-ый → -ого/-его (тревожный → тревожного)
    (r"ий$", "ого"),
    (r"ый$", "ого"),
    # -ая/-яя → -ой/-ей (хроническая → хронической)
    (r"ая$", "ой"),
    (r"яя$", "ей"),
]


def _is_adjective(word: str) -> bool:
    return bool(re.search(r"(ое|ее|ий|ый|ая|яя)$", word))


def decline_phrase_genitive(phrase: str) -> str:
    """Decline a noun phrase to genitive case (rule-based).

    'невротическое расстройство' → 'невротического расстройства'
    'расстройство пищевого поведения' → 'расстройства пищевого поведения'
    """
    words = phrase.split()
    result = []
    for word in words:
        if _is_adjective(word):
            result.append(_decline_word(word, _ADJ_GENITIVE))
        else:
            result.append(_decline_word(word, _NOUN_GENITIVE))
    return " ".join(result)
