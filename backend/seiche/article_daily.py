"""Publish one evidence-led Seiche article every day.

The dispatch is the desk's fixed-format morning tape.  This module has a
different job: turn the same point-in-time snapshot into a durable article
with an argument, counter-case, historical context, and a useful next step.

There are deliberately two publication modes:

* ``current_analysis`` when the newsroom gate found a material new change;
* ``historical_replay`` when it did not.  A quiet tape is never inflated into
  breaking news.  Instead, the article explains one prior funding episode
  selected from Seiche's own Echo/Tide Tables evidence.

Every market claim comes from the snapshot or a bounded episode guide.  When
an editorial model is configured, it receives that evidence dossier twice:
first as the writer and then as a sceptical standards editor.  A deterministic
publish gate remains the final authority.  If either model pass fails, or if
the copy introduces an unsupported number or link, the desk publishes the
deterministic evidence-led edition instead.  Missing model credentials must
never turn into a missing daily article.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SCHEMA = "seiche.daily-article.v1"
SITE = "https://seiche.info"
REPO_ROOT = Path(__file__).resolve().parents[2]
ARTICLE_DIR = REPO_ROOT / "frontend" / "public" / "articles"
ARTICLE_INDEX = ARTICLE_DIR / "index.json"
DISPATCH_DIR = REPO_ROOT / "frontend" / "public" / "dispatches"
EDITORIAL_MEMORY_URL = "https://api.seiche.info/editorial/memory.json"

EDITORIAL_DIRECTIVES = frozenset({
    "strengthen_thesis",
    "show_mechanism",
    "tighten_evidence_boundary",
    "surface_countercase",
    "name_falsifier",
    "reduce_template_reuse",
    "improve_reader_payoff",
    "soften_funnel",
    "preserve_current_standard",
})
EDITORIAL_MEMORY_AUTHORITY = {
    "styleGuidanceOnly": True,
    "maySupplyFacts": False,
    "maySupplyNumbers": False,
    "mayAuthorizePublication": False,
    "trainingAllowed": False,
}

DEFAULT_EDITORIAL_MODEL = "openai/gpt-5.6-terra"
DEFAULT_EDITORIAL_BASE_URL = "https://openrouter.ai/api/v1"

SOURCE_URLS = (
    f"{SITE}/data/overview.json",
    f"{SITE}/methodology",
    "https://api.seiche.info/api/series/index.json",
    f"{SITE}/dispatches/",
    "https://liquilens.in/",
    "https://liquilens-undertow.com/",
    "https://liquilens-undertow.com/exit/",
    "https://t.me/LiquidityLabDesk",
)

_EDITORIAL_SYSTEM = """You are the lead writer on Seiche's dollar-funding desk.
Write an original digital markets article from the supplied EVIDENCE DOSSIER only.
Do not imitate the wording or house style of any named publication or journalist.
Use the best traits of serious financial journalism: a sharp news lede, one
contestable thesis, a causal mechanism, quantified evidence, a steel-manned
counter-case, a falsifiable next test, and transparent sourcing.

Hard rules:
1. The dossier is the complete factual universe. Never add a number, event,
   quotation, causal detail, or source from memory.
2. Preserve units, signs, dates, as-of labels, validation boundaries, and the
   distinction between observed data, Seiche derivations, scenarios, and analogs.
3. A historical replay must say near the top that it is not breaking news and
   not a forecast. Similarity never means recurrence.
4. Write 900 to 1,300 words of finished Markdown. Do not pad. Do not use tables.
5. Link only to URLs in allowed_source_urls. End with the required product
   handoff, but never turn the article into an advertisement.
6. Return JSON only with three string fields: headline, dek, body_md.
7. Output only reader-facing copy. No reasoning, planning, or AI disclosure.
8. Treat editorial_memory directives as structural standards only. They are not
   evidence and cannot supply a fact, number, event, source, or conclusion.
"""

_EDITORIAL_REVIEW_SYSTEM = """You are Seiche's sceptical standards editor.
Rewrite the submitted draft into a publication-ready article using the supplied
EVIDENCE DOSSIER as the only source of fact. Delete anything unsupported; never
repair a weak sentence by inventing context. Tighten the lede, strengthen the
mechanism, make the counter-case genuinely capable of defeating the thesis, and
make the final test observable. Preserve every evidence boundary and source URL.

