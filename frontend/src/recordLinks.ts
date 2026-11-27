/**
 * The homepage's links into Seiche's published record, read from the archive
 * indexes the publisher already writes (/articles/index.json,
 * /dispatches/index.json, /investigations/index.json). No title, date or
 * address is written here by hand: a row that does not validate is left out,
 * and an unreadable index yields no rows.
 */
export type RecordLink = { href: string; title: string; date: string };

const SLUG = /^[a-z0-9-]+$/;
const DATE = /^(\d{4}-\d{2}-\d{2})(?:$|T)/;

/** Archived headlines keep their words; the page keeps plain punctuation. */
export function plainPunctuation(text: string): string {
  return text.replace(/\s*\u2014\s*/g, ", ").replace(/\u2013/g, "-").replace(/\s+/g, " ").trim();
}

function rowsOf(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((row): row is Record<string, unknown> => !!row && typeof row === "object") : [];
}

function newest(rows: Record<string, unknown>[], dateKey: string, titleKey: string, href: (slug: string) => string, limit: number): RecordLink[] {
  const links: RecordLink[] = [];
  for (const row of rows) {
    const slug = row.slug, title = row[titleKey], date = row[dateKey];
    const day = typeof date === "string" ? DATE.exec(date)?.[1] : undefined;
    if (typeof slug !== "string" || !SLUG.test(slug) || typeof title !== "string" || !title.trim() || !day) continue;
    links.push({ href: href(slug), title: plainPunctuation(title), date: day });
  }
  return links.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0)).slice(0, limit);
}

/** Article pages use directory URLs: /articles/{slug}/. */
export const latestArticles = (index: unknown, limit = 3) =>
  newest(rowsOf(index), "date", "headline", (slug) => `/articles/${slug}/`, limit);

/** Letters keep their clean URL: /dispatches/{slug}. */
export const latestLetters = (index: unknown, limit = 3) =>
  newest(rowsOf(index), "date", "title", (slug) => `/dispatches/${slug}`, limit);

/** Seiche weekly notes: /weekly/{issue Monday}/, rows shaped like the article index. */
export const latestWeekly = (index: unknown, limit = 3) =>
  newest(rowsOf(index), "date", "headline", (slug) => `/weekly/${slug}/`, limit);

/** Only investigations the index marks as published. */
export const latestInvestigations = (index: unknown, limit = 3) => {
  const articles = index && typeof index === "object" ? (index as { articles?: unknown }).articles : undefined;
  const published = rowsOf(articles).filter((row) => row.publication_status === undefined || row.publication_status === "PUBLISHED");
  return newest(published, "published_at", "headline", (slug) => `/investigations/${slug}/`, limit);
};
