/* IA Launcher — interface. Vanilla JS, pas de dépendance.
   Sections : machine (barre latérale), moteur, modèle, mémoire, réglages, lancement, chat. */
'use strict';

// ------------------------------------------------------------------ utilitaires
const $ = (id) => document.getElementById(id);
const GiB = 1024 ** 3, MiB = 1024 ** 2;
const fmtG = (b) => (b / GiB).toFixed(b < GiB ? 2 : 1).replace('.', ',') + ' Gio';
const fmtB = (b) => b >= GiB ? fmtG(b) : (b / MiB).toFixed(0) + ' Mio';
const fmtN = (n) => Number(n).toLocaleString('fr-FR');
const esc = (s) => String(s ?? '').replace(/[<>&"]/g, c => ({'<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;'}[c]));
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
}
let toastT;
function toast(msg, ms = 3500) { const t = $('toast'); t.textContent = msg; t.classList.remove('hidden'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.add('hidden'), ms); }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ------------------------------------------------------------------ état
const S = {
  hw: null, engine: null, models: [], catalog: null, presets: [],
  model: null,      // {id} local ou {repo,file,size,mmproj} distant
  modelInfo: null,  // entrée de liste (nom, taille, source…)
  profile: null,    // profil mémoire (arch, couches…)
  cfg: null,        // réglages courants
  est: null,        // dernière estimation
  server: null,
  chat: [],
  helpOpen: new Set(),
};

// ------------------------------------------------------------------ machine
async function refreshHardware(force) {
  S.hw = await api('/api/hardware' + (force ? '?refresh=1' : ''));
  renderMachine(S.hw);
  $('machine-notes').innerHTML = (S.hw.notes || []).map(n => '<div>⚠ ' + esc(n) + '</div>').join('');
}
function gauge(name, used, total, sub, color) {
  const pct = total ? Math.min(100, Math.round(used * 100 / total)) : 0;
  return `<div class="stat"><div class="stat-h"><span>${esc(name)}</span><span class="stat-v">${fmtG(used)} / ${fmtG(total)}</span></div>
    <div class="bar"><div style="width:${pct}%;background:${color || 'var(--acc)'}"></div></div><div class="stat-s">${esc(sub)}</div></div>`;
}
function renderMachine(hw) {
  let h = '';
  for (const g of hw.gpus) {
    h += gauge(g.name, g.vram_used, g.vram_total, `VRAM · ${g.util != null ? 'charge ' + g.util + ' % · ' : ''}${g.temp != null ? g.temp + ' °C' : ''}${g.compute_cap ? ' · sm ' + g.compute_cap : ''}`);
  }
  if (!hw.gpus.length) h += '<div class="stat"><span class="stat-s">Aucun GPU dédié détecté — mode CPU.</span></div>';
  h += gauge('Mémoire vive (RAM)', hw.ram.total - hw.ram.available, hw.ram.total, `${Math.round((hw.ram.total - hw.ram.available) * 100 / hw.ram.total)} % utilisée${hw.ram.host_total && hw.ram.host_total > hw.ram.total * 1.15 ? ` · le PC en a ${fmtG(hw.ram.host_total)}` : ''}`, 'var(--ok)');
  h += gauge('SSD (dossier modèles)', hw.disk.used, hw.disk.total, fmtG(hw.disk.free) + ' libres', 'var(--c-cache)');
  h += `<div class="stat"><div class="stat-h"><span>${esc(hw.cpu.name)}</span></div><div class="stat-s">${hw.cpu.cores} cœurs / ${hw.cpu.threads} threads${hw.cpu.avx512 ? ' · AVX-512' : hw.cpu.avx2 ? ' · AVX2' : ''} · ${esc(hw.os)}${hw.wsl ? ' (WSL2)' : ''}</div></div>`;
  $('machine').innerHTML = h;
}
async function liveTick() {
  if (!S.hw) return;
  try {
    const l = await api('/api/live');
    for (const g of S.hw.gpus) { const u = l.gpus.find(x => x.index === g.index); if (u) Object.assign(g, u); }
    S.hw.ram = l.ram; S.hw.disk = l.disk;
    renderMachine(S.hw);
  } catch (e) { /* silencieux */ }
}

// ------------------------------------------------------------------ moteur
async function refreshEngine() {
  S.engine = await api('/api/engine');
  renderEngine();
}
function renderEngine() {
  const e = S.engine, p = e.plan;
  const pill = $('pill-engine');
  pill.textContent = e.installed ? `moteur : ${e.variant || 'ok'} ${e.version}` : 'moteur absent';
  pill.className = 'pill ' + (e.installed ? 'ok' : 'err');
  let h = '';
  if (e.installed) {
    h += `<div class="plan"><div><b>Installé :</b> ${esc(e.variant)} — <span class="mono small">${esc(e.version)}</span><br>
      <span class="muted small mono">${esc(e.bin)}</span><br>
      <span class="small">Périphériques vus par le moteur : ${e.devices.length ? e.devices.map(d => `<b>${esc(d.name)}</b> (${esc(d.id)}, ${fmtG(d.free)} libres)`).join(', ') : '<span class="warn">aucun GPU — le moteur tournera sur CPU</span>'}</span></div>
      <div><button class="btn small" onclick="toggle('engine-more')">changer / mettre à jour</button></div></div>`;
    h += '<div id="engine-more" class="hidden" style="margin-top:10px">';
  }
  h += `<div class="plan"><div><b>Conseillé pour cette machine :</b> ${esc(p.label)}<br><span class="muted small">${esc(p.reason)}</span></div>
    <div><button class="btn primary" onclick="installEngine('${p.method}','${p.variant}','${esc(p.label)}')">${e.installed ? 'Réinstaller' : 'Installer automatiquement'}</button></div></div>`;
  if (p.alternatives.length) {
    h += '<div class="row-inline small muted">Autres variantes : ' + p.alternatives.map(a =>
      `<button class="btn small" onclick="installEngine('${a.method}','${a.variant}','${esc(a.label)}')">${esc(a.label)}</button>`).join(' ') + '</div>';
  }
  h += `<div class="row-inline small muted">Ou un llama-server déjà présent : <input id="engine-custom" placeholder="/chemin/vers/llama-server" size="36"> <button class="btn small" onclick="customEngine()">utiliser</button></div>`;
  if (e.installed_bins.length > 1) {
    h += '<div class="small muted">Versions installées : ' + e.installed_bins.map(b => `<button class="link" onclick="selectEngine('${esc(b)}')">${esc(b.split('/').slice(-2, -1)[0])}</button>`).join(' · ') + '</div>';
  }
  if (e.installed) h += '</div>';
  h += '<div id="engine-job"></div>';
  $('engine').innerHTML = h;
}
function toggle(id) { $(id).classList.toggle('hidden'); }
async function installEngine(method, variant, label) {
  if (!confirm(`Installer le moteur « ${label} » ?\n${method === 'build' ? 'Compilation locale : 5 à 15 minutes selon le processeur.' : 'Téléchargement de quelques dizaines à centaines de Mo.'}`)) return;
  try { const r = await api('/api/engine/install', {method, variant, label}); watchJob(r.job.id, 'engine-job', refreshEngine); }
  catch (e) { toast('Erreur : ' + e.message); }
}
async function customEngine() { try { S.engine = await api('/api/engine/custom', {path: $('engine-custom').value.trim()}); renderEngine(); toast('Moteur enregistré'); } catch (e) { toast(e.message); } }
async function selectEngine(path) { S.engine = await api('/api/engine/select', {path}); renderEngine(); }

// ------------------------------------------------------------------ tâches de fond
const JOBS = {};
function watchJob(id, boxId, onDone) {
  JOBS[id] = {boxId, onDone};
  pollJobs();
}
let jobTimer;
async function pollJobs() {
  clearTimeout(jobTimer);
  const ids = Object.keys(JOBS);
  if (!ids.length) return;
  for (const id of ids) {
    try {
      const j = await api('/api/jobs/' + id);
      const box = $(JOBS[id].boxId);
      if (box) box.innerHTML = renderJob(j);
      if (j.state !== 'running') {
        const cb = JOBS[id].onDone; delete JOBS[id];
        if (j.state === 'error') toast('Échec : ' + j.error, 8000); else if (j.state === 'done') toast(j.label + ' — terminé');
        if (cb) cb(j);
      }
    } catch (e) { delete JOBS[id]; }
  }
  if (Object.keys(JOBS).length) jobTimer = setTimeout(pollJobs, 1000);
}
function renderJob(j) {
  const pct = Math.round(j.progress * 100);
  return `<div class="stat"><div class="stat-h"><span>${esc(j.label)}</span><span class="stat-v">${j.state === 'running' ? pct + ' %' : j.state}</span></div>
    <div class="progress"><div style="width:${pct}%"></div></div><div class="stat-s">${esc(j.detail)} ${j.state === 'running' ? `<button class="link" onclick="cancelJob('${j.id}')">annuler</button>` : ''}</div>
    ${j.log.length ? `<details><summary class="small muted">journal</summary><div class="joblog">${esc(j.log.slice(-40).join('\n'))}</div></details>` : ''}
    ${j.state === 'error' ? `<div class="warn-item error">${esc(j.error)}</div>` : ''}</div>`;
}
async function cancelJob(id) { await api(`/api/jobs/${id}/cancel`, {}); }

// ------------------------------------------------------------------ modèles
function showTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.toggle('hidden', p.id !== 'tab-' + name));
  if (name === 'catalog' && !S.catalog) loadCatalog();
}
async function refreshModels() {
  const d = await api('/api/models');
  S.models = d.models; S.modelsDirBytes = d.models_dir_bytes;
  $('paths').innerHTML = `Modèles téléchargés : <span class="mono">${esc(d.models_dir)}</span><br>Sources analysées : ${d.dirs.map(x => esc(x.label)).join(', ')}`;
  renderLocalModels();
}
function renderLocalModels() {
  if (!S.models.length) { $('models-local').innerHTML = '<p class="muted">Aucun fichier .gguf trouvé. Passez par le catalogue pour en télécharger un.</p>'; return; }
  $('models-local').innerHTML = '<div class="mlist">' + S.models.map(m => `
    <div class="mitem ${S.model && S.model.id === m.id ? 'sel' : ''}" onclick="selectLocal('${m.id}')">
      <div class="n">${esc(m.name)}</div>
      <div class="s"><span>${fmtG(m.size)}</span><span class="badge">${esc(m.source)}</span>${m.mmproj ? '<span class="badge acc">vision</span>' : ''}${m.draft ? '<span class="badge warn" title="brouillon pour le décodage spéculatif, à choisir dans les réglages avancés — pas un modèle à lancer seul">brouillon MTP</span>' : ''}${m.shards > 1 ? `<span class="badge">${m.shards} fragments</span>` : ''}</div>
    </div>`).join('') + '</div>';
}
async function selectLocal(id) {
  const m = S.models.find(x => x.id === id);
  if (m && m.draft) { toast('Ceci est un brouillon MTP : choisissez le modèle principal, puis le brouillon dans Réglages avancés → Décodage spéculatif', 6000); return; }
  S.model = {id}; S.modelInfo = m;
  renderLocalModels();
  $('model-selected').innerHTML = '<p class="muted">Lecture de l’en-tête GGUF…</p>';
  try {
    const d = await api('/api/models/profile?id=' + id);
    S.profile = d.profile;
    renderProfile();
    if (!S.cfg) S.cfg = defaultCfg();
    await recommendCfg(true);
    renderAgents();
  } catch (e) { $('model-selected').innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; }
}
function renderProfile() {
  const p = S.profile, m = S.modelInfo;
  const kinds = p.layer_kinds || [];
  const nAttn = kinds.filter(k => k === 'attn').length, nSwa = kinds.filter(k => k === 'swa').length, nRec = kinds.filter(k => k === 'recurrent').length;
  const arch = [];
  if (p.is_moe) arch.push(`MoE : ${p.n_expert} experts, ${p.n_expert_used} actifs par jeton (experts = ${fmtG(p.expert_bytes)})`);
  if (nRec) arch.push(`attention hybride : ${nAttn} couches d’attention complète + ${nRec} couches récurrentes (cache KV réduit)`);
  if (nSwa) arch.push(`fenêtre glissante (${fmtN(p.sliding_window)} jetons) sur ${nSwa} couches, ${nAttn} couches complètes`);
  if (p.per_layer_embd) arch.push(p.ngram_embd ? `${fmtG(p.per_layer_embd)} d’embeddings n-gram, lus à la demande depuis le SSD (ne comptent ni en VRAM ni en RAM)` : `${fmtG(p.per_layer_embd)} d’embeddings par couche lus à la demande (SSD)`);
  if (p.vision || m.mmproj) arch.push('vision (mmproj)');
  const kvpt = kvPerToken('f16', 'f16');
  $('model-selected').innerHTML = `
    <div class="repo"><b>${esc(p.name || m.name)}</b> <span class="badge">${esc(p.quant)}</span> <span class="badge">${esc(p.arch)}</span>
      ${p.approx ? '<div class="warn-item info">Modèle fragmenté : tailles des couches extrapolées depuis le premier fragment.</div>' : ''}
      <div class="profile">
        <div class="kv"><b>Paramètres</b>${p.n_params ? (p.n_params / 1e9).toFixed(1).replace('.', ',') + ' milliards' : esc(p.size_label || '?')}</div>
        <div class="kv"><b>Poids sur disque</b>${fmtG(p.file_size)}</div>
        <div class="kv"><b>Couches</b>${p.n_layer}</div>
        <div class="kv"><b>Contexte d’entraînement</b>${fmtN(p.n_ctx_train)} jetons</div>
        <div class="kv"><b>Cache KV (f16)</b>${(kvpt / 1024).toFixed(1).replace('.', ',')} Kio / jeton</div>
        <div class="kv"><b>Vocabulaire</b>${fmtN(p.n_vocab)}</div>
      </div>
      ${arch.length ? '<div class="small muted" style="margin-top:8px">' + arch.map(a => '• ' + esc(a)).join('<br>') + '</div>' : ''}
      <div class="small muted" style="margin-top:6px">${m.path ? 'Fichier : <span class="mono">' + esc(m.path) + '</span>' : 'Pas encore téléchargé — les réglages et l’estimation fonctionnent quand même.'}</div>
    </div>`;
}
function kvPerToken(tk, tv) {
  // coût du cache KV par jeton sur les couches à attention complète (les couches
  // à fenêtre glissante ou récurrentes ne dépendent pas du contexte)
  const p = S.profile; if (!p) return 0;
  const bytes = {f32: 4, f16: 2, bf16: 2, q8_0: 34 / 32, q4_0: 18 / 32, q4_1: 20 / 32, iq4_nl: 18 / 32, q5_0: 22 / 32, q5_1: 24 / 32};
  return (p.k_elems || 0) * (bytes[tk] || 2) + (p.v_elems || 0) * (bytes[tv] || 2);
}
async function addDir() {
  try { await api('/api/models/dirs', {path: $('add-dir').value.trim()}); $('add-dir').value = ''; await refreshModels(); toast('Dossier ajouté'); } catch (e) { toast(e.message); }
}

