<?php
/**
 * widget-embed.php — full, copy-paste page that drops the chat bubble into a
 * logged-in PHP app with per-user store scoping (HMAC mode).
 *
 * Serve this from your app where $currentUser is already authenticated.
 * The SECRET_KEY is read server-side and never reaches the browser; only the
 * signature does.
 */

require __DIR__ . '/CityAgentClient.php';

// ---- config (move to env / config file) ----------------------------------
$BASE_URL   = getenv('CITYAGENT_BASE')   ?: 'http://localhost:8011';
$EMBED_ID   = getenv('CITYAGENT_EMBED')  ?: 'emb_rGd8VWW8DloS6WNNssvenA';
$PUBLIC_KEY = getenv('CITYAGENT_PUBKEY') ?: 'pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT';
$SECRET_KEY = getenv('CITYAGENT_EMBED_SECRET');  // REQUIRED — set in your env

// ---- the logged-in user (pull from YOUR session/DB) -----------------------
// Replace this stub with your real auth.
$currentUser = (object)[
    'id'         => 'alice',
    'store_code' => '20063-CCBRBKMY',
];

$user = [
    'id'       => (string)$currentUser->id,
    'store_id' => (string)$currentUser->store_code,
    'role'     => 'staff',                    // staff | customer
];

// sign server-side
$canonical = CityAgentClient::canonical($user);
$signature = hash_hmac('sha256', $canonical, (string)$SECRET_KEY);
?>
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>My Pharmacy</title></head>
<body>
  <h1>Welcome, <?= htmlspecialchars($currentUser->id) ?></h1>
  <p>Your store stock assistant is bottom-right →</p>

  <!-- CityAgent Pharma chat bubble — user-scoped -->
  <script
    src="<?= htmlspecialchars($BASE_URL) ?>/api/embed/widget.js"
    data-embed-id="<?= htmlspecialchars($EMBED_ID) ?>"
    data-public-key="<?= htmlspecialchars($PUBLIC_KEY) ?>"
    data-user='<?= htmlspecialchars($canonical, ENT_QUOTES) ?>'
    data-user-sig="<?= htmlspecialchars($signature) ?>"
    data-title="CityAgent Pharma"
    data-greeting="Hi! Ask about stock, substitutes, or indications."
    data-position="bottom-right"
    data-accent="#c96342"
    data-stream="true"
    async></script>
</body>
</html>
