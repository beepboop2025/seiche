# First conversation: verify the research connection

Paste the following into Hermes after wiring the MCP connection:

1. Discover the currently available tools. The anonymous catalog contains
   exactly `source_rights_status`, `money_market_context`, `market_workbench`, `world_markets_context`, `gift_city_context`, `gold_inventory_carry`, `research_network`, `data_health`.
2. Call `source_rights_status` and `data_health`. Report feature availability,
   source dates and faults. Preserve restrictions and missing values.
3. Retrieve one bounded `money_market_context` section, with sources and dates.
4. For reference FX use `market_workbench(provider="ecb")`; H.10 transported
   through FRED is metadata-only and must not be reconstructed.
5. Report the results and limitations. Do not create scheduled jobs, send
   messages, change memory, execute trades or call retired legacy features.

Legacy forecasts, backtests, composites and dependent recipes remain explicitly
unavailable even with bearer authentication. Private Agent Room tools are
separately identity-gated, signed discussion only, and never execution authority.
