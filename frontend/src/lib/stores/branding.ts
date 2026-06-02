import { writable, derived } from 'svelte/store';

export type Brand = {
  name: string;
  full_name: string;
  domain: string;
  tagline: string;
  logo_url: string;
  favicon_url: string;
  theme: { primary_color: string; background_color: string; accent_color: string };
  footer_text: string;
  show_powered_by: boolean;
  _active_tenant?: string;
};

const DEFAULT: Brand = {
  name: 'Dash',
  full_name: 'Dash',
  domain: 'localhost',
  tagline: '',
  logo_url: '/api/branding/logo.svg',
  favicon_url: '/api/branding/favicon.ico',
  theme: { primary_color: '#00fc40', background_color: '#0a0a0a', accent_color: '#00fc40' },
  footer_text: 'Built with Dash',
  show_powered_by: true,
};

export const brand = writable<Brand>(DEFAULT);
export const agentName = derived(brand, ($b) => $b.name);

export async function loadBrand(): Promise<void> {
  try {
    const r = await fetch('/api/branding');
    if (r.ok) {
      const data = await r.json();
      brand.set({ ...DEFAULT, ...data });
    }
  } catch {
    // Silent fail — keep defaults.
  }
}

export function applyBrandToDocument(b: Brand): void {
  if (typeof document === 'undefined') return;
  document.title = b.full_name || b.name;
  let link = document.querySelector("link[rel='icon']") as HTMLLinkElement | null;
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = b.favicon_url + '?v=' + Date.now();
}