Return JSON only with: verdict (the literal string publish), headline, dek,
body_md, and notes (a short list of material edits). Do not return commentary
outside the JSON object.
"""


DRIVER_MECHANISMS = {
    "weather": (
        "The reserve path is a balance-sheet calendar, not a price forecast. "
        "Treasury settlements and tax dates move cash into the Treasury General "
        "Account; unless another balance-sheet leg offsets them, reserve balances "
        "at commercial banks fall. The useful question is therefore dated: how "
        "much capacity may leave the system, on which day, and what does the "
        "overnight tape say when that day arrives?"
    ),
    "kink": (
        "The fitted reserve-demand kink asks where an additional dollar of reserve "
        "scarcity begins to change overnight funding prices more sharply. It is a "
        "modelled boundary, so the live SOFR-versus-IORB spread remains the "
        "independent check. A model can say capacity is thin while the traded rate "
        "still says cash is abundant; that disagreement is information, not an "
        "error to average away."
    ),
    "officialbid": (
        "Foreign official custody and foreign reverse-repo balances reveal whether "
        "official-sector dollars are moving between Fed facilities or leaving that "
        "footprint altogether. The distinction matters because an internal rotation "
        "changes the composition of demand, while an outright decline changes the "
        "amount of official balance-sheet support visible in the system."
    ),
    "ledger": (
        "The Federal Reserve balance sheet is an identity. Reserve balances cannot "
        "fall without another liability or asset leg explaining the move. Seiche "
        "uses that identity to separate a Treasury cash rebuild, foreign official "
        "parking, currency, and the residual instead of attaching a story to one "
        "headline series."
    ),
    "default": (
        "The composite is a map of mechanisms, not a mood index. The leading "
        "component says which piece of the funding system contributes most to the "
        "reading; the overnight tape, backstop usage, and market-pricing layer then "
        "serve as independent checks on that structural signal."
    ),
}


EPISODE_GUIDES = {
    "sep 2019": {
        "short": "the September 2019 repo spike",
        "setup": (
            "Corporate tax payments and Treasury settlement needs converged on a "
            "reserve system with less immediately deployable cash than the calm "
            "headline suggested. Repo rates jumped even though the broader risk-asset "
            "story had not announced a crisis. The episode is useful because the "
            "break appeared first in the price of secured overnight balance sheet."
        ),
        "lesson": (
            "Calendar pressure becomes dangerous when cash is not in the hands of the "
            "dealers and lenders that need to intermediate it. A large aggregate "
            "reserve number can coexist with a local shortage of usable balance sheet."
        ),
    },
    "mar 2020": {
        "short": "the March 2020 dash for cash",
        "setup": (
            "The shock began outside money markets, but the scramble for dollars and "
            "the sale of normally liquid securities pushed the funding system into the "
            "centre of the event. Treasury-market depth deteriorated, cash became the "
            "asset everyone wanted at once, and official facilities had to replace "
            "private intermediation at extraordinary scale."
        ),
        "lesson": (
            "Funding monitors are strongest on propagation, not omniscience. They may "
            "not predict an external shock, but they can show whether the shock is "
            "turning into a balance-sheet and market-liquidity problem."
        ),
    },
    "sep 2025": {
        "short": "the September 2025 tax-date squeeze",
        "setup": (
            "A tax-date drain met Treasury settlement pressure and briefly widened the "
            "gap between secured and unsecured overnight rates. The move was smaller "
            "than September 2019, but it supplied a cleaner modern test of the same "
            "mechanism: dated cash removal, limited dealer capacity, then a visible "
            "funding response."
        ),
        "lesson": (
            "A calendar event is not itself a crisis signal. Its value is as a "
            "pre-registered test: state the date and the expected transmission path, "
            "then grade whether the funding tape actually moved."
        ),
    },
    "apr 2025": {
        "short": "the April 2025 tariff-shock basis unwind",
        "setup": (
            "A policy shock changed risk and volatility faster than leveraged relative-"
            "value books could adjust. Treasury selling, basis pressure, and dealer "
            "intermediation became one transmission chain rather than three separate "
            "market stories."
        ),
        "lesson": (
            "The relevant funding question is not only how large a position is. It is "
            "whether many holders need the same exit while dealer balance sheets are "
            "absorbing the same volatility shock."
        ),
    },
    "dec 2025": {
        "short": "the December 2025 year-end squeeze",
        "setup": (
            "Year-end reporting incentives reduced the willingness to intermediate "
            "exactly when balance-sheet demand was seasonally high. Heavy use of the "
            "Standing Repo Facility made the constraint observable rather than "
            "inferred from a single market rate."
        ),
        "lesson": (
            "Backstop usage changes the interpretation of a tight print. It can show "
            "that private capacity is constrained while also limiting the probability "
            "that the constraint becomes a disorderly shortage."
        ),
    },
    "mar 2023": {
        "short": "the March 2023 regional-bank run",
        "setup": (
            "Deposit flight turned unrealised duration losses into an immediate cash "
            "problem. The funding system responded through discount-window borrowing "
            "and new official facilities while markets repriced which institutions "
            "could survive the same asset-liability mismatch."
        ),
        "lesson": (
            "System liquidity and institution liquidity are different layers. A "
            "facility can stabilise the aggregate plumbing while individual balance "
            "sheets remain impaired, which is why the handoff from Seiche to LiquiLens "
            "matters."
        ),
    },
}


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _clean(value: Any) -> str:
    return (str(value or "").replace(" — ", ", ").replace("—", ", ")
            .replace(" – ", ", ").replace("–", "-").strip())


def _fmt(value: Any, digits: int = 1) -> str:
    number = _number(value)
    return "unavailable" if number is None else f"{number:,.{digits}f}"


def _pct(value: Any, digits: int = 1) -> str:
    number = _number(value)
    return "unavailable" if number is None else f"{number * 100:.{digits}f}%"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:72].rstrip("-") or "funding-analysis"


def _guide_for(episode: str) -> dict[str, str]:
    key = episode.lower()
    for needle, guide in EPISODE_GUIDES.items():
        if needle in key:
            return guide
    return {
        "short": _clean(episode) or "a prior funding episode",
        "setup": (
            "This episode sits in Seiche's registered funding-event library because "
            "the plumbing, the price of cash, or the capacity to intermediate changed "
            "materially inside its window. The article uses only the episode label and "
            "the values published by today's similarity engines."
        ),
        "lesson": (
            "The replay is useful as a mechanism check. Similar states can end in "
            "different outcomes, so the historical match narrows the questions to ask; "
            "it does not answer them in advance."
        ),
    }


def _editorial_model_config() -> dict[str, str] | None:
    """Return an OpenAI-compatible editorial route, if explicitly configured.

    The generic names let the same secret/variables drive all three desks.  The
    Seiche-prefixed names keep the module compatible with the terminal's
    existing assistant configuration.  We intentionally do not silently read
    OPENAI_API_KEY or OPENROUTER_API_KEY: publishing is a distinct use of a
    credential and must be opted into by the operator.
    """
    key = (
        os.environ.get("EDITORIAL_LLM_API_KEY")
        or os.environ.get("SEICHE_EDITORIAL_LLM_API_KEY")
        or os.environ.get("SEICHE_LLM_API_KEY")
    )
    base = (
        os.environ.get("EDITORIAL_LLM_BASE_URL")
        or os.environ.get("SEICHE_EDITORIAL_LLM_BASE_URL")
        or os.environ.get("SEICHE_LLM_BASE_URL")
    )
    if not key and not base:
        return None
    return {
        "key": key or "",
        "base_url": (base or DEFAULT_EDITORIAL_BASE_URL).rstrip("/"),
        "model": (
            os.environ.get("EDITORIAL_LLM_MODEL")
            or os.environ.get("SEICHE_EDITORIAL_LLM_MODEL")
            or os.environ.get("SEICHE_LLM_MODEL")
            or DEFAULT_EDITORIAL_MODEL
        ),
        "review_model": (
            os.environ.get("EDITORIAL_REVIEW_MODEL")
            or os.environ.get("SEICHE_EDITORIAL_REVIEW_MODEL")
            or os.environ.get("EDITORIAL_LLM_MODEL")
            or os.environ.get("SEICHE_EDITORIAL_LLM_MODEL")
            or DEFAULT_EDITORIAL_MODEL
        ),
    }


def _json_object(text: str) -> dict:
    """Parse a model JSON object without accepting prose around it."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
        candidate = re.sub(r"\s*```$", "", candidate)
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("editorial model returned a non-object")
    return value


