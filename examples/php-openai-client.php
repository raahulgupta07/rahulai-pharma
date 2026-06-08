<?php
/**
 * CityPharma OpenAI-compatible gateway — PHP example.
 *
 * Install:  composer require openai-php/client guzzlehttp/guzzle
 *
 * The CityPharma gateway speaks the OpenAI API, so the standard
 * openai-php/client works unchanged — just point base_uri at /api/v1 and
 * use the store-bound dash-key-* as the API key.
 *
 * Usage:  php php-openai-client.php
 */

require __DIR__ . '/vendor/autoload.php';

// ── Config — set these for your deployment ───────────────────────────────
$HOST    = getenv('CITYPHARMA_HOST') ?: 'http://127.0.0.1:8011';
$API_KEY = getenv('CITYPHARMA_KEY')  ?: 'dash-key-REPLACE_ME';
$MODEL   = 'citypharma-analyst';

$client = OpenAI::factory()
    ->withBaseUri($HOST . '/api/v1')   // note: /api/v1, not the bare host
    ->withApiKey($API_KEY)             // Authorization: Bearer dash-key-...
    ->make();

// ── 1) List models ───────────────────────────────────────────────────────
echo "== models ==\n";
foreach ($client->models()->list()->data as $m) {
    echo "  {$m->id}\n";
}

// ── 2) Blocking chat completion ──────────────────────────────────────────
echo "\n== chat (blocking) ==\n";
$result = $client->chat()->create([
    'model'    => $MODEL,
    'messages' => [
        ['role' => 'user', 'content' => 'is paracetamol in stock at my branch?'],
    ],
    // 'stream' => false  (default)
    // Optional: pin a multi-turn thread server-side
    // 'user' => 'session-abc',
]);

echo $result->choices[0]->message->content . "\n";
echo "tokens: {$result->usage->totalTokens}\n";

// ── 3) Streaming chat completion ─────────────────────────────────────────
echo "\n== chat (streaming) ==\n";
$stream = $client->chat()->createStreamed([
    'model'    => $MODEL,
    'messages' => [
        ['role' => 'user', 'content' => 'what substitutes does paracetamol have?'],
    ],
    'stream'   => true,
]);

foreach ($stream as $chunk) {
    $delta = $chunk->choices[0]->delta->content ?? '';
    if ($delta !== '') {
        echo $delta;
        flush();
    }
}
echo "\n";

/*
 * Access model (store-bound key):
 *   Tier 1  own store (key.store_id)  → full data incl. qty + cost
 *   Tier 2  other stores             → availability only (no qty, no price)
 *   Tier 3  reference (catalog, subs) → unrestricted
 *
 * The boundary is enforced server-side by the toolset, so you cannot pull
 * another store's quantities even with a crafted prompt.
 */