// ---- catalogue / HF
async function loadCatalog() {
  S.catalog = await api('/api/catalog');
  const vram = S.hw && S.hw.gpus.length ? S.hw.gpus[0].vram_total : 0;
  $('catalog').innerHTML = `<p class="muted small">Étiquettes : ${Object.entries(S.catalog.tags).map(([k, v]) => `<span class="badge" title="${esc(v)}">${k}</span>`).join(' ')} — survolez pour l’explication. ${vram ? `Votre GPU a ${fmtG(vram)} de VRAM : un modèle « tient » si ses poids en Q4 laissent 2-4 Gio pour le contexte.` : ''}</p>
    <div class="cat">` + S.catalog.entries.map(c => `
    <div class="mitem" onclick="openRepo('${esc(c.repo)}')">
      <div class="n">${esc(c.name)} <span class="muted small mono">${esc(c.repo)}</span></div>
      <div class="s">${c.tags.map(t => `<span class="badge" title="${esc(S.catalog.tags[t] || '')}">${t}</span>`).join('')}</div>
      <div class="blurb">${esc(c.blurb)}</div>
    </div>`).join('') + '</div>';
}
async function hfSearch() {
  const q = $('hf-q').value.trim(); if (!q) return;
  $('hf-results').innerHTML = '<p class="muted">Recherche…</p>';
  try {
    const r = await api('/api/hf/search?q=' + encodeURIComponent(q));
    $('hf-results').innerHTML = r.length ? '<div class="mlist">' + r.map(m => `<div class="mitem" onclick="openRepo('${esc(m.repo)}')"><div class="n">${esc(m.repo)}</div><div class="s"><span>${fmtN(m.downloads)} téléchargements</span><span>♥ ${m.likes}</span></div></div>`).join('') + '</div>' : '<p class="muted">Rien trouvé.</p>';
  } catch (e) { $('hf-results').innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; }
}
async function openRepo(repo) {
  const box = $('repo-panel'); box.classList.remove('hidden');
  box.innerHTML = `<div class="repo">Lecture du dépôt <b>${esc(repo)}</b>…</div>`;
  try {
    const r = await api('/api/hf/repo?repo=' + encodeURIComponent(repo));
    S.repo = r;
    const g = r.gguf || {};
    const vramFree = S.hw && S.hw.gpus.length ? S.hw.gpus[0].vram_total - S.hw.gpus[0].vram_used : 0;
    const ramAvail = S.hw ? S.hw.ram.available : 0;
    const fit = (size) => {
      if (!S.hw) return '';
      if (vramFree && size + 2.5 * GiB <= vramFree) return '<span class="fit badge ok">tient en VRAM</span>';
      if (size <= vramFree + ramAvail - 4 * GiB) return '<span class="fit badge warn">VRAM + RAM</span>';
      return '<span class="fit badge err">trop gros</span>';
    };
    box.innerHTML = `<div class="repo">
      <div class="row-inline"><b>${esc(repo)}</b> <a href="https://huggingface.co/${esc(repo)}" target="_blank" class="small">page Hugging Face ↗</a>
        ${g.architecture ? `<span class="badge">${esc(g.architecture)}</span>` : ''}${g.total ? `<span class="badge">${(g.total / 1e9).toFixed(1).replace('.', ',')} G params</span>` : ''}${g.context_length ? `<span class="badge">ctx ${fmtN(g.context_length)}</span>` : ''}
        ${r.license ? `<span class="badge">${esc(r.license)}</span>` : ''}<span class="muted small">${fmtN(r.downloads || 0)} téléchargements</span>
        <button class="link small" onclick="$('repo-panel').classList.add('hidden')">fermer</button></div>
      <p class="muted small">Choisissez une <b>quantification</b> : Q8 ≈ qualité d’origine, Q6/Q5 quasi identique, Q4_K_M le meilleur compromis, Q3/IQ2 pour faire tenir un gros modèle au prix de la qualité, IQ1 = dernier recours. « UD » = quantification dynamique unsloth (couches sensibles gardées plus précises).</p>
      <div class="row-inline"><button class="btn" id="btn-analyze" onclick="analyzeRepo()">🔍 Analyser les versions pour ma machine</button><span class="muted small">lit l’en-tête de chaque fichier (quelques Mo) et calcule la configuration conseillée, de la plus petite à la plus grosse</span></div>
      <div id="analyze-job"></div><div id="analyze-summary"></div>
      <div class="qlist">${r.quants.map((q, i) => `<div class="q" id="q-${i}" onclick="pickQuant(${i})"><span><b>${esc(q.quant)}</b>${q.shards > 1 ? ` <span class="muted">(${q.shards} fichiers)</span>` : ''}<span>${fmtG(q.size)}</span></span><span id="qv-${i}">${fit(q.size)}</span></div>`).join('')}</div>
      ${r.mmprojs.length ? `<div class="small muted" style="margin-top:8px">Projecteur vision disponible : ${r.mmprojs.map(m => esc(m.file.split('/').pop()) + ' (' + fmtB(m.size) + ')').join(', ')} — téléchargé avec le modèle.</div>` : ''}
      ${r.drafts.length ? `<div class="small" style="margin-top:8px"><label class="switch"><input type="checkbox" id="dl-draft" checked> Télécharger aussi le <b>brouillon MTP</b> (${esc(pickDraft(r.drafts).file.split('/').pop())}, ${fmtB(pickDraft(r.drafts).size)})</label>
        <div class="muted">Têtes de prédiction multi-jetons publiées à part : avec le décodage spéculatif (<code>--spec-type draft-mtp</code>), la génération va 1,3 à 1,7× plus vite pour une sortie identique. Nécessite un llama.cpp récent qui connaît le MTP de cette architecture.</div></div>` : ''}
      <div id="quant-action"></div></div>`;
  } catch (e) { box.innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; }
}
// Brouillon MTP conseillé : la variante « shared » en Q8_0 (petite, précise), sinon la plus petite.
function pickDraft(drafts) {
  return drafts.find(d => /shared/i.test(d.file) && /q8_0/i.test(d.file)) || drafts.find(d => /shared/i.test(d.file)) || drafts[0];
}
async function pickQuant(i) {
  const q = S.repo.quants[i];
  document.querySelectorAll('.q').forEach((el, j) => el.classList.toggle('sel', j === i));
  const mm = S.repo.mmprojs.find(m => /f16/i.test(m.file)) || S.repo.mmprojs[0];
  const dr = S.repo.drafts.length && (!$('dl-draft') || $('dl-draft').checked) ? pickDraft(S.repo.drafts) : null;
  S.model = {repo: S.repo.repo, file: q.file, size: q.size, mmproj: mm ? mm.file : null, draft: dr ? dr.file : null};
  S.modelInfo = {name: q.file, size: q.size, path: '', mmproj: ''};
  // déjà téléchargé ?
  const local = S.models.find(m => m.kind === 'launcher' && m.path.endsWith('/' + q.file.split('/').pop()) && m.path.includes(S.repo.repo.replace('/', '__')));
  $('quant-action').innerHTML = local
    ? `<div class="row-inline"><span class="badge ok">déjà téléchargé</span><button class="btn primary" onclick="selectLocal('${local.id}')">Utiliser ce fichier</button></div>`
    : `<div class="row-inline"><button class="btn primary" onclick="downloadQuant(${i})">⬇ Télécharger ${esc(q.quant)} (${fmtG(q.size + (mm ? mm.size : 0) + (dr ? dr.size : 0))})</button><span class="muted small">Estimation de la mémoire ci-dessous, avant même de télécharger.</span></div><div id="dl-job"></div>`;
  $('model-selected').innerHTML = '<p class="muted">Lecture de l’en-tête GGUF à distance (quelques Mo)…</p>';
  try {
    const d = await api(`/api/hf/profile?repo=${encodeURIComponent(S.repo.repo)}&file=${encodeURIComponent(q.file)}&size=${q.size}`);
    S.profile = d.profile; renderProfile();
    if (!S.cfg) S.cfg = defaultCfg();
    await recommendCfg(true);
  } catch (e) { $('model-selected').innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; }
}
async function downloadQuant(i) {
  const q = S.repo.quants[i];
  try {
    const draft = S.model.draft;
    const r = await api('/api/hf/download', {repo: S.repo.repo, file: q.file, size: q.size, mmproj: S.model.mmproj, draft});
    watchJob(r.job.id, 'dl-job', async (j) => {
      await refreshModels();
      if (j.state === 'done' && j.result) {
        const m = S.models.find(x => x.path === j.result.path);
        if (m) {
          showTab('local'); await selectLocal(m.id);
          if (draft) { const d = S.models.find(x => x.draft && x.path.startsWith(j.result.dir)); if (d) { S.cfg.spec_type = 'draft-mtp'; S.cfg.draft_model = d.path; S.cfg.spec_n_max = 5; onCfgChange(); toast('Brouillon MTP activé (décodage spéculatif)'); } }
        }
      }
    });
    // pendant le téléchargement, la jauge SSD se remplit : on rafraîchit
    const t = setInterval(async () => { if (!JOBS[r.job.id]) { clearInterval(t); return; } await liveTick(); runEstimate(); }, 3000);
  } catch (e) { toast(e.message); }
}

