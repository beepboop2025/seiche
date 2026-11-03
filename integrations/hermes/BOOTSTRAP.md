# First conversation: verify the research connection

Paste the following into Hermes after wiring the MCP connection:

1. Discover the currently available tools. The anonymous catalog contains
   exactly `source_rights_status`, `money_market_context`, `market_workbench`, `world_markets_context`, `gift_city_context`, `gold_inventory_carry`, `research_network`, `data_health`, `latest_article`, `funding_stress_now`, `historical_analogs`, `proof_backtest`, `crypto_stress_record`, `institutional_flows`, `oil_funding_context`, `fx_materials_passage`, `trade_safety_risk_context`, `funding_stress_forecast`.
2. Call `source_rights_status` and `data_health`. Report feature availability,
   source dates and faults. Preserve restrictions and missing values.
3. Retrieve one bounded `money_market_context` section, with sources and dates.
4. For reference FX use `market_workbench(provider="ecb")`; H.10 transported
   through FRED is metadata-only and must not be reconstructed.
5. Report the results and limitations. Do not create scheduled jobs, send
   messages, change memory or execute trades.

Legacy tools are shown for free research use while source permissions are
sought; their forecasts and backtests are research diagnostics, not validated
predictions. Three legacy tools need bearer authentication, the Book and
`positioning_book` stay withheld, and no payment unlocks a legacy tool. Private Agent Room tools are separately identity-gated,
signed discussion only, and never execution authority.
