"""
Manual dev-only chat script for testing the ReAct loop end-to-end against
real Groq + real quarter-barber-dev Calendar. Not part of the app or the
test suite -- throwaway, meant to be deleted once the loop is validated.

Any tool result containing an event_id is printed explicitly so real events
created on quarter-barber-dev during testing can be found and deleted by
hand afterward.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.loop import run_agent_turn

SESSION_ID = "638242539"


def main():
    print("Quarter Barber Agent -- dev chat (escribe 'salir' para terminar)\n")

    while True:
        try:
            user_input = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit"):
            break

        response = run_agent_turn(SESSION_ID, user_input)
        print(f"Agente: {response}\n")


if __name__ == "__main__":
    main()