const VERDICT = {
  vram: ['ok', 'tout sur le GPU'], offload_moe: ['ok', 'experts en RAM'], offload: ['warn', 'partiellement en RAM'],
  cpu: ['warn', 'CPU seul'], too_big: ['err', 'ne tient pas'], error: ['err', 'illisible'],
};
async function analyzeRepo() {
  if (!S.repo) return;
  $('btn-analyze').disabled = true;
  try {
    const r = await api('/api/hf/analyze', {repo: S.repo.repo, goal: $('goal').value});
    watchJob(r.job.id, 'analyze-job', (j) => {
      $('btn-analyze').disabled = false;
      if (j.state !== 'done' || !j.result) return;
      $('analyze-job').innerHTML = '';
      const res = j.result.results;
      let best = null;
      for (const e of res) {
        const i = S.repo.quants.findIndex(q => q.file === e.file);
        const [cls, label] = VERDICT[e.verdict] || ['', e.verdict];
        const el = $('qv-' + i);
        if (el) {
          let detail = '';
          if (e.verdict === 'offload_moe') detail = ` · experts de ${e.cpu_moe_all ? 'toutes les' : e.n_cpu_moe} couches en RAM (${fmtG(e.ram)}), ctx ${fmtN(e.ctx)}`;
          else if (e.verdict === 'vram') detail = ` · ${fmtG(e.vram)} VRAM, ctx ${fmtN(e.ctx)}`;
          else if (e.verdict === 'offload') detail = ` · ${e.ngl}/${e.n_layer} couches GPU, ctx ${fmtN(e.ctx)}`;
          else if (e.verdict === 'too_big') detail = e.streamed ? ` · ${fmtG(e.streamed)} relus depuis le SSD` : '';
          el.innerHTML = `<span class="fit badge ${cls}" title="${esc(e.summary || '')}">${label}${detail}</span>`;
        }
        if (['vram', 'offload_moe', 'offload'].includes(e.verdict)) best = e;  // le plus gros qui tient = meilleure qualité
      }
      const skipped = j.result.skipped ? ` Les ${j.result.skipped} versions plus grosses n’ont pas été testées : elles ne tiendraient pas non plus.` : '';
      $('analyze-summary').innerHTML = best
        ? `<div class="warn-item tip">💡 <b>Meilleure qualité qui tient sur cette machine : ${esc(best.quant)}</b> (${fmtG(best.size)}) — ${esc(best.summary)}${best.lazy ? ` Plus ${fmtG(best.lazy)} lus à la demande sur le SSD.` : ''}${skipped} Cliquez sur la version pour la sélectionner.</div>`
        : `<div class="warn-item error">⛔ Aucune version de ce dépôt ne tient sur cette machine sans relire le SSD à chaque jeton.${skipped}</div>`;
    });
  } catch (e) { toast(e.message); $('btn-analyze').disabled = false; }
}

