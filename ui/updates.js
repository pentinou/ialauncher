/* IA Launcher — onglet Mises à jour : moteurs et nouvelles versions des modèles.
   Réutilise les utilitaires d'app.js et gen.js ($, api, esc, toast, watchJob, fmtGB, LS). */
'use strict';

const U = {scan: null, fiches: {}};
const FEATS = {ref_images: 'retouche par image de référence', init_image: 'image de départ', end_image: 'image de fin'};

async function loadUpdates() {
  const root = $('mode-updates');
  if (!root.dataset.ready) {
    root.innerHTML = `
    <section class="card"><h1>Moteurs</h1>
      <p class="lead">Les programmes qui font tourner les modèles. Une nouvelle version apporte souvent la prise en charge de nouveaux modèles : mettez-les à jour avant d’essayer un modèle récent.</p>
      <div id="upd-engines"><p class="muted">Vérification des dernières versions…</p></div></section>
    <section class="card"><h1>Nouvelles versions des modèles</h1>
      <p class="lead">Le launcher compare le nom de chaque modèle (catalogue texte, GGUF téléchargés, catalogue image et vidéo) aux dépôts publiés depuis sur Hugging Face par le même auteur et les quantiseurs reconnus. Il propose une <b>nouvelle version</b> (numéro plus élevé : Qwen 3.6 → 3.8, FLUX.2 → FLUX.3) ou une <b>variante</b> (même modèle, autre branche : Turbo, base, MTP…). Rien ne change sans votre accord : <b>Remplacer</b> met le nouveau modèle à la place de l’ancien dans le catalogue. Les fichiers déjà téléchargés restent sur le disque (l’onglet Stockage permet de les supprimer).</p>
      <div class="row-inline"><button class="btn primary" id="upd-scan" onclick="scanUpdates(true)">🔍 Chercher les nouvelles versions</button><span id="upd-when" class="muted small"></span></div>
      <div id="upd-models"></div></section>
    <section class="card"><h1>Choix déjà validés</h1><div id="upd-user" class="small"></div></section>`;
    root.dataset.ready = '1';
  }
  loadEngineUpdates();
  scanUpdates(false);
  loadUserChoices();
}

// ------------------------------------------------------------------ moteurs
async function loadEngineUpdates() {
  let d; try { d = await api('/api/updates/engines'); } catch (e) { $('upd-engines').innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; return; }
  $('upd-engines').innerHTML = d.engines.map(e => `<div class="plan" style="margin-bottom:8px"><div>
      <b>${esc(e.name)}</b> <span class="muted small">${esc(e.role || '')}</span><br>
      ${e.error ? `<span class="warn small">vérification impossible : ${esc(e.error)}</span>`
        : e.installed ? `<span class="small">installé : <span class="mono">${esc(e.installed)}</span>${e.latest ? ` · dernière : <span class="mono">${esc(e.latest)}</span>` : ''}</span> ${e.update ? '<span class="badge warn">mise à jour disponible</span>' : e.latest ? '<span class="badge ok">à jour</span>' : ''}`
        : ''}${e.note ? `<br><span class="muted small">${esc(e.note)}</span>` : ''}</div>
      <div>${e.update && e.action ? `<button class="btn primary small" onclick='updateEngine(${esc(JSON.stringify(e))})'>Mettre à jour</button>` : ''}</div></div>
    <div id="upd-job-${esc(e.id)}"></div>`).join('');
}
async function updateEngine(e) {
  if (!confirm(`Mettre à jour ${e.name} (${e.installed} → ${e.latest}) ?\n${e.long || ''}. L’ancienne version reste installée.`)) return;
  try { const r = await api(e.action.url, e.action.body); watchJob(r.job.id, 'upd-job-' + e.id, loadEngineUpdates); }
  catch (err) { toast(err.message, 6000); }
}