def _memory_sha(value: dict) -> str:
    body = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def validate_editorial_memory(payload: dict, *, product: str = "seiche",
                              now: datetime | None = None) -> dict:
    """Reduce public MyQuant memory to a closed, non-factual directive receipt."""
    required = {
        "schema", "generated_at", "source_run_id", "source_manifest_sha256",
        "rubric_version", "global_directives", "products", "authority",
        "memory_fingerprint",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("editorial memory has an invalid field set")
    if (
        payload.get("schema") != "mqdnse.editorial-memory.v1"
        or payload.get("rubric_version") != "mqdnse.editorial-rubric.v1"
        or payload.get("authority") != EDITORIAL_MEMORY_AUTHORITY
    ):
        raise ValueError("editorial memory has an invalid contract or authority")
    for field in ("source_run_id", "source_manifest_sha256", "memory_fingerprint"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(payload.get(field) or "")):
            raise ValueError(f"editorial memory has an invalid {field}")
    identity = {
        key: value for key, value in payload.items() if key != "memory_fingerprint"
    }
    if _memory_sha(identity) != payload["memory_fingerprint"]:
        raise ValueError("editorial memory fingerprint does not match its content")
    generated_at = datetime.fromisoformat(
        payload["generated_at"].replace("Z", "+00:00")
    )
    if generated_at.tzinfo is None:
        raise ValueError("editorial memory generation clock lacks a timezone")
    held_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_seconds = (held_now - generated_at.astimezone(timezone.utc)).total_seconds()
    if age_seconds < -300 or age_seconds > 72 * 60 * 60:
        raise ValueError("editorial memory is future-dated or stale")

    def directives(value: object, field: str) -> list[str]:
        if (
            not isinstance(value, list)
            or len(value) > 3
            or len(set(value)) != len(value)
            or any(row not in EDITORIAL_DIRECTIVES for row in value)
        ):
            raise ValueError(f"editorial memory has invalid {field}")
        return list(value)

    global_rows = directives(payload["global_directives"], "global directives")
    products = payload.get("products")
    if not isinstance(products, dict):
        raise ValueError("editorial memory products must be an object")
    product_rows: list[str] = []
    held = products.get(product)
    if held is not None:
        expected = {
            "articleId", "articleRevisionSha256", "criticStatus", "verdict",
            "score", "directives",
        }
        if not isinstance(held, dict) or set(held) != expected:
            raise ValueError("editorial memory product receipt is malformed")
        if not re.fullmatch(
            r"sha256:[a-f0-9]{64}",
            str(held.get("articleRevisionSha256") or ""),
        ):
            raise ValueError("editorial memory product revision is invalid")
        product_rows = directives(held["directives"], "product directives")
        if held.get("criticStatus") != "validated_shadow_critique" and product_rows:
            raise ValueError("unvalidated editorial memory carries directives")
    combined = list(dict.fromkeys([*product_rows, *global_rows]))[:3]
    return {
        "status": "applied" if combined else "empty",
        "source_run_id": payload["source_run_id"],
        "memory_fingerprint": payload["memory_fingerprint"],
        "rubric_version": payload["rubric_version"],
        "directives": combined,
    }


def fetch_editorial_memory(url: str = EDITORIAL_MEMORY_URL) -> dict:
    if url != EDITORIAL_MEMORY_URL:
        raise ValueError("editorial memory URL is not allowlisted")
    try:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "seiche-editorial/1",
            },
        )
        with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed URL
            if int(getattr(response, "status", 200)) != 200:
                raise ValueError("editorial memory returned a non-200 response")
            body = response.read(256 * 1024 + 1)
        if len(body) > 256 * 1024:
            raise ValueError("editorial memory exceeded its byte budget")
        return validate_editorial_memory(json.loads(body))
    except Exception as exc:  # daily continuity records an unavailable lesson lane
        return {
            "status": "unavailable",
            "source_run_id": None,
            "memory_fingerprint": None,
            "rubric_version": "mqdnse.editorial-rubric.v1",
            "directives": [],
            "reason": f"{type(exc).__name__}: {str(exc)[:180]}",
        }


def _complete_json(config: dict[str, str], messages: list[dict], *, max_tokens: int) -> dict:
    """Call one OpenAI-compatible pass and require a JSON object response."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "seiche-daily-editorial/1.0",
    }
    if config["key"]:
        headers["Authorization"] = f"Bearer {config['key']}"
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0.35,
        "max_tokens": max_tokens,
        "reasoning_effort": os.environ.get("EDITORIAL_REASONING_EFFORT", "low"),
        "response_format": {"type": "json_object"},
    }
    endpoint = f"{config['base_url']}/chat/completions"

    def request_once(body: dict) -> dict:
        for attempt in range(3):
            request = Request(
                endpoint,
                data=json.dumps(body).encode(),
                headers=headers,
                method="POST",
            )
            try:
                with urlopen(request, timeout=120) as response:  # noqa: S310 - operator-configured HTTPS endpoint
                    return json.loads(response.read())
            except HTTPError as exc:
                if exc.code != 429 or attempt == 2:
                    raise
                detail = exc.read().decode(errors="replace")
                match = re.search(r"try again in\s+([0-9.]+)s", detail, flags=re.I)
                try:
                    retry_after = float(exc.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    retry_after = float(match.group(1)) if match else 20.0 * (attempt + 1)
                time.sleep(min(60.0, max(2.0, retry_after + 1.0)))
        raise RuntimeError("editorial model retry loop exhausted")

    try:
        envelope = request_once(payload)
    except HTTPError as exc:
        # Some OpenAI-compatible hosts do not implement response_format.  A
        # single retry without it preserves portability while the prompt still
        # requires strict JSON.
        if exc.code not in {400, 404, 422}:
            raise
        payload.pop("response_format")
        envelope = request_once(payload)
    content = envelope["choices"][0]["message"]["content"]
    return _json_object(content)


def build_evidence_dossier(snap: dict, story: dict, *, date: str, echo: dict,
                           article_type: str,
                           editorial_memory: dict | None = None) -> dict:
    """Build the model's complete and auditable factual universe."""
    try:
        # Reuse the live assistant's compact board extract when the backend's
        # optional HTTP dependencies are installed.
        from .ai import context_pack

        board = context_pack(snap)
    except ImportError:
        # Article construction itself stays stdlib-only.  This lean equivalent
        # is sufficient for deterministic builds and isolated test runners.
        engines = snap.get("engines") or {}
        deep = snap.get("deep") or {}
        board = {
            "generated_at": snap.get("generated_at"),
            "composite": engines.get("composite"),
            "headline": snap.get("headline"),
            "weather": engines.get("weather"),
            "kink": engines.get("kink"),
            "echo": engines.get("echo"),
            "tell": deep.get("tell"),
            "tide_tables": deep.get("tidetables"),
            "swell": deep.get("swell"),
            "bathymetry": deep.get("bathymetry"),
            "faults": snap.get("faults"),
        }
    guide = _guide_for(str(echo.get("episode") or ""))
    dispatch_url = str(story.get("canonical_url") or f"{SITE}/dispatches/")
    allowed_urls = list(dict.fromkeys((*SOURCE_URLS, dispatch_url)))
    return {
        "schema": "seiche.editorial-dossier.v1",
        "desk": "Seiche",
        "desk_question": "Is dollar-funding capacity tightening, and through which mechanism?",
        "publication_date": date,
        "article_type": article_type,
        "editorial_memory": {
            key: (editorial_memory or {}).get(key)
            for key in (
                "status", "source_run_id", "memory_fingerprint",
                "rubric_version", "directives",
            )
        },
        "mode_instruction": (
            "Lead on the material new board change and test whether independent market evidence confirms it."
            if article_type == "current_analysis"
            else "Teach the selected prior episode. State explicitly that today's tape did not clear the full-story gate."
        ),
        "newsworthiness": story.get("newsworthiness") or {},
        "dispatch": {
            key: story.get(key)
            for key in ("headline", "dek", "canonical_url", "editorial_class", "published_at", "limitations")
        },
        "editorial_read": snap.get("editorial") or {},
        "board_context": board,
        "selected_historical_episode": {
            "engine_output": echo,
            "bounded_desk_guide": guide,
            "warning": (
                "The guide is background copy maintained by the desk. The engine fields are "
                "construction-PIT comparisons, not publication-vintage backtests or forecasts."
            ),
        },
        "allowed_source_urls": allowed_urls,
        "required_sections": [
            "The mechanism or historical setup",
            "Quantified evidence with as-of context",
            "The strongest counter-case",
            "A falsifiable next test",
            "Follow the pressure chain",
            "Sources, method, and limits",
        ],
        "product_boundaries": {
            "Seiche": "system dollar-funding capacity",
            "LiquiLens": "institution and lender balance-sheet risk",
            "Undertow": "market liquidity, crowding, and exit capacity",
        },
    }


