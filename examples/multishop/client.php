<?php
/**
 * CityPharma multi-shop client — ONE file, every outlet.
 *
 * No per-shop code. Drop your `.env` (the admin "Copy .env" download — all 53
 * outlet keys) next to this file and run. Each shop is one CITYPHARMA_KEY_*
 * entry; this client streams the answer AND the live agent-thinking trace.
 *
 *   php client.php "is paracetamol in stock at my branch?"      # ask every shop
 *   php client.php "..." 20003-CCJ8                              # ask one shop
 *
 * Warm pharmacist formatting (Medicine·Salt·Stock·Price + Tip) is produced
 * server-side, so it lands automatically. The live thinking trace needs the
 * raw SSE parse below + header "X-Agent-Steps: 1" — official OpenAI SDKs drop
 * the non-standard x_agent_step frames, so do NOT swap this for an SDK.
 */

const MODEL = "citypharma-analyst";

/** Load BASE + every CITYPHARMA_KEY_<outlet> from a .env file. */
function load_shops(string $envPath): array {
    if (!is_file($envPath)) {
        fwrite(STDERR, "missing $envPath — download it from the admin 'Copy .env' button\n");
        exit(1);
    }
    $base = null; $shops = [];   // outlet => key
    foreach (file($envPath, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line === "" || $line[0] === "#" || strpos($line, "=") === false) continue;
        [$k, $v] = array_map("trim", explode("=", $line, 2));
        if ($k === "CITYPHARMA_BASE") { $base = rtrim($v, "/"); continue; }
        if (strpos($k, "CITYPHARMA_KEY_") === 0) {
            $outlet = str_replace("_", "-", substr($k, strlen("CITYPHARMA_KEY_")));
            if ($v !== "") $shops[$outlet] = $v;
        }
    }
    if (!$base)   { fwrite(STDERR, "no CITYPHARMA_BASE in .env\n"); exit(1); }
    if (!$shops)  { fwrite(STDERR, "no CITYPHARMA_KEY_* in .env\n"); exit(1); }
    return [$base, $shops];
}

/**
 * Ask one shop. Streams: $onToken(string) per answer chunk,
 * $onThink(label, icon) per live agent step. Returns the full answer.
 */
function ask_shop(string $base, string $key, string $question,
                  callable $onToken, callable $onThink): string {
    $answer = "";
    $ch = curl_init("$base/chat/completions");
    curl_setopt_array($ch, [
        CURLOPT_POST       => true,
        CURLOPT_HTTPHEADER => [
            "Authorization: Bearer $key",
            "Content-Type: application/json",
            "X-Agent-Steps: 1",                 // opt-in: live agent thinking
        ],
        CURLOPT_POSTFIELDS => json_encode([
            "model"    => MODEL,
            "stream"   => true,
            "messages" => [["role" => "user", "content" => $question]],
        ]),
        CURLOPT_WRITEFUNCTION => function ($ch, $chunk) use (&$answer, $onToken, $onThink) {
            static $buf = "";
            $buf .= $chunk;
            while (($nl = strpos($buf, "\n")) !== false) {
                $line = trim(substr($buf, 0, $nl));  $buf = substr($buf, $nl + 1);
                if (strpos($line, "data:") !== 0) continue;
                $data = trim(substr($line, 5));
                if ($data === "[DONE]") continue;
                $delta = json_decode($data, true)["choices"][0]["delta"] ?? [];
                if (!empty($delta["x_agent_step"])) {
                    $s = $delta["x_agent_step"];
                    $onThink($s["label"] ?? "", $s["icon"] ?? "");
                }
                if (isset($delta["content"])) {
                    $answer .= $delta["content"];
                    $onToken($delta["content"]);
                }
            }
            return strlen($chunk);
        },
    ]);
    if (curl_exec($ch) === false) fwrite(STDERR, "  ! " . curl_error($ch) . "\n");
    curl_close($ch);
    return $answer;
}

// ---- CLI: ask one shop or fan out to all ----
$question = $argv[1] ?? "is paracetamol in stock at my branch?";
$only     = $argv[2] ?? null;                 // optional single outlet code
[$base, $shops] = load_shops(__DIR__ . "/.env");
if ($only) $shops = array_intersect_key($shops, [$only => 1]);

foreach ($shops as $outlet => $key) {
    fwrite(STDERR, "\n=== $outlet ===\n");
    $onToken = function ($t) { echo $t; flush(); };
    $onThink = function ($label, $icon) { fwrite(STDERR, "  \u{27f3} $icon $label\n"); };
    ask_shop($base, $key, $question, $onToken, $onThink);
    echo "\n";
}
