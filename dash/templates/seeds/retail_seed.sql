-- ============================================================================
-- retail_seed.sql
-- Sample seed data for the "retail" agent template ontology.
-- PostgreSQL syntax. Executable top-to-bottom.
-- Multi-banner retail demo: grocery, apparel, dept store.
-- ============================================================================

-- ---------- DROP (reverse dependency order) ----------
DROP TABLE IF EXISTS line_item;
DROP TABLE IF EXISTS basket;
DROP TABLE IF EXISTS planogram;
DROP TABLE IF EXISTS inventory;
DROP TABLE IF EXISTS promotion;
DROP TABLE IF EXISTS customer;
DROP TABLE IF EXISTS sku;
DROP TABLE IF EXISTS vendor;
DROP TABLE IF EXISTS store;
DROP TABLE IF EXISTS banner;

-- ---------- SCHEMA ----------
CREATE TABLE IF NOT EXISTS banner (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    country      TEXT NOT NULL,
    founded_year INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS store (
    id         INTEGER PRIMARY KEY,
    banner_id  INTEGER NOT NULL REFERENCES banner(id),
    name       TEXT NOT NULL,
    region     TEXT NOT NULL,
    format     TEXT NOT NULL,
    sq_ft      INTEGER NOT NULL,
    opened_at  DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS vendor (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    lead_time_days  INTEGER NOT NULL,
    fill_rate       NUMERIC(4,3) NOT NULL,
    payment_terms   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sku (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    brand          TEXT NOT NULL,
    vendor_id      INTEGER NOT NULL REFERENCES vendor(id),
    season         TEXT NOT NULL,
    regular_price  NUMERIC(8,2) NOT NULL,
    cost           NUMERIC(8,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS customer (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    tier            TEXT NOT NULL,
    join_date       DATE NOT NULL,
    lifetime_spend  NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS promotion (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,
    start_date    DATE NOT NULL,
    end_date      DATE NOT NULL,
    discount_pct  NUMERIC(5,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    id               INTEGER PRIMARY KEY,
    store_id         INTEGER NOT NULL REFERENCES store(id),
    sku_id           INTEGER NOT NULL REFERENCES sku(id),
    on_hand_qty      INTEGER NOT NULL,
    last_count_date  DATE NOT NULL,
    shrink_value     NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS basket (
    id           INTEGER PRIMARY KEY,
    store_id     INTEGER NOT NULL REFERENCES store(id),
    customer_id  INTEGER REFERENCES customer(id),
    ts           TIMESTAMP NOT NULL,
    total        NUMERIC(10,2) NOT NULL,
    item_count   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS line_item (
    id            INTEGER PRIMARY KEY,
    basket_id     INTEGER NOT NULL REFERENCES basket(id),
    sku_id        INTEGER NOT NULL REFERENCES sku(id),
    qty           INTEGER NOT NULL,
    price         NUMERIC(8,2) NOT NULL,
    discount      NUMERIC(8,2) NOT NULL DEFAULT 0,
    promotion_id  INTEGER REFERENCES promotion(id)
);

CREATE TABLE IF NOT EXISTS planogram (
    id        INTEGER PRIMARY KEY,
    store_id  INTEGER NOT NULL REFERENCES store(id),
    category  TEXT NOT NULL,
    facings   INTEGER NOT NULL,
    capacity  INTEGER NOT NULL
);

-- ---------- BANNERS (3) ----------
INSERT INTO banner (id, name, country, founded_year) VALUES
    (1, 'FreshMart',  'USA', 1987),
    (2, 'UrbanFit',   'USA', 2004),
    (3, 'MegaStyle',  'USA', 1972);

-- ---------- STORES (6: 2 per banner) ----------
INSERT INTO store (id, banner_id, name, region, format, sq_ft, opened_at) VALUES
    (1, 1, 'FreshMart Downtown',     'Northeast', 'Grocery',         28000, '2010-04-12'),
    (2, 1, 'FreshMart Riverside',    'Midwest',   'Grocery Express', 12000, '2018-09-03'),
    (3, 2, 'UrbanFit SoHo',          'Northeast', 'Apparel Flagship',  9500, '2015-06-20'),
    (4, 2, 'UrbanFit Westfield',     'West',      'Apparel Mall',      6200, '2019-11-15'),
    (5, 3, 'MegaStyle Galleria',     'South',     'Department',       95000, '1998-08-01'),
    (6, 3, 'MegaStyle Northpark',    'West',      'Department',       82000, '2005-03-22');

-- ---------- VENDORS (5) ----------
-- Note: vendor 4 has bad fill_rate 0.72 (used for shrink/anomaly demo)
INSERT INTO vendor (id, name, lead_time_days, fill_rate, payment_terms) VALUES
    (1, 'Heartland Foods Co.',      5,  0.972, 'Net 30'),
    (2, 'Pacific Apparel Group',   14,  0.945, 'Net 45'),
    (3, 'Northstar Distribution',   7,  0.961, 'Net 30'),
    (4, 'Sunrise Imports Ltd.',    21,  0.720, 'Net 60'),  -- anomaly vendor
    (5, 'Metro Beauty Brands',      9,  0.938, 'Net 30');

-- ---------- SKUS (15) ----------
-- Note: SKU 7 (Classic Denim Jacket) is the "aged > 90d w/o markdown" demo item
INSERT INTO sku (id, name, category, brand, vendor_id, season, regular_price, cost) VALUES
    (1,  'Organic Whole Milk 1gal',   'Dairy',       'FreshFarm',     1, 'All',    5.49,  3.10),
    (2,  'Cage-Free Eggs Dozen',      'Dairy',       'FreshFarm',     1, 'All',    4.99,  2.40),
    (3,  'Sourdough Loaf',            'Bakery',      'ArtisanCo',     1, 'All',    6.49,  2.80),
    (4,  'Cold Brew Coffee 32oz',     'Beverage',    'BrewLab',       3, 'All',    7.99,  3.60),
    (5,  'Greek Yogurt 4-pack',       'Dairy',       'OliveGrove',    1, 'All',    8.49,  4.10),
    (6,  'Heirloom Tomatoes lb',      'Produce',     'FieldFresh',    1, 'Summer', 4.99,  2.20),
    (7,  'Classic Denim Jacket',      'Outerwear',   'UrbanFit',      2, 'Spring', 89.99, 32.00),  -- aged demo
    (8,  'Slim Fit Chinos',           'Bottoms',     'UrbanFit',      2, 'All',    59.99, 19.50),
    (9,  'Performance Tee',           'Tops',        'FlexCore',      2, 'Summer', 29.99,  8.75),
    (10, 'Wool Blend Overcoat',       'Outerwear',   'NorthEdge',     2, 'Winter', 189.99, 72.00),
    (11, 'Leather Crossbody Bag',     'Accessories', 'MetroLuxe',     4, 'All',    79.99, 28.00),
    (12, 'Silk Scarf',                'Accessories', 'MetroLuxe',     4, 'Fall',   45.99, 14.50),
    (13, 'Hydrating Face Serum',      'Beauty',      'LuminaSkin',    5, 'All',    34.99, 11.20),
    (14, 'Vitamin C Cleanser',        'Beauty',      'LuminaSkin',    5, 'All',    22.99,  7.40),
    (15, 'Cashmere Throw Blanket',    'Home',        'NorthEdge',     2, 'Winter', 129.99, 48.00);

-- ---------- CUSTOMERS (8) ----------
-- Note: customer 3 (Marcus Rivera) is gold tier inactive > 60 days (loyalty_churn demo)
INSERT INTO customer (id, name, tier, join_date, lifetime_spend) VALUES
    (1, 'Aisha Patel',       'gold',     '2019-03-14', 8420.55),
    (2, 'James Chen',        'silver',   '2021-07-22', 2310.40),
    (3, 'Marcus Rivera',     'gold',     '2017-11-05', 12890.00),  -- inactive churn risk
    (4, 'Sofia Nakamura',    'platinum', '2016-02-18', 24150.75),
    (5, 'Ethan Brooks',      'bronze',   '2023-05-30',  410.20),
    (6, 'Olivia Hartman',    'silver',   '2022-01-10', 1985.65),
    (7, 'Daniel Kowalski',   'gold',     '2018-09-09', 7240.00),
    (8, 'Priya Srinivasan',  'platinum', '2015-12-01', 31420.90);

-- ---------- PROMOTIONS (3 active) ----------
INSERT INTO promotion (id, name, type, start_date, end_date, discount_pct) VALUES
    (1, 'Spring Refresh 20% Off Apparel', 'category_pct', (CURRENT_DATE - INTERVAL '5 days')::date,  (CURRENT_DATE + INTERVAL '9 days')::date,  20.00),
    (2, 'Dairy Doorbuster',               'sku_dollar',   (CURRENT_DATE - INTERVAL '2 days')::date,  (CURRENT_DATE + INTERVAL '5 days')::date,  15.00),
    (3, 'Beauty BOGO 50%',                'bogo',         (CURRENT_DATE - INTERVAL '7 days')::date,  (CURRENT_DATE + INTERVAL '14 days')::date, 50.00);

-- ---------- INVENTORY (20) ----------
INSERT INTO inventory (id, store_id, sku_id, on_hand_qty, last_count_date, shrink_value) VALUES
    (1,  1,  1,  142, (CURRENT_DATE - INTERVAL '3 days')::date,  18.40),
    (2,  1,  2,  210, (CURRENT_DATE - INTERVAL '3 days')::date,   9.60),
    (3,  1,  3,   48, (CURRENT_DATE - INTERVAL '2 days')::date,  12.00),
    (4,  1,  5,   76, (CURRENT_DATE - INTERVAL '2 days')::date,   0.00),
    (5,  1,  6,   33, (CURRENT_DATE - INTERVAL '1 days')::date,  22.00),
    (6,  2,  1,   88, (CURRENT_DATE - INTERVAL '4 days')::date,   6.20),
    (7,  2,  4,   54, (CURRENT_DATE - INTERVAL '4 days')::date,   0.00),
    (8,  2,  5,   40, (CURRENT_DATE - INTERVAL '3 days')::date,   8.20),
    (9,  3,  7,   12, (CURRENT_DATE - INTERVAL '95 days')::date, 64.00),  -- aged inventory
    (10, 3,  8,   45, (CURRENT_DATE - INTERVAL '6 days')::date,   0.00),
    (11, 3,  9,   90, (CURRENT_DATE - INTERVAL '5 days')::date,   0.00),
    (12, 4,  8,   28, (CURRENT_DATE - INTERVAL '7 days')::date,  19.50),
    (13, 4,  9,   62, (CURRENT_DATE - INTERVAL '6 days')::date,   0.00),
    (14, 5, 10,   15, (CURRENT_DATE - INTERVAL '10 days')::date, 72.00),
    (15, 5, 11,   24, (CURRENT_DATE - INTERVAL '9 days')::date, 280.00),  -- high shrink (vendor 4)
    (16, 5, 13,  110, (CURRENT_DATE - INTERVAL '4 days')::date,  22.40),
    (17, 5, 14,  130, (CURRENT_DATE - INTERVAL '4 days')::date,   7.40),
    (18, 6, 12,   38, (CURRENT_DATE - INTERVAL '8 days')::date, 145.00),  -- high shrink (vendor 4)
    (19, 6, 13,   85, (CURRENT_DATE - INTERVAL '5 days')::date,  11.20),
    (20, 6, 15,   18, (CURRENT_DATE - INTERVAL '12 days')::date, 96.00);

-- ---------- BASKETS (10, last 14 days) ----------
INSERT INTO basket (id, store_id, customer_id, ts, total, item_count) VALUES
    (1,  1, 1,    NOW() - INTERVAL '1 days',   24.46,  4),
    (2,  1, 5,    NOW() - INTERVAL '2 days',   12.98,  2),
    (3,  2, 6,    NOW() - INTERVAL '3 days',   16.48,  3),
    (4,  3, 4,    NOW() - INTERVAL '4 days',  149.98,  2),
    (5,  3, 2,    NOW() - INTERVAL '5 days',   89.99,  1),
    (6,  4, 8,    NOW() - INTERVAL '6 days',  119.97,  3),
    (7,  5, 7,    NOW() - INTERVAL '8 days',  214.97,  3),
    (8,  5, NULL, NOW() - INTERVAL '9 days',   57.98,  2),
    (9,  6, 4,    NOW() - INTERVAL '10 days', 175.98,  2),
    (10, 6, 1,    NOW() - INTERVAL '13 days',  68.97,  3);

-- ---------- LINE_ITEMS (25) ----------
INSERT INTO line_item (id, basket_id, sku_id, qty, price, discount, promotion_id) VALUES
    (1,  1,  1,  2,  5.49, 0.00, NULL),
    (2,  1,  2,  1,  4.99, 0.00, NULL),
    (3,  1,  3,  1,  6.49, 0.00, NULL),
    (4,  1,  6,  1,  4.99, 2.00, NULL),
    (5,  2,  1,  1,  5.49, 0.00, NULL),
    (6,  2,  4,  1,  7.99, 0.49, NULL),
    (7,  3,  2,  2,  4.99, 0.00, NULL),
    (8,  3,  5,  1,  8.49, 1.49, 2),
    (9,  4, 10,  1, 189.99, 40.00, 1),
    (10, 4,  8,  1,  59.99, 12.00, 1),
    (11, 5,  7,  1,  89.99,  0.00, NULL),
    (12, 6,  9,  2,  29.99,  6.00, 1),
    (13, 6,  8,  1,  59.99, 12.00, 1),
    (14, 7, 13,  2,  34.99,  0.00, 3),
    (15, 7, 14,  1,  22.99, 11.50, 3),
    (16, 7, 11,  1,  79.99,  0.00, NULL),
    (17, 8, 13,  1,  34.99,  0.00, 3),
    (18, 8, 14,  1,  22.99,  0.00, 3),
    (19, 9, 15,  1, 129.99,  0.00, NULL),
    (20, 9, 10,  1, 189.99, 40.00, 1),
    (21, 10, 1,  3,   5.49,  0.00, NULL),
    (22, 10, 5,  1,   8.49,  0.00, NULL),
    (23, 10, 3,  1,   6.49,  0.00, NULL),
    (24, 5, 12,  1,  45.99,  0.00, NULL),
    (25, 9, 12,  1,  45.99,  9.20, 1);

-- ---------- PLANOGRAMS (6) ----------
INSERT INTO planogram (id, store_id, category, facings, capacity) VALUES
    (1, 1, 'Dairy',       18, 540),
    (2, 1, 'Produce',     22, 660),
    (3, 3, 'Outerwear',    8,  96),
    (4, 4, 'Tops',        14, 196),
    (5, 5, 'Beauty',      20, 400),
    (6, 6, 'Accessories', 12, 240);

-- ============================================================================
-- summary (row counts per table):
--   banner       : 3
--   store        : 6
--   vendor       : 5
--   sku          : 15
--   customer     : 8
--   promotion    : 3
--   inventory    : 20
--   basket       : 10
--   line_item    : 25
--   planogram    : 6
--   ----------------
--   TOTAL ROWS   : 101
-- ============================================================================