def _draft_with_model(dossier: dict, config: dict[str, str]) -> dict:
    """Run a writer pass and an independent standards-editor rewrite."""
    writer = _complete_json(
        config,
        [
            {"role": "system", "content": _EDITORIAL_SYSTEM},
            {"role": "user", "content": "EVIDENCE DOSSIER:\n" + json.dumps(dossier, ensure_ascii=False)},
        ],
        max_tokens=4200,
    )
    for field in ("headline", "dek", "body_md"):
        if not isinstance(writer.get(field), str) or not writer[field].strip():
            raise ValueError(f"writer omitted {field}")

    review_config = {**config, "model": config.get("review_model") or config["model"]}
    reviewer = _complete_json(
        review_config,
        [
            {"role": "system", "content": _EDITORIAL_REVIEW_SYSTEM},
            {
                "role": "user",
                "content": (
                    "EVIDENCE DOSSIER:\n" + json.dumps(dossier, ensure_ascii=False)
                    + "\n\nDRAFT TO AUDIT AND REWRITE:\n" + json.dumps(writer, ensure_ascii=False)
                ),
            },
        ],
        max_tokens=4400,
    )
    if str(reviewer.get("verdict") or "").lower() != "publish":
        raise ValueError("standards editor did not return a publish verdict")
    for field in ("headline", "dek", "body_md"):
        if not isinstance(reviewer.get(field), str) or not reviewer[field].strip():
            raise ValueError(f"standards editor omitted {field}")
    return {
        "headline": reviewer["headline"].strip(),
        "dek": reviewer["dek"].strip(),
        "body_md": reviewer["body_md"].strip(),
        "review_notes": reviewer.get("notes") if isinstance(reviewer.get("notes"), list) else [],
    }


_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\$?\d[\d,]*(?:\.\d+)?(?:%|bps?|bp|bn|tn|[BMK])?", re.I)


def _numeric_values(value: Any) -> set[float]:
    """Collect comparable numeric values from nested evidence, including % forms."""
    found: set[float] = set()

    def add(number: float) -> None:
        if number == number and abs(number) != float("inf"):
            found.add(round(number, 8))
            if 0 <= number <= 1:
                found.add(round(number * 100, 8))

    def walk(item: Any) -> None:
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, (int, float)):
            add(float(item))
            return
        if isinstance(item, dict):
            for child in item.values():
                walk(child)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                walk(child)
            return
        if isinstance(item, str):
            clean = _URL_RE.sub("", item)
            for match in _NUMBER_RE.finditer(clean):
                raw = re.sub(r"[^0-9.+-]", "", match.group(0).replace(",", ""))
                try:
                    add(float(raw))
                except ValueError:
                    continue

    walk(value)
    return found


def model_grounding_issues(copy: dict, dossier: dict) -> list[str]:
    """Reject model copy that escapes the dossier's numbers or source list."""
    combined = "\n".join(str(copy.get(key) or "") for key in ("headline", "dek", "body_md"))
    without_urls = _URL_RE.sub("", combined)
    allowed_numbers = _numeric_values(dossier)
    unsupported_numbers: list[str] = []
    for match in _NUMBER_RE.finditer(without_urls):
        token = match.group(0)
        raw = re.sub(r"[^0-9.+-]", "", token.replace(",", ""))
        try:
            number = round(float(raw), 8)
        except ValueError:
            continue
        if number not in allowed_numbers:
            unsupported_numbers.append(token)

    allowed_urls = set(dossier.get("allowed_source_urls") or [])
    unsupported_urls = sorted({url.rstrip(".,;") for url in _URL_RE.findall(combined)} - allowed_urls)
    issues = []
    if unsupported_numbers:
        issues.append("unsupported numbers: " + ", ".join(sorted(set(unsupported_numbers))[:8]))
    if unsupported_urls:
        issues.append("unsupported links: " + ", ".join(unsupported_urls[:5]))
    return issues


def _recent_topics(index_path: Path = ARTICLE_INDEX, limit: int = 7,
                   exclude_date: str | None = None) -> list[str]:
    try:
        rows = json.loads(index_path.read_text())
    except (OSError, ValueError):
        return []
    eligible = [row for row in rows if row.get("date") != exclude_date]
    return [str(row.get("topic") or "") for row in eligible[:limit] if row.get("topic")]


