import {readJSON, record} from './core';
import {seriesModel, type EconomicSeries} from './seriesModel';

export const FUNDING_KEYS = ['SOFR', 'EFFR', 'IORB', 'TGCR', 'BGCR', 'WRESBAL', 'TGA_LONG', 'RRPONTSYD', 'WALCL', 'CP_FIN_3M', 'CP_NONFIN_3M', 'DGS3M', 'DGS2', 'DGS10', 'DGS30', 'MMF_REPO_FED', 'MMF_REPO_FICC', 'MMF_REPO_TOT'];
const API = 'https://api.seiche.info';
const retained = (model: EconomicSeries): EconomicSeries => ({...model, state: 'previously verified; refresh unavailable'});

function verified(payload: unknown, entry: Record<string, unknown>): EconomicSeries {
  const model = seriesModel(payload, entry);
  if (model.source !== entry.source || model.remote !== entry.remote_id) throw new Error('Source identity differs');
  return model;
}

export function publishedFunding(value: unknown): {models: Map<string, EconomicSeries>; generatedAt: string} {
  if (!record(value) || value.schema !== 'seiche.funding-history.v1' ||
      typeof value.generated_at !== 'string' || !Number.isFinite(Date.parse(value.generated_at)) ||
      Date.parse(value.generated_at) > Date.now() || !Array.isArray(value.series) ||
      value.series.length > FUNDING_KEYS.length) throw new Error('Published histories could not be verified');
  const models = new Map<string, EconomicSeries>();
  for (const row of value.series) {
    if (!record(row) || !record(row.entry) || typeof row.entry.mnemonic !== 'string' ||
        !FUNDING_KEYS.includes(row.entry.mnemonic) || models.has(row.entry.mnemonic) ||
        row.entry.json !== `/api/series/${row.entry.mnemonic}`) throw new Error('Published source differs');
    const model = verified(row.payload, row.entry);
    models.set(model.key, {...model, state: `published ${value.generated_at}; ${model.state}`});
  }
  return {models, generatedAt: value.generated_at};
}

/** A successful catalog can revoke availability; a failed request cannot erase history. */
export async function refreshFunding(previous: Map<string, EconomicSeries>, signal: AbortSignal,
  read: typeof readJSON = readJSON): Promise<{models: Map<string, EconomicSeries>; refreshed: number}> {
  const catalog = await read(`${API}/api/series/index.json`, signal);
  if (!record(catalog) || catalog.schema !== 'seiche.series-index.v1' || !Array.isArray(catalog.series)) {
    throw new Error('Series catalog unavailable');
  }
  const entries: unknown[] = catalog.series, models = new Map<string, EconomicSeries>();
  let refreshed = 0;
  for (let i = 0; i < FUNDING_KEYS.length; i += 3) {
    await Promise.all(FUNDING_KEYS.slice(i, i + 3).map(async key => {
      const matches = entries.filter(row => record(row) && row.mnemonic === key);
      const entry = matches.length === 1 ? matches[0] : null;
      if (!record(entry) || entry.available !== true || entry.csv_restricted || entry.json !== `/api/series/${key}`) return;
      try {
        models.set(key, verified(await read(`${API}${entry.json}?n=520`, signal), entry));
        ++refreshed;
      } catch {
        const old = previous.get(key);
        if (old && old.unit === entry.unit && old.source === entry.source && old.remote === entry.remote_id) {
          models.set(key, retained(old));
        }
      }
    }));
  }
  return {models, refreshed};
}

export function retainFunding(models: Map<string, EconomicSeries>): Map<string, EconomicSeries> {
  return new Map([...models].map(([key, model]) => [key, retained(model)]));
}
