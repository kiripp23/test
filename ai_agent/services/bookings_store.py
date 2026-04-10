"""Mock bookings persistence.

Simple JSON file (data/bookings.json) that receives one record per successful
booking. The FSM calls `append(...)` ONLY when all required fields are
validated (name + surname + DOB + slot + doctor). This file is the single
source of truth for "has this booking actually happened?".
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import DATA_DIR

logger = logging.getLogger(__name__)

BOOKINGS_PATH: Path = DATA_DIR / "bookings.json"


class BookingValidationError(ValueError):
    """Raised when a booking attempt is missing required fields."""


class BookingsStore:
    def __init__(self, path: Path = BOOKINGS_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def _load(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, bookings: List[Dict[str, Any]]) -> None:
        self.path.write_text(
            json.dumps(bookings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _validate(
        patient_name: Optional[str],
        birth_date: Optional[str],
        slot: Optional[str],
        doctor_name: Optional[str],
    ) -> None:
        missing: List[str] = []
        if not patient_name or len((patient_name or "").strip().split()) < 2:
            missing.append("имя и фамилия")
        if not birth_date:
            missing.append("дата рождения")
        if not slot:
            missing.append("слот")
        if not doctor_name:
            missing.append("врач")
        if missing:
            raise BookingValidationError(
                f"Нельзя оформить запись: не хватает {', '.join(missing)}"
            )

    def append(
        self,
        *,
        session_id: str,
        patient_name: str,
        birth_date: str,
        doctor_name: str,
        slot: str,
    ) -> Dict[str, Any]:
        """Validate and persist a booking. Raises BookingValidationError on invalid input."""
        self._validate(patient_name, birth_date, slot, doctor_name)
        record = {
            "id": f"bk_{int(time.time() * 1000)}",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": session_id,
            "patient_name": patient_name,
            "birth_date": birth_date,
            "doctor_name": doctor_name,
            "slot": slot,
        }
        with self._lock:
            bookings = self._load()
            bookings.append(record)
            self._save(bookings)
        logger.info("booking_created id=%s doctor=%s slot=%s", record["id"], doctor_name, slot)
        return record

    def all(self) -> List[Dict[str, Any]]:
        return self._load()