def _select_echo(snap: dict, date: str, recent: list[str]) -> dict:
    matches = [row for row in ((snap.get("engines", {}).get("echo") or {}).get("matches") or [])
               if isinstance(row, dict) and row.get("episode")]
    if not matches:
        analogs = [row for row in ((snap.get("deep", {}).get("tidetables") or {}).get("analogs") or [])
                   if isinstance(row, dict) and row.get("episode")]
        matches = [{
            "episode": row.get("episode"),
            "date": row.get("end_date"),
            "lead_days": None,
            "similarity": None,
            "max_move_5bd_bp": row.get("max_move_5bd_bp"),
            "event_within_5bd": row.get("event_within_5bd"),
        } for row in analogs]
    if not matches:
        return {"episode": "the September 2019 repo spike", "date": "2019-09-17"}

    unused = [row for row in matches if str(row.get("episode")) not in set(recent)]
    pool = unused or matches
    seed = int(hashlib.sha256(f"seiche-article:{date}".encode()).hexdigest(), 16)
    picked = dict(pool[seed % len(pool)])

    # Enrich an Echo match with the nearest Tide Tables row from the same
    # named episode.  The two engines answer different questions; keeping the
    # fields named prevents a similarity score from masquerading as an outcome.
    episode_key = str(picked.get("episode") or "").lower()
    for analog in ((snap.get("deep", {}).get("tidetables") or {}).get("analogs") or []):
        if not isinstance(analog, dict) or not analog.get("episode"):
            continue
        akey = str(analog["episode"]).lower()
        if episode_key in akey or akey in episode_key:
            picked.setdefault("analog_end_date", analog.get("end_date"))
            picked.setdefault("max_move_5bd_bp", analog.get("max_move_5bd_bp"))
            picked.setdefault("event_within_5bd", analog.get("event_within_5bd"))
            break
    return picked


def _evidence_paragraphs(editorial: dict) -> str:
    rows = editorial.get("evidence") or []
    if not rows:
        return (
            "Today's snapshot did not publish the structured evidence rows this "
            "article normally uses. The article therefore does not replace them with "
            "a narrative inference; the live board remains the source of record."
        )
    parts = []
    for row in rows[:4]:
        label = _clean(row.get("label") or "Evidence")
        claim = _clean(row.get("claim") or "")
        asof = _clean(row.get("asof") or "date unavailable")
        source = _clean(row.get("source") or "published source")
        parts.append(f"**{label}.** {claim} The observation is dated {asof}; source: {source}.")
    return "\n\n".join(parts)


def _countercase(editorial: dict) -> str:
    rows = editorial.get("countercase") or []
    if not rows:
        return (
            "The strongest counter-case is the absence of broad confirmation. A "
            "structural funding signal without a traded-rate or facility-usage response "
            "should remain a watch condition, not be promoted into a stress event."
        )
    return "\n\n".join(
        f"**{_clean(row.get('source') or 'Independent check')}, {_clean(row.get('asof') or 'date unavailable')}.** "
        f"{_clean(row.get('claim'))}"
        for row in rows[:3]
    )


def _watch_section(editorial: dict) -> str:
    rows = editorial.get("watch") or []
    if not rows:
        return (
            "No dated pressure window cleared the board's publication bar today. The "
            "next article will grade the same overnight spreads, reserve identity, and "
            "facility usage rather than inventing a catalyst."
        )
    parts = []
    for row in rows[:3]:
        bits = [f"**{_clean(row.get('date') or 'date unavailable')}:** {_clean(row.get('label'))}"]
        if _number(row.get("settlement_b")) is not None:
            bits.append(f"published settlement amount ${_fmt(row['settlement_b'], 0)}B")
        if _number(row.get("worst_case_reserves_b")) is not None:
            bits.append(f"scenario low ${_fmt(row['worst_case_reserves_b'], 0)}B of reserves")
        parts.append("; ".join(bits) + ".")
    return "\n\n".join(parts)


def _historical_context(snap: dict, echo: dict) -> str:
    tide = snap.get("deep", {}).get("tidetables") or {}
    odds = tide.get("event_odds") or {}
    similarity = _number(echo.get("similarity"))
    lead = _number(echo.get("lead_days"))
    move = _number(echo.get("max_move_5bd_bp"))
    clauses = []
    if similarity is not None:
        clauses.append(f"Echo reports a trajectory similarity of {similarity:.3f}")
    if lead is not None:
        clauses.append(f"the comparison window sits {lead:.0f} days from the registered episode date")
    if move is not None:
        clauses.append(f"the matched Tide Tables window was followed by a maximum five-business-day spread move of {move:+.1f}bp")
    if _number(odds.get("p")) is not None and _number(odds.get("n")) is not None:
        clauses.append(
            f"across all {_fmt(odds['n'], 0)} current neighbours, {_pct(odds['p'])} had a registered funding event within five business days"
        )
    if not clauses:
        return "The historical engines published the episode label but no comparable numeric row today."
    return "; ".join(clauses) + ". These are construction-PIT comparisons, not a forecast."


def _current_body(snap: dict, story: dict, echo: dict) -> tuple[str, str, str]:
    editorial = snap.get("editorial") or {}
    thesis = _clean(editorial.get("thesis") or story.get("headline") or "The funding board changed")
    headline = thesis.rstrip(".")
    standfirst = _clean(editorial.get("standfirst") or story.get("dek") or "")
    confidence = _clean(editorial.get("confidence") or "guarded")
    confidence_note = _clean(editorial.get("confidence_note") or "")
    dominant = editorial.get("dominant_driver") or {}
    driver = str(dominant.get("engine") or "default")
    mechanism = DRIVER_MECHANISMS.get(driver, DRIVER_MECHANISMS["default"])
    guide = _guide_for(str(echo.get("episode") or ""))
    history = _historical_context(snap, echo)

    dek = standfirst or (
        f"A {confidence} reading of the dollar-funding system, with the evidence "
        "for it, the evidence against it, and the date that can prove it wrong."
    )
    body = f"""The daily dispatch records every section of the board. This article makes one narrower argument: **{thesis}** The distinction matters because a market funnel should earn attention with a mechanism and a test, not with a louder adjective. Today's editorial confidence is **{confidence}**. {confidence_note}

## The mechanism

{mechanism}

The dominant contribution today is **{_clean(dominant.get('label') or driver)}**, scored at {_fmt(dominant.get('score'))} with {_fmt(dominant.get('contribution'))} composite points. That is the board's attribution, not an observed security price. The independent question is whether the traded funding tape and official backstops confirm it.

That sequencing prevents a common category error. Capacity can deteriorate before the marginal price of cash moves, because aggregate reserves, their distribution, and the willingness to intermediate are not the same thing. But a capacity model can also be early or simply wrong. The article therefore treats attribution as a hypothesis about transmission and gives the live market tape veto power over the dramatic version of the story.

## What the evidence actually says

{_evidence_paragraphs(editorial)}

Read together, these rows describe a chain rather than a pile of indicators. The reserve identity says where cash moved. The fitted demand curve says how much structural room the model sees. The overnight spread says whether scarcity is being priced now. The calendar supplies a date on which the disagreement can close. None of those steps is allowed to borrow certainty from the others.

## The strongest counter-case

{_countercase(editorial)}

That counter-case is load-bearing. If overnight cash continues to trade comfortably below the administered rate, the backstop remains unused, and broad market stress stays low, then a high structural contribution is a warning about capacity, not evidence that a squeeze is underway. The article changes its mind when the tape changes, not when the prose needs drama.

## The historical echo

Today's closest named episode is **{guide['short']}**. {guide['setup']}

{history}

The lesson is bounded: {guide['lesson']} Similarity is a question generator. It does not turn one old path into today's destiny, and Seiche's own Tide Tables verdict stays attached to the comparison.

## The test ahead

{_watch_section(editorial)}

The test is simple enough to falsify. Watch the published date, then read SOFR against IORB, repo-tail pressure, backstop usage, and the reserve ledger after the cash movement lands. If those independent checks remain calm, the structural thesis did not transmit. If several turn together, the story graduates from capacity risk to observed funding stress.

## Follow the pressure chain

Seiche answers the system question: **is dollar funding capacity tightening?** If that answer matters to a lender or counterparty decision, [see which institutions should feel it first](https://liquilens.in/). If the concern is whether a position can be sold without moving the market, [price the exit on Undertow](https://liquilens-undertow.com/exit/). To receive one reviewed read across all three layers, [join the Liquidity Lab daily channel](https://t.me/LiquidityLabDesk).

Those are three different jobs. Cross-linking them is useful only when the boundary stays visible: system liquidity does not identify a weak bank, and a weak bank does not tell you the cost of exiting a market position.

## Sources, method, and limits

- [Today's full Seiche snapshot]({SITE}/data/overview.json), including the evidence rows, counter-case, and dated watch list quoted above.
- [Seiche methodology]({SITE}/methodology), including component definitions, validation status, and change log.
- [Public series catalog](https://api.seiche.info/api/series/index.json), with source, native cadence, as-of date, and freshness for each raw series.
- [The daily dispatch]({story.get('canonical_url') or SITE + '/dispatches/'}), which preserves the complete fixed-order reading behind this article.

The composite is a Seiche derivation, not an observed price. Historical comparisons use final or current-vintage data constrained chronologically and are labelled construction-PIT; they are not publication-vintage backtests and are not real-money eligible. Source publication time is not collected uniformly. Related LiquiLens and Undertow readings are handoffs, not inputs to the Seiche composite. Research and market data, not investment advice.
"""
    return headline, dek, body


