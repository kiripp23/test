import re
from typing import List


class MedeskService:
    """
    Mock-сервис слотов.
    """

    def __init__(self):
        self.mock_slots = {
            "Хайретдинов Олег Замильевич": [
                "2026-04-06 10:00",
                "2026-04-06 11:00",
                "2026-04-07 14:00",
            ],
            "Тер-Исраелян Алексей Юрьевич": [
                "2026-04-08 10:00",
                "2026-04-09 14:00",
            ],
            "Некрылов Юрий Иванович": [
                "2026-04-08 11:00",
                "2026-04-10 10:00",
            ],
            "Лазебный Даниил Леонидович": [
                "2026-04-08 12:00",
                "2026-04-08 15:00",
                "2026-04-10 09:30",
            ],
            "Нисанова Наталия Николаевна": [
                "2026-04-09 10:00",
                "2026-04-09 15:00",
            ],
            "Вишнивецкая Виктория Михайловна": [
                "2026-04-08 09:00",
                "2026-04-10 11:00",
            ],
            "Шипотько Дмитрий Александрович": [
                "2026-04-07 16:00",
                "2026-04-09 12:00",
            ],
            "Бутова Светлана Юрьевна": [
                "2026-04-08 14:00",
                "2026-04-10 10:00",
            ],
            "Жданов Юрий Александрович": [
                "2026-04-08 13:00",
                "2026-04-10 15:00",
            ],
            "Майорова Карина Владиславовна": [
                "2026-04-09 11:00",
                "2026-04-10 14:00",
            ],
        }

    def get_available_slots(self, doctor_name: str) -> List[str]:
        return self.mock_slots.get(doctor_name, [])

    def find_matching_or_next(self, doctor_name: str, requested_slots: List[str]) -> tuple[list[str], list[str]]:
        """
        Возвращает (совпавшие, альтернативные).
        Поддерживает нечёткое сопоставление дат/времени:
        "6 апреля в 11" должно матчить "2026-04-06 11:00".
        """
        available = self.get_available_slots(doctor_name)
        if not available:
            return [], []

        matched = []
        for slot in available:
            for req in requested_slots:
                if _slot_matches(req, slot):
                    matched.append(slot)
                    break

        alternatives = [slot for slot in available if slot not in matched]
        return matched, alternatives


def _extract_numbers(text: str) -> list[str]:
    """Extract all number strings from text."""
    return re.findall(r'\d+', text)


def _slot_matches(requested: str, available: str) -> bool:
    """Fuzzy match between patient's slot request and actual slot.

    Examples that should match:
      "6 апреля в 11"    <-> "2026-04-06 11:00"
      "6 апреля 11:00"   <-> "2026-04-06 11:00"
      "завтра в 14"      <-> "2026-04-07 14:00"
      "2026-04-06 11:00" <-> "2026-04-06 11:00"  (exact)
    """
    req = requested.strip().lower()
    avail = available.strip().lower()

    # Exact match
    if req == avail:
        return True

    # Extract day and hour from available slot (e.g. "2026-04-06 11:00" -> day=6, hour=11)
    avail_nums = _extract_numbers(avail)
    if len(avail_nums) < 4:
        return False
    avail_day = avail_nums[2]    # "06" -> "6"
    avail_hour = avail_nums[3]   # "11"

    # Extract numbers from requested text
    req_nums = _extract_numbers(req)
    if not req_nums:
        return False

    # Check if day and hour are both present in request numbers
    avail_day_int = str(int(avail_day))   # "06" -> "6"
    avail_hour_int = str(int(avail_hour))  # "11" -> "11"

    req_nums_int = [str(int(n)) for n in req_nums]

    has_day = avail_day_int in req_nums_int
    has_hour = avail_hour_int in req_nums_int

    return has_day and has_hour
