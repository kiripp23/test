"""Entry point for LangGraph Studio — exposes the compiled graph."""

from ai_agent.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from ai_agent.graph import build_graph
from ai_agent.services.doctors_service import DoctorsService
from ai_agent.services.faq_service import FAQService
from ai_agent.services.llm_service import LLMService
from ai_agent.services.medesk_service import MedeskService

graph = build_graph(
    llm=LLMService(api_key=LLM_API_KEY, model=LLM_MODEL, base_url=LLM_BASE_URL),
    doctors_service=DoctorsService(),
    medesk_service=MedeskService(),
    faq_service=FAQService(),
)