def _historical_body(snap: dict, story: dict, echo: dict) -> tuple[str, str, str]:
    editorial = snap.get("editorial") or {}
    guide = _guide_for(str(echo.get("episode") or ""))
    episode = guide["short"]
    current = editorial.get("standfirst") or story.get("dek") or "The current tape did not clear the full-story gate."
    headline = f"Before the break: what {episode} teaches about funding pressure"
    dek = (
        f"A data-backed replay of {episode}, why the plumbing mattered, what "
        "today's board shares with it, and where the analogy stops."
    )
    tide = snap.get("deep", {}).get("tidetables") or {}
    odds = tide.get("event_odds") or {}
    skill = tide.get("skill") or {}
    history = _historical_context(snap, echo)
    body = f"""Nothing on today's tape cleared Seiche's full-story bar. That is not a reason to manufacture urgency. It is a chance to open the record and explain one episode the machinery is built to recognise: **{episode}**.

Today's board still provides the point of comparison: {_clean(current)} The comparison below is explicitly a historical replay. It is not breaking news and it is not a forecast.

## The setup

{guide['setup']}

Funding events often look obvious after the rate spike and ambiguous before it. The useful work is to separate three clocks: the underlying cash movement, the moment a source published it, and the moment the market price confirmed it. Seiche's live record keeps those clocks separate where the source permits; the older reconstruction cannot recover every publication vintage and says so.

## What the model sees in the old episode

{history}

Echo compares 30-day trajectories across funding variables and asks whether today's path resembles windows around named episodes. Tide Tables asks a different question: among the nearest historical state paths, what happened during the next five to ten business days? Neither engine is allowed to call resemblance causation. Their disagreement is part of the result.

The current neighbour set contains {_fmt(odds.get('n'), 0)} observations. Its five-business-day event share is {_pct(odds.get('p'))}, versus a published base rate of {_pct(odds.get('base_rate'))}. The interval is deliberately wide where the sample is thin. The walk-forward verdict is: **{_clean(skill.get('verdict') or 'skill verdict unavailable')}**. A model that does not beat climatology is context, not conviction.

## What happened, and why the plumbing mattered

{guide['setup']}

The key mechanism was not the headline alone. {guide['lesson']} That is why Seiche decomposes the system into a reserve identity, the price of secured cash, official backstop usage, dealer and leveraged positioning, and dated settlement pressure. A single high percentile can describe an unusual level; a funding event requires transmission across the chain.

## The strongest counter-case

{_evidence_paragraphs(editorial)}

The resemblance is useful only alongside the differences. Today's strongest counter-evidence is:

{_countercase(editorial)}

If those counter-signals persist, the old episode is an explanation of a mechanism, not a template for the next week. That is the intended fallback on a quiet day: teach the reader what the instruments mean without pretending history has repeated.

## The next falsifiable test

{_watch_section(editorial)}

When the next dated window arrives, the grading order matters. First record the cash movement. Then check SOFR against IORB and the repo distribution. Then check facility usage and the balance-sheet identity. Only after those prints arrive should the desk decide whether the historical mechanism transmitted.

## Follow the pressure chain

The episode starts at the system layer, but decisions usually continue. [Open LiquiLens](https://liquilens.in/) to examine which institutions carry the balance-sheet exposure. [Open Undertow](https://liquilens-undertow.com/) to see whether liquidity providers and exit costs confirm strain in traded markets. [Join the free daily channel](https://t.me/LiquidityLabDesk) for one reviewed read across all three.

This is the funnel by diagnosis: first the funding system, then the institutions standing on it, then the markets in which risk must be transferred. Each layer can disagree with the others, and the disagreement is often the most useful finding.

## Sources, method, and limits

- [Today's Seiche snapshot]({SITE}/data/overview.json), the source of every current reading quoted here.
- [Seiche methodology]({SITE}/methodology), including Echo, Tide Tables, the construction-PIT boundary, and validation status.
- [Public series catalog](https://api.seiche.info/api/series/index.json), with source and as-of metadata.
- [Today's fixed-order dispatch]({story.get('canonical_url') or SITE + '/dispatches/'}), the complete board reading from which the article was selected.

Historical series are final or current-vintage observations used with chronological transforms. They do not reconstruct every value exactly as a reader could have seen it at the time. The replay is therefore construction-PIT, not a validated backtest, not a probability claim, and not real-money eligible. Similarity does not imply recurrence. Research and market data, not investment advice.
"""
    return headline, dek, body


