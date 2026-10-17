"""Free task routing. Selecting a goal does not create a participation event."""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .discovery import integrations

GOALS = {"research": "Understand something", "compare": "Compare options",
         "monitor": "Follow changes", "buy": "Research before buying",
         "arbitrage": "Research an arbitrage idea"}
TOPICS = {
    "ai_economy": ("AI usage and online commerce", "wake"),
    "dollar_funding": ("Dollar funding conditions", "seiche"),
    "market_liquidity": ("Market liquidity", "undertow"),
    "company_evidence": ("Company and institution evidence", "liquilens"),
    "china_economy": ("China's economy", "palimpsest"),
    "science": ("Scientific reference calculations", "noisefloor"),
    "public_datasets": ("Public-interest datasets", "narcoscope"),
}


class TaskChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    goal: Literal["research", "compare", "monitor", "buy", "arbitrage"]
    topic: Literal["dollar_funding", "company_evidence", "market_liquidity", "china_economy", "science", "public_datasets", "ai_economy"]


def recommended_product_ids(goal: str, topic: str) -> list[str]:
    """Choose a small useful starting set; preserve the user's selected topic.

    Product choice: add funding and liquidity context to arbitrage research.
    Keep this policy small so the owner can tune breadth versus first-use focus.
    """
    primary = TOPICS[topic][1]
    candidates = [primary]
    if goal == "arbitrage":
        candidates.extend(["undertow", "seiche"])
    return list(dict.fromkeys(candidates))[:3]


def task_guide(choice: TaskChoice, base: str) -> dict:
    products = {p["id"]: p for p in integrations(base)["products"]}
    selected = [products[p] for p in recommended_product_ids(choice.goal, choice.topic)]
    note = "Read source dates, coverage and missing inputs before drawing conclusions."
    if choice.goal == "buy":
        note = "These tools provide research context. They do not provide a shopping catalogue, live merchant quotes or checkout."
    elif choice.goal == "arbitrage":
        note = "These tools provide funding and liquidity research. They do not establish an executable price spread, after-cost profit or trade authority."
    elif choice.goal == "monitor":
        note = "This is a current evidence check. Revisit it to compare updates; selecting this task does not subscribe you to alerts."
    prompt = " ".join(product["first_prompt"] for product in selected)
    if choice.goal == "compare":
        prompt += " Compare only like-for-like evidence and explain any gaps."
    if choice.goal in {"buy", "arbitrage"}:
        prompt += " Identify the missing evidence needed before a decision; do not execute a transaction."
    return {"schema": "liquilens.task-guide.v1", "free": True, "account_required": False,
            "not_recorded": True, "recommended_products": selected, "starting_prompt": prompt,
            "coverage_note": note, "shareable_context": choice.model_dump(),
            "participation": {"optional": True, "owner_enable_required": True,
                              "contract": base.rstrip("/") + "/v1/participation/contract"}}


def mount_task_guide(app, base):
    @app.get("/v1/task-guide")
    def catalog():
        return {"goals": [{"id": k, "label": v} for k, v in GOALS.items()],
                "topics": [{"id": k, "label": v[0]} for k, v in TOPICS.items()],
                "defaults": {"goal": "research", "topic": "ai_economy"},
                "free": True, "account_required": False, "not_recorded": True}

    @app.post("/v1/task-guide")
    def guide(body: TaskChoice):
        return task_guide(body, base)