// ------------------------------------------------------------------ réglages
function defaultCfg() {
  const cores = S.hw ? S.hw.cpu.cores : 4;
  return {ngl: 'all', ctx: 32768, type_k: 'f16', type_v: 'f16', flash_attn: 'on', n_cpu_moe: 0, cpu_moe_all: false, n_cpu_ffn: 0,
    threads: cores, threads_batch: cores, batch: 2048, ubatch: 512, parallel: 1, kv_offload: true, load_mode: 'auto', swa_full: false,
    cache_ram: 8192, cache_reuse: 0, spec_type: 'none', spec_n_max: 16, draft_model: '', draft_ngl: 'all', reasoning: 'auto', reasoning_budget: -1,
    mmproj_offload: true, devices: [], tensor_split: '', split_mode: '', fit: 'on', port: 8080, host: '127.0.0.1', extra_args: ''};
}
const HELP = {
  ngl: `<b>Ce que c’est.</b> Un modèle est une pile de couches identiques que chaque jeton traverse. Ce réglage dit combien de ces couches (en partant de la fin) vivent dans la VRAM ; les autres restent en RAM et sont calculées par le processeur.<br><b>Effet.</b> Une couche sur GPU se calcule 10 à 50× plus vite. Chaque couche laissée au CPU ralentit <i>tout</i> le modèle (le jeton attend la plus lente). « Tout » = toutes les couches + la couche de sortie.<br><b>Conseil.</b> Tout sur le GPU tant que ça tient ; si la VRAM manque, réduire le contexte ou compresser le cache KV est presque toujours préférable à retirer des couches.`,
  ctx: `<b>Ce que c’est.</b> La mémoire de travail : tout ce que le modèle « voit » à la fois (votre conversation, vos documents, sa réponse). Compté en jetons (≈ ¾ de mot en français).<br><b>Effet.</b> Chaque jeton du contexte coûte des octets de <i>cache KV</i> (affiché à droite). Doubler le contexte double ce cache. Un contexte réservé mais inutilisé ne ralentit rien, il occupe juste la mémoire.<br><b>Conseil.</b> 8k suffit pour du chat court ; 32k pour travailler sur des documents ; au-delà du contexte d’entraînement du modèle, la qualité se dégrade.`,
  kv: `<b>Ce que c’est.</b> Le cache KV mémorise, pour chaque jeton du contexte et chaque couche d’attention, les « clés » (K) et « valeurs » (V) calculées, pour ne pas tout refaire à chaque nouveau jeton.<br><b>Effet.</b> f16 = précision d’origine. q8_0 = 8 bits : −47 % de mémoire, perte imperceptible. q4_0 = 4 bits : −72 %, perte visible sur les longues réponses. Le K supporte mieux la compression que le V.<br><b>Conseil.</b> Premier levier quand la VRAM manque : q8_0/q8_0. Compresser V exige flash-attention.`,
  flash_attn: `<b>Ce que c’est.</b> Une façon plus astucieuse de calculer l’attention : mêmes résultats, mais sans matérialiser l’énorme matrice (micro-batch × contexte × têtes).<br><b>Effet.</b> Tampon de calcul bien plus petit (regardez la jauge en le désactivant), souvent plus rapide, et indispensable pour quantifier le cache KV.<br><b>Conseil.</b> Toujours « on » sur GPU ; « auto » laisse llama.cpp décider si le backend le supporte.`,
  n_cpu_moe: `<b>Ce que c’est.</b> Dans un modèle MoE (mélange d’experts), chaque couche contient plusieurs « experts » dont seuls quelques-uns travaillent pour un jeton donné. Les experts pèsent 80-90 % du modèle mais sont peu sollicités.<br><b>Effet.</b> Les experts des N premières couches restent en RAM ; l’attention et le reste des couches vont sur le GPU. La vitesse baisse peu, la VRAM libérée est énorme.<br><b>Conseil.</b> C’est LE réglage qui fait tourner un MoE bien plus gros que la VRAM. Le bouton « configuration optimale » cherche le plus petit N qui tient.`,
  n_cpu_ffn: `<b>Ce que c’est.</b> L’équivalent pour un modèle dense : la partie « feed-forward » (≈ ⅔ des poids d’une couche) des N premières couches reste en RAM.<br><b>Effet.</b> Plus fin que retirer des couches entières, mais comme toute la couche sert à chaque jeton, la vitesse chute davantage qu’avec les experts d’un MoE.`,
  load_mode: `<b>auto / mmap.</b> Le fichier est « mappé » : le système lit les poids côté CPU depuis le SSD à la demande et les garde en RAM tant qu’il y a de la place. S’il n’y en a pas, il les relit depuis le SSD à chaque jeton : ça fonctionne, mais à quelques jetons par seconde — c’est ce que montre la zone rouge de la jauge SSD.<br><b>none.</b> Copie classique en RAM : échec net si ça ne tient pas, mais aucune relecture surprise.<br><b>mlock / mmap+mlock.</b> Verrouille les poids en RAM (jamais renvoyés sur le disque ni compressés) : latence stable, au prix d’une RAM réservée.<br><b>dio.</b> Lecture directe (saute le cache système) : chargement plus rapide sur NVMe.`,
  threads: `<b>Ce que c’est.</b> Threads utilisés pour la génération, côté CPU (couches non déportées, échantillonnage).<br><b>Conseil.</b> Vos cœurs <i>physiques</i>, pas les threads hyperthreading : la génération est limitée par la bande passante mémoire, pas par le calcul, et trop de threads se gênent. Sans couche sur le CPU, ce réglage compte peu.`,
  threads_batch: `Threads pour le traitement du prompt (calcul plus dense, mieux parallélisable) : tous les cœurs physiques.`,
  batch: `<b>batch</b> = nombre maximal de jetons soumis d’un coup (traitement du prompt). <b>ubatch</b> = taille des tranches réellement calculées.<br><b>Effet.</b> ubatch fixe la taille du tampon de calcul (logits : ubatch × taille du vocabulaire × 4 octets, plus la matrice d’attention sans flash-attention). 512 est le bon défaut ; 256 économise de la VRAM ; 1024-2048 accélère les très longs prompts si la VRAM le permet.`,
  parallel: `<b>Ce que c’est.</b> Nombre de conversations servies en même temps (« slots »).<br><b>Effet.</b> Le contexte total est partagé : 32k avec 4 slots = 8k par conversation. Les modèles récurrents allouent un état par slot.<br><b>Conseil.</b> 1, sauf si plusieurs clients (IDE + chat, plusieurs utilisateurs) interrogent le serveur simultanément.`,
  kv_offload: `Garder le cache KV sur le GPU (défaut). Le mettre en RAM libère de la VRAM pour les poids, mais l’attention doit alors lire tout le contexte depuis la RAM à chaque jeton : très lent. À réserver aux cas désespérés.`,
  cache_reuse: `<b>Ce que c’est.</b> Quand un nouveau prompt partage un long début avec le précédent mais diffère ensuite (un agent qui résume ou retire un message), llama-server peut décaler et recycler des blocs du cache KV au lieu de tout recalculer.<br><b>Conseil.</b> 256 pour les agents de code ; 0 pour du chat simple (le préfixe commun est déjà réutilisé sans ce réglage).`,
  cache_ram: `<b>Ce que c’est.</b> llama-server garde en RAM les caches KV des conversations récentes (jusqu’à ce plafond) pour reprendre une conversation sans recalculer tout son prompt.<br><b>Effet.</b> Grossit à l’usage jusqu’au plafond (zone hachurée de la jauge RAM). 0 = désactivé.`,
  spec_type: `<b>Ce que c’est.</b> Le décodage spéculatif devine plusieurs jetons d’avance puis les fait vérifier en un seul passage du gros modèle : la sortie est strictement identique, mais plus rapide quand les devinettes tombent juste.<br><b>n-grammes</b> (rien à installer) : reprend des suites déjà vues dans le contexte — efficace pour du code, des reformulations, des réponses qui recopient l’entrée ; coût mémoire négligeable (quelques Mo en RAM). <i>ngram-mod</i> est le plus polyvalent.<br><b>Modèle brouillon</b> (draft-simple) : un petit modèle de la même famille propose les jetons — prend de la VRAM (ses poids + son propre cache KV).<br><b>MTP / EAGLE-3 / dFlash / dSpark</b> : têtes de prédiction spécialisées, seulement si le modèle (ou un fichier compagnon) en a.`,
  spec_n_max: `Jetons devinés par étape. Plus haut = plus de gain si les devinettes sont bonnes, plus de temps perdu sinon. 16 pour les n-grammes, 3-8 pour un modèle brouillon.`,
  reasoning: `<b>Ce que c’est.</b> Les modèles « pensants » écrivent un raisonnement avant la réponse.<br><b>Effet.</b> Meilleure qualité sur les problèmes difficiles, mais beaucoup de jetons (temps + contexte). off = interdit ; auto = selon le modèle.`,
  reasoning_budget: `Plafond de jetons de réflexion (−1 = illimité). Utile pour borner le temps de réponse d’un modèle bavard.`,
  mmproj_offload: `L’encodeur d’images (mmproj) sur le GPU : rapide. Sur CPU : libère ≈ 0,5-1 Gio de VRAM, l’analyse d’image devient lente mais le texte n’est pas affecté.`,
  swa_full: `Pour les modèles à fenêtre glissante (Gemma, gpt-oss) : garder un cache complet sur ces couches. Plus de mémoire, mais permet la réutilisation du cache de prompts dans tous les cas.`,
  fit: `Laisser llama.cpp ajuster automatiquement ce que vous n’avez PAS fixé pour tenir dans la mémoire (--fit). Le launcher fixe déjà les réglages importants ; ce filet ne concerne que les options restées par défaut.`,
  port: `Port HTTP de llama-server (API OpenAI sur /v1, interface web incluse). S’il est occupé, le launcher prend le suivant.`,
  extra_args: `Options supplémentaires passées telles quelles à llama-server (voir <code>llama-server --help</code>). Ex. <code>--temp 0.7 --top-p 0.9</code>.`,
  devices: `Cartes utilisées. Avec plusieurs cartes, llama.cpp répartit les couches proportionnellement à leur VRAM (ou selon --tensor-split, ex. « 3,1 »).`,
};
const KV_COMBOS = [['f16', 'f16', 'f16 / f16 — précision d’origine'], ['q8_0', 'q8_0', 'q8_0 / q8_0 — −47 %, perte imperceptible'], ['q8_0', 'q4_0', 'q8_0 / q4_0 — −60 %'], ['q5_1', 'q5_1', 'q5_1 / q5_1 — −62 %'], ['q4_0', 'q4_0', 'q4_0 / q4_0 — −72 %, perte visible']];
const CTX_STEPS = [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144];

