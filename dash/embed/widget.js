/* Dash Agent Embed Widget — Consumer Polish
 * Vanilla JS, shadow-DOM isolated
 *
 * Usage:
 *   <script src="https://your-dash-host/api/embed/widget.js"
 *           data-embed-id="emb_xxx"
 *           data-key="pub_xxx"
 *           data-user='{"id":"alice","store_id":"MUM01"}'
 *           data-user-sig="<hmac>"
 *           data-position="bottom-right"
 *           data-theme="consumer"
 *           data-greeting="Ask anything…"
 *           data-show-branding="1"
 *           async></script>
 */
(function () {
  "use strict";

  var script = document.currentScript ||
               document.querySelector('script[data-embed-id]');
  if (!script) return;

  var embedId  = script.getAttribute('data-embed-id');
  var pubKey   = script.getAttribute('data-key') || script.getAttribute('data-public-key');
  var userJson = script.getAttribute('data-user');
  var userSig  = script.getAttribute('data-user-sig');
  var position = script.getAttribute('data-position') || 'bottom-right';
  var theme    = script.getAttribute('data-theme') || 'consumer';
  var greeting = script.getAttribute('data-greeting') || 'Hi! How can I help you today?';
  var title    = script.getAttribute('data-title') || 'Support';
  var showBranding = script.getAttribute('data-show-branding') === '1';
  // SSE streaming: opt-out via data-stream="false". Default ON; falls back
  // to non-streaming POST /chat on any 4xx (e.g. consumer-mode → 400).
  var streamingEnabled = script.getAttribute('data-stream') !== 'false';

  if (!embedId || !pubKey) {
    console.error('[Dash Widget] missing data-embed-id or data-key');
    return;
  }

  var apiOrigin = (function () {
    try {
      var u = new URL(script.src, window.location.href);
      return u.origin;
    } catch (e) { return ''; }
  })();

  var userPayload = null;
  if (userJson) {
    try { userPayload = JSON.parse(userJson); }
    catch (e) { console.warn('[Dash Widget] data-user is not valid JSON'); }
  }

  var sessionToken = null;
  var sessionExpiresAt = 0;
  var messages = [];
  var sending = false;
  var firstReplyReceived = false;

  var explicit = {
    position: script.hasAttribute('data-position'),
    theme:    script.hasAttribute('data-theme'),
    greeting: script.hasAttribute('data-greeting'),
    title:    script.hasAttribute('data-title'),
    accent:   script.hasAttribute('data-accent'),
  };
  var accent = script.getAttribute('data-accent') || null;
  var logoUrl = script.getAttribute('data-logo') || null;

  function fetchServerConfig(cb) {
    try {
      fetch(apiOrigin + '/api/embed/config/' + encodeURIComponent(embedId))
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(cfg) {
          if (cfg) {
            if (!explicit.position && cfg.position) position = cfg.position;
            if (!explicit.theme    && cfg.theme)    theme    = cfg.theme;
            if (!explicit.greeting && cfg.welcome_msg) greeting = cfg.welcome_msg;
            if (!explicit.title    && cfg.name)     title    = cfg.name;
            if (!explicit.accent   && cfg.primary_color) accent = cfg.primary_color;
            if (!logoUrl && cfg.logo_url) logoUrl = cfg.logo_url;
          }
          cb();
        })
        .catch(function() { cb(); });
    } catch (e) { cb(); }
  }

  fetchServerConfig(buildWidget);

  function buildWidget() {

  // Resolve theme=auto → consumer (light) or dark by system pref
  if (theme === 'auto') {
    var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    theme = prefersDark ? 'dark' : 'consumer';
  }

  var host = document.createElement('div');
  host.id = 'dash-agent-embed';
  host.style.cssText = 'position:fixed;z-index:2147483647;';
  if (position.indexOf('bottom') >= 0) host.style.bottom = '20px';
  if (position.indexOf('top')    >= 0) host.style.top    = '20px';
  if (position.indexOf('right')  >= 0) host.style.right  = '20px';
  if (position.indexOf('left')   >= 0) host.style.left   = '20px';
  document.body.appendChild(host);
  var sr = host.attachShadow ? host.attachShadow({ mode: 'open' }) : host;

  var themes = {
    dark: {
      bg: '#0f0f12', surface: '#1a1a1f', fg: '#e5e5e8', dim: '#8a8a92',
      accent: '#5b8def', border: '#2a2a30',
      userBg: '#5b8def', userFg: '#ffffff',
      botBg: '#23232a', botFg: '#e5e5e8',
    },
    light: {
      bg: '#ffffff', surface: '#f7f7f9', fg: '#1c1c20', dim: '#7a7a82',
      accent: '#0066ff', border: '#e4e4e8',
      userBg: '#0066ff', userFg: '#ffffff',
      botBg: '#f0f0f3', botFg: '#1c1c20',
    },
    consumer: {
      bg: '#ffffff', surface: '#f7f7f9', fg: '#1c1c20', dim: '#7a7a82',
      accent: '#0066ff', border: '#e4e4e8',
      userBg: '#0066ff', userFg: '#ffffff',
      botBg: '#f0f0f3', botFg: '#1c1c20',
    }
  };
  var t = themes[theme] || themes.consumer;
  if (accent) { t.accent = accent; t.userBg = accent; }

  var SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  var MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';

  var style = document.createElement('style');
  style.textContent = `
    :host, .panel, .bubble { text-align: left; }
    :host, * { box-sizing: border-box; font-family: ${SANS}; }
    code, pre, code *, pre * { font-family: ${MONO}; }

    .bubble {
      width: 60px; height: 60px; border-radius: 50%;
      background: ${t.accent}; color: #fff; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 6px 20px rgba(0,0,0,0.18), 0 2px 6px rgba(0,0,0,0.08);
      transition: transform 0.2s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.2s ease;
    }
    .bubble:hover { transform: scale(1.08) translateY(-2px); box-shadow: 0 10px 28px rgba(0,0,0,0.22); }
    .bubble:active { transform: scale(0.96); }
    .bubble svg { width: 28px; height: 28px; }

    .panel {
      width: 380px; height: 580px; max-height: calc(100vh - 60px);
      background: ${t.bg}; border: 1px solid ${t.border};
      border-radius: 16px;
      display: none; flex-direction: column; overflow: hidden;
      box-shadow: 0 16px 48px rgba(0,0,0,0.18), 0 4px 12px rgba(0,0,0,0.08);
    }
    .panel.open { display: flex; }

    .header {
      padding: 14px 16px; background: ${t.surface};
      border-bottom: 1px solid ${t.border};
      display: flex; align-items: center; gap: 10px;
    }
    .header-logo {
      width: 28px; height: 28px; border-radius: 50%;
      background: ${t.accent}; color: #fff;
      display: flex; align-items: center; justify-content: center;
      font-weight: 600; font-size: 13px; flex-shrink: 0;
      object-fit: cover; overflow: hidden;
    }
    .header-logo img { width: 100%; height: 100%; object-fit: cover; }
    .header-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
    .header-title-row { display: flex; align-items: center; gap: 6px; }
    .title { color: ${t.fg}; font-weight: 600; font-size: 14px; }
    .online-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: #22c55e; display: inline-block;
      box-shadow: 0 0 0 2px rgba(34,197,94,0.2);
    }
    .header-sub { color: ${t.dim}; font-size: 11px; }
    .close {
      background: transparent; border: none; color: ${t.dim};
      cursor: pointer; padding: 4px; border-radius: 6px;
      display: flex; align-items: center; justify-content: center;
      transition: background 0.15s ease;
    }
    .close:hover { background: rgba(0,0,0,0.06); color: ${t.fg}; }
    .close svg { width: 18px; height: 18px; }

    .messages {
      flex: 1; overflow-y: auto; padding: 16px 14px;
      display: flex; flex-direction: column; gap: 10px;
      background: ${t.bg};
    }

    .msg-row { display: flex; gap: 8px; align-items: flex-end; }
    .msg-row.user { justify-content: flex-end; }
    .msg-row.bot { justify-content: flex-start; }

    .msg-avatar {
      width: 26px; height: 26px; border-radius: 50%;
      background: ${t.accent}; color: #fff;
      display: flex; align-items: center; justify-content: center;
      font-size: 12px; font-weight: 600; flex-shrink: 0;
      overflow: hidden;
    }
    .msg-avatar img { width: 100%; height: 100%; object-fit: cover; }

    .msg {
      padding: 10px 14px; max-width: 80%;
      font-size: 13.5px; line-height: 1.5;
      word-wrap: break-word; overflow-wrap: break-word;
    }
    .msg p { margin: 0 0 8px 0; }
    .msg p:last-child { margin-bottom: 0; }
    .msg ul, .msg ol { margin: 4px 0; padding-left: 20px; }
    .msg li { margin: 2px 0; }
    .msg .md-h { margin: 8px 0 4px; font-size: 13.5px; font-weight: 700; line-height: 1.35; }
    .msg b { font-weight: 700; }
    .msg i { font-style: italic; }
    .msg a { color: inherit; text-decoration: underline; }
    .msg code { background: rgba(0,0,0,0.08); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
    .msg pre { background: rgba(0,0,0,0.06); padding: 8px 10px; border-radius: 8px; overflow-x: auto; font-size: 12px; margin: 6px 0; }

    .msg.user {
      background: ${t.userBg}; color: ${t.userFg};
      border-radius: 18px 18px 4px 18px;
    }
    .msg.bot {
      background: ${t.botBg}; color: ${t.botFg};
      border-radius: 18px 18px 18px 4px;
    }
    .msg.system {
      align-self: center; color: ${t.dim};
      font-size: 11.5px; font-style: italic;
      background: transparent; padding: 4px 8px;
      max-width: 90%; text-align: center;
    }

    .greeting {
      color: ${t.dim}; font-size: 13px;
      padding: 24px 16px; text-align: center;
      line-height: 1.5;
    }

    .typing-row { display: flex; gap: 8px; align-items: flex-end; }
    .typing {
      display: inline-flex; gap: 4px; padding: 12px 14px;
      background: ${t.botBg}; border-radius: 18px 18px 18px 4px;
      align-items: center;
    }
    .typing span {
      width: 7px; height: 7px; border-radius: 50%;
      background: ${t.dim}; display: inline-block;
      animation: typing-bounce 1.3s infinite ease-in-out both;
    }
    .typing span:nth-child(1) { animation-delay: -0.32s; }
    .typing span:nth-child(2) { animation-delay: -0.16s; }
    @keyframes typing-bounce {
      0%, 80%, 100% { transform: scale(0.7); opacity: 0.5; }
      40% { transform: scale(1); opacity: 1; }
    }
    /* loading dots — store-accent bouncing wave (no blink) */
    .load-dots {
      display: inline-flex; gap: 4px; vertical-align: middle; margin-left: 4px;
    }
    .load-dots i {
      width: 7px; height: 7px; border-radius: 50%;
      background: ${t.accent}; display: inline-block;
      animation: load-bounce 1.2s infinite ease-in-out both;
    }
    .load-dots i:nth-child(1) { animation-delay: -0.32s; }
    .load-dots i:nth-child(2) { animation-delay: -0.16s; }
    @keyframes load-bounce {
      0%, 80%, 100% { transform: scale(0.5); opacity: 0.35; }
      40% { transform: scale(1); opacity: 1; }
    }

    /* live agent activity strip (what the agent is doing) */
    .agent-steps {
      display: flex; flex-direction: column; gap: 3px;
      margin: 0 0 6px 34px; padding: 8px 10px;
      background: ${t.surface}; border: 1px solid ${t.border};
      border-radius: 10px; max-width: 80%;
    }
    .agent-steps.done {
      gap: 0; padding: 5px 10px; cursor: pointer;
      color: ${t.dim};
    }
    .agent-steps-head {
      font-size: 11px; font-weight: 600; color: ${t.dim};
      display: flex; align-items: center; gap: 6px;
    }
    .agent-steps-head .spin {
      width: 9px; height: 9px; border-radius: 50%;
      border: 1.5px solid ${t.accent}; border-top-color: transparent;
      animation: agent-spin 0.7s linear infinite;
    }
    @keyframes agent-spin { to { transform: rotate(360deg); } }
    .agent-step {
      font-size: 12px; color: ${t.fg}; opacity: 0.85;
      display: flex; align-items: center; gap: 7px;
      animation: agent-step-in 0.25s ease;
    }
    @keyframes agent-step-in {
      from { opacity: 0; transform: translateY(2px); }
      to { opacity: 0.85; }
    }
    .agent-step .ic { flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; width: 12px; height: 12px; }
    .agent-step .ic.ok { color: ${t.accent}; font-weight: 700; }
    .agent-step .step-spin {
      width: 9px; height: 9px; border-radius: 50%;
      border: 1.5px solid ${t.accent}; border-top-color: transparent;
      animation: agent-spin 0.7s linear infinite;
    }
    .agent-step.agent-step-done { opacity: 0.5; }
    .agent-step.agent-step-done .step-tx { text-decoration: none; }
    .agent-steps.done .agent-step { display: none; }
    .agent-steps.done .agent-steps-head { font-weight: 500; }

    .chips {
      display: flex; flex-wrap: wrap; gap: 6px;
      padding: 4px 0 0 34px;
    }
    .chip {
      background: transparent; color: ${t.accent};
      border: 1px solid ${t.accent}; border-radius: 16px;
      padding: 6px 12px; font-size: 12px; cursor: pointer;
      font-family: inherit;
      transition: background 0.15s ease, color 0.15s ease;
    }
    .chip:hover { background: ${t.accent}; color: #fff; }

    .input-row {
      padding: 10px 12px; border-top: 1px solid ${t.border};
      background: ${t.bg};
      display: flex; gap: 8px; align-items: flex-end;
    }
    textarea {
      flex: 1; padding: 10px 14px;
      background: ${t.surface}; border: 1px solid ${t.border};
      color: ${t.fg}; font-family: inherit; font-size: 13.5px;
      resize: none; outline: none; line-height: 1.4;
      border-radius: 20px; max-height: 100px;
      transition: border-color 0.15s ease;
    }
    textarea:focus { border-color: ${t.accent}; }
    textarea::placeholder { color: ${t.dim}; }

    .send {
      width: 36px; height: 36px; border-radius: 50%;
      background: ${t.accent}; color: #fff; border: none;
      cursor: pointer; display: flex; align-items: center; justify-content: center;
      flex-shrink: 0; transition: transform 0.15s ease, background 0.15s ease;
      box-shadow: 0 2px 6px rgba(0,0,0,0.12);
    }
    .send:hover:not(:disabled) { transform: scale(1.05); }
    .send:active:not(:disabled) { transform: scale(0.95); }
    .send:disabled {
      background: ${t.border}; color: ${t.dim};
      cursor: not-allowed; box-shadow: none;
    }
    .send svg { width: 16px; height: 16px; }

    .footer {
      padding: 6px 12px; font-size: 10.5px; color: ${t.dim};
      text-align: center; background: ${t.bg};
      border-top: 1px solid ${t.border};
    }
    .footer a { color: ${t.dim}; text-decoration: none; }
    .footer a:hover { color: ${t.accent}; }
    .footer.empty { display: none; }

    @media (max-width: 480px) {
      .panel { width: 100vw; height: 100vh; max-height: 100vh; border-radius: 0;
               position: fixed; top: 0; left: 0; right: 0; bottom: 0; }
    }
  `;
  sr.appendChild(style);

  // Chat bubble SVG icon
  var CHAT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  var CLOSE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  var SEND_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>';

  var bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.title = title;
  bubble.innerHTML = CHAT_ICON;
  sr.appendChild(bubble);

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function avatarContent() {
    if (logoUrl) {
      return '<img src="' + escapeHtml(logoUrl) + '" alt="">';
    }
    var ch = (title || 'A').trim().charAt(0).toUpperCase();
    return escapeHtml(ch);
  }

  var panel = document.createElement('div');
  panel.className = 'panel';
  panel.innerHTML =
    '<div class="header">' +
      '<div class="header-logo">' + avatarContent() + '</div>' +
      '<div class="header-text">' +
        '<div class="header-title-row">' +
          '<span class="title">' + escapeHtml(title) + '</span>' +
          '<span class="online-dot" title="Online"></span>' +
        '</div>' +
        '<span class="header-sub">We typically reply instantly</span>' +
      '</div>' +
      '<button class="close" title="Close" aria-label="Close">' + CLOSE_ICON + '</button>' +
    '</div>' +
    '<div class="messages"></div>' +
    '<div class="input-row">' +
      '<textarea rows="1" placeholder="' + escapeHtml(greeting || 'Type your question...') + '"></textarea>' +
      '<button class="send" title="Send" aria-label="Send">' + SEND_ICON + '</button>' +
    '</div>' +
    '<div class="footer' + (showBranding ? '' : ' empty') + '">' +
      (showBranding ? 'powered by <a href="https://dash.example" target="_blank" rel="noopener">Dash</a>' : '') +
    '</div>';
  sr.appendChild(panel);

  var msgList   = panel.querySelector('.messages');
  var inputEl   = panel.querySelector('textarea');
  var sendBtn   = panel.querySelector('.send');
  var closeBtn  = panel.querySelector('.close');

  // ── Markdown renderer ───────────────────────────────────────────────
  function renderMd(s) {
    if (!s) return '';
    // Strip leftover tag patterns like [TAG: ...] (defense in depth).
    // Two-pass: drop complete [TAG: ...] tokens, then mop up any TRUNCATED
    // tag that lost its closing bracket mid-stream (a real past bug — a raw
    // "[CONFIDENCE_BREAK…" leaking into consumer view). The second pattern
    // only fires on a line that *starts* with a known/uppercase tag head so
    // we never eat ordinary "[note]" prose.
    var cleaned = String(s)
      .replace(/\[[A-Z_]+:[^\]]*\]/g, '')
      .replace(/\[(?:HEADLINE|LEAD|CONFIDENCE|CONFIDENCE_BREAKDOWN|SOURCE|KPI|WHY|SO_WHAT|RELATED|FINDING|SEGMENT|ANCHOR|BECAUSE|KILL|ASSUMPTION|MODE|DRUG|COMPOSITION|INDICATION|DOSE|CAUTION|INTERACTS|STOCK|EQUIV|EVIDENCE)\b[^\n\]]*$/gim, '');

    // Third pass — orphan-bracket / truncated-tag scrub (parity with the main
    // chat AnswerCard `scrubTags`). Removes a `[TAG: …` fragment that lost its
    // closing `]` mid-stream, an orphaned `[TAG:` at end-of-line, and lone
    // stray `[`/`]` brackets. UPPERCASE `[TAG:`-shaped only, so markdown links
    // `[label](url)` and ordinary prose brackets survive.
    cleaned = cleaned
      // unclosed tag at end-of-buffer (stream cut mid-tag)
      .replace(/\[[A-Z][A-Z0-9_]*:[^\]\n]*$/g, '')
      // orphaned opening `[TAG:` with no content/close on the rest of the line
      .replace(/\[[A-Z][A-Z0-9_]*:\s*$/gm, '')
      // a lone `]` or `[` on its own line
      .replace(/^[ \t]*[[\]][ \t]*$/gm, '')
      // stray leading `]` / trailing `[`/`]` clinging to the whole body
      .replace(/^\s*\]+/, '')
      .replace(/[[\]]+\s*$/, '')
      .replace(/\n{3,}/g, '\n\n');

    // Escape HTML first
    var esc = escapeHtml(cleaned);

    // Code spans first (so we don't mangle inside)
    esc = esc.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold + italic
    esc = esc.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    esc = esc.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<i>$2</i>');

    // Links [label](url)
    esc = esc.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(_, label, url) {
      var safeUrl = url.replace(/"/g, '%22');
      if (!/^https?:\/\//i.test(safeUrl) && !/^mailto:/i.test(safeUrl)) safeUrl = '#';
      return '<a href="' + safeUrl + '" target="_blank" rel="noopener">' + label + '</a>';
    });

    // Split into paragraphs by \n\n, then handle lists inside
    var paragraphs = esc.split(/\n\n+/);
    var html = paragraphs.map(function(p) {
      var lines = p.split(/\n/);
      var out = [];
      var listBuf = [];
      var listTag = 'ul';
      var flushList = function() {
        if (listBuf.length) {
          out.push('<' + listTag + '>' + listBuf.map(function(it) { return '<li>' + it + '</li>'; }).join('') + '</' + listTag + '>');
          listBuf = [];
        }
      };
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        var hm = line.match(/^\s*(#{1,3})\s+(.+)$/);
        var bm = line.match(/^\s*[-*•]\s+(.+)$/);
        var om = line.match(/^\s*\d+[.)]\s+(.+)$/);
        if (hm) {
          flushList();
          var lvl = hm[1].length + 2; // ## -> h4
          out.push('<h' + lvl + ' class="md-h">' + hm[2] + '</h' + lvl + '>');
        } else if (bm) {
          if (listTag !== 'ul') flushList();
          listTag = 'ul'; listBuf.push(bm[1]);
        } else if (om) {
          if (listTag !== 'ol') flushList();
          listTag = 'ol'; listBuf.push(om[1]);
        } else {
          flushList();
          if (line.trim()) out.push(line);
        }
      }
      flushList();
      // If output is just text lines, wrap as <p>
      var joined = out.join('<br>');
      if (joined && joined.indexOf('<ul>') === -1 && joined.indexOf('<ol>') === -1 &&
          joined.indexOf('<h4') === -1 && joined.indexOf('<h5') === -1 &&
          joined.indexOf('<pre>') === -1) {
        return '<p>' + joined + '</p>';
      }
      return joined;
    }).join('');

    return html;
  }

  // ── Clinical monograph (parity with main-chat AnswerCard) ──
  function _tag1(s, tag) {
    var m = s.match(new RegExp('\\[' + tag + ':\\s*([^\\]]+)\\]'));
    return m ? m[1].trim() : '';
  }
  function _tagMany(s, tag) {
    var out = [], re = new RegExp('\\[' + tag + ':\\s*([^\\]]+)\\]', 'g'), m;
    while ((m = re.exec(s)) !== null) { if (m[1].trim()) out.push(m[1].trim()); }
    return out;
  }
  function _parts(raw, n) {
    var p = String(raw).split('|').map(function(x){ return x.trim(); });
    while (p.length < n) p.push('');
    return p;
  }
  function renderMonograph(s) {
    var dm = s.match(/\[DRUG:\s*([^\]]+)\]/);
    if (!dm) return null;
    var d = _parts(dm[1], 5); // salt|brand|status|class|article
    var salt = d[0]; if (!salt) return null;
    var brand = d[1], status = d[2], klass = d[3], article = d[4];
    var e = escapeHtml;
    var h = '<div style="border-left:3px solid #c96342;padding-left:12px;margin:2px 0;">';
    // head
    h += '<div style="display:flex;align-items:flex-start;gap:8px;">';
    h += '<span style="font-size:18px;">🧪</span><div style="flex:1;min-width:0;">';
    h += '<div style="font-size:16px;font-weight:700;text-transform:uppercase;color:#1f1a14;">' + e(salt) + '</div>';
    if (brand || article) {
      h += '<div style="font-size:11px;color:#7a6f60;">' + e(brand);
      if (brand && article) h += ' · ';
      if (article) h += '<span style="font-family:monospace;">' + e(article) + '</span>';
      h += '</div>';
    }
    h += '</div>';
    var st = [status, klass].filter(Boolean).join(' · ');
    if (st) h += '<span style="font-size:9px;font-weight:700;text-transform:uppercase;color:#c96342;background:rgba(201,99,66,0.1);border-radius:999px;padding:3px 9px;white-space:nowrap;">' + e(st) + '</span>';
    h += '</div>';
    // fields
    function row(k, v) { return v ? '<div style="display:grid;grid-template-columns:104px 1fr;gap:10px;padding:6px 0;border-bottom:1px solid #f1e6d2;font-size:13px;"><span style="font-size:9px;font-weight:700;letter-spacing:0.08em;color:#7a6f60;">' + k + '</span><span style="color:#1f1a14;">' + e(v) + '</span></div>' : ''; }
    var f = row('COMPOSITION', _tag1(s, 'COMPOSITION')) + row('INDICATION', _tag1(s, 'INDICATION')) + row('DOSE', _tag1(s, 'DOSE'));
    if (f) h += '<div style="margin-top:10px;">' + f + '</div>';
    // safety
    var caut = _tagMany(s, 'CAUTION'), inter = _tagMany(s, 'INTERACTS');
    if (caut.length || inter.length) {
      h += '<div style="margin-top:10px;background:rgba(192,57,43,0.07);border:1px solid rgba(192,57,43,0.25);border-left:3px solid #c0392b;border-radius:4px;padding:8px 10px;">';
      caut.forEach(function(c){ h += '<div style="font-size:13px;margin:2px 0;"><span style="font-size:9px;font-weight:700;color:#c0392b;">⚠ CAUTION</span>&nbsp; ' + e(c) + '</div>'; });
      inter.forEach(function(it){ h += '<div style="font-size:13px;margin:2px 0;"><span style="font-size:9px;font-weight:700;color:#c0392b;">⚠ INTERACTS</span>&nbsp; ' + e(it) + '</div>'; });
      h += '</div>';
    }
    // stock
    var sm = s.match(/\[STOCK:\s*([^\]]+)\]/);
    if (sm) {
      var sp = _parts(sm[1], 5); // qty|skus|branch|cost|status
      h += '<div style="margin-top:10px;background:#f6ecda;border-radius:4px;padding:9px 12px;">';
      h += '<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;color:#7a6f60;">DISPENSING' + (sp[2] ? ' · branch ' + e(sp[2]) : '') + '</div>';
      h += '<div style="display:flex;justify-content:space-between;gap:10px;margin-top:3px;flex-wrap:wrap;"><span style="font-size:15px;font-weight:600;">' + e(sp[4] || '✅') + ' ' + e(sp[0]) + (sp[1] ? ' · ' + e(sp[1]) + ' SKUs' : '') + '</span>';
      if (sp[3]) h += '<span style="font-size:12px;color:#7a6f60;">COST ' + e(sp[3]) + '</span>';
      h += '</div></div>';
    }
    // equivalents
    var eq = _tagMany(s, 'EQUIV');
    if (eq.length) {
      h += '<div style="margin-top:10px;"><div style="font-size:9px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:#7a6f60;margin-bottom:4px;">🔄 Therapeutic equivalents</div>';
      eq.forEach(function(raw){
        var ep = _parts(raw, 4); // name|qty|cost|article
        var meta = [ep[1] ? ep[1] + ' u' : '', ep[2], ep[3]].filter(Boolean).join(' · ');
        h += '<div style="display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-bottom:1px solid #f1e6d2;font-size:13px;"><span style="font-weight:600;">' + e(ep[0]) + '</span><span style="color:#7a6f60;">' + e(meta) + '</span></div>';
      });
      h += '</div>';
    }
    // evidence
    var ev = s.match(/\[EVIDENCE:\s*([^\]]+)\]/);
    if (ev) {
      var evp = _parts(ev[1], 2);
      h += '<div style="margin-top:12px;font-size:11px;font-family:monospace;color:#7a6f60;display:flex;align-items:center;gap:4px;flex-wrap:wrap;">🔗 evidence&nbsp; ' + e(evp[0]) + (evp[1] ? ' · ' + e(evp[1]) : '') + '<span style="margin-left:auto;color:#16a34a;font-weight:700;">✓ verified</span></div>';
    }
    h += '</div>';
    // prose after tags
    var prose = s.replace(/\[(DRUG|COMPOSITION|INDICATION|DOSE|CAUTION|INTERACTS|STOCK|EQUIV|EVIDENCE):\s*[^\]]+\]/g, '').trim();
    if (prose.length > 30) h += '<div style="margin-top:10px;">' + renderMd(prose) + '</div>';
    return h;
  }
  // ── Universal "chemist-grade" band + analytical metrics body (parity) ──
  function _confDot(level) {
    var L = String(level || '').toUpperCase();
    if (L === 'HIGH') return { g: '●', c: '#2e7d32', bg: 'rgba(46,125,50,0.10)', t: 'HIGH' };
    if (L === 'MEDIUM') return { g: '◐', c: '#b9770e', bg: 'rgba(185,119,14,0.10)', t: 'MEDIUM' };
    if (L === 'LOW') return { g: '○', c: '#8a8a8a', bg: 'rgba(138,138,138,0.12)', t: 'LOW' };
    return null;
  }
  // Returns a band (title + confidence dot + source chip) for any tagged answer.
  function _renderBand(s, title) {
    var e = escapeHtml;
    var conf = _confDot(_tag1(s, 'CONFIDENCE'));
    var src = _tag1(s, 'SOURCE') || 'articles';
    var h = '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding-bottom:8px;margin-bottom:10px;border-bottom:1px solid rgba(0,0,0,0.08);">';
    h += '<div style="display:flex;align-items:center;gap:7px;min-width:0;"><span style="color:#9a4a2f;font-size:14px;">⬡</span>';
    h += '<span style="font-size:15px;font-weight:700;color:#1f1a14;overflow-wrap:anywhere;">' + e(title || 'Answer') + '</span></div>';
    h += '<div style="display:flex;align-items:center;gap:6px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end;">';
    if (conf) h += '<span style="font-size:10px;font-weight:600;padding:2px 8px;border-radius:999px;white-space:nowrap;color:' + conf.c + ';background:' + conf.bg + ';">' + conf.g + ' ' + conf.t + '</span>';
    h += '<span style="font-size:10px;font-weight:600;color:#9a4a2f;background:#f3ece1;padding:2px 8px;border-radius:999px;white-space:nowrap;">⌖ ' + e(src) + '</span>';
    h += '</div></div>';
    return h;
  }
  // Analytical card: band + lead + KPI tiles + Why + So-what. Null if no signal.
  function renderMetrics(s) {
    var e = escapeHtml;
    var headline = _tag1(s, 'HEADLINE');
    var lead = _tag1(s, 'LEAD');
    var kpis = _tagMany(s, 'KPI');
    var whys = _tagMany(s, 'WHY');
    var soWhatRaw = _tag1(s, 'SO_WHAT');
    // Need at least one structured signal to render the card.
    if (!headline && !lead && !kpis.length && !whys.length && !soWhatRaw) return null;

    var h = '<div style="border-left:3px solid #c96342;padding-left:12px;margin:2px 0;">';
    h += _renderBand(s, headline);
    if (lead) h += '<p style="display:flex;gap:7px;align-items:baseline;font-size:14px;font-weight:600;line-height:1.5;margin:0 0 10px;"><span style="color:#9a4a2f;">➜</span><span>' + e(lead) + '</span></p>';
    if (kpis.length) {
      h += '<div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0;">';
      kpis.forEach(function(raw){
        var kp = _parts(raw, 3); // value|label|change
        h += '<div style="flex:1;min-width:90px;background:#f6ecda;border-radius:5px;padding:8px 10px;">';
        h += '<div style="font-size:17px;font-weight:700;color:#1f1a14;">' + e(kp[0]) + '</div>';
        h += '<div style="font-size:11px;color:#7a6f60;">' + e(kp[1]) + '</div>';
        if (kp[2]) h += '<div style="font-size:11px;color:#2e7d32;font-weight:600;">' + e(kp[2]) + '</div>';
        h += '</div>';
      });
      h += '</div>';
    }
    if (whys.length) {
      h += '<div style="margin:10px 0 4px;"><div style="font-size:10px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#8a7a66;margin-bottom:3px;">Why</div><ul style="margin:0;padding-left:18px;">';
      whys.forEach(function(w){ h += '<li style="font-size:13px;line-height:1.5;margin:2px 0;">' + e(w) + '</li>'; });
      h += '</ul></div>';
    }
    if (soWhatRaw) {
      var sw = _parts(soWhatRaw, 3); // action|owner|effort
      var meta = [sw[1], sw[2]].filter(Boolean).join(' · ');
      h += '<div style="margin:10px 0 4px;padding:8px 12px;background:rgba(154,74,47,0.05);border-left:2px solid #9a4a2f;border-radius:3px;font-size:13px;">';
      h += '<span style="font-weight:700;color:#9a4a2f;">So what →</span> <span style="font-weight:600;">' + e(sw[0]) + '</span>';
      if (meta) h += ' <span style="color:#8a7a66;font-size:12px;">(' + e(meta) + ')</span>';
      h += '</div>';
    }
    h += '</div>';
    // Prose after stripping all known tags.
    var prose = s.replace(/\[(HEADLINE|LEAD|CONFIDENCE|SOURCE|KPI|WHY|SO_WHAT|RELATED):\s*[^\]]+\]/g, '').trim();
    if (prose.length > 30) h += '<div style="margin-top:10px;">' + renderMd(prose) + '</div>';
    return h;
  }
  // Render assistant answer: monograph if [DRUG:], else metrics card if
  // analytical tags present, else plain markdown (chitchat).
  function renderAnswer(s) {
    if (!s) return '';
    var str = String(s);
    var mono = renderMonograph(str);
    if (mono != null) return mono;
    var metrics = renderMetrics(str);
    if (metrics != null) return metrics;
    var md = renderMd(str);
    // Blank-body guard: no monograph, no metrics card, and the markdown came
    // back empty (everything was tags / a truncated tag got scrubbed away).
    // Return a small fallback rather than an empty bubble.
    if (!md || !md.replace(/<[^>]*>/g, '').trim()) {
      return '<p><i>Sorry, I didn’t catch that — could you rephrase?</i></p>';
    }
    return md;
  }

  function pushMsg(role, content) {
    if (role === 'system') {
      var sysDiv = document.createElement('div');
      sysDiv.className = 'msg system';
      sysDiv.textContent = content;
      msgList.appendChild(sysDiv);
    } else {
      var row = document.createElement('div');
      row.className = 'msg-row ' + role;

      if (role === 'bot') {
        var av = document.createElement('div');
        av.className = 'msg-avatar';
        av.innerHTML = avatarContent();
        row.appendChild(av);
      }

      var div = document.createElement('div');
      div.className = 'msg ' + role;
      div.innerHTML = renderAnswer(content);
      row.appendChild(div);
      msgList.appendChild(row);
    }
    msgList.scrollTop = msgList.scrollHeight;
    messages.push({ role: role, content: content });
  }

  function showGreeting() {
    var g = document.createElement('div');
    g.className = 'greeting';
    g.textContent = greeting;
    msgList.appendChild(g);
  }
  showGreeting();

  // ── Typing indicator ────────────────────────────────────────────────
  var typingRow = null;
  function showTyping() {
    if (typingRow) return;
    typingRow = document.createElement('div');
    typingRow.className = 'typing-row';
    var av = document.createElement('div');
    av.className = 'msg-avatar';
    av.innerHTML = avatarContent();
    typingRow.appendChild(av);
    var t = document.createElement('div');
    t.className = 'typing';
    t.innerHTML = '<span></span><span></span><span></span>';
    typingRow.appendChild(t);
    msgList.appendChild(typingRow);
    msgList.scrollTop = msgList.scrollHeight;
  }
  function hideTyping() {
    if (typingRow && typingRow.parentNode) typingRow.parentNode.removeChild(typingRow);
    typingRow = null;
  }

  // ── Suggested questions chips ───────────────────────────────────────
  function loadSuggestions() {
    fetch(apiOrigin + '/api/embed/config/' + encodeURIComponent(embedId) + '/suggestions')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) {
        if (!d || !d.questions || !d.questions.length) return;
        renderChips(d.questions.slice(0, 3));
      })
      .catch(function() { /* silent */ });
  }

  function renderChips(questions) {
    var existing = msgList.querySelector('.chips');
    if (existing) existing.parentNode.removeChild(existing);
    var wrap = document.createElement('div');
    wrap.className = 'chips';
    questions.forEach(function(q) {
      var btn = document.createElement('button');
      btn.className = 'chip';
      btn.type = 'button';
      btn.textContent = q;
      btn.addEventListener('click', function() {
        inputEl.value = q;
        wrap.parentNode && wrap.parentNode.removeChild(wrap);
        submit();
      });
      wrap.appendChild(btn);
    });
    msgList.appendChild(wrap);
    msgList.scrollTop = msgList.scrollHeight;
  }

  // ── Open / close ─────────────────────────────────────────────────────
  bubble.addEventListener('click', function () {
    panel.classList.add('open');
    bubble.style.display = 'none';
    setTimeout(function () { inputEl.focus(); }, 50);
  });
  closeBtn.addEventListener('click', function () {
    panel.classList.remove('open');
    bubble.style.display = 'flex';
  });

  // ── Auto-grow textarea (1–4 rows) ───────────────────────────────────
  function autoGrow() {
    inputEl.style.height = 'auto';
    var lineH = 20; // approx
    var maxH = lineH * 4 + 20;
    inputEl.style.height = Math.min(maxH, inputEl.scrollHeight) + 'px';
  }
  inputEl.addEventListener('input', autoGrow);

  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });
  sendBtn.addEventListener('click', submit);

  // ── Session bootstrap ────────────────────────────────────────────────
  function ensureSession() {
    if (sessionToken && Date.now() < sessionExpiresAt - 5000) {
      return Promise.resolve(sessionToken);
    }
    var body = { embed_id: embedId, public_key: pubKey };
    if (userPayload) body.user = userPayload;
    if (userSig)     body.signature = userSig;

    return fetch(apiOrigin + '/api/embed/session/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'omit',
    }).then(function (r) {
      if (!r.ok) return r.text().then(function (text) {
        var msg = 'session error';
        try { msg = JSON.parse(text).detail || msg; } catch (_) {}
        throw new Error(msg + ' (HTTP ' + r.status + ')');
      });
      return r.json();
    }).then(function (d) {
      sessionToken = d.session_token;
      sessionExpiresAt = Date.now() + (d.expires_in || 900) * 1000;
      return sessionToken;
    });
  }

  // ── Chat ─────────────────────────────────────────────────────────────
  // Stream a chat response via SSE. On success, calls onDelta for each
  // token, onDone with final payload, onError on any error. Returns
  // promise rejected with status code so caller can fall back to
  // non-streaming /chat on 4xx (e.g. consumer-mode 400).
  function streamChat(token, msg, onDelta, onDone, onError, onStep) {
    return fetch(apiOrigin + '/api/embed/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ embed_id: embedId, session_token: token, message: msg }),
      credentials: 'omit',
    }).then(function (r) {
      if (!r.ok) {
        // Reject with status so caller can route to fallback.
        var err = new Error('stream http ' + r.status);
        err.status = r.status;
        throw err;
      }
      if (!r.body || !r.body.getReader) {
        var err2 = new Error('streams unsupported');
        err2.status = 0;
        throw err2;
      }
      var reader = r.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      function pump() {
        return reader.read().then(function (res) {
          if (res.done) {
            // Flush any remaining bytes.
            if (buffer.trim()) parseFrames(buffer);
            return;
          }
          buffer += decoder.decode(res.value, { stream: true });
          // SSE frames separated by blank line.
          var parts = buffer.split('\n\n');
          buffer = parts.pop();
          for (var i = 0; i < parts.length; i++) parseFrames(parts[i]);
          return pump();
        });
      }
      function parseFrames(frame) {
        var lines = frame.split('\n');
        var type = 'message';
        var data = '';
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i];
          if (line.indexOf(':') === 0) continue; // SSE comment / heartbeat
          if (line.indexOf('event:') === 0) type = line.slice(6).trim();
          else if (line.indexOf('data:') === 0) data += line.slice(5).trim();
        }
        if (!data) return;
        var parsed;
        try { parsed = JSON.parse(data); } catch (_) { return; }
        if (type === 'token' && typeof parsed.delta === 'string') onDelta(parsed.delta);
        else if (type === 'step') { if (onStep) onStep(parsed); }
        else if (type === 'done') onDone(parsed);
        else if (type === 'error') onError(parsed);
        // meta event ignored for UX (could be used for trace UI later)
      }
      return pump();
    });
  }

  function submit() {
    if (sending) return;
    var msg = inputEl.value.trim();
    if (!msg) return;
    inputEl.value = '';
    autoGrow();
    pushMsg('user', msg);
    sendBtn.disabled = true;
    sending = true;
    showTyping();

    function finish() {
      sending = false;
      sendBtn.disabled = false;
      hideTyping();
      inputEl.focus();
    }

    function nonStreamPath(tokenStr) {
      return fetch(apiOrigin + '/api/embed/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_token: tokenStr, message: msg }),
        credentials: 'omit',
      })
        .then(function (r) {
          if (r.status === 429) throw new Error('Too many messages — please wait a moment.');
          if (r.status === 401) {
            sessionToken = null;
            throw new Error('Session expired. Please reload the page.');
          }
          if (r.status === 403) return r.text().then(function (text) {
            var d = ''; try { d = JSON.parse(text).detail || text; } catch (_) { d = text; }
            if (/disabled/i.test(d)) {
              disableWidget();
              throw new Error('This agent is currently unavailable.');
            }
            throw new Error(d || 'Forbidden');
          });
          if (!r.ok) return r.text().then(function (text) {
            var d = ''; try { d = JSON.parse(text).detail || text; } catch (_) { d = text; }
            throw new Error(d || 'Error');
          });
          return r.json();
        })
        .then(function (d) {
          pushMsg('bot', d.content || '(empty response)');
          if (!firstReplyReceived) {
            firstReplyReceived = true;
            loadSuggestions();
          }
        });
    }

    ensureSession()
      .then(function (tokenStr) {
        if (!streamingEnabled) return nonStreamPath(tokenStr);

        // Streaming path: live activity strip + bot bubble w/ live deltas.
        hideTyping();
        var strip = document.createElement('div');
        strip.className = 'agent-steps';
        strip.innerHTML = '<div class="agent-steps-head"><span class="spin"></span>thinking…</div>';
        msgList.appendChild(strip);

        var stepStart = Date.now();
        var stepCount = 0;
        var pendingLine = null;   // the step currently "in progress" (spinner)
        // Mark the in-progress step complete (✓) before the next one starts.
        function tickPending() {
          if (pendingLine) {
            var ic = pendingLine.querySelector('.ic');
            if (ic) { ic.textContent = '✓'; ic.classList.add('ok'); }
            pendingLine.classList.add('agent-step-done');
            pendingLine = null;
          }
        }
        function addStep(p) {
          tickPending();
          stepCount++;
          var line = document.createElement('div');
          line.className = 'agent-step';
          line.innerHTML = '<span class="ic"><span class="step-spin"></span></span>' +
                           '<span class="step-tx">' + escapeHtml(p.label || '') + '</span>';
          strip.appendChild(line);
          pendingLine = line;
          msgList.scrollTop = msgList.scrollHeight;
        }
        // Seed an immediate step so the strip is never a blank "thinking…".
        addStep({ label: 'understanding your question' });
        function collapseStrip() {
          if (strip.classList.contains('done')) return;
          tickPending();
          var secs = ((Date.now() - stepStart) / 1000).toFixed(1);
          strip.classList.add('done');
          if (stepCount === 0) { strip.remove(); return; }
          strip.querySelector('.agent-steps-head').innerHTML =
            '✓ done · ' + secs + 's · ' + stepCount + ' step' + (stepCount === 1 ? '' : 's');
          strip.addEventListener('click', function () { strip.classList.toggle('done'); });
        }

        var row = document.createElement('div');
        row.className = 'msg-row bot';
        var av = document.createElement('div');
        av.className = 'msg-avatar';
        av.innerHTML = avatarContent();
        row.appendChild(av);
        var div = document.createElement('div');
        div.className = 'msg bot';
        div.innerHTML = '<span class="load-dots"><i></i><i></i><i></i></span>';
        row.appendChild(div);
        msgList.appendChild(row);
        msgList.scrollTop = msgList.scrollHeight;

        var assembled = '';
        var streamErrored = false;
        var writingShown = false;
        return streamChat(
          tokenStr, msg,
          function onDelta(delta) {
            if (!writingShown) { writingShown = true; addStep({ label: 'writing answer' }); }
            assembled += delta;
            div.innerHTML = renderMd(assembled) + '<span class="load-dots"><i></i><i></i><i></i></span>';
            msgList.scrollTop = msgList.scrollHeight;
          },
          function onDone(_payload) {
            collapseStrip();
            div.innerHTML = renderAnswer(assembled || '(empty response)');
            messages.push({ role: 'bot', content: assembled });
            if (!firstReplyReceived) {
              firstReplyReceived = true;
              loadSuggestions();
            }
          },
          function onError(err) {
            streamErrored = true;
            collapseStrip();
            div.innerHTML = renderMd('(stream error: ' + (err.detail || err.code || 'unknown') + ')');
          },
          addStep
        ).catch(function (e) {
          // 400 = consumer-mode; 404 = older Dash w/o /chat/stream. Fall back.
          if (e && (e.status === 400 || e.status === 404 || e.status === 405)) {
            // Remove the empty streaming bubble + strip; retry via non-stream.
            try { row.remove(); } catch (_) {}
            try { strip.remove(); } catch (_) {}
            showTyping();
            return nonStreamPath(tokenStr);
          }
          if (!streamErrored) {
            div.innerHTML = renderMd('(error: ' + (e.message || 'stream failed') + ')');
          }
          throw e;
        });
      })
      .catch(function (e) {
        pushMsg('system', e.message || 'Something went wrong.');
      })
      .then(finish, finish);
  }

  function disableWidget() {
    sendBtn.disabled = true;
    inputEl.disabled = true;
    inputEl.placeholder = 'Agent unavailable';
    bubble.style.opacity = '0.4';
    bubble.style.cursor = 'not-allowed';
  }

  window.DashAgent = window.DashAgent || {
    open:  function () { bubble.click(); },
    close: function () { closeBtn.click(); },
    send:  function (m) { inputEl.value = m; submit(); },
    config: { embedId: embedId, apiOrigin: apiOrigin, theme: theme },
  };
  } // end buildWidget
})();
