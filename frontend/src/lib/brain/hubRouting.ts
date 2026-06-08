export interface HubState {
  section: string;
  item: string;
  scope: string;
}

export const DEFAULT_HUB_STATE: HubState = {
  section: 'knowledge',
  item: 'definitions',
  scope: 'all',
};

const VALID_SECTIONS = new Set(['knowledge', 'ops', 'sharing', 'activity']);
const VALID_SCOPES = new Set(['agent', 'company', 'personal', 'all']);

export function parseHash(hash?: string): HubState {
  let raw: string;
  if (hash !== undefined) {
    raw = hash;
  } else if (typeof window !== 'undefined') {
    raw = window.location.hash;
  } else {
    return { ...DEFAULT_HUB_STATE };
  }

  // Strip leading '#'
  const stripped = raw.startsWith('#') ? raw.slice(1) : raw;

  // Split path from query string
  const qIdx = stripped.indexOf('?');
  const pathPart = qIdx !== -1 ? stripped.slice(0, qIdx) : stripped;
  const queryPart = qIdx !== -1 ? stripped.slice(qIdx + 1) : '';

  // Parse section and item from '<section>/<item>'
  const slashIdx = pathPart.indexOf('/');
  const rawSection = slashIdx !== -1 ? pathPart.slice(0, slashIdx) : pathPart;
  const rawItem = slashIdx !== -1 ? pathPart.slice(slashIdx + 1) : '';

  const section = VALID_SECTIONS.has(rawSection)
    ? rawSection
    : DEFAULT_HUB_STATE.section;

  const item =
    rawItem.trim().length > 0 ? rawItem.trim() : DEFAULT_HUB_STATE.item;

  // Parse scope from query string
  let scope = DEFAULT_HUB_STATE.scope;
  if (queryPart.length > 0) {
    const params = new URLSearchParams(queryPart);
    const rawScope = params.get('scope') ?? '';
    if (VALID_SCOPES.has(rawScope)) {
      scope = rawScope;
    }
  }

  return { section, item, scope };
}

export function writeHash(state: Partial<HubState>): void {
  if (typeof window === 'undefined') return;

  const current = parseHash(window.location.hash);
  const merged: HubState = {
    section: state.section !== undefined ? state.section : current.section,
    item: state.item !== undefined ? state.item : current.item,
    scope: state.scope !== undefined ? state.scope : current.scope,
  };

  const newHash = `#${merged.section}/${merged.item}?scope=${merged.scope}`;

  if (typeof history !== 'undefined' && typeof history.replaceState === 'function') {
    history.replaceState(null, '', newHash);
  } else {
    window.location.hash = newHash;
  }
}

export function onHashChange(cb: (state: HubState) => void): () => void {
  if (typeof window === 'undefined') {
    return () => {};
  }

  const handler = () => {
    cb(parseHash(window.location.hash));
  };

  window.addEventListener('hashchange', handler);

  return () => {
    window.removeEventListener('hashchange', handler);
  };
}