function renderSettings() {
  const c = S.cfg, p = S.profile;
  if (!c) { $('settings').innerHTML = '<p class="muted">Choisissez d’abord un modèle.</p>'; return; }
  const nl = p ? p.n_layer : 0, hasGpu = S.hw && S.hw.gpus.length;
  const row = (key, label, sub, ctl, val) => `<div class="setting"><div class="lab">${label}<small>${sub || ''}</small></div><div class="ctl">${ctl}</div><div class="val">${val || ''} <button class="qbtn ${S.helpOpen.has(key) ? 'on' : ''}" onclick="toggleHelp('${key}')" title="explication">?</button></div>${S.helpOpen.has(key) ? `<div class="help">${HELP[key] || ''}</div>` : ''}</div>`;
  const num = (key, min, max, step) => `<input type="number" min="${min}" max="${max}" step="${step || 1}" value="${c[key]}" onchange="setCfg('${key}', +this.value)">`;
  const sel = (key, opts) => `<select onchange="setCfg('${key}', this.value)">${opts.map(([v, l]) => `<option value="${v}" ${String(c[key]) === String(v) ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
  const sw = (key, label) => `<label class="switch"><input type="checkbox" ${c[key] ? 'checked' : ''} onchange="setCfg('${key}', this.checked)"> ${label}</label>`;
  const nglAll = c.ngl === 'all' || c.ngl === -1;
  const kvpt = kvPerToken(c.type_k, c.type_v);
  let h = '<div class="group"><h3>Essentiels</h3>';
  h += row('ngl', 'Couches sur le GPU', `-ngl · ${nl} couches + sortie`,
    hasGpu ? `<input type="range" min="0" max="${nl + 1}" value="${nglAll ? nl + 1 : c.ngl}" oninput="setCfg('ngl', +this.value === ${nl + 1} ? 'all' : +this.value)" style="width:220px"> <label class="switch"><input type="checkbox" ${nglAll ? 'checked' : ''} onchange="setCfg('ngl', this.checked ? 'all' : ${nl})"> tout</label>` : '<span class="muted">pas de GPU : 0</span>',
    nglAll ? `tout (${nl} + sortie)` : `${c.ngl} / ${nl}` + (S.est ? ` · ${fmtG(S.est.vram.length ? S.est.vram[0].segments.weights : 0)} en VRAM` : ''));
  h += row('ctx', 'Contexte', '-c · jetons', `<input type="range" min="0" max="${CTX_STEPS.length - 1}" value="${nearestStep(c.ctx)}" oninput="setCfg('ctx', ${JSON.stringify(CTX_STEPS)}[+this.value])" style="width:220px"> ${num('ctx', 512, 2097152, 512)}`,
    `${fmtN(c.ctx)} jetons ≈ ${fmtG(kvpt * c.ctx)} de cache KV${p && c.ctx > p.n_ctx_train ? ' ⚠ > entraînement' : ''}`);
  h += row('kv', 'Cache KV', '-ctk / -ctv · compression du contexte', `<select onchange="setKv(this.value)">${KV_COMBOS.map(([k, v, l]) => `<option value="${k}|${v}" ${c.type_k === k && c.type_v === v ? 'selected' : ''}>${l}</option>`).join('')}</select>`,
    `${(kvpt / 1024).toFixed(1).replace('.', ',')} Kio / jeton`);
  h += row('flash_attn', 'Flash attention', '-fa', sel('flash_attn', [['on', 'on'], ['auto', 'auto'], ['off', 'off']]));
  if (p && p.is_moe) h += row('n_cpu_moe', 'Experts MoE en RAM', `--n-cpu-moe · ${p.n_expert} experts / couche`,
    `<input type="range" min="0" max="${nl}" value="${c.cpu_moe_all ? nl : c.n_cpu_moe}" oninput="setCfg('n_cpu_moe', +this.value)" style="width:220px"> ${num('n_cpu_moe', 0, nl)}`,
    `${c.cpu_moe_all ? nl : c.n_cpu_moe} / ${nl} couches` + (S.est ? ` · ${fmtG(S.est.ram.segments.experts)} en RAM` : ''));
  if (p && !p.is_moe && hasGpu) h += row('n_cpu_ffn', 'FFN denses en RAM', '--n-cpu-ffn · réglage fin', `${num('n_cpu_ffn', 0, nl)}`, S.est && S.est.ram.segments.ffn ? fmtG(S.est.ram.segments.ffn) + ' en RAM' : '');
  h += row('load_mode', 'Chargement des poids', '--load-mode · mmap / RAM / SSD', sel('load_mode', [['auto', 'auto (mmap)'], ['mmap', 'mmap'], ['none', 'none — copie en RAM'], ['mlock', 'mlock — verrouillé en RAM'], ['mmap+mlock', 'mmap + mlock'], ['dio', 'dio — lecture directe']]));
  h += '</div><div class="group"><h3 onclick="toggle(\'adv\')">Avancés ▾</h3><div id="adv" class="' + (S.advOpen ? '' : 'hidden') + '">';
  h += row('threads', 'Threads (génération)', '-t', num('threads', 1, 256), `${S.hw ? S.hw.cpu.cores + ' cœurs physiques' : ''}`);
  h += row('threads_batch', 'Threads (prompt)', '-tb', num('threads_batch', 1, 256));
  h += row('batch', 'Batch / micro-batch', '-b / -ub', `${num('batch', 32, 8192, 32)} ${num('ubatch', 32, 8192, 32)}`, S.est && S.est.vram.length ? 'tampon ' + fmtB(S.est.vram[0].segments.compute) : '');
  h += row('parallel', 'Conversations en parallèle', '-np', num('parallel', 1, 32), c.parallel > 1 ? `${fmtN(Math.floor(c.ctx / c.parallel))} jetons chacune` : '');
  h += row('cache_ram', 'Cache de prompts', '--cache-ram · Mio', num('cache_ram', 0, 262144, 256), fmtG(c.cache_ram * MiB) + ' max');
  h += row('cache_reuse', 'Réutilisation du cache par blocs', '--cache-reuse · jetons (0 = désactivé)', num('cache_reuse', 0, 4096, 64));
  h += row('kv_offload', 'Cache KV sur le GPU', '--no-kv-offload si décoché', sw('kv_offload', 'sur le GPU'));
  const draftList = S.models.filter(m => m.draft || /draft|eagle|mtp|dflash|dspark|0\.[5-8]b|1\.[0-9]b/i.test(m.name));
  const specOpts = [['none', 'aucun'], ['ngram-mod', 'n-grammes (mod) — polyvalent'], ['ngram-simple', 'n-grammes (simple)'], ['ngram-map-k', 'n-grammes (map-k)'], ['ngram-map-k4v', 'n-grammes (map-k4v)'], ['ngram-cache', 'n-grammes (cache)'], ['draft-simple', 'modèle brouillon'], ['draft-mtp', 'MTP (intégré)'], ['draft-eagle3', 'EAGLE-3'], ['draft-dflash', 'dFlash'], ['draft-dspark', 'dSpark']];
  h += row('spec_type', 'Décodage spéculatif', '--spec-type', sel('spec_type', specOpts) + (String(c.spec_type).startsWith('draft') ? ` <select onchange="setCfg('draft_model', this.value)"><option value="">— fichier brouillon —</option>${draftList.map(m => `<option value="${esc(m.path)}" ${c.draft_model === m.path ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}</select>` : ''),
    S.est ? (S.est.ram.segments.ngram ? fmtB(S.est.ram.segments.ngram) + ' RAM' : (S.est.vram.length && S.est.vram[0].segments.draft ? fmtB(S.est.vram[0].segments.draft) + ' VRAM' : '')) : '');
  if (c.spec_type !== 'none') h += row('spec_n_max', 'Jetons devinés par étape', '--spec-draft-n-max', num('spec_n_max', 1, 64));
  h += row('reasoning', 'Raisonnement', '--reasoning', sel('reasoning', [['auto', 'auto (selon le modèle)'], ['on', 'on'], ['off', 'off']]) + ' budget ' + num('reasoning_budget', -1, 1000000, 256));
  if (S.modelInfo && (S.modelInfo.mmproj || S.model.mmproj)) h += row('mmproj_offload', 'Encodeur vision sur le GPU', '--no-mmproj-offload si décoché', sw('mmproj_offload', 'sur le GPU'));
  if (p && p.n_swa) h += row('swa_full', 'Cache complet sur les couches à fenêtre', '--swa-full', sw('swa_full', 'activer'));
  if (S.hw && S.hw.gpus.length > 1) h += row('devices', 'Cartes graphiques', '--device / -ts', S.hw.gpus.map(g => `<label class="switch"><input type="checkbox" ${!c.devices.length || c.devices.includes(String(g.index)) ? 'checked' : ''} onchange="toggleDevice('${g.index}', this.checked)"> ${esc(g.name)}</label>`).join(' ') + ` répartition <input placeholder="ex. 3,1" value="${esc(c.tensor_split)}" size="8" onchange="setCfg('tensor_split', this.value)">`);
  h += row('fit', 'Ajustement automatique llama.cpp', '--fit', sel('fit', [['on', 'on'], ['off', 'off']]));
  h += row('port', 'Port', '--port', num('port', 1024, 65535));
  h += row('extra_args', 'Options supplémentaires', 'passées telles quelles', `<input value="${esc(c.extra_args)}" size="40" onchange="setCfg('extra_args', this.value)">`);
  h += '</div></div>';
  $('settings').innerHTML = h;
}
function nearestStep(v) { let best = 0; CTX_STEPS.forEach((s, i) => { if (Math.abs(s - v) < Math.abs(CTX_STEPS[best] - v)) best = i; }); return best; }
function toggleHelp(k) { S.helpOpen.has(k) ? S.helpOpen.delete(k) : S.helpOpen.add(k); renderSettings(); }
function setKv(v) { const [k, val] = v.split('|'); S.cfg.type_k = k; S.cfg.type_v = val; if (val !== 'f16' && S.cfg.flash_attn === 'off') { S.cfg.flash_attn = 'on'; toast('Flash-attention activée : nécessaire pour compresser V'); } onCfgChange(); }
function setCfg(k, v) {
  if (k === 'n_cpu_moe') S.cfg.cpu_moe_all = false;
  if (k === 'ngl' && S.profile && +v >= S.profile.n_layer + 1) v = 'all';
  S.cfg[k] = v;
  if (k === 'ubatch' && S.cfg.batch < v) S.cfg.batch = v;
  onCfgChange();
}
function toggleDevice(idx, on) {
  const all = S.hw.gpus.map(g => String(g.index));
  let d = S.cfg.devices.length ? [...S.cfg.devices] : [...all];
  d = on ? [...new Set([...d, idx])] : d.filter(x => x !== idx);
  if (!d.length) { toast('Gardez au moins une carte'); return; }
  S.cfg.devices = d.length === all.length ? [] : d;
  onCfgChange();
}
function onCfgChange() {
  document.querySelector('#adv') && (S.advOpen = !$('adv').classList.contains('hidden'));
  const why = $('why');
  if (!why.classList.contains('hidden') && !why.querySelector('.stale')) why.insertAdjacentHTML('afterbegin', '<div class="stale muted small">Réglages modifiés à la main depuis cette proposition — la jauge ci-dessus reflète vos valeurs, pas celles-ci.</div>');
  renderSettings();
  runEstimate();
  updateCmdline();
}

// ------------------------------------------------------------------ estimation & mémoire
const runEstimate = debounce(async () => {
  if (!S.model || !S.cfg) return;
  try {
    const d = await api('/api/estimate', {model: S.model, cfg: S.cfg});
    S.est = d.estimate;
    renderMemory();
    renderSettings();
  } catch (e) { $('memory').innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; }
}, 200);

const SEG = {
  vram: [
    ['system', 'Déjà utilisé', 'var(--c-system)', 'Occupé par l’affichage, les pilotes et d’autres programmes au moment de la mesure. Sous Windows/WSL, la VRAM manquante peut être « débordée » en RAM par le pilote : ça ne plante pas, mais ça rampe.'],
    ['weights', 'Poids du modèle (couches GPU)', 'var(--c-weights)', 'Les couches placées sur le GPU (-ngl). C’est le gros morceau : la taille du fichier moins ce qui reste en RAM.'],
    ['kv', 'Cache KV', 'var(--c-kv)', 'Cache KV des couches GPU : contexte × coût par jeton (dépend du nombre de têtes KV, du type f16/q8_0 et du type de couche : fenêtre glissante et couches récurrentes coûtent bien moins).'],
    ['compute', 'Tampon de calcul', 'var(--c-compute)', 'Résultats intermédiaires d’un micro-batch : dominé par les logits (ubatch × vocabulaire × 4 octets) ; sans flash-attention s’ajoute la matrice d’attention (ubatch × contexte × têtes × 4 octets).'],
    ['mmproj', 'Encodeur vision', 'var(--c-mmproj)', 'Le projecteur multimodal (mmproj) : encode les images en jetons.'],
    ['draft', 'Modèle brouillon', 'var(--c-draft)', 'Décodage spéculatif : poids du petit modèle + son cache KV.'],
    ['overhead', 'Pilote / CUDA', 'var(--c-overhead)', 'Contexte CUDA (bibliothèques, files de commandes, espace cuBLAS) : ≈ 0,5 Gio incompressible par carte.'],
  ],
  ram: [
    ['system', 'Déjà utilisé', 'var(--c-system)', 'Système et autres programmes au moment de la mesure.'],
    ['weights', 'Poids côté CPU', 'var(--c-weights)', 'L’embedding d’entrée (toujours en RAM) + les couches non déportées + la sortie si elle n’est pas sur le GPU. Avec mmap ce sont des pages du fichier, renvoyables au SSD si la RAM manque.'],
    ['experts', 'Experts MoE', 'var(--c-experts)', 'Experts gardés en RAM (--n-cpu-moe). Seuls quelques-uns servent par jeton : lus par petits morceaux, la RAM suffit en débit.'],
    ['ffn', 'FFN denses', 'var(--c-ffn)', 'Blocs feed-forward gardés en RAM (--n-cpu-ffn).'],
    ['kv', 'Cache KV', 'var(--c-kv)', 'Cache KV des couches CPU (ou tout le cache avec --no-kv-offload).'],
    ['compute', 'Tampon de calcul', 'var(--c-compute)', 'Tampon hôte : petits intermédiaires, transferts.'],
    ['output', 'Tampon de sortie', 'var(--c-output)', 'Les logits finaux (vocabulaire × slots × 4 octets).'],
    ['mmproj', 'Encodeur vision', 'var(--c-mmproj)', 'Projecteur multimodal laissé sur CPU.'],
    ['draft', 'Modèle brouillon', 'var(--c-draft)', 'Modèle brouillon laissé sur CPU.'],
    ['ngram', 'N-grammes', 'var(--c-ngram)', 'Tables de n-grammes du décodage spéculatif : quelques dizaines de Mo, c’est tout.'],
    ['cache', 'Cache de prompts (max)', 'hatch', 'Cache des conversations récentes (--cache-ram) : grossit à l’usage jusqu’à ce plafond. Zone hachurée = « jusqu’à ».'],
  ],
  ssd: [
    ['other', 'Autres fichiers', 'var(--c-other)', 'Tout ce qui occupe déjà le disque.'],
    ['models', 'Autres modèles', 'var(--c-models)', 'Les autres .gguf du dossier du launcher.'],
    ['this_model', 'Ce modèle', 'var(--c-this)', 'Le fichier .gguf sélectionné.'],
    ['download', 'À télécharger', 'hatch', 'Place que prendra le téléchargement.'],
  ],
};
function renderMemory() {
  const e = S.est; if (!e) return;
  let h = '<div class="mem">';
  const bar = (title, total, segs, defs, extra) => {
    const hard = segs.filter(s => !s.soft).reduce((a, s) => a + s.bytes, 0);
    const used = segs.reduce((a, s) => a + s.bytes, 0);
    const over = Math.max(0, hard - total);
    const scale = Math.max(total, used);
    let inner = segs.filter(s => s.bytes > 0).map(s => `<i style="width:${(s.bytes / scale * 100).toFixed(3)}%;${s.cls ? '' : 'background:' + s.color}" class="${s.cls || ''}" title="${esc(s.label)} : ${fmtB(s.bytes)}"></i>`).join('');
    if (over) inner += `<i class="over" style="width:${(over / scale * 100).toFixed(3)}%" title="dépassement : ${fmtB(over)}"></i>`;
    const legend = segs.filter(s => s.bytes > 0).map(s => `<span title="${esc(s.help)}"><i style="${s.cls ? '' : 'background:' + s.color}" class="${s.cls || ''}"></i>${esc(s.label)} <span class="v">${fmtB(s.bytes)}</span></span>`).join('');
    return `<div class="pool"><div class="pool-h"><b>${title}</b><span>${fmtG(hard)} / ${fmtG(total)}${over ? ` — <span style="color:var(--err)">dépasse de ${fmtG(over)}</span>` : ` — ${fmtG(total - hard)} de marge${used > hard ? ' (hors plafonds hachurés)' : ''}`}${extra || ''}</span></div>
      <div class="stack">${inner}</div><div class="legend">${legend}</div></div>`;
  };
  for (const g of e.vram) {
    const segs = [{key: 'system', bytes: g.used_other}].concat(Object.entries(g.segments).map(([k, v]) => ({key: k, bytes: v})));
    h += bar(`VRAM — ${esc(g.name)}`, g.total, decorate(segs, SEG.vram), SEG.vram);
  }
  const r = e.ram;
  const rsegs = [{key: 'system', bytes: r.used_other}].concat(Object.entries(r.segments).map(([k, v]) => ({key: k, bytes: v})));
  h += bar('RAM (mémoire centrale)', r.total, decorate(rsegs, SEG.ram), SEG.ram, r.streamed ? ` — <span style="color:var(--err)">${fmtG(r.streamed)} relus depuis le SSD</span>` : '');
  const s = e.ssd;
  const ssegs = decorate(Object.entries(s.segments).map(([k, v]) => ({key: k, bytes: v})), SEG.ssd);
  if (s.lazy) ssegs.push({key: 'lazy', label: 'Embeddings lus à la demande', bytes: s.lazy, color: 'var(--c-lazy)', cls: 'lazy', soft: true, help: 'Embeddings n-gram (Qwen 3.8 Flash Next) ou par couche (Gemma « E ») : > 4 Gio, donc lus depuis le SSD ligne par ligne au fil des jetons (mode lazy de llama.cpp). Ne réservent ni VRAM ni RAM : ils passent par le cache disque. Hachuré = déjà compté dans « ce modèle ».'});
  if (s.streamed) ssegs.push({key: 'streamed', label: 'Relu faute de RAM ⚠', bytes: s.streamed, color: 'var(--c-streamed)', cls: 'over', help: 'Poids qui ne tiennent pas en RAM et sont relus depuis le SSD à chaque jeton (mmap). Le SSD devient le goulot : quelques jetons par seconde au mieux.'});
  h += bar('SSD (dossier des modèles)', s.total, ssegs, SEG.ssd);
  h += '</div>';
  // résumé lisible
  const res = e.resolved;
  const g0 = e.vram[0];
  let summary = '';
  if (g0) summary += `${res.n_gpu_layers}/${res.n_layer} couches sur le GPU${res.n_cpu_moe ? `, experts de ${res.n_cpu_moe} couches en RAM` : ''} · cache KV ${(res.kv_per_token / 1024).toFixed(1).replace('.', ',')} Kio/jeton × ${fmtN(res.ctx)} · ${res.fa ? 'flash-attention' : 'sans flash-attention'} · ${g0.fits && !r.streamed ? '<b style="color:var(--ok)">ça tient</b>' : '<b style="color:var(--err)">ça ne tient pas</b>'}`;
  else summary += `Exécution sur CPU : ${fmtG(r.weights_cpu)} de poids en RAM · ${r.streamed ? '<b style="color:var(--err)">RAM insuffisante</b>' : '<b style="color:var(--ok)">ça tient</b>'}`;
  $('memory').innerHTML = `<div class="verdict">${summary} <button class="link small" onclick="runFit()">vérifier avec llama.cpp</button><span id="fit-out" class="small muted"></span></div>` + h;
  $('warnings').innerHTML = e.warnings.map(w => `<div class="warn-item ${w.level}">${w.level === 'error' ? '⛔' : w.level === 'warn' ? '⚠' : w.level === 'tip' ? '💡' : 'ℹ'} ${esc(w.text)}</div>`).join('');
}
function decorate(segs, defs) {
  return defs.map(([key, label, color, help]) => { const s = segs.find(x => x.key === key); return s ? {key, label, bytes: s.bytes, color, cls: color === 'hatch' || color === 'lazy' ? color : '', soft: color === 'hatch', help} : null; }).filter(Boolean);
}
async function runFit() {
  if (!S.model || !S.model.id) { toast('Le modèle doit être sur le disque pour cette vérification'); return; }
  $('fit-out').textContent = ' … llama-fit-params en cours';
  try {
    const r = await api('/api/fit', {model: S.model, cfg: S.cfg});
    const proj = (r.lines || []).find(l => l.includes('projected'));
    $('fit-out').innerHTML = proj ? ' → llama.cpp : ' + esc(proj.split(': ').slice(1).join(': ')) + (r.fitted ? ` <span class="mono">(${esc(r.fitted)})</span>` : '') : ' → ' + esc(r.error || (r.lines || []).join(' | '));
  } catch (e) { $('fit-out').textContent = ' → ' + e.message; }
}

// ------------------------------------------------------------------ recommandation
async function recommendCfg(silent) {
  if (!S.model) { toast('Choisissez d’abord un modèle'); return; }
  try {
    const r = await api('/api/recommend', {model: S.model, cfg: S.cfg || {}, goal: $('goal').value});
    const keep = {port: S.cfg ? S.cfg.port : 8080, extra_args: S.cfg ? S.cfg.extra_args : ''};
    S.cfg = Object.assign(r.cfg, keep);
    S.est = r.estimate;
    $('why').classList.remove('hidden');
    $('why').innerHTML = `<b>${esc(r.verdict)}</b><ul>${r.why.map(w => `<li><b class="p">${esc(labelOf(w.param))}</b> — ${esc(w.text)}</li>`).join('')}</ul>`;
    renderSettings(); renderMemory(); updateCmdline();
    if (!silent) toast('Configuration proposée appliquée');
  } catch (e) { toast('Erreur : ' + e.message); }
}
function labelOf(k) { return {ngl: 'Couches GPU', ctx: 'Contexte', kv: 'Cache KV', flash_attn: 'Flash attention', n_cpu_moe: 'Experts en RAM', threads: 'Threads', cache_ram: 'Cache de prompts', cache_reuse: 'Réutilisation du cache', parallel: 'Parallélisme', model: 'Modèle', agent: 'Agent'}[k] || k; }

// ------------------------------------------------------------------ lancement
const updateCmdline = debounce(async () => {
  if (!S.model || !S.cfg) return;
  try { const r = await api('/api/cmdline', {model: S.model, cfg: S.cfg}); $('cmdline').textContent = r.cmd; } catch (e) { /* */ }
}, 200);
async function startServer() {
  if (!S.model || !S.model.id) { toast('Le modèle doit être téléchargé avant de lancer'); return; }
  if (S.est && S.est.warnings.some(w => w.level === 'error') && !confirm('L’estimation prévoit un problème (voir les avertissements). Lancer quand même ?')) return;
  try { S.server = await api('/api/server/start', {model: S.model, cfg: S.cfg}); renderServer(); pollServer(); } catch (e) { toast('Erreur : ' + e.message, 6000); }
}
async function stopServer() { S.server = await api('/api/server/stop', {}); renderServer(); }
let srvTimer;
async function pollServer() {
  clearTimeout(srvTimer);
  try { S.server = await api('/api/server'); } catch (e) { return; }
  renderServer();
  if (S.server.state === 'starting' || S.server.state === 'ready') srvTimer = setTimeout(pollServer, S.server.state === 'starting' ? 1000 : 4000);
}
function renderServer() {
  const s = S.server; if (!s) return;
  const pill = $('pill-server');
  const labels = {stopped: 'serveur arrêté', starting: 'chargement du modèle…', ready: 'serveur prêt :' + s.port, error: 'erreur serveur'};
  pill.textContent = labels[s.state]; pill.className = 'pill ' + ({stopped: '', starting: 'busy', ready: 'ok', error: 'err'}[s.state]);
  $('btn-start').disabled = s.state === 'starting' || s.state === 'ready';
  $('btn-stop').disabled = !(s.state === 'starting' || s.state === 'ready');
  $('server-state').innerHTML = s.state === 'error' ? `<span style="color:var(--err)">${esc(s.error)}</span>` : s.state === 'ready' ? `prêt depuis ${Math.round(s.uptime)} s${s.port_note ? ' · ' + esc(s.port_note) : ''}` : s.state === 'starting' ? 'chargement… (lecture du fichier, copie en VRAM)' : '';
  $('log').textContent = (s.log || []).join('\n');
  const links = $('server-links');
  if (s.state === 'ready') { links.classList.remove('hidden'); links.innerHTML = `Interface web de llama.cpp : <a href="http://127.0.0.1:${s.port}" target="_blank">http://127.0.0.1:${s.port}</a> · API compatible OpenAI : <span class="mono">http://127.0.0.1:${s.port}/v1</span> (modèle : <span class="mono">${esc(s.model.name)}</span>)`; }
  else links.classList.add('hidden');
  renderActual(s);
  $('chat-send').disabled = s.state !== 'ready';
  if (s.state !== S.lastServerState) { S.lastServerState = s.state; refreshAgents(); if (s.state === 'ready') api('/api/agents/scripts', {}).then(r => { S.agentScripts = r; }).catch(() => {}); }
}
function renderActual(s) {
  const box = $('actual');
  if (!s.mem || !Object.keys(s.mem).length) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const sum = (o) => Object.values(o || {}).reduce((a, b) => a + b, 0);
  const gpuOf = (o) => Object.entries(o || {}).filter(([d]) => !/CPU|Host/.test(d)).reduce((a, [, v]) => a + v, 0);
  const cpuOf = (o) => Object.entries(o || {}).filter(([d]) => /CPU|Host/.test(d)).reduce((a, [, v]) => a + v, 0);
  const e = S.est, g = e && e.vram[0] ? e.vram[0].segments : null, r = e ? e.ram.segments : null;
  const row = (l, pred, act) => `<tr><td>${l}</td><td>${pred != null ? fmtB(pred) : '—'}</td><td>${act != null ? act.toFixed(0) + ' Mio' : '—'}</td><td>${pred != null && act ? (100 * (act * MiB - pred) / pred).toFixed(0) + ' %' : ''}</td></tr>`;
  let h = `<h3 class="small muted" style="margin:12px 0 4px">PRÉVU vs RÉEL (lu dans le journal de llama-server)</h3><table class="cmp"><tr><th></th><th>prévu</th><th>réel</th><th>écart</th></tr>`;
  h += row('Poids sur GPU', g ? g.weights + g.experts : null, gpuOf(s.mem.weights));
  h += row('Poids en RAM', r ? r.weights + r.experts + r.ffn : null, cpuOf(s.mem.weights));
  h += row('Cache KV + état récurrent (GPU)', g ? g.kv : null, gpuOf(s.mem.kv) + gpuOf(s.mem.rs));
  h += row('Cache KV (RAM)', r ? r.kv : null, cpuOf(s.mem.kv) + cpuOf(s.mem.rs));
  h += row('Tampon de calcul (GPU)', g ? g.compute : null, gpuOf(s.mem.compute));
  h += '</table>';
  if (s.offloaded) h += `<div class="small muted">Couches déportées : ${s.offloaded[0]} / ${s.offloaded[1]}${s.projected ? ` · projection llama.cpp : ${fmtN(s.projected[0])} Mio sur ${fmtN(s.projected[1])} Mio libres` : ''}${s.rss ? ` · RAM résidente du processus : ${fmtB(s.rss)}` : ''}</div>`;
  h += `<div class="small muted">Le tampon de calcul réel est souvent plus petit que prévu : l’estimation reprend la projection prudente de llama.cpp (--fit), le tampon effectivement réservé recycle ses intermédiaires.</div>`;
  box.innerHTML = h;
}

// ------------------------------------------------------------------ agents (outils de code)
async function refreshAgents() {
  try { S.agents = await api('/api/agents'); } catch (e) { return; }
  renderAgents();
}
function renderAgents() {
  const a = S.agents; if (!a) return;
  const p = S.profile, c = S.cfg;
  // état du modèle vis-à-vis des exigences d'un agent
  let check = '';
  if (p) {
    const tools = p.tools_template;
    const ctx = c ? c.ctx : 0;
    check += `<div class="seglist">
      <div><i style="background:${tools ? 'var(--ok)' : 'var(--err)'}"></i><span><b>Appels d’outils</b> : ${tools ? 'le gabarit de chat de ce modèle sait présenter des outils' : 'le gabarit de ce modèle ne mentionne pas d’outils — les agents ne fonctionneront probablement pas'}</span></div>
      <div><i style="background:${ctx >= 65536 ? 'var(--ok)' : ctx >= 32768 ? 'var(--warn)' : 'var(--err)'}"></i><span><b>Contexte</b> : ${fmtN(ctx)} jetons ${ctx >= 65536 ? '— confortable' : ctx >= 32768 ? '— limite : Claude Code devra résumer souvent' : '— trop court pour un agent (≥ 64k conseillé)'}</span></div>
      <div><i style="background:${S.server && S.server.state === 'ready' ? 'var(--ok)' : 'var(--muted)'}"></i><span><b>Serveur</b> : ${S.server && S.server.state === 'ready' ? `prêt sur le port ${S.server.port}, alias <span class="mono">${esc(a.alias)}</span>` : 'à lancer (étape 4) avant d’ouvrir un agent'}</span></div>
    </div>`;
  } else check = '<p class="muted">Choisissez un modèle pour vérifier qu’il convient à un agent.</p>';
  $('agents-check').innerHTML = check;
  $('btn-tooltest').disabled = !(S.server && S.server.state === 'ready');
  const card = (key, s) => {
    const inst = a.installed[key];
    const env = Object.entries(s.env).map(([k, v]) => `${k}=${v.length > 90 ? v.slice(0, 90) + '…' : v}`).join('\n');
    return `<div class="repo" style="margin-top:10px"><div class="row-inline"><b>${esc(s.name)}</b> ${inst ? `<span class="badge ok">installé</span> <span class="muted small mono">${esc(inst)}</span>` : '<span class="badge warn">non trouvé dans le PATH</span>'}
        <button class="btn small" onclick="openAgent('${key}')" ${a.ready ? '' : 'disabled'}>▶ Ouvrir un terminal</button>
        <button class="btn small" onclick="copyText(agentScript('${key}'))">copier le script</button></div>
      <div class="small muted" style="margin:6px 0">${esc(s.how)}</div>
      <pre class="cmd">${esc(env)}\n${esc(s.cmd)}</pre></div>`;
  };
  $('agents').innerHTML = Object.entries(a.specs).map(([k, s]) => card(k, s)).join('') +
    `<div class="small muted" style="margin-top:8px">Scripts prêts à l’emploi : <span class="mono">${esc((S.agentScripts && S.agentScripts.claude && S.agentScripts.claude.sh.replace(/[^/\\]+$/, '')) || '~/.ialauncher/agents/')}</span> (<span class="mono">*-local.sh</span> pour Linux/macOS/WSL, <span class="mono">*-local.cmd</span> pour Windows). Le launcher doit rester ouvert : il fait tourner llama-server et relaie les requêtes de Claude Code.</div>`;
}
function agentScript(key) {
  const s = S.agents.specs[key];
  return Object.entries(s.env).map(([k, v]) => `export ${k}=${JSON.stringify(v)}`).join('\n') + '\n' + s.cmd;
}
async function copyText(t) { try { await navigator.clipboard.writeText(t); toast('Copié'); } catch (e) { prompt('Copiez :', t); } }
async function openAgent(key) {
  try { const r = await api('/api/agents/open', {tool: key, cwd: ''}); toast('Terminal ouvert (' + r.terminal + ')'); }
  catch (e) { toast(e.message, 6000); }
}
async function testToolCall() {
  $('tooltest-out').textContent = ' … test en cours (quelques secondes)';
  try {
    const r = await api('/api/agents/test', {});
    $('tooltest-out').innerHTML = r.ok ? `<span style="color:var(--ok)">✓ ${esc(r.detail)}</span>` : `<span style="color:var(--err)">✗ ${esc(r.detail)}</span>${r.content ? ` <span class="muted">« ${esc(r.content.slice(0, 160))} »</span>` : ''}`;
  } catch (e) { $('tooltest-out').textContent = ' ✗ ' + e.message; }
}

// ------------------------------------------------------------------ chat
function chatKey(ev) { if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); sendChat(); } }
function clearChat() { S.chat = []; $('chat').innerHTML = ''; $('chat-stats').textContent = ''; }
function addMsg(role, content) {
  const el = document.createElement('div'); el.className = 'msg ' + role; el.textContent = content; $('chat').appendChild(el); $('chat').scrollTop = 1e9; return el;
}
async function sendChat() {
  const inp = $('chat-input'); const text = inp.value.trim(); if (!text || !S.server || S.server.state !== 'ready') return;
  inp.value = '';
  S.chat.push({role: 'user', content: text}); addMsg('user', text);
  const el = addMsg('assistant', ''); el.innerHTML = '<span class="muted">…</span>';
  $('chat-send').disabled = true; $('chat-stop').disabled = false;
  S.abort = new AbortController();
  let content = '', reasoning = '', timings = null, t0 = performance.now(), nTok = 0, tFirst = 0;
  // Rendu au plus toutes les 80 ms : ré-injecter tout le HTML à chaque jeton
  // rendait la page inerte sur les longues réponses.
  let painted = 0, paintTimer = null;
  const doPaint = () => {
    painted = performance.now(); paintTimer = null;
    el.innerHTML = (reasoning ? `<details open><summary>raisonnement (${reasoning.length} car.)</summary><pre>${esc(reasoning)}</pre></details>` : '') + esc(content);
    $('chat').scrollTop = 1e9;
  };
  const paint = () => { if (paintTimer) return; const wait = Math.max(0, 80 - (performance.now() - painted)); paintTimer = setTimeout(doPaint, wait); };
  try {
    // max_tokens borne une génération qui ne s'arrêterait pas (petits modèles) ; le
    // chat de test n'a pas vocation à produire des romans.
    const r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({messages: S.chat, max_tokens: 4096}), signal: S.abort.signal});
    if (!r.ok) throw new Error((await r.json()).error || 'HTTP ' + r.status);
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
    while (true) {
      const {done, value} = await reader.read(); if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim(); if (data === '[DONE]') continue;
        try {
          const j = JSON.parse(data);
          if (j.timings) timings = j.timings;
          const d = j.choices && j.choices[0] && j.choices[0].delta || {};
          if (d.reasoning_content) reasoning += d.reasoning_content;
          if (d.content) { content += d.content; if (!tFirst) tFirst = performance.now(); }
          nTok++;
        } catch (e) { /* ligne partielle */ }
        paint();
      }
    }
    clearTimeout(paintTimer); paintTimer = null; doPaint();
    S.chat.push({role: 'assistant', content});
    const dt = (performance.now() - t0) / 1000;
    let stats = '';
    if (timings) stats = `prompt ${timings.prompt_n} jetons à ${Math.round(timings.prompt_per_second || 0)} tok/s · génération ${timings.predicted_n} jetons à ${(timings.predicted_per_second || 0).toFixed(1)} tok/s`;
    else stats = `${nTok} morceaux en ${dt.toFixed(1)} s ≈ ${(nTok / dt).toFixed(1)} tok/s`;
    el.innerHTML += `<div class="meta">${esc(stats)}</div>`;
    $('chat-stats').textContent = stats;
  } catch (e) {
    clearTimeout(paintTimer); paintTimer = null; doPaint();
    if (e.name === 'AbortError') { S.chat.push({role: 'assistant', content}); el.innerHTML += '<div class="meta">interrompu</div>'; }
    else el.innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`;
  }
  $('chat-send').disabled = false; $('chat-stop').disabled = true;
  liveTick();
}
function stopChat() { if (S.abort) S.abort.abort(); }

// ------------------------------------------------------------------ presets
async function refreshPresets() {
  S.presets = await api('/api/presets');
  $('presets').innerHTML = S.presets.length ? S.presets.map(p => `<div class="preset" onclick="loadPreset('${p.id}')"><div>${esc(p.name)}<small>${esc(p.model.name || '')} · ctx ${fmtN(p.cfg.ctx)} · ngl ${p.cfg.ngl}</small></div><button class="link" onclick="event.stopPropagation();deletePreset('${p.id}')" title="supprimer">✕</button></div>`).join('') : '<div class="muted small">Aucun preset. Réglez un modèle puis « + enregistrer ».</div>';
}
async function savePreset() {
  if (!S.model || !S.cfg) { toast('Rien à enregistrer'); return; }
  const name = prompt('Nom du preset :', (S.modelInfo && S.modelInfo.name || 'preset').split('/').pop().slice(0, 40)); if (!name) return;
  await api('/api/presets', {name, model: Object.assign({}, S.model, {name: S.modelInfo ? S.modelInfo.name : ''}), cfg: S.cfg});
  refreshPresets(); toast('Preset enregistré');
}
async function loadPreset(id) {
  const p = S.presets.find(x => x.id === id); if (!p) return;
  S.cfg = Object.assign(defaultCfg(), p.cfg);
  if (p.model.id && S.models.find(m => m.id === p.model.id)) { await selectLocalKeepCfg(p.model.id); }
  else if (p.model.repo) { await openRepo(p.model.repo); const i = S.repo.quants.findIndex(q => q.file === p.model.file); if (i >= 0) await pickQuant(i); }
  else toast('Le modèle de ce preset n’est plus sur le disque');
  S.cfg = Object.assign(defaultCfg(), p.cfg); renderSettings(); runEstimate(); updateCmdline();
}
async function selectLocalKeepCfg(id) {
  const m = S.models.find(x => x.id === id); S.model = {id}; S.modelInfo = m; renderLocalModels();
  const d = await api('/api/models/profile?id=' + id); S.profile = d.profile; renderProfile();
}
async function deletePreset(id) { if (!confirm('Supprimer ce preset ?')) return; await api('/api/presets/delete', {id}); refreshPresets(); }

// ------------------------------------------------------------------ démarrage
(async function init() {
  try {
    await refreshHardware();
    await Promise.all([refreshEngine(), refreshModels(), refreshPresets()]);
    renderSettings();
    pollServer();
    refreshAgents();
    setInterval(liveTick, 3000);
    // reprend les tâches en cours (ex. compilation lancée avant un rechargement de page)
    const jobs = await api('/api/jobs');
    for (const j of jobs.filter(j => j.state === 'running' && ['engine', 'download', 'analyze'].includes(j.kind))) watchJob(j.id, j.kind === 'engine' ? 'engine-job' : 'dl-job', j.kind === 'engine' ? refreshEngine : refreshModels);
    // lien direct : ?select=<id de modèle local> ou ?repo=<dépôt HF>
    const q = new URLSearchParams(location.search);
    if (q.get('select')) await selectLocal(q.get('select'));
    else if (q.get('repo')) {
      showTab('catalog'); await openRepo(q.get('repo'));
      if (q.get('quant') && S.repo) { const i = S.repo.quants.findIndex(x => x.quant === q.get('quant').toUpperCase()); if (i >= 0) await pickQuant(i); }
    }
  } catch (e) { toast('Erreur au démarrage : ' + e.message, 8000); }
})();
