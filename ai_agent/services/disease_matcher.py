"""Fuzzy disease matcher — matches user input against the clinic disease catalog.

Usage:
    matcher = DiseaseMatcher()
    results = matcher.match("у меня депрессия и бессонница")
    # [MatchResult(canonical="депрессия", matched_on="депрессия", score=1.0, doctors=[...]),
    #  MatchResult(canonical="нарушения сна", matched_on="бессонница", score=1.0, doctors=[...])]
"""

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Optional

from ..config import DATA_DIR

DISEASES_PATH = DATA_DIR / "diseases.json"
DOCTORS_PATH = DATA_DIR / "doctors.json"

# Minimum similarity for fuzzy match (0.0–1.0)
FUZZY_THRESHOLD = 0.70


@dataclass
class MatchResult:
    canonical: str
    matched_on: str  # which alias or canonical matched
    score: float     # 0.0–1.0
    doctors: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    return text.lower().strip()


def _strip_parens(text: str) -> str:
    """Remove parenthetical clarifications: 'СДВГ (дети)' → 'СДВГ'."""
    return re.sub(r"\s*\(.*?\)", "", text).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


# ── Doctor ranking by seniority ──────────────────────────────
# Higher score = more experienced / senior doctor.
# Extracted from the "info" field of doctors.json.

_TITLE_SCORES: list[tuple[str, int]] = [
    ("доктор медицинских наук", 50),
    ("д.м.н", 50),
    ("кандидат медицинских наук", 30),
    ("к.м.н", 30),
    ("главный врач", 40),
    ("ведущий врач", 25),
    ("высшей квалификационной категории", 20),
    ("высшей категории", 20),
    ("доцент", 15),
    ("профессор", 35),
    ("орден", 10),
    ("московский врач", 10),
    ("член российского общества", 5),
]


def _compute_doctor_rank(doctor: dict) -> int:
    """Score a doctor by titles, degrees, and years of experience."""
    info_lower = doctor.get("info", "").lower()
    score = 0

    # Title-based scoring
    for keyword, points in _TITLE_SCORES:
        if keyword in info_lower:
            score += points

    # Experience field (e.g. "более 30 лет")
    exp = doctor.get("experience", "")
    if exp:
        years_match = re.search(r"(\d+)", exp)
        if years_match:
            score += int(years_match.group(1))

    # Graduation year — earlier = more experience
    grad_match = re.search(r"(?:окончил|образование).*?(\d{4})", info_lower)
    if grad_match:
        grad_year = int(grad_match.group(1))
        years_since = 2026 - grad_year
        score += min(years_since, 40)  # cap at 40

    return score


class DiseaseMatcher:
    def __init__(
        self,
        diseases_path=DISEASES_PATH,
        doctors_path=DOCTORS_PATH,
    ):
        with open(diseases_path, "r", encoding="utf-8") as f:
            self.catalog: list[dict] = json.load(f)

        with open(doctors_path, "r", encoding="utf-8") as f:
            self.doctors: list[dict] = json.load(f)

        # Build flat lookup: normalized_alias → canonical
        self._alias_map: dict[str, str] = {}
        for entry in self.catalog:
            canon = entry["canonical"]
            self._alias_map[_normalize(canon)] = canon
            self._alias_map[_normalize(_strip_parens(canon))] = canon
            for alias in entry.get("aliases", []):
                self._alias_map[_normalize(alias)] = canon
                self._alias_map[_normalize(_strip_parens(alias))] = canon

        # All known terms for fuzzy matching
        self._all_terms: list[str] = list(self._alias_map.keys())

    def _doctors_for_canonical(self, canonical: str) -> list[str]:
        """Find doctors who treat this canonical disease, ranked by seniority."""
        entry = next((e for e in self.catalog if e["canonical"] == canonical), None)
        if not entry:
            return []

        # Collect all terms in this group
        group_terms = {_normalize(canonical), _normalize(_strip_parens(canonical))}
        for alias in entry.get("aliases", []):
            group_terms.add(_normalize(alias))
            group_terms.add(_normalize(_strip_parens(alias)))

        matching_docs = []
        for doc in self.doctors:
            doc_diseases = [_normalize(d) for d in doc.get("diseases", [])]
            doc_diseases_stripped = [_normalize(_strip_parens(d)) for d in doc.get("diseases", [])]
            all_doc = set(doc_diseases) | set(doc_diseases_stripped)
            if group_terms & all_doc:
                matching_docs.append(doc)

        # Sort by rank (most experienced first)
        matching_docs.sort(key=_compute_doctor_rank, reverse=True)
        return [doc["name"] for doc in matching_docs]

    def match_term(self, user_term: str) -> Optional[MatchResult]:
        """Match a single disease term from user input."""
        term = _normalize(user_term)
        if not term or len(term) < 2:
            return None

        # 1. Exact match
        if term in self._alias_map:
            canon = self._alias_map[term]
            return MatchResult(
                canonical=canon,
                matched_on=user_term,
                score=1.0,
                doctors=self._doctors_for_canonical(canon),
            )

        # 2. Substring match (user term inside alias or alias inside user term)
        for alias_norm, canon in self._alias_map.items():
            if len(alias_norm) >= 4 and (term in alias_norm or alias_norm in term):
                score = min(len(term), len(alias_norm)) / max(len(term), len(alias_norm))
                if score >= FUZZY_THRESHOLD:
                    return MatchResult(
                        canonical=canon,
                        matched_on=alias_norm,
                        score=score,
                        doctors=self._doctors_for_canonical(canon),
                    )

        # 3. Fuzzy match
        best_score = 0.0
        best_alias = ""
        best_canon = ""
        for alias_norm, canon in self._alias_map.items():
            if abs(len(term) - len(alias_norm)) > max(len(term), len(alias_norm)) * 0.5:
                continue  # skip wildly different lengths
            score = _similarity(term, alias_norm)
            if score > best_score:
                best_score = score
                best_alias = alias_norm
                best_canon = canon

        if best_score >= FUZZY_THRESHOLD:
            return MatchResult(
                canonical=best_canon,
                matched_on=best_alias,
                score=round(best_score, 2),
                doctors=self._doctors_for_canonical(best_canon),
            )

        return None

    def match(self, user_text: str) -> list[MatchResult]:
        """Match diseases from free-form user text.

        Tries progressively shorter n-grams (4-gram, 3-gram, 2-gram, 1-gram)
        to catch multi-word disease names like 'биполярное аффективное расстройство'.
        """
        words = _normalize(user_text).split()
        results: list[MatchResult] = []
        seen_canonicals: set[str] = set()
        used_word_indices: set[int] = set()

        # Try n-grams from longest to shortest
        for n in range(min(4, len(words)), 0, -1):
            for i in range(len(words) - n + 1):
                # Skip if any word in this n-gram already matched
                indices = set(range(i, i + n))
                if indices & used_word_indices:
                    continue

                phrase = " ".join(words[i:i + n])
                if len(phrase) < 3:
                    continue

                result = self.match_term(phrase)
                if result and result.canonical not in seen_canonicals:
                    results.append(result)
                    seen_canonicals.add(result.canonical)
                    used_word_indices |= indices

        return results
