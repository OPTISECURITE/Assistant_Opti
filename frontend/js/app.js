/*
 * Assistant Opti — front (étape 3)
 * Connexion, conversations enregistrées côté serveur (historique, renommer,
 * épingler, télécharger, supprimer, recherche), lien direct /c/<id>.
 */
(() => {
  'use strict';

  const root = document.getElementById('opti-design');
  const $ = (s) => root.querySelector(s);
  const $$ = (s) => [...root.querySelectorAll(s)];

  // ── État ──────────────────────────────────────────────────────────────────
  const state = {
    config: { model: '', model_label: 'Opti · Qwen 3' },
    me: { display_name: 'Invité' },
    chats: [],          // résumés : { id, title, pinned, updated_at }
    active: null,       // conversation ouverte : { id, title, pinned, messages: [...] }
    controller: null,   // génération en cours
    menuChat: null,     // conversation visée par le menu « … »
    menuAnchor: null,
  };

  // ── Utilitaires ───────────────────────────────────────────────────────────
  const icons = () => window.lucide?.createIcons({ attrs: { width: 18, height: 18 } });
  const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  marked.setOptions({ gfm: true, breaks: true });
  const renderMarkdown = (text) => DOMPurify.sanitize(marked.parse(text || ''));

  async function api(path, options = {}) {
    const resp = await fetch(path, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    if (resp.status === 401) showLogin();
    if (!resp.ok) {
      const err = new Error(`HTTP ${resp.status}`);
      err.status = resp.status;
      throw err;
    }
    return resp.status === 204 ? null : resp.json();
  }

  let toastTimer = null;
  function toast(message) {
    const el = $('.op-toast');
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), 3500);
  }

  // ── Thème : Système / Clair / Sombre ──────────────────────────────────────
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const getTheme = () => { try { return localStorage.getItem('op-theme') || 'system'; } catch { return 'system'; } };
  function applyTheme() {
    const pref = getTheme();
    const dark = pref === 'dark' || (pref === 'system' && media.matches);
    root.style.colorScheme = dark ? 'dark' : 'light';
    root.dataset.theme = dark ? 'Sombre' : 'Clair';
    root.style.setProperty('--op-blue', dark ? '#72bedf' : '#1070aa');
    root.style.setProperty('--op-blue-hover', dark ? '#a0d7ef' : '#095b8d');
    root.style.setProperty('--op-on-blue', dark ? '#102b3d' : '#fff');
    document.body.style.background = dark ? '#10202e' : '#f5f8fa';
  }
  media.addEventListener('change', applyTheme);

  // ── Modales ───────────────────────────────────────────────────────────────
  let lastFocus = null;
  function modal(title, bodyHtml) {
    closeMenu();
    lastFocus = document.activeElement;
    $('#op-modal-title').textContent = title;
    $('.op-modal-body').innerHTML = bodyHtml;
    $('.op-modal-backdrop').hidden = false;
    icons();
    ($('.op-modal-body').querySelector('input,select,textarea,button') || $('[data-action="close-modal"]')).focus();
  }
  function closeModal() {
    if ($('.op-modal-backdrop').hidden) return;
    $('.op-modal-backdrop').hidden = true;
    lastFocus?.focus();
  }

  // ── Navigation (URL /c/<id>) ──────────────────────────────────────────────
  function setUrl(chatId, replace = false) {
    const url = chatId ? `/c/${chatId}` : '/';
    if (location.pathname !== url) history[replace ? 'replaceState' : 'pushState']({}, '', url);
  }

  async function route() {
    const m = location.pathname.match(/^\/c\/([\w-]+)$/);
    if (m) await openChat(m[1], { push: false });
    else showHome({ push: false });
  }
  window.addEventListener('popstate', () => { if (!state.controller) route(); });

  // ── Vues ──────────────────────────────────────────────────────────────────
  function showHome({ push = true } = {}) {
    if (state.controller) stopGeneration();
    state.active = null;
    $('#op-home').hidden = false;
    $('#op-conversation').hidden = true;
    $('.op-main').classList.remove('op-chat-active');
    $('#op-header-title').textContent = 'Assistant Opti';
    $('.op-header-thread').hidden = true;
    $('#op-thread').replaceChildren();
    $('.op-app').classList.remove('op-nav-open');
    if (push) setUrl(null);
    renderRecents();
    $('#op-question').focus();
  }

  function showConversation() {
    const chat = state.active;
    $('#op-home').hidden = true;
    $('#op-conversation').hidden = false;
    $('.op-main').classList.add('op-chat-active');
    $('#op-header-title').textContent = chat.title;
    $('.op-header-thread').hidden = false;
    $('.op-app').classList.remove('op-nav-open');
    renderThread();
    renderRecents();
  }

  async function openChat(id, { push = true } = {}) {
    if (state.controller) return toast('Une réponse est en cours de génération.');
    try {
      state.active = await api(`/api/chats/${id}`);
    } catch (e) {
      toast(e.status === 404 ? 'Cette conversation n’existe plus.' : 'Impossible d’ouvrir la conversation.');
      return showHome();
    }
    if (push) setUrl(id);
    showConversation();
    scrollToBottom();
  }

  // ── Fil de discussion ─────────────────────────────────────────────────────
  function answerBlock(content, { streaming = false, interrupted = false } = {}) {
    const wrap = document.createElement('div');
    wrap.className = 'op-answer' + (streaming ? ' is-streaming' : '');
    wrap.innerHTML = `
      <img src="/static/img/mark.png" alt="">
      <div class="op-answer-body">
        <div class="op-answer-name">Assistant Opti <span>${esc(state.config.model_label)}</span></div>
        <div class="op-answer-content"></div>
        ${interrupted ? '<div class="op-answer-interrupted">Réponse interrompue</div>' : ''}
        <div class="op-answer-actions" aria-label="Actions sur la réponse">
          <button type="button" class="op-icon" aria-label="Copier la réponse" data-action="copy-answer"><i data-lucide="copy"></i></button>
          <button type="button" class="op-icon" aria-label="Régénérer la réponse" data-action="regenerate"><i data-lucide="refresh-cw"></i></button>
        </div>
      </div>`;
    wrap.querySelector('.op-answer-content').innerHTML = renderMarkdown(content);
    return wrap;
  }

  function renderThread() {
    const thread = $('#op-thread');
    thread.replaceChildren();
    let exchange = null;
    for (const m of state.active?.messages || []) {
      if (m.role === 'user') {
        exchange = document.createElement('div');
        exchange.className = 'op-chat-exchange';
        const bubble = document.createElement('div');
        bubble.className = 'op-user-message';
        bubble.textContent = m.content;
        exchange.appendChild(bubble);
        thread.appendChild(exchange);
      } else if (exchange) {
        exchange.appendChild(answerBlock(m.content, { interrupted: !m.done }));
      }
    }
    // seule la dernière réponse peut être régénérée
    $$('#op-thread [data-action="regenerate"]').forEach((b, i, all) => (b.hidden = i !== all.length - 1));
    icons();
  }

  const scrollToBottom = () => { const c = $('#op-conversation'); c.scrollTop = c.scrollHeight; };

  // ── Historique dans la barre latérale ─────────────────────────────────────
  function periodOf(ts) {
    const d = new Date(ts), now = new Date();
    const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
    if (days <= 0) return 'Aujourd’hui';
    if (days === 1) return 'Hier';
    if (days < 7) return '7 derniers jours';
    if (days < 30) return '30 derniers jours';
    return 'Plus ancien';
  }

  function renderRecents() {
    const box = $('#op-recents');
    box.replaceChildren();
    if (!state.chats.length) {
      box.innerHTML = '<div class="op-empty-history">Aucune conversation pour le moment.</div>';
      return;
    }
    let current = null;
    for (const chat of state.chats) {
      const period = chat.pinned ? 'Épinglées' : periodOf(chat.updated_at);
      if (period !== current) {
        current = period;
        const p = document.createElement('div');
        p.className = 'op-period';
        p.textContent = period;
        box.appendChild(p);
      }
      const row = document.createElement('div');
      row.className = 'op-thread-row' + (state.active?.id === chat.id ? ' is-active' : '');
      row.dataset.chat = chat.id;
      row.innerHTML = `
        <button type="button" class="op-thread-open"><i data-lucide="message-square"></i><span class="op-thread-title"></span>${chat.pinned ? '<i class="op-thread-pin" data-lucide="pin"></i>' : ''}</button>
        <button type="button" class="op-icon" data-action="thread-menu" aria-expanded="false"><i data-lucide="ellipsis"></i></button>`;
      row.querySelector('.op-thread-title').textContent = chat.title;
      row.querySelector('[data-action="thread-menu"]').setAttribute('aria-label', `Actions : ${chat.title}`);
      row.querySelector('.op-thread-open').addEventListener('click', () => openChat(chat.id));
      box.appendChild(row);
    }
    icons();
  }

  async function refreshChats() {
    try { state.chats = await api('/api/chats'); } catch { /* on garde la liste actuelle */ }
    renderRecents();
  }

  // ── Menu « … » d'une conversation ─────────────────────────────────────────
  function openMenu(button) {
    const menu = $('.op-thread-menu');
    if (!menu.hidden && state.menuAnchor === button) return closeMenu(true);
    const id = button.closest('[data-chat]')?.dataset.chat || state.active?.id;
    state.menuChat = state.chats.find((c) => c.id === id) || state.active;
    if (!state.menuChat) return;
    state.menuAnchor = button;
    menu.querySelector('[data-action="pin"] span').textContent = state.menuChat.pinned ? 'Désépingler' : 'Épingler';
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    const r = button.getBoundingClientRect();
    const w = menu.offsetWidth, h = menu.offsetHeight;
    menu.style.left = Math.max(12, Math.min(r.right - 10, document.documentElement.clientWidth - w - 12)) + 'px';
    menu.style.top = Math.max(12, Math.min(r.bottom + 5, document.documentElement.clientHeight - h - 12)) + 'px';
    menu.querySelector('button').focus();
  }
  function closeMenu(restoreFocus = false) {
    const menu = $('.op-thread-menu');
    if (menu.hidden) return;
    menu.hidden = true;
    $$('[data-action="thread-menu"]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
    if (restoreFocus) state.menuAnchor?.focus();
  }

  async function updateChat(chat, changes) {
    const updated = await api(`/api/chats/${chat.id}`, { method: 'PATCH', body: JSON.stringify(changes) });
    if (state.active?.id === chat.id) {
      Object.assign(state.active, updated);
      $('#op-header-title').textContent = updated.title;
    }
    await refreshChats();
  }

  // ── Envoi et streaming ────────────────────────────────────────────────────
  function setBusy(busy) {
    $$('.op-send').forEach((btn) => {
      btn.classList.toggle('is-stop', busy);
      btn.setAttribute('aria-label', busy ? 'Arrêter la génération' : 'Envoyer le message');
      btn.innerHTML = `<i data-lucide="${busy ? 'square' : 'arrow-up'}"></i>`;
    });
    icons();
  }
  const stopGeneration = () => state.controller?.abort();

  async function send(text) {
    text = (text || '').trim();
    if (!text || state.controller) return;
    try {
      if (!state.active) {
        const chat = await api('/api/chats', { method: 'POST' });
        state.active = { ...chat, messages: [] };
        setUrl(chat.id);
      }
    } catch {
      return toast('Impossible de créer la conversation.');
    }
    state.active.messages.push({ role: 'user', content: text, done: true });
    showConversation();
    await generate(`/api/chats/${state.active.id}/messages`, { content: text });
  }

  async function regenerate() {
    const chat = state.active;
    if (!chat || state.controller) return;
    if (chat.messages.at(-1)?.role === 'assistant') chat.messages.pop();
    renderThread();
    await generate(`/api/chats/${chat.id}/regenerate`);
  }

  async function generate(url, body) {
    const chatId = state.active.id;
    const exchange = $('#op-thread').lastElementChild;
    const block = answerBlock('', { streaming: true });
    exchange.appendChild(block);
    icons();
    const contentEl = block.querySelector('.op-answer-content');
    scrollToBottom();

    const controller = new AbortController();
    state.controller = controller;
    setBusy(true);

    let full = '', pending = false, failed = false;
    const paint = () => { pending = false; contentEl.innerHTML = renderMarkdown(full); scrollToBottom(); };

    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      if (resp.status === 401) { showLogin(); throw new Error('401'); }
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        full += decoder.decode(value, { stream: true });
        if (!pending) { pending = true; requestAnimationFrame(paint); }
      }
      paint();
    } catch (err) {
      if (err.name !== 'AbortError') failed = true;
    } finally {
      state.controller = null;
      setBusy(false);
    }

    // On recharge la conversation depuis le serveur : c'est lui qui fait foi.
    if (state.active?.id === chatId) {
      if (!failed) await new Promise((r) => setTimeout(r, 150)); // laisse le serveur enregistrer une réponse interrompue
      try {
        state.active = await api(`/api/chats/${chatId}`);
        showConversation();
      } catch { /* affichage local conservé */ }
      if (failed) {
        const errBox = document.createElement('div');
        errBox.className = 'op-answer-error';
        errBox.textContent = 'Impossible de joindre l’assistant pour le moment. Réessayez dans un instant.';
        $('#op-thread').lastElementChild?.appendChild(errBox);
      }
      scrollToBottom();
      $('#op-chat-question').focus();
    }
    refreshChats();
  }

  // ── Téléchargement d'une conversation (Markdown) ──────────────────────────
  async function download(chatSummary) {
    const chat = state.active?.id === chatSummary.id ? state.active : await api(`/api/chats/${chatSummary.id}`);
    const lines = [`# ${chat.title}`, ''];
    for (const m of chat.messages) {
      lines.push(m.role === 'user' ? '## Vous' : '## Assistant Opti', '', m.content, '');
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = chat.title.replace(/[^\p{L}\p{N}_-]+/gu, '-').slice(0, 60) + '.md';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ── Actions (boutons data-action) ─────────────────────────────────────────
  const actions = {
    new: () => showHome(),
    profile: () => toggleProfile(),
    logout: async () => {
      toggleProfile(false);
      try { await fetch('/api/auth/logout', { method: 'POST' }); } catch {}
      showLogin();
    },
    password: (btn) => {
      const input = $('#op-password');
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.setAttribute('aria-label', show ? 'Masquer le mot de passe' : 'Afficher le mot de passe');
      btn.innerHTML = `<i data-lucide="${show ? 'eye-off' : 'eye'}"></i>`;
      icons();
    },
    'login-help': () => modal('Aide à la connexion',
      `<p>Votre compte est créé par le service informatique d’Opti Sécurité.</p>
       <p>Mot de passe oublié ou compte bloqué : contactez votre administrateur.</p>`),
    nav: () => $('.op-app').classList.toggle('op-nav-open'),
    'close-modal': closeModal,
    soon: () => toast('Fonctionnalité prévue dans une prochaine étape.'),
    'thread-menu': openMenu,
    regenerate,

    'copy-answer': async (btn) => {
      const text = btn.closest('.op-answer').querySelector('.op-answer-content').innerText;
      try { await navigator.clipboard.writeText(text); toast('Réponse copiée.'); }
      catch { toast('Copie impossible dans ce navigateur (HTTPS requis).'); }
    },

    rename: () => {
      const chat = state.menuChat;
      modal('Renommer la conversation',
        `<label for="op-chat-name">Titre</label><input id="op-chat-name" maxlength="200" autocomplete="off">
         <div class="op-modal-actions"><button type="button" class="op-secondary" data-action="close-modal">Annuler</button>
         <button type="button" class="op-primary" data-action="save-name">Enregistrer</button></div>`);
      const input = $('#op-chat-name');
      input.value = chat.title;
      input.select();
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') actions['save-name'](); });
    },
    'save-name': async () => {
      const title = $('#op-chat-name').value.trim();
      if (!title) return $('#op-chat-name').focus();
      try { await updateChat(state.menuChat, { title }); closeModal(); }
      catch { toast('Le renommage a échoué.'); }
    },

    pin: async () => {
      const chat = state.menuChat;
      const wasPinned = chat.pinned;
      closeMenu();
      try {
        await updateChat(chat, { pinned: !wasPinned });
        toast(wasPinned ? 'Conversation désépinglée.' : 'Conversation épinglée.');
      } catch { toast('Action impossible pour le moment.'); }
    },

    download: async () => {
      const chat = state.menuChat;
      closeMenu();
      try { await download(chat); } catch { toast('Téléchargement impossible.'); }
    },

    delete: () => {
      const chat = state.menuChat;
      modal('Supprimer la conversation ?',
        `<p>« ${esc(chat.title)} » sera définitivement supprimée.</p>
         <div class="op-modal-actions"><button type="button" class="op-secondary" data-action="close-modal">Annuler</button>
         <button type="button" class="op-primary op-danger" data-action="confirm-delete">Supprimer</button></div>`);
    },
    'confirm-delete': async () => {
      const chat = state.menuChat;
      try {
        await api(`/api/chats/${chat.id}`, { method: 'DELETE' });
        closeModal();
        if (state.active?.id === chat.id) showHome();
        await refreshChats();
        toast('Conversation supprimée.');
      } catch { toast('La suppression a échoué.'); }
    },

    model: () => modal('Votre modèle',
      `<div class="op-modal-row"><i data-lucide="cpu"></i><span>${esc(state.config.model_label)}
        <small style="display:block;overflow-wrap:anywhere">${esc(state.config.model)}</small></span><i data-lucide="check"></i></div>
       <p style="margin-top:18px">Modèle hébergé sur les serveurs d’Opti Sécurité. Aucune donnée ne sort de l’entreprise.</p>`),

    settings: () => {
      modal('Réglages',
        `<label for="op-theme">Apparence</label>
         <select id="op-theme"><option value="system">Système</option><option value="light">Clair</option><option value="dark">Sombre</option></select>
         <div class="op-modal-row"><span>Modèle actif</span><small>${esc(state.config.model_label)}</small></div>
         <div class="op-modal-row"><span>Langue de l’interface</span><small>Français</small></div>`);
      const sel = $('#op-theme');
      sel.value = getTheme();
      sel.addEventListener('change', (e) => { try { localStorage.setItem('op-theme', e.target.value); } catch {} applyTheme(); });
    },

    help: () => modal('Bien démarrer',
      `<p>Décrivez votre besoin puis envoyez votre message avec Entrée (Maj + Entrée pour aller à la ligne).</p>
       <p>Vos conversations sont enregistrées : retrouvez-les dans la barre latérale, renommez-les ou épinglez-les avec le bouton « … ».</p>
       <p>Pendant une réponse, le bouton d’envoi devient un bouton « stop » pour l’interrompre.</p>`),

    search: () => {
      modal('Rechercher une conversation',
        `<label for="op-search">Rechercher dans les titres et les messages</label>
         <input id="op-search" placeholder="Rechercher…" autocomplete="off"><div class="op-search-results"></div>`);
      let timer = null;
      const run = async (q) => {
        const box = $('.op-search-results');
        let hits = [];
        try { hits = await api('/api/chats' + (q ? `?q=${encodeURIComponent(q)}` : '')); } catch {}
        box.replaceChildren();
        if (!hits.length) { box.textContent = 'Aucune conversation trouvée.'; return; }
        hits.slice(0, 30).forEach((c) => {
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'op-modal-row'; b.textContent = c.title;
          b.addEventListener('click', () => { closeModal(); openChat(c.id); });
          box.appendChild(b);
        });
      };
      run('');
      $('#op-search').addEventListener('input', (e) => { clearTimeout(timer); timer = setTimeout(() => run(e.target.value.trim()), 250); });
    },
  };

  // ── Écouteurs ─────────────────────────────────────────────────────────────
  root.addEventListener('click', (e) => {
    if (!e.target.closest('.op-thread-menu,[data-action="thread-menu"]')) closeMenu();
    if (!e.target.closest('.op-profile-anchor')) toggleProfile(false);
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.prompt) { $('#op-question').value = b.dataset.prompt; $('#op-question').focus(); return; }
    if (b.dataset.action && actions[b.dataset.action]) actions[b.dataset.action](b);
  });

  function bindComposer(formSel, inputSel) {
    const form = $(formSel), input = $(inputSel);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      if (state.controller) return stopGeneration();
      const text = input.value;
      input.value = '';
      input.style.height = '';
      send(text);
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    });
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 180) + 'px';
    });
  }
  bindComposer('#op-home-form', '#op-question');
  bindComposer('#op-chat-form', '#op-chat-question');

  $('.op-modal-backdrop').addEventListener('click', (e) => { if (e.target === $('.op-modal-backdrop')) closeModal(); });
  window.addEventListener('resize', () => closeMenu());
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeMenu(true); closeModal(); toggleProfile(false); $('.op-app').classList.remove('op-nav-open'); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && !$('.op-app').hidden) { e.preventDefault(); actions.search(); }
    const menu = $('.op-thread-menu');
    if (!menu.hidden && ['ArrowDown', 'ArrowUp'].includes(e.key)) {
      e.preventDefault();
      const items = [...menu.querySelectorAll('button')];
      const i = items.indexOf(document.activeElement);
      items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
    }
  });

  // ── Menu profil ───────────────────────────────────────────────────────────
  function toggleProfile(open) {
    const menu = $('.op-profile-menu');
    const next = open ?? menu.hidden;
    menu.hidden = !next;
    $('.op-profile').setAttribute('aria-expanded', String(next));
  }

  // ── Connexion ─────────────────────────────────────────────────────────────
  function showLogin() {
    if (!$('.op-login').hidden) return;
    state.controller?.abort();
    state.chats = [];
    state.active = null;
    closeModal();
    closeMenu();
    $('.op-app').hidden = true;
    $('.op-login').hidden = false;
    $('#op-password').value = '';
    $('.op-login-error').hidden = true;
    if (location.pathname !== '/') history.replaceState({}, '', '/');
    icons();
    $('#op-login-user').focus();
  }

  $('#op-login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = $('#op-login-user').value.trim();
    const password = $('#op-password').value;
    const errBox = $('.op-login-error');
    const submit = $('.op-login-submit');
    if (!username || !password) {
      errBox.textContent = 'Saisissez votre identifiant et votre mot de passe.';
      errBox.hidden = false;
      return;
    }
    submit.disabled = true;
    errBox.hidden = true;
    try {
      const resp = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (resp.ok) {
        $('#op-password').value = '';
        await startApp();
        return;
      }
      const data = await resp.json().catch(() => ({}));
      errBox.textContent = data.detail || 'Connexion impossible.';
      errBox.hidden = false;
      $('#op-password').select();
    } catch {
      errBox.textContent = 'Serveur injoignable. Réessayez dans un instant.';
      errBox.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });

  // ── Démarrage ─────────────────────────────────────────────────────────────
  function applyIdentity() {
    const name = state.me.display_name || state.me.username || '';
    const first = name.split(/\s+/)[0] || '';
    const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';
    $('#op-greeting').textContent = first ? `BONJOUR, ${first.toLocaleUpperCase('fr')}` : 'BONJOUR';
    $$('[data-display-name]').forEach((el) => (el.textContent = name));
    $$('[data-username]').forEach((el) => (el.textContent = state.me.username || ''));
    $$('[data-initials]').forEach((el) => (el.textContent = initials));
  }

  async function startApp() {
    try { state.me = await api('/api/me'); } catch { return; }   // 401 → écran de connexion
    try { state.config = await api('/api/config'); } catch {}
    $$('.op-model').forEach((b) => (b.innerHTML = `<span class="op-model-dot"></span> ${esc(state.config.model_label)} <i data-lucide="chevron-down"></i>`));
    applyIdentity();
    $('.op-login').hidden = true;
    $('.op-app').hidden = false;
    await refreshChats();
    await route();
    icons();
  }

  applyTheme();
  icons();
  startApp();
})();
