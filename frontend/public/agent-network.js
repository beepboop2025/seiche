export const AGENT_SCENARIOS = Object.freeze({
  context: {
    label: "Public MCP · context request",
    title: "Funding evidence, with its clocks intact",
    summary:
      "An external risk agent asks for funding context. Seiche returns cached observations, provenance and an explicit non-executable boundary.",
    stages: [
      "Risk agent requests context",
      "Seiche reads cached evidence",
      "Boundary is attached",
      "Agent receives JSON",
    ],
    events: [
      "Authenticated or public MCP client sends structured parameters.",
      "Seiche selects already-collected evidence; no request-time scrape occurs.",
      "Source freshness and unavailable inputs stay visible.",
      "The caller—not Seiche—owns any downstream decision.",
    ],
    packet: {
      transport: "mcp",
      tool: "trade_safety_risk_context",
      parameters: { include: ["regime", "freshness", "provenance"] },
      response_contract: {
        cache_only: true,
        executable: false,
        source_clocks_attached: true,
      },
    },
  },
  negotiate: {
    label: "Private Agent Room · signed discussion",
    title: "A negotiation record without execution authority",
    summary:
      "Two identity-bound agents can exchange a signed pricing discussion. Seiche countersigns and chains the record, but does not route an order or attest that a trade occurred.",
    stages: [
      "Agent signs an event",
      "Bearer identity is bound",
      "Seiche countersigns + chains",
      "Room members read the record",
    ],
    events: [
      "A registered room member signs the exact event payload.",
      "The hosted transport binds the call to its bearer-derived actor.",
      "The append receives a server signature and previous-event hash link.",
      "Any actual trade remains outside Seiche and outside this evidence record.",
    ],
    packet: {
      transport: "authenticated_mcp",
      tool: "agent_room_append_event",
      event: {
        kind: "indicative_price_discussion",
        executable: false,
        client_signature: "required",
      },
      server_record: {
        countersignature: "attached_after_validation",
        previous_hash: "linked",
      },
    },
  },
  audit: {
    label: "Private Agent Room · verification",
    title: "Replay the chain; keep the legal claim bounded",
    summary:
      "An authorized agent verifies the full room history. The receipt proves the implemented signature and hash-link checks—not automatic compliance with any law or retention rule.",
    stages: [
      "Authorized agent requests replay",
      "Every signature is checked",
      "Hash links reach chain head",
      "Verification receipt is returned",
    ],
    events: [
      "The verifier reads the complete private room chain.",
      "Client signatures and Seiche countersignatures are validated.",
      "Genesis, ordering, previous hashes and the chain head are reconciled.",
      "Counsel and compliance owners still assess retention and regulatory sufficiency.",
    ],
    packet: {
      transport: "authenticated_mcp",
      tool: "agent_room_verify",
      checks: [
        "client_signatures",
        "server_countersignatures",
        "hash_links",
        "chain_head",
      ],
      claim_boundary: "cryptographic_evidence_support_not_legal_compliance",
    },
  },
});

const formatTimestamp = (value) => {
  if (!value) return "clock unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toISOString();
};

export const formatHealthReceipt = (health) => {
  const provenance = Array.isArray(health?.provenance)
    ? health.provenance.length
    : "unknown";
  const faults = Array.isArray(health?.faults) ? health.faults.length : "unknown";
  return `${health?.version || "version unavailable"} · ${formatTimestamp(
    health?.generated_at,
  )} · ${provenance} provenance · ${faults} faults`;
};

export const initialiseAgentNetwork = (root = document) => {
  const tabs = [...root.querySelectorAll("[data-scenario]")];
  const panel = root.querySelector("#agent-route-panel");
  const title = root.querySelector("[data-route-title]");
  const label = root.querySelector("[data-route-label]");
  const summary = root.querySelector("[data-route-summary]");
  const events = root.querySelector("[data-route-events]");
  const packet = root.querySelector("[data-route-packet]");
  const stages = [...root.querySelectorAll("[data-stage]")];

  if (!tabs.length || !panel || !title || !label || !summary || !events || !packet) {
    return;
  }

  const selectScenario = (tab, moveFocus = false) => {
    const scenario = AGENT_SCENARIOS[tab.dataset.scenario];
    if (!scenario) return;

    tabs.forEach((candidate) => {
      const selected = candidate === tab;
      candidate.setAttribute("aria-selected", String(selected));
      candidate.tabIndex = selected ? 0 : -1;
    });
    panel.setAttribute("aria-labelledby", tab.id);
    label.textContent = scenario.label;
    title.textContent = scenario.title;
    summary.textContent = scenario.summary;
    events.replaceChildren(
      ...scenario.events.map((event) => {
        const item = root.createElement("li");
        item.textContent = event;
        return item;
      }),
    );
    stages.forEach((stage, index) => {
      stage.textContent = scenario.stages[index] || "";
    });
    packet.textContent = JSON.stringify(scenario.packet, null, 2);
    if (moveFocus) tab.focus();
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectScenario(tab));
    tab.addEventListener("keydown", (event) => {
      const keyMoves = {
        ArrowLeft: (index - 1 + tabs.length) % tabs.length,
        ArrowRight: (index + 1) % tabs.length,
        Home: 0,
        End: tabs.length - 1,
      };
      if (!(event.key in keyMoves)) return;
      event.preventDefault();
      selectScenario(tabs[keyMoves[event.key]], true);
    });
  });

  selectScenario(tabs.find((tab) => tab.getAttribute("aria-selected") === "true") || tabs[0]);
};

export const refreshHealthReceipt = async (
  root = document,
  fetcher = globalThis.fetch,
) => {
  const receipt = root.querySelector("[data-live-receipt]");
  if (!receipt || typeof fetcher !== "function") return;

  try {
    const response = await fetcher("https://api.seiche.info/api/health", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`health returned ${response.status}`);
    const health = await response.json();
    receipt.textContent = formatHealthReceipt(health);
    receipt.dataset.state = "live";
  } catch {
    receipt.textContent = "Public evidence receipt temporarily unavailable";
    receipt.dataset.state = "unavailable";
  }
};

if (typeof document !== "undefined") {
  initialiseAgentNetwork(document);
  void refreshHealthReceipt(document);
}
