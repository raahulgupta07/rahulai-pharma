# Branding

Default brand assets used when `BRANDING_DIR` env var is not set.

## Per-tenant branding

Create `branding/tenant_<name>/` with the same files. Set
env var `BRANDING_DIR=branding/tenant_<name>` on deploy.

## Files

- `company.json` — name, domain, theme colors, support email
- `logo.svg` — header logo (auto-served at `/api/branding/logo.svg`)
- `favicon.ico` — browser tab icon
- `theme.css` — CSS variable overrides
