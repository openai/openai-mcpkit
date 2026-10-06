import { promises as fs } from 'node:fs';
import { basename, extname, resolve } from 'node:path';
import { parse as parseCsv } from 'csv-parse/sync';

export type TrendRow = Record<string, string | number | null | undefined>;

export interface AirfareTrendFilters {
  snapshotDate?: string | null;
  routeContains?: string | null;
  originAirport?: string | null;
  destinationAirport?: string | null;
  airlineContains?: string | null;
  seasonContains?: string | null;
  notableContains?: string | null;
  limit?: number | null;
}

const SUPPORTED_SUFFIXES: Record<string, 'csv' | 'tsv' | 'json'> = {
  '.csv': 'csv',
  '.tsv': 'tsv',
  '.json': 'json'
};

const OPTIONAL_COPY_KEYS = ['linked_tickers', 'provider', 'collection_window', 'query_count', 'week_id'];
const DEFAULT_LIMIT = 25;
const MAX_LIMIT = 200;

function coerceFloat(value: unknown): number | undefined {
  if (value === null || value === undefined || value === '') {
    return undefined;
  }
  const num = Number.parseFloat(String(value));
  return Number.isFinite(num) ? num : undefined;
}

function coerceInt(value: unknown): number | undefined {
  if (value === null || value === undefined || value === '') {
    return undefined;
  }
  const num = Number.parseInt(String(value), 10);
  return Number.isFinite(num) ? num : undefined;
}

function parseIsoDate(value: string | undefined | null): Date | undefined {
  if (!value) {
    return undefined;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }

  const dateMatch = trimmed.match(/^(\d{4})[-/](\d{2})[-/](\d{2})$/);
  if (dateMatch) {
    const year = Number.parseInt(dateMatch[1], 10);
    const month = Number.parseInt(dateMatch[2], 10) - 1;
    const day = Number.parseInt(dateMatch[3], 10);
    const parsed = new Date(0);
    // Date.UTC treats years 0-99 as 1900-1999. Preserve the supplied year.
    parsed.setUTCFullYear(year, month, day);
    return year >= 1 && parsed.getUTCFullYear() === year &&
      parsed.getUTCMonth() === month && parsed.getUTCDate() === day
      ? parsed
      : undefined;
  }

  const weekMatch = trimmed.match(/^(\d{4})-W(\d{2})$/i);
  if (weekMatch) {
    const year = Number.parseInt(weekMatch[1], 10);
    const week = Number.parseInt(weekMatch[2], 10);
    if (year < 1 || week < 1 || week > 53) return undefined;
    const jan4 = new Date(0);
    jan4.setUTCFullYear(year, 0, 4);
    const jan4Weekday = jan4.getUTCDay() || 7;
    const monday = new Date(jan4);
    monday.setUTCDate(jan4.getUTCDate() + (1 - jan4Weekday) + (week - 1) * 7);
    // An ISO week belongs to the calendar year containing its Thursday.
    const thursday = new Date(monday);
    thursday.setUTCDate(monday.getUTCDate() + 3);
    return thursday.getUTCFullYear() === year ? monday : undefined;
  }

  const fallback = new Date(trimmed);
  return Number.isNaN(fallback.getTime()) ? undefined : fallback;
}

