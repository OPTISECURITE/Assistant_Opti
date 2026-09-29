/*
 * Assistant Opti — front (étape 1)
 * Chat en streaming vers le backend, historique de la session, thème, modales.
 * La persistance des conversations arrive à l'étape 2 (base de données).
 */
(() => {
  'use strict';

  const root = document.getElementById('opti-design');
  const $ = (s) => root.querySelector(s);
  const $$ = (s) => [...root.querySelectorAll(s)];

  // ── État ──────────────────────────────────────────────────────────────────
  const state = {
    config: { model: '', model_label: 'Opti · Qwen 3' },
    conversations: [],   // { id, title, messages: [{role, content}], createdAt }
    active: null,        // conversation courante
    controller: null,    // AbortController de la génération en cours
  };

  // ── Utilitaires ───────────────────────────────────────────────────────────
  const icons = () => window.lucide?.createIcons({ attrs: { width: 18, height: 18 } });
  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()));

  marked.setOptions({ gfm: true, breaks: true });
  const renderMarkdown = (text) => DOMPurify.sanitize(marked.parse(text || ''));

  let toastTimer = null;
  function toast(message) {
    const el = $('.op-toast');
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), 3500);
  }

  // ── Thème : Système / Clair / Sombre (mémorisé dans le navigateur) ────────
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  function getTheme() {
    try { return localStorage.getItem('op-theme') || 'system'; } catch { return 'system'; }
  }
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
    lastFocus = document.activeElement;
    $('#op-modal-title').textContent = title;
    $('.op-modal-body').innerHTML = bodyHtml;
    $('.op-modal-backdrop').hidden = false;
    icons();
    const focus = $('.op-modal-body').querySelector('input,select,textarea,button') || $('[data-action="close-modal"]');
    focus.focus();
  }
  function closeModal() {
    if ($('.op-modal-backdrop').hidden) return;
    $('.op-modal-backdrop').hidden = true;
    lastFocus?.focus();
  }

  // ── Vues : accueil / conversation ─────────────────────────────────────────
  function showHome() {
    stopGeneration();
    state.active = null;
    $('#op-home').hidden = false;
    $('#op-conversation').hidden = true;
    $('.op-main').classList.remove('op-chat-active');
    $('#op-header-title').textContent = 'Assistant Opti';
    $('#op-thread').replaceChildren();
    $('#op-question').value = '';
    $('.op-app').classList.remove('op-nav-open');
    renderRecents();
    $('#op-question').focus();
  }

  function showConversation(conv) {
    state.active = conv;
    $('#op-home').hidden = true;
    $('#op-conversation').hidden = false;
    $('.op-main').classList.add('op-chat-active');
    $('#op-header-title').textContent = conv.title;
    $('.op-app').classList.remove('op-nav-open');
    renderThread();
    renderRecents();
  }

  // ── Rendu du fil de discussion ────────────────────────────────────────────
  function userBubble(text) {
    const div = document.createElement('div');
    div.className = 'op-user-message';
    div.textContent = text;
    return div;
  }

  function answerBlock(content, { streaming = false } = {}) {
    const wrap = document.createElement('div');
    wrap.className = 'op-answer' + (streaming ? ' is-streaming' : '');
    wrap.innerHTML = `
      <img src="/static/img/mark.png" alt="">
      <div class="op-answer-body">
        <div class="op-answer-name">Assistant Opti <span>${state.config.model_label}</span></div>
        <div class="op-answer-content"></div>
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
    const msgs = state.active?.messages || [];
    msgs.forEach((m, i) => {
      if (m.role === 'user') {
        const ex = document.createElement('div');
        ex.className = 'op-chat-exchange';
        ex.appendChild(userBubble(m.content));
        const next = msgs[i + 1];
        if (next && next.role === 'assistant') {
          const block = answerBlock(next.content);
          block.dataset.index = i + 1;
          ex.appendChild(block);
        }
        thread.appendChild(ex);
      }
    });
    // la dernière réponse seule peut être régénérée
    $$('#op-thread [data-action="regenerate"]').forEach((b, i, all) => (b.hidden = i !== all.length - 1));
    icons();
  }

  const scrollToBottom = () => {
    const c = $('#op-conversation');
    c.scrollTop = c.scrollHeight;
  };

  // ── Historique (session) dans la barre latérale ───────────────────────────
  function renderRecents() {
    const box = $('#op-recents');
    box.replaceChildren();
    if (!state.conversations.length) {
      const empty = document.createElement('div');
      empty.className = 'op-empty-history';
      empty.textContent = 'Aucune conversation pour le moment.';
      box.appendChild(empty);
      return;
    }
    const period = document.createElement('div');
    period.className = 'op-period';
    period.textContent = 'Aujourd’hui';
    box.appendChild(period);
    [...state.conversations].reverse().forEach((conv) => {
      const row = document.createElement('div');
      row.className = 'op-thread-row' + (conv === state.active ? ' is-active' : '');
      row.innerHTML = `<button type="button" class="op-thread-open"><i data-lucide="message-square"></i><span class="op-thread-title"></span></button>`;
      row.querySelector('.op-thread-title').textContent = conv.title;
      row.querySelector('button').addEventListener('click', () => {
        if (state.controller) return toast('Une réponse est en cours de génération.');
        showConversation(conv);
      });
      box.appendChild(row);
    });
    icons();
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

  function stopGeneration() {
    if (state.controller) state.controller.abort();
  }

  function newConversation(firstMessage) {
    const conv = {
      id: uid(),
      title: firstMessage.replace(/\s+/g, ' ').slice(0, 48) + (firstMessage.length > 48 ? '…' : ''),
      messages: [],
      createdAt: Date.now(),
    };
    state.conversations.push(conv);
    return conv;
  }

  async function send(text) {
    text = (text || '').trim();
    if (!text || state.controller) return;
    const conv = state.active || newConversation(text);
    conv.messages.push({ role: 'user', content: text });
    showConversation(conv);
    await generate(conv);
  }

  async function generate(conv) {
    const thread = $('#op-thread');
    const exchange = thread.lastElementChild;
    const block = answerBlock('', { streaming: true });
    exchange.appendChild(block);
    icons();
    const contentEl = block.querySelector('.op-answer-content');
    scrollToBottom();

    const controller = new AbortController();
    state.controller = controller;
    setBusy(true);

    let full = '';
    let pending = false;
    const paint = () => {
      pending = false;
      contentEl.innerHTML = renderMarkdown(full);
      scrollToBottom();
    };

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: conv.messages }),
        signal: controller.signal,
      });
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
      if (err.name === 'AbortError') {
        if (!full) full = '_Génération interrompue._';
        paint();
      } else {
        contentEl.innerHTML = '<div class="op-answer-error">Impossible de joindre l’assistant pour le moment. Réessayez dans un instant.</div>';
        full = '';
      }
    } finally {
      block.classList.remove('is-streaming');
      state.controller = null;
      setBusy(false);
      if (full) conv.messages.push({ role: 'assistant', content: full });
      else conv.messages.pop(); // échec : on retire la question pour pouvoir la renvoyer
      renderThread();
      scrollToBottom();
      $('#op-chat-question').focus();
    }
  }

  async function regenerate() {
    const conv = state.active;
    if (!conv || state.controller) return;
    if (conv.messages.at(-1)?.role === 'assistant') conv.messages.pop();
    renderThread();
    await generate(conv);
  }

  // ── Actions (boutons data-action) ─────────────────────────────────────────
  const actions = {
    new: showHome,
    nav: () => $('.op-app').classList.toggle('op-nav-open'),
    'close-modal': closeModal,
    soon: () => toast('Fonctionnalité prévue dans une prochaine étape.'),
    'copy-answer': async (btn) => {
      const text = btn.closest('.op-answer').querySelector('.op-answer-content').innerText;
      try { await navigator.clipboard.writeText(text); toast('Réponse copiée.'); }
      catch { toast('Copie impossible dans ce navigateur (HTTPS requis).'); }
    },
    regenerate,
    model: () => modal('Votre modèle',
      `<div class="op-modal-row"><i data-lucide="cpu"></i><span>${state.config.model_label}
        <small style="display:block;overflow-wrap:anywhere">${state.config.model}</small></span><i data-lucide="check"></i></div>
       <p style="margin-top:18px">Modèle hébergé sur les serveurs d’Opti Sécurité. Aucune donnée ne sort de l’entreprise.</p>`),
    settings: () => {
      modal('Réglages',
        `<label for="op-theme">Apparence</label>
         <select id="op-theme"><option value="system">Système</option><option value="light">Clair</option><option value="dark">Sombre</option></select>
         <div class="op-modal-row"><span>Modèle actif</span><small>${state.config.model_label}</small></div>
         <div class="op-modal-row"><span>Langue de l’interface</span><small>Français</small></div>`);
      const sel = $('#op-theme');
      sel.value = getTheme();
      sel.addEventListener('change', (e) => {
        try { localStorage.setItem('op-theme', e.target.value); } catch {}
        applyTheme();
      });
    },
    help: () => modal('Bien démarrer',
      `<p>Décrivez votre besoin puis envoyez votre message avec Entrée (Maj + Entrée pour aller à la ligne).</p>
       <p>Les trois suggestions de l’accueil vous aident à formuler une première demande.</p>
       <p>Pendant une réponse, le bouton d’envoi devient un bouton « stop » pour l’interrompre.</p>`),
    search: () => {
      modal('Rechercher une conversation',
        `<label for="op-search">Rechercher dans vos conversations</label>
         <input id="op-search" placeholder="Rechercher…" autocomplete="off"><div class="op-search-results"></div>`);
      const render = (q) => {
        const box = $('.op-search-results');
        box.replaceChildren();
        const needle = q.toLocaleLowerCase('fr');
        const hits = state.conversations.filter((c) =>
          c.title.toLocaleLowerCase('fr').includes(needle) ||
          c.messages.some((m) => m.content.toLocaleLowerCase('fr').includes(needle)));
        if (!hits.length) { box.textContent = 'Aucune conversation trouvée.'; return; }
        hits.reverse().forEach((c) => {
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'op-modal-row'; b.textContent = c.title;
          b.addEventListener('click', () => { closeModal(); showConversation(c); });
          box.appendChild(b);
        });
      };
      render('');
      $('#op-search').addEventListener('input', (e) => render(e.target.value));
    },
  };

  // ── Écouteurs ─────────────────────────────────────────────────────────────
  root.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.prompt) { $('#op-question').value = b.dataset.prompt; $('#op-question').focus(); return; }
    if (b.dataset.action && actions[b.dataset.action]) actions[b.dataset.action](b);
  });

  // Formulaires : envoi, ou arrêt si une génération est en cours
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
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        form.requestSubmit();
      }
    });
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 180) + 'px';
    });
  }
  bindComposer('#op-home-form', '#op-question');
  bindComposer('#op-chat-form', '#op-chat-question');

  $('.op-modal-backdrop').addEventListener('click', (e) => { if (e.target === $('.op-modal-backdrop')) closeModal(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeModal(); $('.op-app').classList.remove('op-nav-open'); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); actions.search(); }
  });

  // ── Démarrage ─────────────────────────────────────────────────────────────
  async function init() {
    applyTheme();
    try {
      const r = await fetch('/api/config');
      if (r.ok) state.config = await r.json();
    } catch { /* valeurs par défaut */ }
    $$('.op-model').forEach((b) => (b.innerHTML = `<span class="op-model-dot"></span> ${state.config.model_label} <i data-lucide="chevron-down"></i>`));
    renderRecents();
    icons();
  }
  init();
})();
