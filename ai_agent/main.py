"""CLI test harness. Run with: python -m ai_agent.main"""

from .agent import PsyFamilyAgent


def main():
    agent = PsyFamilyAgent()

    print(f"Ассистент: {agent.get_greeting()}")

    while True:
        user_message = input("Пациент: ").strip()
        if user_message.lower() in {"exit", "quit"}:
            break

        print("Ассистент: ", end="", flush=True)
        for chunk in agent(user_message, session_id="demo"):
            print(chunk.text, end="", flush=True)
        print()

        signal = agent.get_call_signal("demo")
        if signal:
            print(f"[signal: {signal}]")
            break


if __name__ == "__main__":
    main()