// ------------------------------------------------------------------ modèles
async function scanUpdates(force) {
  const btn = $('upd-scan'); btn.disabled = true;
  if (force || !U.scan) $('upd-models').innerHTML = '<p class="muted">Recherche sur Hugging Face…</p>';
  try { U.scan = await api('/api/updates/models' + (force ? '?refresh=1' : '')); }
  catch (e) { $('upd-models').innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; btn.disabled = false; return; }
  btn.disabled = false;
  U.fiches = {};
  renderScan();
}
function renderScan() {
  const s = U.scan;
  $('upd-when').textContent = `dernière recherche : ${new Date(s.at * 1000).toLocaleString('fr-FR')} · ${s.refs} modèles comparés`;
  if (!s.groups.length) { $('upd-models').innerHTML = '<p class="muted">Rien de nouveau : tous vos modèles sont à leur dernière version connue.</p>'; return; }
  const all = s.groups.flatMap(g => g.candidates), nv = all.filter(c => c.type === 'version').length;
  const hasV = (g) => g.candidates.some(c => c.type === 'version');
  const part = (kind, title) => {
    // les groupes qui ont une nouvelle version d’abord, les simples variantes ensuite
    const gs = s.groups.map((g, i) => [g, i]).filter(([g]) => g.kind === kind).sort(([a], [b]) => hasV(b) - hasV(a));
    if (!gs.length) return '';
    return `<h3 class="small muted" style="margin:14px 0 6px">${title}</h3>` + gs.map(([g, i]) => `
      <div class="repo"><div><b>${esc(g.ref.name)}</b> <span class="badge">${esc(g.ref.source)}</span> <span class="muted small mono">${esc(g.ref.repo)}</span> <span class="muted small">publié le ${esc(g.ref_created)}</span></div>
        ${g.candidates.map((c, j) => `<div class="updc">
          <div><span class="badge ${c.type === 'version' ? 'acc' : ''}">${c.type === 'version' ? 'nouvelle version' : 'variante'}</span>
            <a href="https://huggingface.co/${esc(c.repo)}" target="_blank" class="mono">${esc(c.repo)}</a>
            <span class="muted small">${esc(c.created)} · ${fmtN(c.downloads)} téléchargements${c.pipeline ? ' · ' + esc(c.pipeline) : ''}</span></div>
          <div class="row-inline" style="margin:4px 0">${kind === 'llm'
            ? `<button class="btn small primary" onclick="acceptLlm(${i},${j})">${g.ref.in_catalog ? 'Remplacer dans le catalogue' : 'Ajouter au catalogue'}</button>
               <button class="btn small" onclick="previewLlm('${esc(c.repo)}')">voir les quantifications</button>`
            : `<button class="btn small primary" onclick="openFiche(${i},${j})">Préparer la fiche…</button>`}
            <button class="btn small" onclick="ignoreCand('${esc(c.repo)}')">ignorer</button></div>
          <div id="fiche-${i}-${j}"></div></div>`).join('')}
      </div>`).join('');
  };
  $('upd-models').innerHTML = `<p><b>${nv}</b> nouvelle${nv > 1 ? 's' : ''} version${nv > 1 ? 's' : ''} et <b>${all.length - nv}</b> variante${all.length - nv > 1 ? 's' : ''} trouvées.</p>`
    + part('llm', 'TEXTE (LLM)') + part('gen', 'IMAGE ET VIDÉO');
}
async function ignoreCand(repo) {
  try { await api('/api/updates/ignore', {repo}); } catch (e) { toast(e.message); return; }
  toast(repo + ' ne sera plus proposé'); scanUpdates(false); loadUserChoices();
}
function previewLlm(repo) { showMode('text'); showTab('catalog'); openRepo(repo); $('repo-panel').scrollIntoView({behavior: 'smooth'}); }
async function acceptLlm(i, j) {
  const g = U.scan.groups[i], c = g.candidates[j];
  if (!confirm(g.ref.in_catalog ? `Remplacer « ${g.ref.name} » par ${c.repo} dans le catalogue texte ?\nLes fichiers déjà téléchargés sont conservés.` : `Ajouter ${c.repo} au catalogue texte ?`)) return;
  try { await api('/api/updates/accept', {kind: 'llm', repo: c.repo, replaces: g.ref.in_catalog ? g.ref.repo : null}); }
  catch (e) { toast(e.message, 6000); return; }
  toast('Catalogue mis à jour'); loadCatalog(); scanUpdates(false); loadUserChoices();
  if (confirm('Choisir maintenant une quantification de ' + c.repo + ' et la télécharger ?')) previewLlm(c.repo);
}