def article_quality_issues(article: dict) -> list[str]:
    """Return publish-blocking editorial defects."""
    body = str(article.get("body_md") or "")
    words = re.findall(r"\b[\w$%+.-]+\b", body)
    issues = []
    if len(words) < 800:
        issues.append(f"article is too thin ({len(words)} words; need 800)")
    if len(words) > 1_600:
        issues.append(f"article is unfocused ({len(words)} words; maximum 1600)")
    if len(str(article.get("headline") or "")) > 120:
        issues.append("headline is longer than 120 characters")
    if len(str(article.get("dek") or "")) > 260:
        issues.append("dek is longer than 260 characters")
    required = (
        "## The strongest counter-case",
        "## Follow the pressure chain",
        "## Sources, method, and limits",
    )
    for heading in required:
        if heading not in body:
            issues.append(f"missing section: {heading[3:]}")
    if body.count("https://") < 6:
        issues.append("fewer than six traceable links")
    if len(re.findall(r"\d", body)) < 8:
        issues.append("article carries too little numeric evidence")
    if re.search(r"\b(?:nan|TODO|TBD|null)\b", body, flags=re.I):
        issues.append("format placeholder leaked into article")
    if "not investment advice" not in body.lower():
        issues.append("missing research boundary")
    if article.get("article_type") == "historical_replay" and "not a forecast" not in body.lower():
        issues.append("historical replay is not labelled as non-forecast")
    lede = body.split("\n## ", 1)[0]
    if len(re.findall(r"\b\w+\b", lede)) < 55:
        issues.append("lede is too slight to establish the thesis and stakes")
    clichés = (
        "in today's fast-paced",
        "it is important to note",
        "delve into",
        "game-changer",
        "as an ai",
    )
    if any(phrase in body.lower() for phrase in clichés):
        issues.append("generic or AI-meta language leaked into article")
    memory = article.get("editorial_memory") or {}
    directives = set(memory.get("directives") or [])
    lowered = body.lower()
    if "strengthen_thesis" in directives and not any(
        token in lede.lower() for token in ("argument", "thesis", "claim", "**")
    ):
        issues.append("editorial memory requires an explicit thesis in the lede")
    if "show_mechanism" in directives and not (
        "## the mechanism" in lowered or "why the plumbing mattered" in lowered
    ):
        issues.append("editorial memory requires a visible transmission mechanism")
    if "tighten_evidence_boundary" in directives and not all(
        token in lowered for token in ("## sources, method, and limits", "derived")
    ):
        issues.append("editorial memory requires a stronger evidence boundary")
    if "surface_countercase" in directives and "## the strongest counter-case" not in lowered:
        issues.append("editorial memory requires a load-bearing countercase")
    if "name_falsifier" in directives and not any(
        token in lowered for token in ("falsif", "changes its mind", "test ahead")
    ):
        issues.append("editorial memory requires an observable falsifier")
    if "soften_funnel" in directives:
        funnel = body.split("## Follow the pressure chain", 1)[-1].split("\n## ", 1)[0]
        if len(re.findall(r"\b\w+\b", funnel)) > 180:
            issues.append("editorial memory requires a shorter product handoff")
    return issues


def build_article(snap: dict, story: dict, *, date: str,
                  recent_topics: list[str] | None = None,
                  model_config: dict[str, str] | None | bool = False,
                  editorial_memory: dict | None = None) -> dict:
    """Build one article, preferring two-pass model prose when configured.

    ``model_config=False`` means discover configuration from the environment;
    ``None`` explicitly disables model calls, which keeps tests and manual
    deterministic builds unambiguous.
    """
    echo = _select_echo(snap, date, recent_topics or [])
    decision = str((story.get("newsworthiness") or {}).get("decision") or story.get("editorial_class") or "")
    if decision == "full_story":
        article_type = "current_analysis"
        topic = str((snap.get("editorial") or {}).get("dominant_driver", {}).get("engine") or "live-funding")
    else:
        article_type = "historical_replay"
        topic = str(echo.get("episode") or "historical-funding")

    dossier = build_evidence_dossier(
        snap, story, date=date, echo=echo, article_type=article_type,
        editorial_memory=editorial_memory,
    )
    config = _editorial_model_config() if model_config is False else model_config
    generation = {
        "mode": "deterministic_fallback",
        "model": None,
        "passes": 0,
        "dossier_sha256": hashlib.sha256(
            json.dumps(dossier, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "fallback_reason": "editorial model is not configured",
        "editorial_memory": copy.deepcopy(editorial_memory or {
            "status": "not_requested", "source_run_id": None,
            "memory_fingerprint": None,
            "rubric_version": "mqdnse.editorial-rubric.v1", "directives": [],
        }),
    }

    model_copy: dict | None = None
    if isinstance(config, dict):
        try:
            candidate = _draft_with_model(dossier, config)
            grounding = model_grounding_issues(candidate, dossier)
            provisional = {
                "headline": candidate["headline"],
                "dek": candidate["dek"],
                "body_md": candidate["body_md"],
                "article_type": article_type,
                "editorial_memory": editorial_memory or {},
            }
            grounding.extend(article_quality_issues(provisional))
            if grounding:
                raise ValueError("; ".join(grounding))
            model_copy = candidate
            generation = {
                "mode": "model_assisted",
                "model": config["model"],
                "passes": 2,
                "dossier_sha256": generation["dossier_sha256"],
                "fallback_reason": None,
                "review_notes": candidate.get("review_notes") or [],
                "editorial_memory": generation["editorial_memory"],
            }
        except Exception as exc:  # noqa: BLE001 - publication must fall back, with receipt
            generation["fallback_reason"] = f"{type(exc).__name__}: {str(exc)[:240]}"

    if model_copy:
        headline, dek, body = (
            model_copy["headline"], model_copy["dek"], model_copy["body_md"],
        )
    elif article_type == "current_analysis":
        headline, dek, body = _current_body(snap, story, echo)
    else:
        headline, dek, body = _historical_body(snap, story, echo)

    slug = f"{date}-{_slugify(headline)}"
    word_count = len(re.findall(r"\b[\w$%+.-]+\b", body))
    article = {
        "schema": SCHEMA,
        "id": f"seiche:article:{slug}",
        "product": "seiche",
        "slug": slug,
        "date": date,
        "article_type": article_type,
        "editorial_class": decision or "historical_fallback",
        "topic": topic,
        "headline": headline,
        "dek": dek,
        "canonical_url": f"{SITE}/articles/{slug}/",
        "published_at": str(snap.get("generated_at") or f"{date}T00:00:00Z").replace("+00:00", "Z"),
        "evidence_as_of": str((snap.get("engines", {}).get("echo") or {}).get("asof") or date),
        "related_dispatch": story.get("canonical_url"),
        "historical_episode": echo if article_type == "historical_replay" else None,
        "newsworthiness": story.get("newsworthiness") or {},
        "body_md": body.strip() + "\n",
        "word_count": word_count,
        "funnel": [
            {"product": "liquilens", "job": "institution risk", "url": "https://liquilens.in/"},
            {"product": "undertow", "job": "market liquidity and exit cost", "url": "https://liquilens-undertow.com/"},
            {"product": "liquidity-lab-desk", "job": "one reviewed daily read", "url": "https://t.me/LiquidityLabDesk"},
        ],
        "limitations": story.get("limitations") or [],
        "editorial_memory": copy.deepcopy(generation["editorial_memory"]),
        "generation": generation,
    }
    issues = article_quality_issues(article)
    if issues:
        raise SystemExit("article failed quality gate: " + "; ".join(issues))
    article["quality_gate"] = {
        "status": "PASS",
        "checks": [
            "depth", "structure", "lede", "countercase", "sources", "funnel",
            "numeric_grounding", "link_grounding", "boundaries",
        ],
    }
    return article


def write_article(article: dict, repo_root: Path | None = None) -> list[str]:
    root = repo_root or REPO_ROOT
    out_dir = root / "frontend" / "public" / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = article["slug"]
    md_path = out_dir / f"{slug}.md"
    json_path = out_dir / f"{slug}.json"
    index_path = out_dir / "index.json"
    md_path.write_text(article["body_md"])
    sidecar = {key: value for key, value in article.items() if key != "body_md"}
    json_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n")

    try:
        rows = json.loads(index_path.read_text())
    except (OSError, ValueError):
        rows = []
    stale_slugs = {
        str(row.get("slug") or "")
        for row in rows
        if row.get("date") == article["date"] and row.get("slug") != slug
    }
    # A forced same-day rewrite may legitimately change the headline and thus
    # the slug. Remove only exact, validated prior slugs for that day so the
    # archive, sitemap and crawlers cannot retain two versions of one edition.
    for stale in stale_slugs:
        if not re.fullmatch(r"[a-z0-9-]+", stale):
            raise SystemExit(f"unsafe stale article slug in {index_path}: {stale!r}")
        for suffix in (".md", ".json"):
            path = out_dir / f"{stale}{suffix}"
            if path.exists() and path.is_file() and not path.is_symlink():
                path.unlink()
        rendered = out_dir / stale
        if rendered.exists() and rendered.is_dir() and not rendered.is_symlink():
            shutil.rmtree(rendered)
    rows = [row for row in rows if row.get("slug") != slug and row.get("date") != article["date"]]
    entry_keys = (
        "slug", "date", "article_type", "editorial_class", "topic", "headline", "dek",
        "canonical_url", "published_at", "evidence_as_of", "related_dispatch", "word_count",
    )
    rows.append({key: article.get(key) for key in entry_keys})
    rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("published_at") or "")), reverse=True)
    index_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    learning_path = write_learning_feed(out_dir, rows)
    return [str(md_path), str(json_path), str(index_path), str(learning_path)]


