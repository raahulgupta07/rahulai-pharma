/**
 * rest_client.js — drop-in Node SDK for the CityAgent Pharma embed API.
 *
 * Zero deps. Node 18+ (global fetch + crypto). CommonJS.
 *
 *   const { CityAgent } = require('./rest_client');
 *   const ca = new CityAgent(BASE, EMBED_ID, PUBLIC_KEY, SECRET_KEY, 'https://yoursite.com');
 *   console.log(await ca.ask('is paracetamol in stock at my branch?',
 *               { id: 'alice', store_id: '20063-CCBRBKMY', role: 'staff' }));
 *
 * Pass user=null for anonymous public mode (tier-3 global/catalog scope).
 * SECRET_KEY stays server-side; never ship to a browser.
 */
'use strict';
const crypto = require('crypto');

class CityAgentError extends Error {}

class CityAgent {
  constructor(baseUrl, embedId, publicKey, secretKey = null, origin = null, timeout = 30000) {
    this.base = baseUrl.replace(/\/+$/, '');
    this.embedId = embedId;
    this.publicKey = publicKey;
    this.secretKey = secretKey;
    this.origin = origin;
    this.timeout = timeout;
    this._session = null;
    this._exp = 0;
  }

  /** canonical JSON: sorted keys, no spaces — must byte-match the server */
  static canonical(user) {
    const sorted = {};
    for (const k of Object.keys(user).sort()) sorted[k] = user[k];
    return JSON.stringify(sorted);
  }

  sign(user) {
    if (!this.secretKey) throw new CityAgentError('secret_key required for user-scoped (hmac) mode');
    return crypto.createHmac('sha256', this.secretKey).update(CityAgent.canonical(user)).digest('hex');
  }

  _headers(extra = {}) {
    const h = { 'Content-Type': 'application/json', ...extra };
    if (this.origin) h.Origin = this.origin;
    return h;
  }

  async session(user = null) {
    if (this._session && Date.now() / 1000 < this._exp - 30) return this._session;
    const body = { embed_id: this.embedId, public_key: this.publicKey };
    if (user) { body.user = user; body.signature = this.sign(user); }
    const res = await this._post('/api/embed/session/create', body);
    if (!res.session_token) throw new CityAgentError('no session_token: ' + JSON.stringify(res));
    this._session = res.session_token;
    this._exp = Date.now() / 1000 + (res.expires_in || 900);
    return this._session;
  }

  async chat(message, user = null) {
    const token = await this.session(user);
    const res = await this._post('/api/embed/chat', { session_token: token, message });
    return res.content || '';
  }

  ask(message, user = null) { return this.chat(message, user); }

  /** Streaming. onToken(delta), onStep({label,icon}) optional. Returns full answer. */
  async stream(message, onToken, onStep = null, user = null) {
    const token = await this.session(user);
    const r = await fetch(this.base + '/api/embed/chat/stream', {
      method: 'POST',
      headers: this._headers({ Accept: 'text/event-stream' }),
      body: JSON.stringify({ session_token: token, message }),
    });
    if (!r.ok) throw new CityAgentError(`HTTP ${r.status} on /chat/stream`);

    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '', full = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) !== -1) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        let event = 'message', data = '';
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) data += line.slice(5).trim();
        }
        if (!data || data === '[DONE]' || event === 'done') continue;
        let j; try { j = JSON.parse(data); } catch { continue; }
        if (event === 'step' && onStep) onStep(j);
        else if (j && j.delta != null) { full += j.delta; onToken(j.delta); }
      }
    }
    return full;
  }

  async _post(path, body) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), this.timeout);
    let r;
    try {
      r = await fetch(this.base + path, {
        method: 'POST', headers: this._headers(),
        body: JSON.stringify(body), signal: ctrl.signal,
      });
    } finally { clearTimeout(t); }
    const text = await r.text();
    let j; try { j = JSON.parse(text); } catch { j = null; }
    if (!r.ok) {
      const detail = (j && j.detail) || text;
      throw new CityAgentError(`HTTP ${r.status} on ${path}: ${detail}`);
    }
    return j || {};
  }
}

module.exports = { CityAgent, CityAgentError };

// quick smoke test:  node rest_client.js
if (require.main === module) {
  (async () => {
    const ca = new CityAgent(
      process.env.CITYAGENT_BASE   || 'http://localhost:8011',
      process.env.CITYAGENT_EMBED  || 'emb_rGd8VWW8DloS6WNNssvenA',
      process.env.CITYAGENT_PUBKEY || 'pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT',
      process.env.CITYAGENT_EMBED_SECRET || null,
      process.env.CITYAGENT_ORIGIN || 'https://yourpharmacy.com',
    );
    console.log('Blocking:', await ca.ask('hello'));
    process.stdout.write('Streaming: ');
    await ca.stream('list substitutes for amoxicillin',
      d => process.stdout.write(d),
      s => process.stdout.write(`\n[${s.icon || ''} ${s.label || ''}]\n`));
    console.log();
  })().catch(e => { console.error(e.message); process.exit(1); });
}