// ------------------------------------------------------------------ fiche image / vidéo
async function openFiche(i, j) {
  const g = U.scan.groups[i], c = g.candidates[j], box = $(`fiche-${i}-${j}`);
  box.innerHTML = '<p class="muted small">Lecture du dépôt et de la documentation de stable-diffusion.cpp…</p>';
  let f; try { f = await api(`/api/updates/prefill?ref=${encodeURIComponent(g.ref.key)}&repo=${encodeURIComponent(c.repo)}&type=${c.type}`); }
  catch (e) { box.innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; return; }
  U.fiches[`${i}-${j}`] = f;
  const d = f.defaults, isV = f.kind === 'video', id = `${i}-${j}`;
  const num = (k, step) => `<input type="number" step="${step || 1}" data-def="${k}" value="${d[k] ?? ''}" style="width:80px">`;
  box.innerHTML = `<div class="fiche" id="fiche-form-${id}">
    ${f.warnings.map(w => `<div class="warn-item warn small">⚠ ${esc(w)}</div>`).join('')}
    ${f.notes.map(n => `<div class="muted small">ℹ ${esc(n)}</div>`).join('')}
    ${f.doc ? `<div class="small">Source : <a href="${esc(f.doc.url)}" target="_blank">docs/${esc(f.doc.name)}</a> de stable-diffusion.cpp — vérifiez la fiche avec elle.</div>` : ''}
    <div class="grow"><label>Nom</label><div><input data-k="name" value="${esc(f.name)}" size="40"></div></div>
    <div class="grow"><label>Licence</label><div><input data-k="license" value="${esc(f.license)}" size="40"></div></div>
    <div class="grow"><label>Description</label><div><textarea data-k="blurb" rows="3" style="width:100%">${esc(f.blurb)}</textarea></div></div>
    <div class="grow"><label>Avertissement</label><div><textarea data-k="notice" rows="2" style="width:100%" placeholder="(aucun)">${esc(f.notice)}</textarea>${f.notice ? '<span class="muted small">repris de l’ancien modèle : à vérifier pour le nouveau</span>' : ''}</div></div>
    <div class="glab">Fichiers</div>
    ${f.files.map((x, k) => `<div class="grow"><label>${esc(x.label)}</label><div style="flex-direction:column;align-items:stretch;gap:2px">
      <div class="row-inline" style="margin:0"><input data-file="${k}" data-part="repo" value="${esc(x.repo)}" placeholder="auteur/dépôt" size="30">
        <input data-file="${k}" data-part="file" value="${esc(x.file)}" placeholder="chemin/fichier.gguf" size="40">${x.size ? `<span class="muted small">${fmtGB(x.size)}</span>` : ''}</div>
      <span class="muted small">${esc(x.source)}${x.hint ? ` · l’exemple de la doc utilise <span class="mono">${esc(x.hint)}</span>` : ''}</span></div></div>`).join('')}
    <div class="glab">Réglages conseillés</div>
    <div class="grow"><label>Étapes / CFG</label><div>${num('steps')} étapes ${d.high_noise_steps ? '+ ' + num('high_noise_steps') + ' « bruit fort »' : ''} · CFG ${num('cfg', 0.5)}
      <label class="switch"><input type="checkbox" data-def="negative" ${d.negative ? 'checked' : ''}> prompt négatif</label></div></div>
    <div class="grow"><label>Sampler</label><div><select data-def="sampler"><option value="">défaut du modèle</option>${SAMPLERS.map(v => `<option ${v === d.sampler ? 'selected' : ''}>${v}</option>`).join('')}</select></div></div>
    <div class="grow"><label>Taille</label><div>${num('width', 16)} × ${num('height', 16)} px</div></div>
    ${isV ? `<div class="grow"><label>Vidéo</label><div>${num('frames')} images à ${num('fps')} i/s · flow shift ${num('flow_shift', 0.5)}</div></div>` : ''}
    <div class="grow"><label>Entrées</label><div>${Object.entries(FEATS).map(([k, l]) => `<label class="switch"><input type="checkbox" data-feat="${k}" ${f.features.includes(k) ? 'checked' : ''}> ${l}</label>`).join(' ')}</div></div>
    <div class="row-inline" style="margin-top:10px"><button class="btn primary" onclick="acceptFiche('${id}')">✓ Valider : remplacer « ${esc(U.scan.groups[i].ref.name)} »</button>
      <button class="btn" onclick="$('fiche-${id}').innerHTML=''">annuler</button></div></div>`;
}
async function acceptFiche(id) {
  const f = U.fiches[id], form = $('fiche-form-' + id);
  const entry = {...f, defaults: {...f.defaults}, files: f.files.map(x => ({...x})), features: []};
  form.querySelectorAll('[data-k]').forEach(el => { entry[el.dataset.k] = el.value; });
  form.querySelectorAll('[data-file]').forEach(el => { entry.files[+el.dataset.file][el.dataset.part] = el.value.trim(); });
  form.querySelectorAll('[data-def]').forEach(el => { entry.defaults[el.dataset.def] = el.type === 'checkbox' ? el.checked : el.value; });
  form.querySelectorAll('[data-feat]').forEach(el => { if (el.checked) entry.features.push(el.dataset.feat); });
  let r; try { r = await api('/api/updates/accept', {kind: 'gen', entry}); } catch (e) { toast(e.message, 8000); return; }
  toast(`« ${entry.name} » remplace l’ancien modèle dans l’onglet ${f.kind === 'video' ? 'Vidéo' : 'Image'} (à télécharger)`, 6000);
  G.models = {}; scanUpdates(false); loadUserChoices();
}

