import json
from difflib import SequenceMatcher
from typing import List, Optional

from ..config import DOCTORS_PATH
from ..schemas import DoctorRecord

# Minimum similarity ratio for fuzzy name matching (0.0 - 1.0)
FUZZY_THRESHOLD = 0.75


def _normalize(text: str) -> str:
    """Lowercase and strip whitespace."""
    return text.lower().strip()


def _fuzzy_match(query: str, target: str) -> float:
    """Return similarity ratio between query and target (0.0 - 1.0)."""
    return SequenceMatcher(None, _normalize(query), _normalize(target)).ratio()


def _name_matches(query: str, full_name: str) -> bool:
    """Check if query matches full_name via substring or fuzzy matching.

    Handles cases like:
      "Хайретдинов" in "Хайретдинов Олег Замильевич"  → exact substring
      "Харединов" vs "Хайретдинов"                     → fuzzy match
      "Олег Замильевич" vs parts of full name           → part-by-part
    """
    q = _normalize(query)
    name_lower = _normalize(full_name)

    # Exact word match — each query word must match a name word fully
    # "Хайретдинов" matches "Хайретдинов Олег Замильевич" (word match)
    # "Иванов" does NOT match "Иванович" (different word)
    name_parts = name_lower.split()
    query_parts = q.split()
    if query_parts and all(qp in name_parts for qp in query_parts):
        return True

    # Part-by-part fuzzy match (фамилия, имя — NOT отчество)
    # "Харединов" vs "Хайретдинов" → fuzzy match ✓
    # "Иванов" vs "Иванович" → should NOT match (different word, отчество)
    for qp in query_parts:
        if len(qp) < 3:
            continue
        for i, np in enumerate(name_parts):
            # Skip отчество (3rd part, i>=2) for fuzzy — too many false positives
            # (Иванов/Иванову ≠ Иванович, Петров ≠ Петрович)
            if i >= 2:
                continue
            if _fuzzy_match(qp, np) >= FUZZY_THRESHOLD:
                return True

    return False


class DoctorsService:
    def __init__(self, path=DOCTORS_PATH):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.doctors: List[DoctorRecord] = [DoctorRecord(**item) for item in raw]

    def find_by_name(self, name: str) -> Optional[DoctorRecord]:
        if not name:
            return None
        q = _normalize(name)
        best = None
        best_score = 0.0
        for doctor in self.doctors:
            name_lower = _normalize(doctor.name)
            # Exact word match — highest priority
            q_parts = q.split()
            n_parts = name_lower.split()
            if q_parts and all(qp in n_parts for qp in q_parts):
                score = 2.0
            else:
                # Full name fuzzy score
                score = _fuzzy_match(q, name_lower)
                if score < FUZZY_THRESHOLD:
                    # Part-by-part: best single part match (skip отчество)
                    part_score = 0.0
                    n_parts = name_lower.split()
                    for qp in q.split():
                        if len(qp) < 3:
                            continue
                        for i, np in enumerate(n_parts):
                            if i >= 2:
                                continue
                            part_score = max(part_score, _fuzzy_match(qp, np))
                    score = part_score
            if score >= FUZZY_THRESHOLD and score > best_score:
                best_score = score
                best = doctor
        return best

    def find_by_procedure(self, procedure: str) -> Optional[DoctorRecord]:
        if not procedure:
            return None
        lowered = procedure.lower()
        for doctor in self.doctors:
            if any(lowered == p.lower() or lowered in p.lower() for p in doctor.procedures):
                return doctor
        return None

    def find_by_disease(
        self,
        diseases: List[str],
        patient_age_group: str = "adult",
    ) -> Optional[DoctorRecord]:
        """Find a doctor that treats any of the given diseases.

        Prefers doctors whose patients_category matches patient_age_group.
        Default is "adult" — use "child"/"teen" only if the caller knows the
        patient is minor. Fallback: any matching doctor.
        """
        if not diseases:
            return None
        disease_l = [d.lower() for d in diseases]

        preferred_cat = {
            "adult": "взрослый",
            "child": "детский",
            "teen": "подростковый",
        }.get(patient_age_group, "взрослый")

        matches: List[DoctorRecord] = []
        for doctor in self.doctors:
            doctor_diseases = [d.lower() for d in doctor.diseases]
            hit = False
            for d in disease_l:
                for dd in doctor_diseases:
                    if d == dd or d in dd or dd in d:
                        hit = True
                        break
                    if _fuzzy_match(d, dd) >= FUZZY_THRESHOLD:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                matches.append(doctor)

        if not matches:
            return None

        # Prefer doctors whose patients_category includes the preferred group.
        preferred = [d for d in matches if preferred_cat in (d.patients_category or [])]
        if preferred:
            return preferred[0]
        return matches[0]

    def find_by_specialization(self, query: str) -> Optional[DoctorRecord]:
        """Search for a doctor by specialization keyword in their info field."""
        if not query:
            return None
        q = query.lower()
        for doctor in self.doctors:
            info_lower = doctor.info.lower()
            if q in info_lower:
                return doctor
            if _fuzzy_match(q, info_lower) >= FUZZY_THRESHOLD:
                return doctor
        return None

    def find_similar_doctor(
        self, doctor_name: str, exclude: Optional[List[str]] = None,
    ) -> Optional[DoctorRecord]:
        """Find another doctor with overlapping diseases, excluding already tried."""
        original = self.find_by_name(doctor_name)
        if not original:
            return None
        exclude_names = set(exclude or [])
        exclude_names.add(original.name)
        original_diseases = [d.lower() for d in original.diseases]
        best = None
        best_overlap = 0
        for doctor in self.doctors:
            if doctor.name in exclude_names:
                continue
            their_diseases = [d.lower() for d in doctor.diseases]
            overlap = 0
            for od in original_diseases:
                for td in their_diseases:
                    if od == td or od in td or td in od or _fuzzy_match(od, td) >= FUZZY_THRESHOLD:
                        overlap += 1
                        break
            if overlap > best_overlap:
                best_overlap = overlap
                best = doctor
        return best

    def doctor_treats_disease(self, doctor_name: str, diseases: List[str]) -> bool:
        """Check if a specific doctor treats any of the given diseases."""
        doctor = self.find_by_name(doctor_name)
        if not doctor:
            return False
        doctor_diseases = [d.lower() for d in doctor.diseases]
        for d in diseases:
            dl = d.lower()
            for dd in doctor_diseases:
                if dl == dd or dl in dd or dd in dl:
                    return True
                if _fuzzy_match(dl, dd) >= FUZZY_THRESHOLD:
                    return True
        return False
