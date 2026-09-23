export type EditorialProduct = "all" | "liquilens" | "seiche" | "liquilens-undertow" | "myquant" | "other";
export type EditorialItem = {
  id: string; product: Exclude<EditorialProduct, "all" | "other">; title: string;
  summary: string; date: string; url: string; original: string | null;
  limitation: string; lane: "INTERPRETED" | "MYQUANT_ANALYSIS";
};
export function editorialItems(feed: unknown): EditorialItem[];
export function mountEditorial(root: HTMLElement, options?: { initialProduct?: EditorialProduct }): () => void;
