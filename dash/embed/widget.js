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
    .msg ul { margin: 4px 0; padding-left: 20px; }
    .msg li { margin: 2px 0; }
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
    .stream-cursor {
      display: inline-block;
      width: 0.5em;
      color: ${t.accent};
      animation: stream-blink 1s infinite step-start;
      margin-left: 1px;
    }
    @keyframes stream-blink {
      50% { opacity: 0; }
    }

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
    // Strip leftover tag patterns like [TAG: ...] (defense in depth)
    var cleaned = String(s).replace(/\[[A-Z_]+:[^\]]*\]/g, '');

    // Escape HTML first
    var esc = escapeHtml(cleaned);

    // Code spans first (so we don't mangle inside)
    esc = esc.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold
    esc = esc.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');

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
      var flushList = function() {
        if (listBuf.length) {
          out.push('<ul>' + listBuf.map(function(it) { return '<li>' + it + '</li>'; }).join('') + '</ul>');
          listBuf = [];
        }
      };
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        var m = line.match(/^\s*-\s+(.+)$/);
        if (m) {
          listBuf.push(m[1]);
        } else {
          flushList();
          if (line.trim()) out.push(line);
        }
      }
      flushList();
      // If output is just text lines, wrap as <p>
      var joined = out.join('<br>');
      if (joined && joined.indexOf('<ul>') === -1 && joined.indexOf('<pre>') === -1) {
        return '<p>' + joined + '</p>';
      }
      return joined;
    }).join('');

    return html;
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
      div.innerHTML = renderMd(content);
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
  function streamChat(token, msg, onDelta, onDone, onError) {
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

        // Streaming path: build an empty bot bubble + append deltas live.
        var row = document.createElement('div');
        row.className = 'msg-row bot';
        var av = document.createElement('div');
        av.className = 'msg-avatar';
        av.innerHTML = avatarContent();
        row.appendChild(av);
        var div = document.createElement('div');
        div.className = 'msg bot';
        div.innerHTML = '<span class="stream-cursor">▍</span>';
        row.appendChild(div);
        msgList.appendChild(row);
        msgList.scrollTop = msgList.scrollHeight;
        hideTyping();

        var assembled = '';
        var streamErrored = false;
        return streamChat(
          tokenStr, msg,
          function onDelta(delta) {
            assembled += delta;
            div.innerHTML = renderMd(assembled) + '<span class="stream-cursor">▍</span>';
            msgList.scrollTop = msgList.scrollHeight;
          },
          function onDone(_payload) {
            div.innerHTML = renderMd(assembled || '(empty response)');
            messages.push({ role: 'bot', content: assembled });
            if (!firstReplyReceived) {
              firstReplyReceived = true;
              loadSuggestions();
            }
          },
          function onError(err) {
            streamErrored = true;
            div.innerHTML = renderMd('(stream error: ' + (err.detail || err.code || 'unknown') + ')');
          }
        ).catch(function (e) {
          // 400 = consumer-mode; 404 = older Dash w/o /chat/stream. Fall back.
          if (e && (e.status === 400 || e.status === 404 || e.status === 405)) {
            // Remove the empty streaming bubble + retry via non-stream.
            try { row.remove(); } catch (_) {}
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
