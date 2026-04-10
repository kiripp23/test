"""Appointment node — thin wrapper around BookingFSM."""
from typing import Optional

from ..services.bookings_store import BookingsStore
from ..services.doctors_service import DoctorsService
from ..services.medesk_service import MedeskService
from ..state import AgentState
from .booking_fsm import run_booking_fsm


def appointment_node(
    state: AgentState,
    doctors_service: DoctorsService,
    medesk_service: MedeskService,
    bookings_store: Optional[BookingsStore] = None,
) -> AgentState:
    return run_booking_fsm(
        state, doctors_service, medesk_service, bookings_store=bookings_store,
    )