function buildRow(raw: Record<string, unknown>, sourceFile: string, sourceFormat: string): TrendRow {
  let query = String(
    raw['query'] ?? raw['keyword'] ?? raw['term'] ?? raw['search_term'] ?? ''
  ).trim();

  const snapshotDateValue = String(raw['snapshot_date'] ?? '').trim();
  const dateValue = snapshotDateValue || String(
    raw['date'] ?? raw['week'] ?? raw['week_id'] ?? raw['period'] ?? ''
  ).trim();

  const region = String(raw['region'] ?? raw['region_name'] ?? raw['geo'] ?? '').trim();

  const searchIndex = coerceInt(raw['search_index'] ?? raw['index'] ?? raw['score']);
  const brandingMix = coerceFloat(
    raw['branding_mix'] ??
      raw['mix_share'] ??
      raw['implied_unit_sales_impact'] ??
      raw['metric']
  );

  const notableEvent = String(
    raw['notable_driver'] ??
      raw['notable_event'] ??
      raw['notes'] ??
      raw['channel_checks'] ??
      raw['processing_notes'] ??
      ''
  ).trim();

  const route = String(raw['route'] ?? '').trim();
  const airline = String(raw['airline'] ?? '').trim();
  const season = String(raw['season'] ?? '').trim();

  if (!query) {
    if (route || airline) {
      query = [route, airline, season].filter(Boolean).join(' ').trim();
    }
    if (!query) {
      query = String(raw['provider'] ?? raw['category'] ?? raw['topic'] ?? '').trim();
    }
  }

  const row: TrendRow = {
    query: query || sourceFile,
    date: dateValue,
    snapshot_date: snapshotDateValue || dateValue,
    region,
    search_index: searchIndex,
    branding_mix: brandingMix,
    notable_event: notableEvent,
    source_file: sourceFile,
    source_format: sourceFormat
  };

  for (const key of OPTIONAL_COPY_KEYS) {
    const value = raw[key];
    if (value !== undefined && value !== null && value !== '') {
      row[key] = value as string;
    }
  }

  if (route) {
    row['route'] = route;
    if (route.includes('-')) {
      const [origin, destination] = route.split('-', 2).map(item => item.trim());
      if (origin) row['origin_airport'] = origin;
      if (destination) row['destination_airport'] = destination;
    }
  }

  if (airline) {
    row['airline'] = airline;
  }

  if (season) {
    row['season'] = season;
  }

  const avgFare = coerceFloat(raw['avg_fare_usd']);
  if (avgFare !== undefined) {
    row['avg_fare_usd'] = avgFare;
  }

  const premiumFare = coerceFloat(raw['premium_fare_usd']);
  if (premiumFare !== undefined) {
    row['premium_fare_usd'] = premiumFare;
  }

  const fareYoy = coerceFloat(raw['fare_yoy_pct']);
  if (fareYoy !== undefined) {
    row['fare_yoy_pct'] = fareYoy;
  }

  const advancePurchase = coerceInt(raw['advance_purchase_days']);
  if (advancePurchase !== undefined) {
    row['advance_purchase_days'] = advancePurchase;
  }

  const loadFactor = coerceFloat(raw['load_factor_pct']);
  if (loadFactor !== undefined) {
    row['load_factor_pct'] = loadFactor;
  }

  const ancillaryMix = coerceFloat(raw['ancillary_revenue_pct']);
  if (ancillaryMix !== undefined) {
    row['ancillary_revenue_pct'] = ancillaryMix;
  }

  const notableDriver = String(raw['notable_driver'] ?? '').trim();
  if (notableDriver) {
    row['notable_driver'] = notableDriver;
    row['notable_event'] = notableDriver;
  }

  return row;
}

async function loadTabularRows(filePath: string, delimiter: string) {
  const content = await fs.readFile(filePath, 'utf-8');
  const lines = content.split(/\r?\n/);
  let headerLine: string | undefined;
  const dataLines: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    if (trimmed.startsWith('#')) {
      const comment = trimmed.slice(1).trim();
      if (!headerLine && comment.includes(delimiter)) {
        headerLine = comment;
      }
      continue;
    }
    dataLines.push(line);
  }

  if (dataLines.length === 0) {
    return [];
  }

  const csvSource = `${headerLine ? `${headerLine}\n` : ''}${dataLines.join('\n')}`;

  const records = parseCsv(csvSource, {
    columns: true,
    skip_empty_lines: true,
    delimiter
  }) as Record<string, unknown>[];

  const sourceFile = basename(filePath);
  const sourceFormat = delimiter === '\t' ? 'tsv' : 'csv';

  return records.map(record => buildRow(record, sourceFile, sourceFormat));
}

