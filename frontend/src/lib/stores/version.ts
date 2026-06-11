// Build version + data freshness + What's-new feed — fetched ONCE from the
// public GET /api/version, shared across the top-nav chip, footer chip,
// Admin Overview card and Profile card. Fail-soft: stays null on error.
import { writable, get } from 'svelte/store';

export type Release = { version: string; date?: string; title?: string; items: string[] };
export type VersionInfo = {
  version: string;
  commit?: string;
  built_at?: string | null;
  image_age_hours?: number | null;
  stale?: boolean;
  product?: string;
  data?: {
    last_upload?: string | null;
    catalog_rows?: number | null;
    stock_rows?: number | null;
    shop_flat?: { both: number; catalog_only: number; stock_only: number } | null;
  } | null;
  changelog?: Release[];
};

export const versionInfo = writable<VersionInfo | null>(null);

let _loaded = false;
let _inflight: Promise<void> | null = null;

/** Fetch once per page-load; subsequent callers reuse the cached store. */
export async function loadVersion(force = false): Promise<void> {
  if (_loaded && !force) return;
  if (_inflight) return _inflight;
  _inflight = (async () => {
    try {
      const res = await fetch('/api/version', { headers: { Accept: 'application/json' } });
      if (res.ok) versionInfo.set(await res.json());
      else versionInfo.set(null);
    } catch {
      versionInfo.set(null);
    } finally {
      _loaded = true;
      _inflight = null;
    }
  })();
  return _inflight;
}

export function shortCommit(v: VersionInfo | null): string {
  const c = v?.commit;
  return c && c !== 'unknown' ? c.slice(0, 7) : '';
}

export function builtLabel(v: VersionInfo | null): string {
  const iso = v?.built_at;
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

export function ageLabel(v: VersionInfo | null): string {
  const h = v?.image_age_hours;
  if (h == null) return '';
  if (h < 1) return 'just now';
  if (h < 24) return `${Math.round(h)}h ago`;
  const d = Math.round(h / 24);
  return `${d}d ago`;
}

/** True once a load has been attempted (so callers can avoid double-fetch). */
export function versionLoaded(): boolean {
  return _loaded || get(versionInfo) != null;
}
