<?php
/**
 * CityAgentClient — drop-in PHP SDK for the CityAgent Pharma embed API.
 *
 * No Composer / no dependencies. Pure curl. PHP 7.4+.
 * Copy this one file into your project and you can talk to the agent in 3 lines:
 *
 *   require 'CityAgentClient.php';
 *   $ca  = new CityAgentClient(BASE_URL, EMBED_ID, PUBLIC_KEY, SECRET_KEY);
 *   echo $ca->ask('is paracetamol in stock at my branch?', ['id'=>'alice','store_id'=>'20063-CCBRBKMY','role'=>'staff']);
 *
 * Auth model:
 *   - public mode : pass null for $user → anonymous, global/catalog scope (tier 3)
 *   - hmac mode   : pass $user array → server signs with SECRET_KEY, agent masks
 *                   other stores' qty/price (3-tier scope binds to user.store_id)
 *
 * SECRET_KEY stays server-side. Never echo it to a browser.
 */

final class CityAgentClientError extends \RuntimeException {}

final class CityAgentClient
{
    private string $base;
    private string $embedId;
    private string $publicKey;
    private ?string $secretKey;
    private ?string $origin;
    private int $timeout;

    /** cached session so repeated ask()s reuse one token */
    private ?string $session = null;
    private int $sessionExp = 0;

    public function __construct(
        string $baseUrl,
        string $embedId,
        string $publicKey,
        ?string $secretKey = null,   // required only for user-scoped (hmac) mode
        ?string $origin = null,      // must match an allowlisted origin on the embed
        int $timeout = 30
    ) {
        $this->base      = rtrim($baseUrl, '/');
        $this->embedId   = $embedId;
        $this->publicKey = $publicKey;
        $this->secretKey = $secretKey;
        $this->origin    = $origin;
        $this->timeout   = $timeout;
    }

    /**
     * Canonical JSON the server expects for HMAC: sorted keys, no spaces,
     * unescaped slashes/unicode. Must byte-match server-side or signature fails.
     */
    public static function canonical(array $user): string
    {
        ksort($user);
        return json_encode($user, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    }

    /** HMAC-SHA256 signature of the canonical user payload. */
    public function sign(array $user): string
    {
        if (!$this->secretKey) {
            throw new CityAgentClientError('secret_key required for user-scoped (hmac) mode');
        }
        return hash_hmac('sha256', self::canonical($user), $this->secretKey);
    }

    /**
     * Create (or reuse) a session token. Pass $user for hmac mode, null for public.
     * Tokens are short-lived (~15 min); this caches and auto-refreshes.
     */
    public function session(?array $user = null): string
    {
        if ($this->session && time() < $this->sessionExp - 30) {
            return $this->session;
        }
        $body = ['embed_id' => $this->embedId, 'public_key' => $this->publicKey];
        if ($user !== null) {
            $body['user']      = $user;
            $body['signature'] = $this->sign($user);
        }
        $res = $this->post('/api/embed/session/create', $body);
        if (empty($res['session_token'])) {
            throw new CityAgentClientError('no session_token in response: ' . json_encode($res));
        }
        $this->session    = $res['session_token'];
        $this->sessionExp = time() + (int)($res['expires_in'] ?? 900);
        return $this->session;
    }

    /** Send one message, get the full answer (blocking). */
    public function chat(string $message, ?array $user = null): string
    {
        $token = $this->session($user);
        $res   = $this->post('/api/embed/chat', ['session_token' => $token, 'message' => $message]);
        return (string)($res['content'] ?? '');
    }

    /** Alias — the one-liner. */
    public function ask(string $message, ?array $user = null): string
    {
        return $this->chat($message, $user);
    }

    /**
     * Streaming chat. $onToken(string $delta) fires per chunk; $onStep(array{label,icon})
     * fires on agent-activity events (optional). Returns the full answer.
     */
    public function stream(string $message, callable $onToken, ?callable $onStep = null, ?array $user = null): string
    {
        $token = $this->session($user);
        $full  = '';
        $buf   = '';

        $ch = curl_init($this->base . '/api/embed/chat/stream');
        curl_setopt_array($ch, [
            CURLOPT_POST           => true,
            CURLOPT_POSTFIELDS     => json_encode(['session_token' => $token, 'message' => $message]),
            CURLOPT_HTTPHEADER     => $this->headers(['Accept: text/event-stream']),
            CURLOPT_TIMEOUT        => 0,           // streaming: no overall timeout
            CURLOPT_WRITEFUNCTION  => function ($ch, $chunk) use (&$buf, &$full, $onToken, $onStep) {
                $buf .= $chunk;
                // SSE frames are separated by a blank line
                while (($pos = strpos($buf, "\n\n")) !== false) {
                    $frame = substr($buf, 0, $pos);
                    $buf   = substr($buf, $pos + 2);
                    [$event, $data] = self::parseSse($frame);
                    if ($data === '[DONE]' || $event === 'done') {
                        continue;
                    }
                    $json = json_decode($data, true);
                    if ($event === 'step' && $onStep && is_array($json)) {
                        $onStep($json);
                    } elseif (is_array($json) && isset($json['delta'])) {
                        $full .= $json['delta'];
                        $onToken($json['delta']);
                    }
                }
                return strlen($chunk);
            },
        ]);
        $ok = curl_exec($ch);
        if ($ok === false) {
            $err = curl_error($ch);
            curl_close($ch);
            throw new CityAgentClientError("stream failed: $err");
        }
        curl_close($ch);
        return $full;
    }

    // ---- internals ---------------------------------------------------------

    private static function parseSse(string $frame): array
    {
        $event = 'message';
        $data  = '';
        foreach (explode("\n", $frame) as $line) {
            if (strpos($line, 'event:') === 0) {
                $event = trim(substr($line, 6));
            } elseif (strpos($line, 'data:') === 0) {
                $data .= ($data ? "\n" : '') . trim(substr($line, 5));
            }
        }
        return [$event, $data];
    }

    private function headers(array $extra = []): array
    {
        $h = ['Content-Type: application/json'];
        if ($this->origin) {
            $h[] = 'Origin: ' . $this->origin;
        }
        return array_merge($h, $extra);
    }

    private function post(string $path, array $body): array
    {
        $ch = curl_init($this->base . $path);
        curl_setopt_array($ch, [
            CURLOPT_POST           => true,
            CURLOPT_POSTFIELDS     => json_encode($body),
            CURLOPT_HTTPHEADER     => $this->headers(),
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => $this->timeout,
        ]);
        $raw  = curl_exec($ch);
        $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err  = curl_error($ch);
        curl_close($ch);

        if ($raw === false) {
            throw new CityAgentClientError("request to $path failed: $err");
        }
        $json = json_decode($raw, true);
        if ($code >= 400) {
            $detail = is_array($json) ? ($json['detail'] ?? $raw) : $raw;
            throw new CityAgentClientError("HTTP $code on $path: $detail", $code);
        }
        return is_array($json) ? $json : [];
    }
}
