-- 186_shop_flat_link_status.sql — [data-mismatch] make shop_flat a FULL OUTER join.
--
-- Reality: catalog (article) and balance-stock are maintained by different ERP
-- processes and DO NOT match 100% (frozen/advance-entry articles → no stock;
-- delayed catalog updates → stock with no article). The old shop_flat looped
-- over STOCK only, so catalog-only articles vanished — the agent could not tell
-- "in catalog, 0 stock" from "we don't carry it". This adds an explicit presence
-- column so all three states are representable + an honest "Not Found".
--
--   both         = article in catalog AND a stock row exists
--   catalog_only = article in catalog, NO stock row (Scenario 1, common) — site_code='__none__'
--   stock_only   = stock row, NO catalog match (Scenario 2, rare) — brand/generic NULL
--
-- Back-compat: legacy `linked` boolean kept. Backfill maps existing rows
-- (all were stock-driven): linked=true → 'both', linked=false → 'stock_only'.
-- The catalog_only rows appear only after the next build_shop_flat run.

ALTER TABLE citypharma.shop_flat
  ADD COLUMN IF NOT EXISTS link_status text NOT NULL DEFAULT 'both';

UPDATE citypharma.shop_flat
   SET link_status = CASE WHEN linked THEN 'both' ELSE 'stock_only' END
 WHERE link_status = 'both';   -- only untouched rows (idempotent re-run safe)

CREATE INDEX IF NOT EXISTS shop_flat_link_status_idx
  ON citypharma.shop_flat (link_status);