def write_learning_feed(out_dir: Path, rows: list[dict]) -> Path:
    """Publish exact full-body revisions for non-training shadow critique."""
    articles = []
    for row in rows[:30]:
        slug = str(row.get("slug") or "")
        sidecar_path = out_dir / f"{slug}.json"
        markdown_path = out_dir / f"{slug}.md"
        if not sidecar_path.is_file() or not markdown_path.is_file():
            raise SystemExit(f"article learning feed is missing revision files for {slug}")
        sidecar = json.loads(sidecar_path.read_text())
        body = markdown_path.read_text()
        generation = sidecar.get("generation") or {}
        quality = sidecar.get("quality_gate") or {}
        evidence_fingerprint = str(generation.get("dossier_sha256") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", evidence_fingerprint):
            evidence_fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "evidence_as_of": sidecar.get("evidence_as_of"),
                        "related_dispatch": sidecar.get("related_dispatch"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        articles.append({
            "schema": "editorial.learning-article.v1",
            "id": sidecar["id"],
            "product": "seiche",
            "slug": sidecar["slug"],
            "article_type": sidecar["article_type"],
            "headline": sidecar["headline"],
            "dek": sidecar["dek"],
            "canonical_url": sidecar["canonical_url"],
            "published_at": sidecar["published_at"],
            "evidence_as_of": sidecar["evidence_as_of"],
            "body_markdown": body,
            "word_count": len(re.findall(r"\b[\w$%+.-]+\b", body)),
            "evidence_fingerprint": evidence_fingerprint,
            "generation_mode": generation.get("mode") or "deterministic_fallback",
            "quality_gate": {
                "status": quality.get("status"),
                "checks": list(quality.get("checks") or []),
            },
        })
    if not articles:
        raise SystemExit("article learning feed cannot be empty")
    feed = {
        "schema": "editorial.learning-feed.v1",
        "product": "seiche",
        "generated_at": max(row["published_at"] for row in articles),
        "articles": articles,
        "authority": {
            "shadow_review_allowed": True,
            "training_allowed": False,
            "factual_authority": "published_article_only",
        },
    }
    path = out_dir / "learning.json"
    temporary = out_dir / f".learning.json.tmp-{os.getpid()}"
    temporary.write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, path)
    return path


def _load_story(date: str, root: Path) -> dict:
    path = root / "frontend" / "public" / "dispatches" / f"{date}-daily.json"
    if not path.exists():
        raise SystemExit(f"daily dispatch story is missing at {path}; article selection needs its newsroom gate")
    try:
        story = json.loads(path.read_text())
    except ValueError as exc:
        raise SystemExit(f"daily dispatch story is not valid JSON: {path}: {exc}") from exc
    return story


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write today's evidence-led Seiche article.")
    ap.add_argument("--snapshot", required=True, help="point-in-time overview JSON used by the daily dispatch")
    ap.add_argument("--date", default=None, help="publication day, YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="replace today's article")
    args = ap.parse_args(argv)
    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = json.loads(Path(args.snapshot).read_text())

    if ARTICLE_INDEX.exists() and not args.force:
        try:
            if any(row.get("date") == date for row in json.loads(ARTICLE_INDEX.read_text())):
                print(f"article for {date} already published, nothing to do")
                return 0
        except ValueError:
            pass

    story = _load_story(date, REPO_ROOT)
    article = build_article(
        snap, story, date=date,
        recent_topics=_recent_topics(exclude_date=date),
        editorial_memory=fetch_editorial_memory(),
    )
    for path in write_article(article):
        print(f"wrote {path}")
    print(f"article ready: {article['slug']} [{article['article_type']}] {article['word_count']} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
