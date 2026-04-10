"""Post-processing guard: prevents bot from claiming a booking that hasn't
actually happened.

Rule: if state["booking_completed"] is not True, strip any sentence from the
LLM-generated text that declares the booking is done ("записываю вас",
"вы записаны", "оформляю запись" и т.п.). The template-driven confirmation
from _complete_booking is allowed because it runs only after successful
BookingsStore.append().
"""
from __future__ import annotations

import re
from typing import Any, Dict

# Phrases that claim booking is completed (all tenses/persons).
_FAKE_BOOK_PATTERNS = [
    r"(?:вы\s+)?записан[аоы]?(?:\s+на\s+приём)?",  # вы записаны, записана, записано
    r"записываю\s+вас",                              # present 1sg
    r"записываем\s+вас",                             # present 1pl
    r"записал[иа]?\s+вас",                           # past: записал, записала, записали
    r"запишу\s+вас",                                 # future 1sg
    r"запишем\s+вас",                                # future 1pl
    r"оформляю\s+(?:вам\s+)?запись",
    r"оформил[иа]?\s+(?:вам\s+)?запись",
    r"запись\s+оформлена",
    r"приём\s+(?:подтверждён|назначен|забронирован)",
    r"уже\s+записан[аоы]?",
]
_FAKE_BOOK_RE = re.compile("|".join(_FAKE_BOOK_PATTERNS), re.IGNORECASE)


_FALLBACK_MISSING_DATA = (
    "Для оформления записи подскажите, пожалуйста, ваше имя и фамилию, "
    "а также дату рождения."
)


def strip_fake_booking_claims(text: str, state: Dict[str, Any]) -> str:
    """Strip sentences that claim booking is done when it isn't.

    If booking_completed is True, text is returned unchanged.
    Otherwise, any sentence containing a booking-claim phrase is removed.
    If after stripping nothing is left, return a safe re-ask template
    instead of returning the original false claim.
    """
    if not text:
        return text
    if state.get("booking_completed") is True:
        return text

    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for p in parts:
        # Questions (sentences ending with "?") are CTAs, not claims — keep them.
        # Only strip declarative sentences with booking-claim phrases.
        if p.rstrip().endswith("?"):
            kept.append(p)
            continue
        if _FAKE_BOOK_RE.search(p):
            continue
        kept.append(p)
    cleaned = " ".join(kept).strip()
    if cleaned:
        return cleaned
    return _FALLBACK_MISSING_DATA