async function loadJsonRows(filePath: string) {
  const content = await fs.readFile(filePath, 'utf-8');
  const payload = JSON.parse(content) as unknown;
  const sourceFile = basename(filePath);

  const rows: TrendRow[] = [];

  const ingest = (data: Record<string, unknown>, defaults?: Record<string, unknown>) => {
    const merged = { ...(defaults ?? {}), ...data };
    rows.push(buildRow(merged, sourceFile, 'json'));
  };

  if (Array.isArray(payload)) {
    for (const entry of payload) {
      if (entry && typeof entry === 'object') {
        ingest(entry as Record<string, unknown>);
      }
    }
    return rows;
  }

  if (payload && typeof payload === 'object') {
    const objectPayload = payload as Record<string, unknown>;
    const regions = objectPayload['regions'];
    if (regions && typeof regions === 'object') {
      const weekValue = objectPayload['week'];
      for (const [regionName, regionPayload] of Object.entries(regions as Record<string, unknown>)) {
        if (!regionPayload || typeof regionPayload !== 'object') continue;
        const defaults: Record<string, unknown> = {
          region: regionName,
          week: weekValue,
          channel_checks: (regionPayload as Record<string, unknown>)['channel_checks']
        };
        const topQueries = (regionPayload as Record<string, unknown>)['top_queries'];
        if (Array.isArray(topQueries)) {
          for (const entry of topQueries) {
            if (entry && typeof entry === 'object') {
              ingest(entry as Record<string, unknown>, defaults);
            }
          }
        } else {
          ingest(regionPayload as Record<string, unknown>, defaults);
        }
      }
      return rows;
    }

    ingest(objectPayload);
  }

  return rows;
}

async function loadTrendRowsFromFile(filePath: string, suffix: 'csv' | 'tsv' | 'json') {
  if (suffix === 'csv') {
    return loadTabularRows(filePath, ',');
  }
  if (suffix === 'tsv') {
    return loadTabularRows(filePath, '\t');
  }
  return loadJsonRows(filePath);
}

export async function loadTrendDataset(trendDataDir: string) {
  const directory = resolve(trendDataDir);
  let entries: string[] = [];

  try {
    const dirents = await fs.readdir(directory, { withFileTypes: true });
    entries = dirents
      .filter(entry => entry.isFile())
      .map(entry => resolve(directory, entry.name))
      .filter(filePath => {
        const suffix = SUPPORTED_SUFFIXES[extname(filePath).toLowerCase()];
        return Boolean(suffix);
      });
  } catch (error) {
    return { rows: [], availableFiles: [] };
  }

  entries.sort((a, b) => basename(a).localeCompare(basename(b)));

  const aggregatedRows: TrendRow[] = [];

  for (const filePath of entries) {
    const suffix = SUPPORTED_SUFFIXES[extname(filePath).toLowerCase()];
    if (!suffix) continue;
    try {
      const rows = await loadTrendRowsFromFile(filePath, suffix);
      aggregatedRows.push(...rows);
    } catch (error) {
      console.warn(`Failed to ingest trend data from ${filePath}:`, error);
    }
  }

  return {
    rows: aggregatedRows,
    availableFiles: entries.map(filePath => basename(filePath))
  };
}

export function collectTextFromContent(content: unknown): string {
  const texts: string[] = [];

  const walk = (node: unknown) => {
    if (!node) {
      return;
    }
    if (typeof node === 'string') {
      texts.push(node);
      return;
    }
    if (Array.isArray(node)) {
      for (const item of node) {
        walk(item);
      }
      return;
    }
    if (typeof node === 'object') {
      const record = node as Record<string, unknown>;
      if (typeof record.text === 'string') {
        texts.push(record.text);
      }
      if (record.data !== undefined) {
        walk(record.data);
      }
      if (record.contents !== undefined) {
        walk(record.contents);
      }
    }
  };

  walk(content);
  return texts.join('\n');
}

