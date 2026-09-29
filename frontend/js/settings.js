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
  const errMsg = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    if (Array.isArray(data.detail)) return 'Valeur invalide : ' + data.detail.map((d) => d.loc?.at(-1)).join(', ');
    return data.detail || 'Action impossible.';
  };
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
        ['tone', 'length'].forEach((name) => pane.querySelectorAll(`input[name=${name}]`)
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
        pane.innerHTML = `
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
          ${isNew ? '<div class="op-field"><label for="f-username">Identifiant</label><input type="text" id="f-username" placeholder="prenom.nom" autocomplete="off"></div>' : ''}
          <div class="op-field"><label for="f-name">Nom affiché</label><input type="text" id="f-name" placeholder="Prénom Nom"></div>
          ${isNew ? '<div class="op-field"><label for="f-pass">Mot de passe</label><input type="password" id="f-pass" autocomplete="new-password"></div>' : ''}
        </div>
        ${u && u.id === O.state.me.id ? '' : toggle('f-admin', 'Administrateur', 'Accès à cette page d’administration', u?.is_admin)}
        <div class="op-pane-actions"><button type="button" class="op-primary" id="f-save">${isNew ? 'Créer le compte' : 'Enregistrer'}</button>
          <button type="button" class="op-secondary" id="f-cancel">Annuler</button></div>
      </div>`;
    if (u) box.querySelector('#f-name').value = u.display_name;
    box.querySelector('input').focus();
    box.querySelector('#f-cancel').addEventListener('click', () => (box.innerHTML = ''));
    box.querySelector('#f-save').addEventListener('click', async () => {
      const body = { display_name: box.querySelector('#f-name').value.trim() };
      const adm = box.querySelector('#f-admin');
      if (adm) body.is_admin = adm.checked;
      try {
        if (isNew) {
          body.username = box.querySelector('#f-username').value.trim();
          body.password = box.querySelector('#f-pass').value;
          await send('/api/admin/users', 'POST', body);
          O.toast(`Compte ${body.username} créé.`);
        } else {
          await send(`/api/admin/users/${u.id}`, 'PATCH', body);
          O.toast('Compte mis à jour.');
        }
        renderUsers(pane);
      } catch (e) { O.toast(e.message); }
    });
  }

  async function userAction(kind, u, pane) {
    const box = pane.querySelector('#op-user-form');
    if (kind === 'edit') return userForm(pane, u);
    if (kind === 'password') {
      box.innerHTML = `<div class="op-inline-form"><h3 style="margin-bottom:12px">Nouveau mot de passe pour ${esc(u.username)}</h3>
        <div class="op-field"><input type="password" id="f-newpass" autocomplete="new-password" placeholder="12 caractères minimum"></div>
        <p class="op-hint">L’utilisateur sera déconnecté de toutes ses sessions.</p>
        <div class="op-pane-actions"><button type="button" class="op-primary" id="f-go">Changer le mot de passe</button><button type="button" class="op-secondary" id="f-cancel">Annuler</button></div></div>`;
      box.querySelector('#f-newpass').focus();
      box.querySelector('#f-cancel').addEventListener('click', () => (box.innerHTML = ''));
      box.querySelector('#f-go').addEventListener('click', async () => {
        try { await send(`/api/admin/users/${u.id}`, 'PATCH', { password: box.querySelector('#f-newpass').value }); O.toast('Mot de passe modifié.'); box.innerHTML = ''; }
        catch (e) { O.toast(e.message); }
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
        <div class="op-grid2">
          <div class="op-field"><label for="f-max">Taille maximale (Mo)</label><input type="number" id="f-max" min="1" max="100"></div>
          <div class="op-field"><label for="a-steps">Essais de calcul par question</label><input type="number" id="a-steps" min="1" max="5"></div>
          <div class="op-field"><label for="a-timeout">Durée maximale d’un calcul (s)</label><input type="number" id="a-timeout" min="10" max="300"></div>
        </div></section>
      <div class="op-pane-actions"><button type="button" class="op-primary" id="x-save">Enregistrer</button></div>`;
    const $p = (s) => pane.querySelector(s);
    $p('#w-results').value = v.web_results;
    $p('#w-pages').value = v.web_pages_read;
    $p('#w-url').value = v.searxng_url;
    $p('#f-max').value = v.max_upload_mb;
    $p('#a-steps').value = v.max_analysis_steps;
    $p('#a-timeout').value = v.sandbox_timeout;
    $p('#x-save').addEventListener('click', async () => {
      try {
        await saveAppSettings({
          web_enabled: $p('#w-on').checked, web_results: Number($p('#w-results').value), web_pages_read: Number($p('#w-pages').value),
          searxng_url: $p('#w-url').value.trim(), uploads_enabled: $p('#f-on').checked, analysis_enabled: $p('#a-on').checked,
          max_upload_mb: Number($p('#f-max').value), max_analysis_steps: Number($p('#a-steps').value), sandbox_timeout: Number($p('#a-timeout').value),
        });
        O.toast('Réglages enregistrés.');
      } catch (e) { O.toast(e.message); }
    });
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
