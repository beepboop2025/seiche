export type ActivityEvent = {
  schema_version?: "1.0"; event_id: string; agent_id: string; agent_version: string;
  journey_id: string; kind: "decision"|"tool_call"|"order"|"fill"|"position_snapshot"|"cash_flow"|"payment_authorized"|"payment_settled"|"fulfilled"|"refunded"|"failed";
  occurred_at: string; mode: "live"|"paper"|"backtest"|"test"|"unknown"; venue: string;
  evidence_sha256: string; external_ref?: string; decision_ref?: string;
  instrument?: string; side?: "buy"|"sell"|"in"|"out"; quantity?: string; amount?: string;
  currency?: string; fee?: string; tool?: string; trace_id?: string;
};
export class AgentActivityClient {
  constructor(endpoint: string, credential: string);
  send(event: ActivityEvent): Promise<unknown>;
  claim(identity: Record<string, unknown>): Promise<unknown>;
  report(): Promise<unknown>;
  withdraw(): Promise<unknown>;
}