export async function queryAirfareTrends(trendDataDir: string, filters: AirfareTrendFilters) {
  const { rows, availableFiles } = await loadTrendDataset(trendDataDir);

  if (rows.length === 0) {
    return {
      rows: [],
      available_files: availableFiles,
      filters,
      total_rows: 0,
      matched_rows: 0,
      rows_returned: 0,
      trend_data_dir: resolve(trendDataDir)
    };
  }

  const snapshotFilterDate = parseIsoDate(filters.snapshotDate ?? undefined);
  const snapshotFilterIso = snapshotFilterDate ? snapshotFilterDate.toISOString() : undefined;

  const routeFilter = (filters.routeContains ?? '').trim().toLowerCase();
  const originFilter = (filters.originAirport ?? '').trim().toLowerCase();
  const destinationFilter = (filters.destinationAirport ?? '').trim().toLowerCase();
  const airlineFilter = (filters.airlineContains ?? '').trim().toLowerCase();
  const seasonFilter = (filters.seasonContains ?? '').trim().toLowerCase();
  const notableFilter = (filters.notableContains ?? '').trim().toLowerCase();

  const filtered = rows.filter(row => {
    const snapshotDateValue = typeof row['snapshot_date'] === 'string' ? row['snapshot_date'] as string : undefined;
    const parsedSnapshot = parseIsoDate(snapshotDateValue);
    const rowSnapshotIso = parsedSnapshot ? parsedSnapshot.toISOString() : undefined;
    if (snapshotFilterIso && rowSnapshotIso !== snapshotFilterIso) {
      return false;
    }

    const routeValue = String(row['route'] ?? row['query'] ?? '').toLowerCase();
    if (routeFilter && !routeValue.includes(routeFilter)) {
      return false;
    }

    if (originFilter) {
      const origin = String(row['origin_airport'] ?? '').toLowerCase();
      if (origin !== originFilter) {
        return false;
      }
    }

    if (destinationFilter) {
      const destination = String(row['destination_airport'] ?? '').toLowerCase();
      if (destination !== destinationFilter) {
        return false;
      }
    }

    if (airlineFilter) {
      const airline = String(row['airline'] ?? '').toLowerCase();
      if (!airline.includes(airlineFilter)) {
        return false;
      }
    }

    if (seasonFilter) {
      const season = String(row['season'] ?? '').toLowerCase();
      if (!season.includes(seasonFilter)) {
        return false;
      }
    }

    if (notableFilter) {
      const notable = String(row['notable_event'] ?? '').toLowerCase();
      if (!notable.includes(notableFilter)) {
        return false;
      }
    }

    return true;
  });

  const sortedRows = filtered
    .map(row => ({ ...row }))
    .sort((a, b) => {
      const dateA = parseIsoDate(typeof a['snapshot_date'] === 'string' ? (a['snapshot_date'] as string) : undefined)?.getTime() ?? -Infinity;
      const dateB = parseIsoDate(typeof b['snapshot_date'] === 'string' ? (b['snapshot_date'] as string) : undefined)?.getTime() ?? -Infinity;
      if (dateA !== dateB) {
        return dateB - dateA;
      }
      const routeA = String(a['route'] ?? a['query'] ?? '');
      const routeB = String(b['route'] ?? b['query'] ?? '');
      if (routeA !== routeB) {
        return routeA.localeCompare(routeB);
      }
      const airlineA = String(a['airline'] ?? '');
      const airlineB = String(b['airline'] ?? '');
      return airlineA.localeCompare(airlineB);
    });

  const limit = Math.min(Math.max(filters.limit ?? DEFAULT_LIMIT, 1), MAX_LIMIT);
  const limitedRows = sortedRows.slice(0, limit);

  return {
    rows: limitedRows,
    available_files: availableFiles,
    filters: {
      snapshot_date: filters.snapshotDate ?? null,
      route_contains: filters.routeContains ?? null,
      origin_airport: filters.originAirport ?? null,
      destination_airport: filters.destinationAirport ?? null,
      airline_contains: filters.airlineContains ?? null,
      season_contains: filters.seasonContains ?? null,
      notable_contains: filters.notableContains ?? null,
      limit
    },
    total_rows: rows.length,
    matched_rows: sortedRows.length,
    rows_returned: limitedRows.length,
    trend_data_dir: resolve(trendDataDir)
  };
}
