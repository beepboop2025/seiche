export interface PaperView {
  state: "ready" | "unavailable";
  lines: string[];
}

export function riptidePaperView(payload: unknown): PaperView;
export function seicheGaugeLine(gauge: unknown): string;
export function undertowRuleReading(board: unknown): PaperView;
export function formatWeight(value: number): string;
export function composePaperNote(
  payload: unknown,
  gauge: unknown,
  board: unknown,
  lead?: string,
  options?: { skipGauge?: boolean },
): PaperView;
export function loadPaperNote(
  fetchImpl?: typeof fetch,
  options?: {
    lead?: string;
    allocationUrl?: string;
    boardUrl?: string;
    gaugeUrl?: string;
    skipGauge?: boolean;
  },
): Promise<PaperView>;
export function fillPaperNote(root: HTMLElement, fetchImpl?: typeof fetch): Promise<PaperView>;
