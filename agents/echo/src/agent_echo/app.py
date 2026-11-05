"""Echo agent: implements the turn contract with no LLM."""

import os
from collections import defaultdict

import uvicorn
from fastapi import FastAPI
from turn_contract import Health, TurnRequest, TurnResponse, install_error_handlers

app = FastAPI()
install_error_handlers(app)
_turns: defaultdict[str, int] = defaultdict(int)


@app.get("/health")
def health() -> Health:
    return Health(name="echo", framework="echo", model="none")


@app.post("/turn")
def turn(req: TurnRequest) -> TurnResponse:
    _turns[req.session_id] += 1
    return TurnResponse(reply=f"echo: {req.text} (turn {_turns[req.session_id]})")


def main() -> None:
    uvicorn.run(app, host=os.environ.get("AGENT_HOST", "127.0.0.1"), port=8201)
