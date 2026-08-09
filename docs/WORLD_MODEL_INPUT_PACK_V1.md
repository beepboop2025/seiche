# Money-market world-model input pack v1

`seiche.markets.world_model` exports a portable research input contract from
Seiche's canonical, bitemporal observations. It does not fit a model, publish a
forecast, or add an API. Callers must declare every required market/semantic
role explicitly; Seiche does not ship a default state vector.

```python
from datetime import UTC, datetime

from seiche.domain.observation import SemanticRole
from seiche.markets.world_model import (
    RequiredWorldModelState,
    build_world_model_input_pack_from_repository,
)
from seiche.repository import get_repository

pack = build_world_model_input_pack_from_repository(
    get_repository(),
    as_of=datetime.now(UTC),
    required_states=(
        RequiredWorldModelState(
            "usd_secured_rate", "US-USD", SemanticRole.SECURED_OVERNIGHT
        ),
        RequiredWorldModelState(
            "usd_unsecured_rate", "US-USD", SemanticRole.UNSECURED_OVERNIGHT
        ),
    ),
)
```

The v1 schema is `seiche.world-model-input-pack.v1`. `pack_digest` is SHA-256
over canonical UTF-8 JSON (`sort_keys=True`, compact separators,
`ensure_ascii=False`, `allow_nan=False`) with only the top-level digest field
omitted.

## Temporal and revision contract

The repository seam includes **every canonical revision knowable by `as_of`**,
not only the latest value. An `observation_id` is stable only for the exact
`market_id`/`instrument_id`/`event_time` slot; its rows receive one-based
`revision_ordinal` values ordered by knowledge time, publication time, and
revision ID. Seiche does not yet carry an upstream source-event identifier, so
a timestamp correction is a new slot with a new `observation_id`. The common
`event_grid` is derived from the latest-as-of selection, while the older rows
remain available for rolling-origin prefix reconstruction.

Every row retains its event, source-publication and knowledge clocks, market,
monetary area, currency, instrument, semantic role, unit and rate conventions,
source, source classification, redistribution policy, revision ID, evidence
hash, quality and staleness. Values are finite canonical decimal strings, not
binary floats.

The builder rejects rather than repairs:

- event, publication, or knowledge clocks after `as_of`, and knowledge before
  the event;
- unusable or non-finite observations;
- any raw observation whose `redistribution_status` is not exactly `allowed`;
- duplicate identities, same-knowledge revision ties, duplicate revision IDs,
  or source changes within one observation event;
- ambiguous role-to-instrument mappings or mixed units/semantics;
- a required state missing any event on the common grid. No forward fill or
  other imputation occurs.

This is an **input** pack, not a first-release target set. Latest-known-as-of
inputs are correct for a decision-time state, but the pack makes no claim that
the earliest revision is a validated outcome label.

## Evidence and authority boundary

The exporter declares:

```json
{
  "maturity": "research",
  "validation_mode": "rolling_origin_research",
  "imputation": "forbidden",
  "capture_kind": "retrospective_export",
  "forward_evidence_eligible": false,
  "can_publish": false,
  "can_execute": false
}
```

`as_of` is a query cutoff, not proof that the pack was issued at that time. A
later append-only, externally anchored receipt may establish forward evidence,
but that authority lives outside this pack. The payload can be passed to the
existing sealed-snapshot repository boundary with evidence eligibility set to
false; it is intentionally not wired into `materialize_market` by default.

Likewise, `can_publish=false` is an authority guard, not permission to serialize
restricted source values. V1 exports raw values only when the canonical row has
`redistribution_status=allowed`; `derived_only`, `metadata_only`, and
`prohibited` rows fail closed.
