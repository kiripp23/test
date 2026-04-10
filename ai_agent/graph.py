from langgraph.graph import StateGraph, END

from .state import AgentState
from .nodes.extract import extract_node
from .nodes.router import router_node
from .nodes.appointment import appointment_node
from .nodes.doctor_info import doctor_info_node
from .nodes.faq import faq_node
from .nodes.fallback import fallback_node


def build_graph(
    llm,
    doctors_service,
    medesk_service,
    faq_service,
    disease_matcher=None,
    bookings_store=None,
):
    graph = StateGraph(AgentState)

    graph.add_node("extract", lambda state: extract_node(state, llm))
    graph.add_node("router", router_node)
    graph.add_node(
        "appointment",
        lambda state: appointment_node(
            state, doctors_service, medesk_service, bookings_store=bookings_store,
        ),
    )
    graph.add_node(
        "doctor_info",
        lambda state: doctor_info_node(state, doctors_service, disease_matcher),
    )
    graph.add_node(
        "faq",
        lambda state: faq_node(state, faq_service),
    )
    graph.add_node("fallback", fallback_node)

    graph.set_entry_point("extract")
    graph.add_edge("extract", "router")

    def route_by_intent(state: AgentState):
        return state["final_route"]

    graph.add_conditional_edges(
        "router",
        route_by_intent,
        {
            "appointment": "appointment",
            "doctor_info": "doctor_info",
            "faq": "faq",
            "fallback": "fallback",
        },
    )

    graph.add_edge("appointment", END)
    graph.add_edge("doctor_info", END)
    graph.add_edge("faq", END)
    graph.add_edge("fallback", END)

    return graph.compile()