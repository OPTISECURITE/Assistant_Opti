/*
 * Assistant Opti — Réglages (chaque utilisateur) et Administration (comptes admin).
 * S'appuie sur les briques exposées par app.js (window.Opti).
 */
(() => {
  'use strict';
  const O = window.Opti;
  const { $, esc } = O;

  // ── Fenêtre à onglets ─────────────────────────────────────────────────────
  function tabbed(title, tabs, initial = 0) {
    O.modal(title, `<div class="op-panes"><nav class="op-tabs" role="tablist"></nav><div class="op-pane" role="tabpanel"></div></div>`);
    $('.op-modal').classList.add('is-wide');
    const nav = $('.op-tabs');
    const pane = $('.op-pane');
    tabs.forEach((t, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'tab');
      b.innerHTML = `<i data-lucide="${t.icon}"></i><span></span>`;
      b.querySelector('span').textContent = t.label;
      b.addEventListener('click', () => select(i));
      nav.appendChild(b);
    });
    async function select(i) {
      [...nav.children].forEach((b, j) => b.setAttribute('aria-selected', String(i === j)));
      pane.innerHTML = '';
      pane.scrollTop = 0;
      await tabs[i].render(pane);
      O.icons();
    }
    select(initial);
    O.icons();
  }

  const choices = (name, options, value) =>
    `<div class="op-choices">${options.map(([v, l]) =>
      `<label><input type="radio" name="${name}" value="${v}" ${v === value ? 'checked' : ''}><span>${esc(l)}</span></label>`).join('')}</div>`;
  const toggle = (id, label, hint, checked) =>
    `<label class="op-toggle"><span>${esc(label)}${hint ? `<small>${esc(hint)}</small>` : ''}</span><input type="checkbox" id="${id}" ${checked ? 'checked' : ''}></label>`;
  const fmtDate = (ts) => (ts ? new Date(ts).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : 'jamais');
  const FIELD_NAMES = { password: 'Le mot de passe', username: 'L’identifiant', display_name: 'Le nom affiché',
    system_prompt: 'La consigne système', model_label: 'Le nom affiché du modèle', searxng_url: 'L’adresse de SearXNG',
    export_footer: 'La mention en pied de page', session_idle_minutes: 'Le délai d’inactivité', session_max_hours: 'La durée maximale de session',
    retention_days: 'La durée de conservation des conversations', audit_retention_days: 'La durée de conservation du journal',
    ollama_slots: 'Le nombre de places d’Ollama', voice_reserve: 'La réserve', max_assistant_slots: 'Le plafond de places',
    max_per_user: 'Le plafond par utilisateur', queue_timeout_seconds: 'Le délai d’attente' };
  const humanize = (d) => {
    const field = d.loc?.at(-1);
    const name = FIELD_NAMES[field] || `Le champ « ${field} »`;
    if (d.type === 'string_too_short') return `${name} doit contenir au moins ${d.ctx?.min_length} caractères.`;
    if (d.type === 'string_too_long') return `${name} est trop long (${d.ctx?.max_length} caractères au maximum).`;
    if (d.type === 'string_pattern_mismatch' && field === 'username') return `${name} ne peut contenir que des lettres, chiffres, points, tirets et underscores (sans espace ni accent).`;
    if (d.type?.startsWith('greater_than') || d.type?.startsWith('less_than')) return `${name} est hors des limites autorisées.`;
    if (d.type === 'value_error') return String(d.msg || '').replace(/^Value error, /, '');
    return `${name} n’est pas valide.`;
  };
  const errMsg = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    if (Array.isArray(data.detail)) return data.detail.map(humanize).join(' ');
    return data.detail || 'Action impossible.';
  };

  // ── Mots de passe : contrôle et générateur ────────────────────────────────
  const PW_MIN = 12;
  const USERNAME_RX = /^[A-Za-z0-9._-]{2,}$/;
  function generatePassword(length = 16) {
    const sets = ['abcdefghjkmnpqrstuvwxyz', 'ABCDEFGHJKLMNPQRSTUVWXYZ', '23456789', '!#$%*+-=?@'];
    const all = sets.join('');
    const rnd = (max) => { const a = new Uint32Array(1); let x; do { crypto.getRandomValues(a); x = a[0]; } while (x >= 4294967296 - (4294967296 % max)); return x % max; };
    const chars = sets.map((set) => set[rnd(set.length)]);          // au moins un de chaque famille
    while (chars.length < length) chars.push(all[rnd(all.length)]);
    for (let i = chars.length - 1; i > 0; i--) { const j = rnd(i + 1); [chars[i], chars[j]] = [chars[j], chars[i]]; }
    return chars.join('');
  }
  const passwordProblem = (pw) => (pw.length < PW_MIN
    ? `Le mot de passe doit contenir au moins ${PW_MIN} caractères (il en contient ${pw.length}).` : '');
  const pwField = (id, label = 'Mot de passe') => `
    <div class="op-field"><label for="${id}">${label}</label>
      <div class="op-pw-row"><input type="text" id="${id}" autocomplete="off" spellcheck="false" placeholder="${PW_MIN} caractères minimum">
        <button type="button" class="op-secondary" data-pw="generate">Générer</button>
        <button type="button" class="op-secondary" data-pw="copy">Copier</button></div></div>`;
  function bindPw(box, id) {
    const input = box.querySelector('#' + id);
    box.querySelector('[data-pw=generate]').addEventListener('click', () => { input.value = generatePassword(); input.focus(); });
    box.querySelector('[data-pw=copy]').addEventListener('click', async () => {
      if (!input.value) return O.toast('Aucun mot de passe à copier.');
      try { await navigator.clipboard.writeText(input.value); O.toast('Mot de passe copié.'); }
      catch { input.select(); O.toast('Copie automatique impossible : sélectionnez et copiez le texte.'); }
    });
  }
  function showFormError(box, msg) {
    let el = box.querySelector('.op-form-error');
    if (!msg) { el?.remove(); return; }
    if (!el) { el = document.createElement('div'); el.className = 'op-form-error'; el.setAttribute('role', 'alert'); box.querySelector('.op-pane-actions').before(el); }
    el.textContent = msg;
  }
  async function send(url, method, body) {
    const resp = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    if (!resp.ok) throw new Error(await errMsg(resp));
    return resp.status === 204 ? null : resp.json();
  }

  // ════════════════════════════ RÉGLAGES UTILISATEUR ═══════════════════════
  const userTabs = [
    {
      label: 'Général', icon: 'sliders-horizontal',
      render(pane) {
        const p = O.state.prefs;
        pane.innerHTML = `
          <section><h3>Apparence</h3><p class="op-hint">Mémorisée sur ce navigateur.</p>
            ${choices('theme', [['system', 'Système'], ['light', 'Clair'], ['dark', 'Sombre']], O.getTheme())}</section>
          <section><h3>Taille du texte</h3><p class="op-hint">Agrandit les messages de la conversation.</p>
            ${choices('text_size', [['normal', 'Normale'], ['grand', 'Grande'], ['tres-grand', 'Très grande']], p.text_size)}</section>
          <section><h3>Envoi des messages</h3><p class="op-hint">Avec Ctrl + Entrée, la touche Entrée seule passe à la ligne : pratique pour les longs textes.</p>
            ${choices('send_key', [['enter', 'Entrée'], ['ctrl-enter', 'Ctrl + Entrée']], p.send_key)}</section>`;
        pane.querySelectorAll('input[name=theme]').forEach((r) => r.addEventListener('change', () => {
          try { localStorage.setItem('op-theme', r.value); } catch {}
          O.applyTheme();
        }));
        ['text_size', 'send_key'].forEach((name) => pane.querySelectorAll(`input[name=${name}]`)
          .forEach((r) => r.addEventListener('change', () => O.savePrefs({ [name]: r.value }))));
      },
    },
    {
      label: 'Réponses', icon: 'message-square-text',
      render(pane) {
        const p = O.state.prefs;
        pane.innerHTML = `
          <section><h3>Ton</h3><p class="op-hint">La manière dont l’assistant s’adresse à vous.</p>
            ${choices('tone', [['neutre', 'Neutre'], ['cordial', 'Cordial'], ['formel', 'Formel'], ['direct', 'Direct']], p.tone)}</section>
          <section><h3>Longueur des réponses</h3>
            ${choices('length', [['courte', 'Courtes'], ['equilibree', 'Équilibrées'], ['detaillee', 'Détaillées']], p.length)}</section>
          <section><h3>Documents longs</h3>
            <p class="op-hint">Un document trop long pour tenir d’un coup est lu par morceaux. En mode <b>Automatique</b>, l’assistant choisit : quelques passages pour une question précise, une lecture complète pour un résumé ou une question qui porte sur tout le document. En mode <b>Toujours tout lire</b>, chaque question relit le document en entier : plus complet, mais plus long (comptez de quelques dizaines de secondes à plusieurs minutes selon la taille). Vous pouvez aussi écrire « lis tout le document » dans votre message.</p>
            ${choices('doc_mode', [['auto', 'Automatique'], ['full', 'Toujours tout lire']], p.doc_mode)}</section>
          <section><h3>Instructions personnelles</h3>
            <p class="op-hint">Ce que l’assistant doit savoir sur vous ou sur votre façon de travailler. Exemple : « Je suis technicien de maintenance, donne-moi des étapes numérotées. »</p>
            <textarea id="op-instructions" rows="6" maxlength="2000"></textarea>
            <div class="op-pane-actions"><button type="button" class="op-primary" id="op-save-instr">Enregistrer</button><small class="op-hint" id="op-instr-count"></small></div></section>`;
        const ta = pane.querySelector('#op-instructions');
        ta.value = p.instructions;
        const count = () => (pane.querySelector('#op-instr-count').textContent = `${ta.value.length} / 2000`);
        ta.addEventListener('input', count);
        count();
        pane.querySelector('#op-save-instr').addEventListener('click', () => O.savePrefs({ instructions: ta.value.trim() }));
        ['tone', 'length', 'doc_mode'].forEach((name) => pane.querySelectorAll(`input[name=${name}]`)
          .forEach((r) => r.addEventListener('change', () => O.savePrefs({ [name]: r.value }))));
      },
    },
    {
      label: 'Recherche web', icon: 'globe',
      render(pane) {
        if (O.state.config.web_enabled === false) {
          pane.innerHTML = '<section><h3>Recherche web</h3><p class="op-hint">La recherche web est désactivée par l’administrateur.</p></section>';
          return;
        }
        pane.innerHTML = `
          <section><h3>Recherche web</h3>
            <p class="op-hint">En mode automatique, l’assistant cherche sur Internet uniquement quand la question le nécessite (actualité, réglementation, prix, produits…). Les requêtes sont anonymisées et affichées au-dessus de la réponse. Le bouton globe d’une conversation force une recherche.</p>
            ${choices('web_mode', [['auto', 'Automatique'], ['on', 'Toujours'], ['off', 'Jamais']], O.state.prefs.web_mode)}</section>`;
        pane.querySelectorAll('input[name=web_mode]').forEach((r) => r.addEventListener('change', () => O.savePrefs({ web_mode: r.value })));
      },
    },
    {
      label: 'Mes données', icon: 'database',
      render(pane) {
        const days = O.state.config.retention_days || 0;
        const keep = O.state.config.retention_keep_pinned;
        const retention = days > 0
          ? `Vos conversations sont supprimées automatiquement après <b>${days} jour${days > 1 ? 's' : ''}</b> sans activité${keep ? ' (les conversations épinglées sont conservées)' : ''}.`
          : 'Vos conversations sont conservées jusqu’à ce que vous les supprimiez.';
        pane.innerHTML = `
          <section><h3>Conservation et traçabilité</h3>
            <p class="op-hint" style="margin-bottom:6px">${retention}</p>
            <p class="op-hint">Pour la sécurité, les connexions et certaines actions (envoi d’un message, dépôt d’un fichier, export…) sont journalisées avec la date et l’adresse du poste. Le contenu de vos messages n’y figure jamais.</p></section>
          <section><h3>Exporter mes conversations</h3>
            <p class="op-hint">Télécharge toutes vos conversations dans un fichier JSON (titres, messages, noms des fichiers joints).</p>
            <a class="op-primary" style="display:inline-block;padding:10px 20px;font-size:11px;text-decoration:none" href="/api/me/export">Télécharger l’export</a></section>
          <section><h3>Supprimer toutes mes conversations</h3>
            <p class="op-hint">Supprime définitivement toutes vos conversations et leurs fichiers joints. Cette action est irréversible.</p>
            <div id="op-wipe"><button type="button" class="op-secondary" id="op-wipe-ask">Supprimer tout mon historique…</button></div></section>`;
        pane.querySelector('#op-wipe-ask').addEventListener('click', () => {
          pane.querySelector('#op-wipe').innerHTML = `
            <div class="op-inline-form"><p style="margin:0 0 10px">Tapez <b>SUPPRIMER</b> pour confirmer.</p>
              <input type="text" id="op-wipe-confirm" autocomplete="off">
              <div class="op-pane-actions"><button type="button" class="op-primary op-danger" id="op-wipe-go" disabled>Tout supprimer</button></div></div>`;
          const input = pane.querySelector('#op-wipe-confirm');
          const go = pane.querySelector('#op-wipe-go');
          input.focus();
          input.addEventListener('input', () => (go.disabled = input.value.trim() !== 'SUPPRIMER'));
          go.addEventListener('click', async () => {
            try {
              await send('/api/me/chats', 'DELETE');
              O.closeModal();
              O.showHome();
              await O.refreshChats();
              O.toast('Votre historique a été supprimé.');
            } catch (e) { O.toast(e.message); }
          });
        });
      },
    },
  ];

  // ═══════════════════════════════ ADMINISTRATION ══════════════════════════
  const adminTabs = [
    { label: 'Utilisateurs', icon: 'users', render: renderUsers },
    { label: 'Modèle', icon: 'cpu', render: renderModel },
    { label: 'Recherche & fichiers', icon: 'globe', render: renderFeatures },
    { label: 'Connexions', icon: 'plug', render: renderConnections },
    { label: 'Charge', icon: 'gauge', render: renderLoad },
    { label: 'Sécurité', icon: 'shield-check', render: renderSecurity },
    { label: 'Journal', icon: 'scroll-text', render: renderAudit },
    { label: 'Statistiques', icon: 'chart-column', render: renderStats },
  ];

  async function renderUsers(pane) {
    let users = [];
    try { users = await O.api('/api/admin/users'); } catch { pane.textContent = 'Chargement impossible.'; return; }
    pane.innerHTML = `
      <section>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
          <div><h3>Comptes</h3><p class="op-hint" style="margin:0">${users.length} compte(s). Mot de passe : 12 caractères minimum.</p></div>
          <button type="button" class="op-primary" style="padding:9px 16px;font-size:11px" id="op-new-user"><i data-lucide="user-plus"></i> Nouveau compte</button>
        </div>
        <div id="op-user-form"></div>
        <table class="op-table" style="margin-top:12px"><thead><tr><th>Utilisateur</th><th>Rôle</th><th>Statut</th><th>Dernière connexion</th><th>Conv.</th><th></th></tr></thead><tbody></tbody></table>
      </section>`;
    const me = O.state.me.id;
    const tbody = pane.querySelector('tbody');
    users.forEach((u) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><b></b><small></small></td>
        <td><span class="op-badge">${u.is_admin ? 'Admin' : 'Utilisateur'}</span></td>
        <td><span class="op-badge ${u.active ? '' : 'is-off'}">${u.active ? 'Actif' : 'Désactivé'}</span></td>
        <td>${fmtDate(u.last_login_at)}</td>
        <td>${u.chats}</td>
        <td style="white-space:nowrap;text-align:right">
          <button type="button" class="op-icon" data-u="edit" aria-label="Modifier"><i data-lucide="pencil"></i></button>
          <button type="button" class="op-icon" data-u="password" aria-label="Changer le mot de passe"><i data-lucide="key-round"></i></button>
          ${u.id === me ? '' : `<button type="button" class="op-icon" data-u="active" aria-label="${u.active ? 'Désactiver' : 'Réactiver'}"><i data-lucide="${u.active ? 'user-x' : 'user-check'}"></i></button>
          <button type="button" class="op-icon" data-u="delete" aria-label="Supprimer"><i data-lucide="trash-2"></i></button>`}
        </td>`;
      tr.querySelector('b').textContent = u.display_name;
      tr.querySelector('small').textContent = u.username + (u.id === me ? ' (vous)' : '');
      tr.querySelectorAll('[data-u]').forEach((b) => b.addEventListener('click', () => userAction(b.dataset.u, u, pane)));
      tbody.appendChild(tr);
    });
    pane.querySelector('#op-new-user').addEventListener('click', () => userForm(pane, null));
    O.icons();
  }

  function userForm(pane, u) {
    const box = pane.querySelector('#op-user-form');
    const isNew = !u;
    box.innerHTML = `
      <div class="op-inline-form">
        <h3 style="margin-bottom:12px">${isNew ? 'Nouveau compte' : 'Modifier ' + esc(u.username)}</h3>
        <div class="op-grid2">
          ${isNew ? '<div class="op-field"><label for="f-username">Identifiant</label><input type="text" id="f-username" placeholder="initiale.nom (ex. m.chaput)" autocomplete="off"></div>' : ''}
          <div class="op-field"><label for="f-name">Nom affiché</label><input type="text" id="f-name" placeholder="Prénom Nom"></div>
        </div>
        ${isNew ? pwField('f-pass') : ''}
        ${u && u.id === O.state.me.id ? '' : toggle('f-admin', 'Administrateur', 'Accès à cette page d’administration', u?.is_admin)}
        <div class="op-pane-actions"><button type="button" class="op-primary" id="f-save">${isNew ? 'Créer le compte' : 'Enregistrer'}</button>
          <button type="button" class="op-secondary" id="f-cancel">Annuler</button></div>
      </div>`;
    if (u) box.querySelector('#f-name').value = u.display_name;
    if (isNew) bindPw(box, 'f-pass');
    box.querySelector('input').focus();
    box.querySelector('#f-cancel').addEventListener('click', () => (box.innerHTML = ''));
    box.querySelector('#f-save').addEventListener('click', async () => {
      const body = { display_name: box.querySelector('#f-name').value.trim() };
      const adm = box.querySelector('#f-admin');
      if (adm) body.is_admin = adm.checked;
      showFormError(box, '');
      if (!body.display_name) return showFormError(box, 'Indiquez le nom affiché (par exemple « Prénom Nom »).');
      try {
        if (isNew) {
          body.username = box.querySelector('#f-username').value.trim();
          body.password = box.querySelector('#f-pass').value;
          if (!USERNAME_RX.test(body.username)) return showFormError(box, 'L’identifiant doit contenir au moins 2 caractères : lettres, chiffres, points, tirets ou underscores (sans espace ni accent).');
          const problem = passwordProblem(body.password);
          if (problem) return showFormError(box, problem);
          await send('/api/admin/users', 'POST', body);
          O.toast(`Compte ${body.username} créé.`);
        } else {
          await send(`/api/admin/users/${u.id}`, 'PATCH', body);
          O.toast('Compte mis à jour.');
        }
        renderUsers(pane);
      } catch (e) { showFormError(box, e.message); }
    });
  }

  async function userAction(kind, u, pane) {
    const box = pane.querySelector('#op-user-form');
    if (kind === 'edit') return userForm(pane, u);
    if (kind === 'password') {
      box.innerHTML = `<div class="op-inline-form"><h3 style="margin-bottom:12px">Nouveau mot de passe pour ${esc(u.username)}</h3>
        ${pwField('f-newpass', 'Nouveau mot de passe')}
        <p class="op-hint">L’utilisateur sera déconnecté de toutes ses sessions. Pensez à lui transmettre le nouveau mot de passe.</p>
        <div class="op-pane-actions"><button type="button" class="op-primary" id="f-go">Changer le mot de passe</button><button type="button" class="op-secondary" id="f-cancel">Annuler</button></div></div>`;
      bindPw(box, 'f-newpass');
      box.querySelector('#f-newpass').focus();
      box.querySelector('#f-cancel').addEventListener('click', () => (box.innerHTML = ''));
      box.querySelector('#f-go').addEventListener('click', async () => {
        const pw = box.querySelector('#f-newpass').value;
        const problem = passwordProblem(pw);
        if (problem) return showFormError(box, problem);
        try { await send(`/api/admin/users/${u.id}`, 'PATCH', { password: pw }); O.toast('Mot de passe modifié.'); box.innerHTML = ''; }
        catch (e) { showFormError(box, e.message); }
      });
      return;
    }
    if (kind === 'active') {
      try { await send(`/api/admin/users/${u.id}`, 'PATCH', { active: !u.active }); O.toast(u.active ? 'Compte désactivé.' : 'Compte réactivé.'); renderUsers(pane); }
      catch (e) { O.toast(e.message); }
      return;
    }
    if (kind === 'delete') {
      box.innerHTML = `<div class="op-inline-form"><h3>Supprimer ${esc(u.username)} ?</h3>
        <p class="op-hint">Le compte et ses ${u.chats} conversation(s) seront définitivement supprimés. Pour conserver l’historique, désactivez plutôt le compte.</p>
        <div class="op-pane-actions"><button type="button" class="op-primary op-danger" id="f-go">Supprimer définitivement</button><button type="button" class="op-secondary" id="f-cancel">Annuler</button></div></div>`;
      box.querySelector('#f-cancel').addEventListener('click', () => (box.innerHTML = ''));
      box.querySelector('#f-go').addEventListener('click', async () => {
        try { await send(`/api/admin/users/${u.id}`, 'DELETE'); O.toast('Compte supprimé.'); renderUsers(pane); }
        catch (e) { O.toast(e.message); }
      });
    }
  }

  async function loadAppSettings() {
    return O.api('/api/admin/settings');
  }
  async function saveAppSettings(changes) {
    const { values } = await loadAppSettings();
    const saved = await send('/api/admin/settings', 'PUT', { ...values, ...changes });
    await O.reloadConfig();   // applique le nouveau libellé et les options activées
    return saved;
  }

  async function renderModel(pane) {
    const [{ values, defaults }, models] = await Promise.all([loadAppSettings(), O.api('/api/admin/models').catch(() => [])]);
    const list = [...new Set([values.model, ...models])];
    pane.innerHTML = `
      <section><h3>Modèle utilisé</h3>
        <p class="op-hint">Liste des modèles installés dans Ollama. Changer de modèle charge un nouveau modèle en mémoire GPU : à faire hors des heures d’appels de l’agent vocal.</p>
        <div class="op-grid2">
          <div class="op-field"><label for="m-model">Modèle</label><select id="m-model">${list.map((m) => `<option ${m === values.model ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select></div>
          <div class="op-field"><label for="m-label">Nom affiché</label><input type="text" id="m-label" maxlength="60"></div>
        </div>
        <div class="op-field"><label for="m-temp">Créativité (température)</label>
          <div class="op-range"><input type="range" id="m-temp" min="0" max="1.5" step="0.1"><output id="m-temp-out"></output></div>
          <p class="op-hint">Bas : réponses plus constantes et factuelles. Haut : plus variées. 0,7 est un bon réglage par défaut.</p></div></section>
      <section><h3>Consigne système</h3>
        <p class="op-hint">Instructions données à l’assistant au début de chaque conversation, pour tous les utilisateurs. Les préférences de chacun s’y ajoutent.</p>
        <textarea id="m-prompt" rows="9" maxlength="8000"></textarea>
        <button type="button" class="op-link" id="m-reset">Rétablir la consigne par défaut</button></section>
      <div class="op-pane-actions"><button type="button" class="op-primary" id="m-save">Enregistrer</button></div>`;
    const $p = (s) => pane.querySelector(s);
    $p('#m-label').value = values.model_label;
    $p('#m-prompt').value = values.system_prompt;
    $p('#m-temp').value = values.temperature;
    const out = () => ($p('#m-temp-out').textContent = Number($p('#m-temp').value).toFixed(1).replace('.', ','));
    $p('#m-temp').addEventListener('input', out);
    out();
    $p('#m-reset').addEventListener('click', () => ($p('#m-prompt').value = defaults.system_prompt));
    $p('#m-save').addEventListener('click', async () => {
      try {
        await saveAppSettings({ model: $p('#m-model').value, model_label: $p('#m-label').value.trim(),
                                temperature: Number($p('#m-temp').value), system_prompt: $p('#m-prompt').value.trim() });
        O.toast('Réglages du modèle enregistrés.');
      } catch (e) { O.toast(e.message); }
    });
  }

  async function renderFeatures(pane) {
    const { values: v } = await loadAppSettings();
    pane.innerHTML = `
      <section><h3>Recherche web</h3>
        ${toggle('w-on', 'Autoriser la recherche web', 'Si désactivée, l’assistant ne consulte jamais Internet, quel que soit le choix des utilisateurs.', v.web_enabled)}
        <div class="op-grid2">
          <div class="op-field"><label for="w-results">Résultats fournis à l’assistant</label><input type="number" id="w-results" min="1" max="10"></div>
          <div class="op-field"><label for="w-pages">Pages lues en entier</label><input type="number" id="w-pages" min="0" max="5"></div>
        </div>
        <div class="op-field"><label for="w-url">Adresse de SearXNG</label><input type="url" id="w-url"></div></section>
      <section><h3>Fichiers joints</h3>
        ${toggle('f-on', 'Autoriser les pièces jointes', 'PDF, Word, texte, CSV et Excel.', v.uploads_enabled)}
        ${toggle('a-on', 'Autoriser l’analyse de données', 'Exécution de code d’analyse sur les CSV / Excel, dans le bac à sable isolé.', v.analysis_enabled)}
        ${toggle('ocr-on', 'Lire les PDF scannés (OCR)', 'Reconnaissance de texte sur les pages en image, dans le bac à sable. Allonge le temps de lecture d’un PDF scanné.', v.ocr_enabled)}
        <div class="op-grid2">
          <div class="op-field"><label for="f-max">Taille maximale (Mo)</label><input type="number" id="f-max" min="1" max="100"></div>
          <div class="op-field"><label for="a-steps">Essais de calcul par question</label><input type="number" id="a-steps" min="1" max="5"></div>
          <div class="op-field"><label for="a-timeout">Durée maximale d’un calcul (s)</label><input type="number" id="a-timeout" min="10" max="300"></div>
          <div class="op-field"><label for="ocr-max">Pages scannées lues par PDF (OCR)</label><input type="number" id="ocr-max" min="1" max="300"></div>
        </div></section>
      <section><h3>Documents exportés (Word, PDF)</h3>
        <p class="op-hint">Seul le contenu du document rédigé par l’assistant est exporté (sans ses commentaires ni ses conseils).</p>
        ${toggle('e-logo', 'Logo Opti Sécurité en en-tête', 'Coché : un bandeau avec le logo apparaît en haut de chaque page. Décoché : le document ne contient que son texte.', v.export_logo)}
        ${toggle('e-pages', 'Numéroter les pages', 'Utile pour un long document (procédure, rapport) ; inutile pour une lettre.', v.export_page_numbers)}
        <div class="op-field"><label for="e-footer">Mention en pied de page (facultative)</label><input type="text" id="e-footer" maxlength="200" placeholder="Laisser vide : aucun pied de page"></div>
        <p class="op-hint">Exemple : « Document généré par l’Assistant Opti, à vérifier avant diffusion ».</p></section>
      <div class="op-pane-actions"><button type="button" class="op-primary" id="x-save">Enregistrer</button></div>`;
    const $p = (s) => pane.querySelector(s);
    $p('#e-footer').value = v.export_footer;
    $p('#w-results').value = v.web_results;
    $p('#w-pages').value = v.web_pages_read;
    $p('#w-url').value = v.searxng_url;
    $p('#f-max').value = v.max_upload_mb;
    $p('#a-steps').value = v.max_analysis_steps;
    $p('#a-timeout').value = v.sandbox_timeout;
    $p('#ocr-max').value = v.max_ocr_pages;
    $p('#x-save').addEventListener('click', async () => {
      try {
        await saveAppSettings({
          web_enabled: $p('#w-on').checked, web_results: Number($p('#w-results').value), web_pages_read: Number($p('#w-pages').value),
          searxng_url: $p('#w-url').value.trim(), uploads_enabled: $p('#f-on').checked, analysis_enabled: $p('#a-on').checked,
          max_upload_mb: Number($p('#f-max').value), max_analysis_steps: Number($p('#a-steps').value), sandbox_timeout: Number($p('#a-timeout').value),
          ocr_enabled: $p('#ocr-on').checked, max_ocr_pages: Number($p('#ocr-max').value),
          export_logo: $p('#e-logo').checked, export_footer: $p('#e-footer').value.trim(), export_page_numbers: $p('#e-pages').checked,
        });
        O.toast('Réglages enregistrés.');
      } catch (e) { O.toast(e.message); }
    });
  }

  async function renderSecurity(pane) {
    const { values: v } = await loadAppSettings();
    pane.innerHTML = `
      <section><h3>Sessions</h3>
        <p class="op-hint">Sur un poste laissé sans surveillance, une conversation confidentielle ne doit pas rester à l’écran. Passé le délai d’inactivité, l’utilisateur est déconnecté et l’écran est vidé ; une fenêtre l’avertit 60 secondes avant.</p>
        <div class="op-grid2">
          <div class="op-field"><label for="s-idle">Déconnexion après inactivité (minutes)</label><input type="number" id="s-idle" min="5" max="1440"></div>
          <div class="op-field"><label for="s-max">Durée maximale d’une session (heures)</label><input type="number" id="s-max" min="1" max="720"></div>
        </div>
        <p class="op-hint">La durée maximale s’applique même à un utilisateur actif : au-delà, il doit se reconnecter. Tant qu’une réponse se génère ou qu’un document est lu, l’utilisateur n’est pas considéré comme inactif. Les nouveaux délais s’appliquent tout de suite, y compris aux sessions déjà ouvertes.</p>
      </section>
      <section><h3>Conservation des données</h3>
        <p class="op-hint">Une conversation devient inutile, et sensible, avec le temps. Passé ce délai sans aucune activité, elle est supprimée avec ses fichiers joints. Le contrôle a lieu toutes les heures ; les utilisateurs sont informés de la règle dans leurs Réglages.</p>
        <div class="op-grid2">
          <div class="op-field"><label for="r-days">Supprimer les conversations inactives depuis (jours)</label><input type="number" id="r-days" min="0" max="3650"><p class="op-hint" style="margin:4px 0 0">0 = ne jamais supprimer</p></div>
          <div class="op-field"><label for="r-audit">Conserver le journal d’audit (jours)</label><input type="number" id="r-audit" min="30" max="3650"></div>
        </div>
        ${toggle('r-pinned', 'Conserver les conversations épinglées', 'Les épinglées échappent à la suppression automatique.', v.retention_keep_pinned)}
        <div id="r-confirm"></div>
        <div class="op-pane-actions" style="margin-top:8px"><button type="button" class="op-secondary" id="r-run">Lancer la purge maintenant</button></div>
      </section>
      <div class="op-pane-actions"><button type="button" class="op-primary" id="s-save">Enregistrer</button></div>`;
    const $p = (sel) => pane.querySelector(sel);
    $p('#s-idle').value = v.session_idle_minutes;
    $p('#s-max').value = v.session_max_hours;
    $p('#r-days').value = v.retention_days;
    $p('#r-audit').value = v.audit_retention_days;

    async function persist(values) {
      await saveAppSettings(values);
      $p('#r-confirm').innerHTML = '';
      O.toast('Réglages de sécurité enregistrés.');
    }

    $p('#s-save').addEventListener('click', async () => {
      const idle = Number($p('#s-idle').value), max = Number($p('#s-max').value);
      const days = Number($p('#r-days').value), audit = Number($p('#r-audit').value), keep = $p('#r-pinned').checked;
      showFormError(pane, '');
      if (!Number.isInteger(idle) || idle < 5 || idle > 1440) return showFormError(pane, 'Le délai d’inactivité doit être un nombre entier de minutes entre 5 et 1 440.');
      if (!Number.isInteger(max) || max < 1 || max > 720) return showFormError(pane, 'La durée maximale doit être un nombre entier d’heures entre 1 et 720.');
      if (!Number.isInteger(days) || days < 0 || days > 3650) return showFormError(pane, 'La durée de conservation des conversations doit être un nombre entier de jours entre 0 et 3 650.');
      if (!Number.isInteger(audit) || audit < 30 || audit > 3650) return showFormError(pane, 'La durée de conservation du journal doit être un nombre entier de jours entre 30 et 3 650.');
      const values = { session_idle_minutes: idle, session_max_hours: max, retention_days: days, audit_retention_days: audit, retention_keep_pinned: keep };
      try {
        // Une règle de suppression est irréversible : on dit combien de conversations elle emporterait avant de l'enregistrer
        const changed = days !== v.retention_days || keep !== v.retention_keep_pinned;
        if (days > 0 && changed) {
          const { chats } = await send('/api/admin/retention/preview', 'POST', { days, keep_pinned: keep });
          if (chats > 0) {
            $p('#r-confirm').innerHTML = `<div class="op-inline-form"><h3>Confirmer la règle de conservation</h3>
              <p class="op-hint">Avec ${days} jour${days > 1 ? 's' : ''}, <b>${chats} conversation${chats > 1 ? 's' : ''}</b> existante${chats > 1 ? 's seraient supprimées' : ' serait supprimée'} définitivement lors du prochain contrôle (dans l’heure), avec leurs fichiers joints.</p>
              <div class="op-pane-actions"><button type="button" class="op-primary op-danger" id="r-ok">Enregistrer et supprimer</button><button type="button" class="op-secondary" id="r-no">Annuler</button></div></div>`;
            $p('#r-no').addEventListener('click', () => ($p('#r-confirm').innerHTML = ''));
            $p('#r-ok').addEventListener('click', async () => { try { await persist(values); Object.assign(v, values); } catch (e) { showFormError(pane, e.message); } });
            return;
          }
        }
        await persist(values);
        Object.assign(v, values);
      } catch (e) { showFormError(pane, e.message); }
    });

    $p('#r-run').addEventListener('click', () => {
      $p('#r-confirm').innerHTML = `<div class="op-inline-form"><h3>Lancer la purge maintenant ?</h3>
        <p class="op-hint">Applique dès maintenant la règle <b>enregistrée</b> (${v.retention_days > 0 ? `${v.retention_days} jours` : 'aucune suppression de conversations'}) et purge le journal trop ancien. Cette action est irréversible.</p>
        <div class="op-pane-actions"><button type="button" class="op-primary op-danger" id="r-go">Lancer la purge</button><button type="button" class="op-secondary" id="r-no">Annuler</button></div></div>`;
      $p('#r-no').addEventListener('click', () => ($p('#r-confirm').innerHTML = ''));
      $p('#r-go').addEventListener('click', async () => {
        try {
          const out = await send('/api/admin/retention/run', 'POST');
          $p('#r-confirm').innerHTML = '';
          O.toast(`Purge terminée : ${out.chats} conversation(s), ${out.audit} ligne(s) de journal supprimée(s).`);
        } catch (e) { showFormError(pane, e.message); }
      });
    });
  }

  // ── Charge : partage du GPU avec l'agent vocal ────────────────────────────
  // ── Connexions API (Wipsos…) ──────────────────────────────────────────────────────────────────────────────
  const AUTH_LABELS = { none: 'Aucune', bearer: 'Jeton (Bearer)', header: 'Clé dans un en-tête', basic: 'Identifiant et mot de passe' };
  const TYPE_LABELS = { string: 'texte', integer: 'entier', number: 'nombre', boolean: 'booléen' };
  const TYPE_FROM = { texte: 'string', string: 'string', entier: 'integer', integer: 'integer', nombre: 'number', number: 'number', 'booléen': 'boolean', booleen: 'boolean', boolean: 'boolean' };

  const paramsToText = (params) => params.map((p) => [p.name, p.where === 'path' ? 'chemin' : 'requête', TYPE_LABELS[p.type] || 'texte',
    p.required || p.where === 'path' ? 'oui' : 'non', p.description || '', (p.enum || []).join(',')].join(' | ')).join('\n');

  function parseParams(text) {
    const params = [], errors = [];
    text.split('\n').map((l) => l.trim()).filter(Boolean).forEach((line, i) => {
      const [name = '', where = 'requête', type = 'texte', req = 'non', desc = '', en = ''] = line.split('|').map((x) => x.trim());
      if (!/^[A-Za-z_][A-Za-z0-9_.\-]{0,48}$/.test(name)) return errors.push(`Ligne ${i + 1} : nom de paramètre invalide (« ${name} »).`);
      const w = /^(chemin|path)$/i.test(where) ? 'path' : /^(requ[êe]te|query)$/i.test(where) ? 'query' : null;
      if (!w) return errors.push(`Ligne ${i + 1} : indiquez « chemin » ou « requête ».`);
      const t = TYPE_FROM[type.toLowerCase()];
      if (!t) return errors.push(`Ligne ${i + 1} : type inconnu (« ${type} »). Types : texte, entier, nombre, booléen.`);
      const p = { name, where: w, type: t, required: w === 'path' || /^(oui|yes|true|1)$/i.test(req), description: desc };
      const values = en.split(',').map((x) => x.trim()).filter(Boolean);
      if (values.length) p.enum = values;
      params.push(p);
    });
    return { params, errors };
  }

  async function renderConnections(pane) {
    let list = [];
    try { list = await O.api('/api/admin/connections'); } catch { pane.textContent = 'Chargement impossible.'; return; }
    pane.innerHTML = `
      <section>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
          <div><h3>Connexions API</h3><p class="op-hint" style="margin:0">Systèmes internes (Wipsos…) que l’assistant peut consulter, en lecture seule, pour répondre aux questions.</p></div>
          <button type="button" class="op-primary" style="padding:9px 16px;font-size:11px" id="cn-new"><i data-lucide="plus"></i> Nouvelle connexion</button>
        </div>
        ${list.length ? `<table class="op-table" style="margin-top:12px"><thead><tr><th>Connexion</th><th>Adresse</th><th>Accès</th><th>Opérations</th><th>Statut</th><th></th></tr></thead><tbody></tbody></table>`
          : `<div class="op-inline-form" style="margin-top:14px"><h3>Aucune connexion</h3><p class="op-hint" style="margin:0">Ajoutez par exemple Wipsos : adresse de l’API, authentification, puis choisissez les opérations que l’assistant peut utiliser (consultation d’un client, de ses factures, d’un tarif…).</p></div>`}
        <p class="op-hint" style="margin-top:14px">Sécurité : consultation seule (aucune modification possible), secrets chiffrés, accès par utilisateur, chaque appel est journalisé.</p>
      </section>`;
    const tbody = pane.querySelector('tbody');
    list.forEach((c) => {
      const tr = document.createElement('tr');
      const access = c.allow_all ? 'Tous les utilisateurs' : c.users.length ? `${c.users.length} utilisateur${c.users.length > 1 ? 's' : ''}` : 'Personne';
      tr.innerHTML = `<td><b></b><small></small></td><td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></td><td>${access}</td>
        <td>${c.ops_enabled} / ${c.operations.length}</td><td><span class="op-badge ${c.enabled ? '' : 'is-off'}">${c.enabled ? 'Active' : 'Désactivée'}</span></td>
        <td style="white-space:nowrap;text-align:right"><button type="button" class="op-icon" data-c="test" aria-label="Tester la connexion"><i data-lucide="activity"></i></button>
        <button type="button" class="op-icon" data-c="edit" aria-label="Modifier"><i data-lucide="pencil"></i></button>
        <button type="button" class="op-icon" data-c="delete" aria-label="Supprimer"><i data-lucide="trash-2"></i></button></td>`;
      tr.querySelector('b').textContent = c.name;
      tr.querySelector('small').textContent = c.description;
      tr.children[1].textContent = c.base_url; tr.children[1].title = c.base_url;
      tr.querySelector('[data-c=edit]').addEventListener('click', () => editConnection(pane, c));
      tr.querySelector('[data-c=test]').addEventListener('click', async () => {
        O.toast('Test en cours…');
        try { const r = await send(`/api/admin/connections/${c.id}/test`, 'POST'); O.toast(r.message); } catch (e) { O.toast(e.message); }
      });
      tr.querySelector('[data-c=delete]').addEventListener('click', () => {
        const box = pane.querySelector('section');
        box.insertAdjacentHTML('afterbegin', `<div class="op-inline-form" id="cn-del"><h3>Supprimer « ${esc(c.name)} » ?</h3><p class="op-hint">L’assistant n’aura plus accès à ce système. Les conversations passées ne sont pas modifiées.</p>
          <div class="op-pane-actions"><button type="button" class="op-primary op-danger" id="cn-del-go">Supprimer</button><button type="button" class="op-secondary" id="cn-del-no">Annuler</button></div></div>`);
        pane.querySelector('#cn-del-no').addEventListener('click', () => pane.querySelector('#cn-del').remove());
        pane.querySelector('#cn-del-go').addEventListener('click', async () => {
          try { await send(`/api/admin/connections/${c.id}`, 'DELETE'); O.toast('Connexion supprimée.'); renderConnections(pane); } catch (e) { O.toast(e.message); }
        });
      });
      tbody.appendChild(tr);
    });
    pane.querySelector('#cn-new').addEventListener('click', () => editConnection(pane, null));
    O.icons();
  }

  async function editConnection(pane, conn) {
    const isNew = !conn;
    let users = [];
    try { users = await O.api('/api/admin/users'); } catch { /* liste vide */ }
    const st = { ops: structuredClone(conn?.operations || []), users: new Set(conn?.users || []), allowAll: conn?.allow_all ?? false, filter: '' };
    const c = conn || { name: '', description: '', base_url: '', auth_type: 'bearer', auth_header: 'X-API-Key', username: '', has_secret: false, verify_tls: true, timeout_s: 15, test_path: '/', enabled: true };
    pane.innerHTML = `<div id="cn-editor">
      <button type="button" class="op-link" id="cn-back" style="margin-bottom:10px">← Retour aux connexions</button>
      <section><h3>${isNew ? 'Nouvelle connexion' : 'Connexion « ' + esc(c.name) + ' »'}</h3>
        <div class="op-grid2">
          <div class="op-field"><label for="cn-name">Nom</label><input type="text" id="cn-name" maxlength="60" placeholder="Wipsos"></div>
          <div class="op-field"><label for="cn-url">Adresse de l’API</label><input type="url" id="cn-url" placeholder="https://wipsos.amg.lan/api"></div>
        </div>
        <div class="op-field"><label for="cn-desc">À quoi sert cette connexion ?</label><textarea id="cn-desc" rows="2" maxlength="600" placeholder="CRM : clients, contrats, factures et tarifs d’Opti Sécurité."></textarea>
          <p class="op-hint" style="margin-top:4px">L’assistant s’en sert pour savoir quand consulter ce système.</p></div>
        ${toggle('cn-enabled', 'Connexion active', 'Désactivée, elle n’est proposée à personne.', c.enabled)}
      </section>
      <section><h3>Authentification</h3>
        <div class="op-grid2">
          <div class="op-field"><label for="cn-auth">Méthode</label><select id="cn-auth">${Object.entries(AUTH_LABELS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div>
          <div class="op-field" id="cn-f-header"><label for="cn-header">Nom de l’en-tête</label><input type="text" id="cn-header" placeholder="X-API-Key"></div>
          <div class="op-field" id="cn-f-user"><label for="cn-user">Identifiant</label><input type="text" id="cn-user" autocomplete="off"></div>
          <div class="op-field" id="cn-f-secret"><label for="cn-secret" id="cn-secret-label">Jeton</label><input type="password" id="cn-secret" autocomplete="new-password" placeholder="${c.has_secret ? '•••••••• (inchangé)' : ''}"></div>
        </div>
        <div class="op-grid2">
          <div class="op-field"><label for="cn-test">Chemin de test</label><input type="text" id="cn-test" placeholder="/api/ping"><p class="op-hint" style="margin-top:4px">Une adresse légère qui répond quand tout va bien.</p></div>
          <div class="op-field"><label for="cn-timeout">Délai maximal d’une requête (s)</label><input type="number" id="cn-timeout" min="3" max="60"></div>
        </div>
        ${toggle('cn-tls', 'Vérifier le certificat TLS', 'À décocher seulement pour un test. En HTTPS interne, ajoutez plutôt l’autorité de certification (OPTI_CA_BUNDLE).', c.verify_tls)}
        <div class="op-pane-actions"><button type="button" class="op-secondary" id="cn-test-go" ${isNew ? 'disabled title="Enregistrez d’abord la connexion"' : ''}>Tester la connexion</button><span class="op-hint" id="cn-test-out" style="margin:0"></span></div>
      </section>
      <section><h3>Opérations disponibles pour l’assistant</h3>
        <p class="op-hint">Chaque opération est une consultation (GET). Seules celles que vous activez sont proposées au modèle. Décrivez-les clairement : c’est ce texte qui lui dit quand et comment s’en servir.</p>
        <div class="op-pane-actions" style="margin-top:0"><button type="button" class="op-secondary" id="cn-imp-open"><i data-lucide="file-json"></i> Importer une spécification OpenAPI…</button>
          <button type="button" class="op-secondary" id="cn-op-add"><i data-lucide="plus"></i> Ajouter une opération</button></div>
        <div id="cn-imp"></div><div id="cn-opform"></div>
        <input type="text" id="cn-filter" placeholder="Filtrer les opérations…" style="margin-top:12px" hidden>
        <div id="cn-ops" style="margin-top:8px"></div>
      </section>
      <section><h3>Qui peut l’utiliser ?</h3>
        <div class="op-choices"><label><input type="radio" name="cn-access" value="all"><span>Tous les utilisateurs</span></label><label><input type="radio" name="cn-access" value="some"><span>Utilisateurs choisis</span></label></div>
        <div id="cn-users" style="margin-top:10px;max-height:170px;overflow-y:auto"></div>
        <p class="op-hint">Chaque utilisateur peut aussi désactiver une connexion pour lui, dans le menu Outils de la conversation.</p>
      </section>
      <div id="cn-foot"><div class="op-pane-actions"><button type="button" class="op-primary" id="cn-save">${isNew ? 'Créer la connexion' : 'Enregistrer'}</button><button type="button" class="op-secondary" id="cn-cancel">Annuler</button></div></div></div>`;
    const $p = (sel) => pane.querySelector(sel);
    $p('#cn-editor').addEventListener('input', () => showFormError($p('#cn-foot'), ''));      // l'erreur disparaît dès qu'on corrige
    $p('#cn-name').value = c.name; $p('#cn-url').value = c.base_url; $p('#cn-desc').value = c.description; $p('#cn-auth').value = c.auth_type;
    $p('#cn-header').value = c.auth_header || 'X-API-Key'; $p('#cn-user').value = c.username; $p('#cn-test').value = c.test_path; $p('#cn-timeout').value = c.timeout_s;

    const syncAuth = () => {
      const t = $p('#cn-auth').value;
      $p('#cn-f-header').hidden = t !== 'header'; $p('#cn-f-user').hidden = t !== 'basic'; $p('#cn-f-secret').hidden = t === 'none';
      $p('#cn-secret-label').textContent = t === 'basic' ? 'Mot de passe' : t === 'header' ? 'Clé d’API' : 'Jeton';
    };
    $p('#cn-auth').addEventListener('change', syncAuth); syncAuth();
    $p('#cn-back').addEventListener('click', () => renderConnections(pane));
    $p('#cn-cancel').addEventListener('click', () => renderConnections(pane));

    // ── accès
    const drawUsers = () => {
      pane.querySelector(`input[name=cn-access][value=${st.allowAll ? 'all' : 'some'}]`).checked = true;
      const box = $p('#cn-users');
      box.hidden = st.allowAll;
      box.innerHTML = users.filter((u) => u.active).map((u) => `<label class="op-toggle" style="padding:6px 0"><span>${esc(u.display_name)} <small>${esc(u.username)}</small></span><input type="checkbox" data-uid="${u.id}" ${st.users.has(u.id) ? 'checked' : ''}></label>`).join('') || '<p class="op-hint">Aucun utilisateur actif.</p>';
      box.querySelectorAll('input').forEach((i) => i.addEventListener('change', () => { i.checked ? st.users.add(i.dataset.uid) : st.users.delete(i.dataset.uid); }));
    };
    pane.querySelectorAll('input[name=cn-access]').forEach((r) => r.addEventListener('change', () => { st.allowAll = r.value === 'all'; drawUsers(); }));
    drawUsers();

    // ── opérations
    const drawOps = () => {
      const box = $p('#cn-ops');
      const f = st.filter.trim().toLowerCase();
      $p('#cn-filter').hidden = st.ops.length <= 8;
      const shown = st.ops.map((o, i) => [o, i]).filter(([o]) => !f || `${o.id} ${o.label} ${o.path} ${o.description}`.toLowerCase().includes(f));
      box.innerHTML = shown.length ? '' : `<p class="op-hint">${st.ops.length ? 'Aucune opération ne correspond.' : 'Aucune opération. Importez une spécification OpenAPI ou ajoutez-en une à la main.'}</p>`;
      shown.forEach(([o, i]) => {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--op-border)';
        row.innerHTML = `<label class="op-toggle" style="padding:0;flex:1;min-width:0"><span style="overflow:hidden"><b></b><small style="font-family:monospace"></small></span><input type="checkbox" ${o.enabled ? 'checked' : ''}></label>
          <button type="button" class="op-icon" data-o="try" aria-label="Essayer" ${isNew ? 'disabled' : ''}><i data-lucide="play"></i></button>
          <button type="button" class="op-icon" data-o="edit" aria-label="Modifier"><i data-lucide="pencil"></i></button>
          <button type="button" class="op-icon" data-o="del" aria-label="Retirer"><i data-lucide="x"></i></button>`;
        row.querySelector('b').textContent = o.label || o.id;
        row.querySelector('small').textContent = `GET ${o.path}`;
        row.querySelector('input').addEventListener('change', (e) => { o.enabled = e.target.checked; });
        row.querySelector('[data-o=edit]').addEventListener('click', () => opForm(i));
        row.querySelector('[data-o=del]').addEventListener('click', () => { st.ops.splice(i, 1); drawOps(); });
        row.querySelector('[data-o=try]').addEventListener('click', () => tryForm(o));
        box.appendChild(row);
      });
      O.icons();
    };
    $p('#cn-filter').addEventListener('input', (e) => { st.filter = e.target.value; drawOps(); });

    function opForm(index) {
      const o = index == null ? { id: '', label: '', description: '', method: 'GET', path: '/', params: [], enabled: true, max_chars: 8000 } : st.ops[index];
      const box = $p('#cn-opform');
      box.innerHTML = `<div class="op-inline-form"><h3>${index == null ? 'Nouvelle opération' : 'Modifier l’opération'}</h3>
        <div class="op-grid2"><div class="op-field"><label for="of-label">Titre</label><input type="text" id="of-label" maxlength="120" placeholder="Factures d’un client"></div>
          <div class="op-field"><label for="of-id">Identifiant</label><input type="text" id="of-id" maxlength="41" placeholder="factures_client"></div></div>
        <div class="op-field"><label for="of-path">Chemin (GET)</label><input type="text" id="of-path" placeholder="/clients/{client_id}/factures" style="font-family:monospace"></div>
        <div class="op-field"><label for="of-desc">Description pour l’assistant</label><textarea id="of-desc" rows="3" maxlength="800" placeholder="Liste les factures d’un client à partir de son identifiant. Filtre possible : statut."></textarea></div>
        <div class="op-field"><label for="of-params">Paramètres (un par ligne)</label><textarea id="of-params" rows="4" style="font-family:monospace;font-size:11px" placeholder="client_id | chemin | entier | oui | Identifiant du client&#10;statut | requête | texte | non | Statut de la facture | payee,impayee"></textarea>
          <p class="op-hint" style="margin-top:4px">Format : nom | chemin ou requête | texte, entier, nombre ou booléen | obligatoire (oui/non) | description | valeurs possibles (facultatif). Les {parametres} du chemin doivent être déclarés « chemin ».</p></div>
        <div class="op-field"><label for="of-max">Taille maximale du résultat donné à l’assistant (caractères)</label><input type="number" id="of-max" min="500" max="30000"></div>
        <div class="op-pane-actions"><button type="button" class="op-primary" id="of-ok">Valider</button><button type="button" class="op-secondary" id="of-no">Annuler</button></div></div>`;
      const q = (sel) => box.querySelector(sel);
      q('#of-label').value = o.label; q('#of-id').value = o.id; q('#of-path').value = o.path; q('#of-desc').value = o.description;
      q('#of-params').value = paramsToText(o.params); q('#of-max').value = o.max_chars;
      q('#of-no').addEventListener('click', () => (box.innerHTML = ''));
      q('#of-ok').addEventListener('click', () => {
        const { params, errors } = parseParams(q('#of-params').value);
        const id = q('#of-id').value.trim(), path = q('#of-path').value.trim();
        const err = errors[0]
          || (!/^[a-z][a-z0-9_]{0,40}$/.test(id) ? 'L’identifiant doit commencer par une lettre minuscule et ne contenir que des minuscules, chiffres et « _ ».' : '')
          || (!path.startsWith('/') ? 'Le chemin doit commencer par « / ».' : '')
          || (st.ops.some((x, i) => x.id === id && i !== index) ? 'Une autre opération porte déjà cet identifiant.' : '');
        if (err) return showFormError(box.firstElementChild, err);
        const inPath = [...path.matchAll(/\{([^{}]+)\}/g)].map((m) => m[1]).sort().join(',');
        if (inPath !== params.filter((p) => p.where === 'path').map((p) => p.name).sort().join(',')) return showFormError(box.firstElementChild, 'Les {parametres} du chemin doivent correspondre exactement aux paramètres de type « chemin ».');
        const next = { ...o, id, label: q('#of-label').value.trim() || id, path, description: q('#of-desc').value.trim(), params, max_chars: Number(q('#of-max').value) || 8000, method: 'GET' };
        if (index == null) st.ops.push(next); else st.ops[index] = next;
        box.innerHTML = ''; drawOps();
      });
      q('#of-label').focus();
    }
    $p('#cn-op-add').addEventListener('click', () => opForm(null));

    function tryForm(o) {
      const box = $p('#cn-opform');
      box.innerHTML = `<div class="op-inline-form"><h3>Essayer « ${esc(o.label || o.id)} »</h3>
        <p class="op-hint">Envoie une vraie requête avec les valeurs saisies et montre ce que l’assistant recevrait. L’essai est journalisé. Enregistrez d’abord vos modifications.</p>
        ${o.params.map((p, i) => `<div class="op-field"><label for="tr-${i}">${esc(p.name)}${p.required ? ' *' : ''} <small>${esc(p.description)}</small></label><input type="text" id="tr-${i}"></div>`).join('') || '<p class="op-hint">Cette opération n’a pas de paramètre.</p>'}
        <div class="op-pane-actions"><button type="button" class="op-primary" id="tr-go">Lancer l’essai</button><button type="button" class="op-secondary" id="tr-no">Fermer</button></div>
        <div id="tr-out"></div></div>`;
      box.querySelector('#tr-no').addEventListener('click', () => (box.innerHTML = ''));
      box.querySelector('#tr-go').addEventListener('click', async () => {
        const params = {};
        o.params.forEach((p, i) => { const v = box.querySelector(`#tr-${i}`).value.trim(); if (v) params[p.name] = v; });
        const out = box.querySelector('#tr-out'); out.innerHTML = '<p class="op-hint">Requête en cours…</p>';
        try {
          const r = await send(`/api/admin/connections/${conn.id}/try`, 'POST', { operation: o.id, params });
          const card = r.card;
          out.innerHTML = `<p class="op-hint" style="margin:8px 0 4px"><b></b></p><pre style="max-height:220px;overflow:auto;font-size:11px;white-space:pre-wrap;word-break:break-word;background:var(--op-bg);border:1px solid var(--op-border);border-radius:8px;padding:10px"></pre>`;
          out.querySelector('b').textContent = card.ok ? `Réponse reçue (${card.status}, ${card.ms} ms${card.count != null ? ', ' + card.count + ' élément(s)' : ''})` : `Échec : ${card.error || 'erreur'}`;
          out.querySelector('pre').textContent = r.result;
        } catch (e) { out.innerHTML = ''; showFormError(box.firstElementChild, e.message); }
      });
    }

    // ── importer une spécification
    $p('#cn-imp-open').addEventListener('click', () => {
      const box = $p('#cn-imp');
      box.innerHTML = `<div class="op-inline-form"><h3>Importer une spécification OpenAPI / Swagger</h3>
        <p class="op-hint">Indiquez l’adresse du fichier (souvent …/openapi.json ou …/swagger.json) ou collez son contenu (JSON ou YAML). Seules les opérations de consultation (GET) sont importées, et aucune n’est activée : vous choisissez ensuite.</p>
        <div class="op-field"><label for="im-url">Adresse de la spécification</label><input type="url" id="im-url" placeholder="https://wipsos.amg.lan/openapi.json"></div>
        <div class="op-field"><label for="im-text">… ou contenu collé</label><textarea id="im-text" rows="4" style="font-family:monospace;font-size:11px"></textarea></div>
        <div class="op-pane-actions"><button type="button" class="op-primary" id="im-go">Lire</button><button type="button" class="op-secondary" id="im-no">Annuler</button></div></div>`;
      box.querySelector('#im-no').addEventListener('click', () => (box.innerHTML = ''));
      box.querySelector('#im-go').addEventListener('click', async () => {
        const url = box.querySelector('#im-url').value.trim(), text = box.querySelector('#im-text').value.trim();
        if (!url && !text) return showFormError(box.firstElementChild, 'Indiquez une adresse ou collez le contenu de la spécification.');
        try {
          const r = await send('/api/admin/connections/import', 'POST', { url: url || null, text: text || null, connection_id: conn?.id || null });
          const known = new Set(st.ops.map((o) => o.id));
          const added = r.operations.filter((o) => !known.has(o.id));
          st.ops.push(...added);
          if (!$p('#cn-url').value.trim() && r.base_url) $p('#cn-url').value = r.base_url;
          if (!$p('#cn-name').value.trim() && r.title) $p('#cn-name').value = r.title.slice(0, 60);
          box.innerHTML = ''; drawOps();
          O.toast(`${added.length} opération(s) de consultation importée(s)${r.skipped ? `, ${r.skipped} ignorée(s)` : ''}. Activez celles que l’assistant peut utiliser.`);
        } catch (e) { showFormError(box.firstElementChild, e.message); }
      });
    });
    drawOps();

    // ── test, enregistrement
    if (!isNew) $p('#cn-test-go').addEventListener('click', async () => {
      const out = $p('#cn-test-out'); out.textContent = 'Test en cours…';
      try { const r = await send(`/api/admin/connections/${conn.id}/test`, 'POST'); out.textContent = r.message; out.style.color = r.ok ? '#2f7d4f' : '#a75751'; }
      catch (e) { out.textContent = e.message; out.style.color = '#a75751'; }
    });
    $p('#cn-save').addEventListener('click', async () => {
      showFormError($p('#cn-foot'), '');
      const type = $p('#cn-auth').value;
      const body = { name: $p('#cn-name').value.trim(), description: $p('#cn-desc').value.trim(), base_url: $p('#cn-url').value.trim(), auth_type: type,
        auth_header: type === 'header' ? $p('#cn-header').value.trim() : '', username: type === 'basic' ? $p('#cn-user').value.trim() : '',
        verify_tls: $p('#cn-tls').checked, timeout_s: Number($p('#cn-timeout').value) || 15, test_path: $p('#cn-test').value.trim() || '/',
        enabled: $p('#cn-enabled').checked, allow_all: st.allowAll, users: [...st.users], operations: st.ops };
      const secret = $p('#cn-secret').value;
      if (type === 'none') body.secret = '';
      else if (secret !== '') body.secret = secret;
      else if (isNew || !c.has_secret) return showFormError($p('#cn-foot'), type === 'basic' ? 'Indiquez le mot de passe.' : 'Indiquez le jeton ou la clé d’API.');
      if (body.name.length < 2) return showFormError($p('#cn-foot'), 'Donnez un nom à la connexion (2 caractères au moins).');
      if (!body.base_url) return showFormError($p('#cn-foot'), 'Indiquez l’adresse de l’API.');
      if (type === 'header' && !body.auth_header) return showFormError($p('#cn-foot'), 'Indiquez le nom de l’en-tête qui porte la clé.');
      try {
        const saved = await send(isNew ? '/api/admin/connections' : `/api/admin/connections/${conn.id}`, isNew ? 'POST' : 'PUT', body);
        O.toast('Connexion enregistrée.');
        if (isNew) { const t = await send(`/api/admin/connections/${saved.id}/test`, 'POST').catch(() => null); if (t) O.toast(t.message); }
        renderConnections(pane);
      } catch (e) { showFormError($p('#cn-foot'), e.message); }
    });
    O.icons();
  }

  async function renderLoad(pane) {
    const { values: v } = await loadAppSettings();
    pane.innerHTML = `
      <section><h3>État en direct</h3>
        <p class="op-hint">Le GPU est partagé avec l’agent vocal. L’assistant ne prend que les places d’Ollama que les appels en cours ne réclament pas ; les autres demandes patientent en file, avec un message.</p>
        <div class="op-kpis" id="ld-kpis"></div></section>
      <section><h3>Réglages</h3>
        <div class="op-grid2">
          <div class="op-field"><label for="ld-slots">Places d’Ollama (OLLAMA_NUM_PARALLEL)</label><input type="number" id="ld-slots" min="1" max="64"></div>
          <div class="op-field"><label for="ld-reserve">Réserve pour l’agent vocal (places)</label><input type="number" id="ld-reserve" min="0" max="8"></div>
          <div class="op-field"><label for="ld-cap">Plafond de places pour l’assistant</label><input type="number" id="ld-cap" min="1" max="32"></div>
          <div class="op-field"><label for="ld-user">Places simultanées par utilisateur</label><input type="number" id="ld-user" min="1" max="8"></div>
          <div class="op-field"><label for="ld-wait">Attente maximale avant abandon (secondes)</label><input type="number" id="ld-wait" min="30" max="3600"></div>
        </div>
        <p class="op-hint"><b>Places de l’assistant</b> = places d’Ollama − appels en cours − réserve, au moins 1 et au plus le plafond. Une conversation ordinaire prend 1 place ; la lecture complète d’un long document en prend 2. Les appels sont repérés par les connexions ouvertes sur le STT et le TTS de l’agent vocal (ports 8080 et 8089).</p>
      </section>
      <div class="op-pane-actions"><button type="button" class="op-primary" id="ld-save">Enregistrer</button></div>`;
    const $p = (sel) => pane.querySelector(sel);
    $p('#ld-slots').value = v.ollama_slots; $p('#ld-reserve').value = v.voice_reserve; $p('#ld-cap').value = v.max_assistant_slots;
    $p('#ld-user').value = v.max_per_user; $p('#ld-wait').value = v.queue_timeout_seconds;

    const kpi = (n, label) => `<div class="op-kpi"><strong>${n}</strong><span>${label}</span></div>`;
    async function refresh() {
      try {
        const l = await O.api('/api/admin/load');
        const box = pane.querySelector('#ld-kpis');
        if (!box) return false;
        box.innerHTML = kpi(l.voice_calls, 'appels en cours') + kpi(l.capacity, 'places pour l’assistant') + kpi(l.used, 'places utilisées') + kpi(l.queued, 'demandes en attente');
      } catch { /* on réessaie au prochain tour */ }
      return true;
    }
    await refresh();
    const timer = setInterval(async () => { if (!pane.isConnected || !(await refresh())) clearInterval(timer); }, 3000);

    $p('#ld-save').addEventListener('click', async () => {
      const n = (id) => Number($p(id).value);
      const values = { ollama_slots: n('#ld-slots'), voice_reserve: n('#ld-reserve'), max_assistant_slots: n('#ld-cap'), max_per_user: n('#ld-user'), queue_timeout_seconds: n('#ld-wait') };
      showFormError(pane, '');
      const bad = Object.entries(values).find(([, x]) => !Number.isInteger(x));
      if (bad) return showFormError(pane, 'Toutes les valeurs doivent être des nombres entiers.');
      try { await saveAppSettings(values); O.toast('Réglages de charge enregistrés.'); }
      catch (e) { showFormError(pane, e.message); }
    });
  }

  // ── Journal d'audit ───────────────────────────────────────────────────────
  const ACTIONS = {
    'auth.login': 'Connexion', 'auth.login_failed': 'Échec de connexion', 'auth.locked': 'Compte bloqué (trop d’essais)', 'auth.logout': 'Déconnexion',
    'user.create': 'Compte créé', 'user.update': 'Compte modifié', 'user.delete': 'Compte supprimé', 'settings.update': 'Réglages modifiés',
    'chat.message': 'Message envoyé', 'chat.delete': 'Conversation supprimée', 'file.upload': 'Fichier déposé',
    'data.export': 'Export des données', 'data.delete_all': 'Historique supprimé',
    'system.retention_purge': 'Purge automatique', 'system.retention_run': 'Purge manuelle', 'system.audit_export': 'Export du journal',
  };
  function fmtDetail(raw) {
    if (!raw) return '';
    let d; try { d = JSON.parse(raw); } catch { return raw; }
    return Object.entries(d).map(([k, x]) => (x && typeof x === 'object' && 'avant' in x ? `${k} : ${x.avant} → ${x.après}` : `${k} : ${x}`)).join(' · ');
  }

  async function renderAudit(pane) {
    const { values: v } = await loadAppSettings();
    pane.innerHTML = `
      <section><h3>Journal d’audit</h3>
        <p class="op-hint">Qui a fait quoi, quand, depuis quelle adresse. Ce sont des événements : le contenu des conversations n’y figure jamais. Conservé ${v.audit_retention_days} jours.</p>
        <div class="op-audit-filters">
          <select id="au-cat" aria-label="Catégorie"><option value="">Toutes les catégories</option></select>
          <input type="text" id="au-q" placeholder="Rechercher un utilisateur, un fichier…" aria-label="Recherche">
          <a class="op-secondary" id="au-csv" style="text-decoration:none" href="/api/admin/audit/export.csv" download>Exporter en CSV</a>
        </div>
        <table class="op-table"><thead><tr><th>Date</th><th>Acteur</th><th>Action</th><th>Cible</th><th>Détail</th><th>Adresse</th></tr></thead><tbody></tbody></table>
        <p class="op-hint" id="au-empty" hidden>Aucun événement.</p>
        <div class="op-pane-actions"><button type="button" class="op-secondary" id="au-more" hidden>Charger plus</button></div>
      </section>`;
    const $p = (sel) => pane.querySelector(sel);
    let next = null;

    const query = (extra = '') => {
      const p = new URLSearchParams();
      if ($p('#au-cat').value) p.set('category', $p('#au-cat').value);
      if ($p('#au-q').value.trim()) p.set('q', $p('#au-q').value.trim());
      return p.toString() + extra;
    };
    async function load(reset) {
      const tbody = $p('tbody');
      if (reset) { tbody.replaceChildren(); next = null; }
      let data;
      try { data = await O.api(`/api/admin/audit?${query(next ? `&before=${next}` : '')}`); } catch { return O.toast('Chargement du journal impossible.'); }
      if ($p('#au-cat').options.length === 1) Object.entries(data.categories).forEach(([k, label]) => $p('#au-cat').add(new Option(label, k)));
      data.rows.forEach((r) => {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td style="white-space:nowrap"></td><td></td><td></td><td></td><td></td><td></td>';
        const c = tr.children;
        c[0].textContent = new Date(r.ts).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'medium' });
        c[1].textContent = r.actor || '—';
        c[2].textContent = ACTIONS[r.action] || r.action;
        if (r.action === 'auth.login_failed' || r.action === 'auth.locked') c[2].innerHTML = `<span class="op-badge is-off"></span>`, c[2].firstChild.textContent = ACTIONS[r.action];
        c[3].textContent = r.target;
        c[3].style.cssText = 'max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
        c[3].title = r.target;
        c[4].textContent = fmtDetail(r.detail);
        c[5].textContent = r.ip;
        tbody.appendChild(tr);
      });
      next = data.next;
      $p('#au-more').hidden = !next;
      $p('#au-empty').hidden = tbody.children.length > 0;
      $p('#au-csv').href = `/api/admin/audit/export.csv?${query()}`;
    }
    let timer = null;
    $p('#au-cat').addEventListener('change', () => load(true));
    $p('#au-q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => load(true), 300); });
    $p('#au-more').addEventListener('click', () => load(false));
    await load(true);
  }

  async function renderStats(pane) {
    let s;
    try { s = await O.api('/api/admin/stats'); } catch { pane.textContent = 'Chargement impossible.'; return; }
    const max = Math.max(1, ...s.per_day.map((d) => d.messages));
    const kpi = (n, label) => `<div class="op-kpi"><strong>${n.toLocaleString('fr-FR')}</strong><span>${label}</span></div>`;
    pane.innerHTML = `
      <section><h3>Vue d’ensemble</h3><p class="op-hint">Sur les 30 derniers jours, sauf mention contraire.</p>
        <div class="op-kpis">
          ${kpi(s.users, 'comptes')}${kpi(s.active_users_7d, 'actifs sur 7 jours')}${kpi(s.chats, 'conversations au total')}
          ${kpi(s.messages_30d, 'questions posées')}${kpi(s.searches_30d, 'recherches web')}${kpi(s.analyses_30d, 'calculs sur fichiers')}${kpi(s.files_30d, 'fichiers joints')}
        </div></section>
      <section><h3>Questions par jour</h3><p class="op-hint">14 derniers jours.</p>
        <div class="op-bars">${s.per_day.map((d) => `<div title="${d.messages} question(s) le ${new Date(d.day).toLocaleDateString('fr-FR')}"><b>${d.messages || ''}</b><i style="height:${(d.messages / max) * 100}%"></i></div>`).join('')}</div>
        <div class="op-bars-labels">${s.per_day.map((d) => `<span>${new Date(d.day).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })}</span>`).join('')}</div></section>
      <section><h3>Utilisateurs les plus actifs</h3>
        ${s.top_users_30d.length ? `<table class="op-table"><tbody>${s.top_users_30d.map((u) => `<tr><td>${esc(u.name)}</td><td style="text-align:right">${u.messages} question(s)</td></tr>`).join('')}</tbody></table>` : '<p class="op-hint">Aucune activité.</p>'}</section>`;
  }

  // ── Branchement sur les boutons existants ─────────────────────────────────
  O.actions.settings = () => {
    document.querySelector('.op-profile-menu').hidden = true;
    tabbed('Réglages', userTabs);
  };
  O.actions.admin = () => {
    document.querySelector('.op-profile-menu').hidden = true;
    if (!O.state.me.is_admin) return O.toast('Réservé aux administrateurs.');
    tabbed('Administration', adminTabs);
  };
})();