// ------------------------------------------------------------------ choix validés
async function loadUserChoices() {
  let u; try { u = await api('/api/updates/user'); } catch (e) { return; }
  const row = (label, sub, kind, key) => `<div class="row-inline" style="margin:4px 0"><span><b>${esc(label)}</b> <span class="muted">${esc(sub)}</span></span>
    <button class="btn small" onclick="revertChoice('${kind}','${esc(key)}')">${kind === 'ignored' ? 'ne plus ignorer' : 'annuler'}</button></div>`;
  const items = [
    ...u.llm.map(e => row(e.name, `${e.repo} · ${e.replaces ? 'remplace ' + e.replaces : 'ajouté'} · ${e.added}`, 'llm', e.repo)),
    ...u.gen.map(e => row(e.name, `${e.repo || ''} · ${e.replaces ? 'remplace ' + e.replaces : 'ajouté'} · ${e.added}`, 'gen', e.id)),
    ...u.ignored.map(r => row(r, 'ignoré', 'ignored', r)),
  ];
  $('upd-user').innerHTML = items.length ? '<p class="muted">« annuler » remet l’ancien modèle au catalogue (rien n’est supprimé du disque).</p>' + items.join('') : '<p class="muted">Aucun pour l’instant.</p>';
}
async function revertChoice(kind, key) {
  if (kind !== 'ignored' && !confirm('Annuler ce remplacement ? L’ancien modèle revient au catalogue.')) return;
  try { await api('/api/updates/revert', {kind, key}); } catch (e) { toast(e.message); return; }
  G.models = {}; if (kind === 'llm') loadCatalog();
  loadUserChoices(); scanUpdates(false);
}
