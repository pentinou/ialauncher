/* IA Launcher — onglets Image, Vidéo et Musique.
   Réutilise les utilitaires d'app.js ($, api, esc, fmtG, toast, watchJob, renderJob).
   Image et vidéo passent par stable-diffusion.cpp (sd-server), la musique par ACE-Step. */
'use strict';

const G = {
  mode: 'text', status: null, models: {}, sel: {}, loras: {}, form: {}, inputs: {image: {}, video: {}}, viewing: {}, node: {}, families: {}, prev: {},
};
const SAMPLERS = ['euler', 'euler_a', 'heun', 'dpm2', 'dpm++2s_a', 'dpm++2m', 'dpm++2mv2', 'dpm++2m_sde', 'ipndm', 'ipndm_v', 'lcm', 'ddim_trailing', 'tcd', 'res_multistep', 'res_2s', 'er_sde', 'lms'];
const SCHEDULERS = ['discrete', 'karras', 'exponential', 'ays', 'gits', 'sgm_uniform', 'simple', 'smoothstep', 'kl_optimal', 'beta'];
const RATIOS = [['1:1', 1, 1], ['4:3', 4, 3], ['3:4', 3, 4], ['16:9', 16, 9], ['9:16', 9, 16], ['3:2', 3, 2], ['2:3', 2, 3]];
const LANGS = [['fr', 'français'], ['en', 'anglais'], ['es', 'espagnol'], ['de', 'allemand'], ['it', 'italien'], ['pt', 'portugais'], ['ja', 'japonais'], ['ko', 'coréen'], ['zh', 'chinois'], ['ru', 'russe'], ['ar', 'arabe'], ['hi', 'hindi']];
const LS = {
  get(k, d) { try { const v = localStorage.getItem('ialauncher.' + k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('ialauncher.' + k, JSON.stringify(v)); } catch (e) { /* stockage indisponible */ } },
};
G.nsfw = LS.get('nsfw', false);   // case « Contenu adulte » : vignettes et recherche Civitai

// ------------------------------------------------------------------ navigation
function showMode(m) {
  G.mode = m;
  document.querySelectorAll('.mode').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
  ['text', 'image', 'video', 'music', 'storage', 'updates', 'logs'].forEach(x => $('mode-' + x).classList.toggle('hidden', x !== m));
  LS.set('mode', m);
  if (m === 'image' || m === 'video') loadVisual(m);
  if (m === 'music') loadMusic();
  if (m === 'storage') loadStorage();
  if (m === 'updates') loadUpdates();
  if (m === 'logs') loadLogs();
}

async function refreshGenStatus() {
  try { G.status = await api('/api/gen/status'); } catch (e) { return; }
  if (G.mode === 'image' || G.mode === 'video') { renderSdEngine(G.mode); renderServiceLine(G.mode); }
  if (G.mode === 'music') { renderMusicEngine(); renderServiceLine('music'); }
}

// ------------------------------------------------------------------ image / vidéo : squelette
function visualSkeleton(kind) {
  const isV = kind === 'video';
  return `
  <section class="card"><h1>Moteur : stable-diffusion.cpp</h1>
    <p class="lead">Le « llama.cpp de l'image » : même bibliothèque (ggml), mêmes fichiers GGUF quantifiés, compilé pour votre carte. Il lit aussi vos checkpoints de Stable Diffusion WebUI / Forge, sans ComfyUI ni Python. Le launcher lance <code>sd-server</code> en arrière-plan et lui envoie vos réglages.</p>
    <div id="sd-engine-${kind}"></div></section>
  <section class="card adultbar"><label class="switch"><input type="checkbox" class="nsfw-box" ${G.nsfw ? 'checked' : ''} onchange="setNsfw(this.checked)"> 🔞 <b>Contenu adulte</b></label>
    <span class="muted small">Décoché : images Civitai tout public seulement, dans les vignettes des modèles, checkpoints et LoRA comme dans les résultats de recherche. Coché : tout est montré. Vaut pour les onglets Image et Vidéo.</span></section>
  <section class="card"><h1><span class="num">1</span> Modèle</h1>
    <p class="lead">${isV ? 'Un modèle vidéo = un transformer de diffusion + un encodeur de texte + un VAE (qui décode les images latentes en pixels). Le launcher télécharge les trois.' : 'Tous ces modèles tournent dans le même moteur (stable-diffusion.cpp, ci-dessus).'} Choisissez un modèle, puis ses poids (checkpoint), puis d’éventuels LoRA. Il manque un modèle ? « Chercher sur Civitai », type « Modèles de base ».</p>
    <div id="gm-${kind}"></div></section>
  <section class="card"><h1><span class="num">2</span> ${isV ? 'Générer une vidéo' : 'Générer'}</h1>
    <div id="gsvc-${kind}" class="small muted"></div>
    <div class="genwrap"><div id="gf-${kind}" class="genform"></div><div id="go-${kind}" class="genout"><p class="muted">Le résultat s'affichera ici.</p></div></div></section>
  <section class="card"><h1>Galerie <button class="link small" onclick="loadOutputs('${kind}')">↻</button></h1>
    <p class="lead small">Chaque fichier est enregistré avec ses réglages à côté (.json). Cliquez sur une ${isV ? 'vidéo' : 'image'} pour la revoir et reprendre ses réglages ; « ☆ garder » l’affiche ici avec son prompt et ses paramètres, et la conserve dans la galerie même quand elle n’est plus parmi les 60 dernières.</p>
    <div id="gd-${kind}" class="small"></div>
    <div id="gg-${kind}" class="gallery"></div></section>`;
}

async function loadVisual(kind) {
  const root = $('mode-' + kind);
  if (!root.dataset.ready) { root.innerHTML = visualSkeleton(kind); root.dataset.ready = '1'; }
  await refreshGenStatus();
  await loadModels(kind);
  loadOutputs(kind);
}

// ------------------------------------------------------------------ moteur sd.cpp
function renderSdEngine(kind) {
  const box = $('sd-engine-' + kind); if (!box || !G.status) return;
  const e = G.status.sd, p = e.plan;
  let h = '';
  if (e.installed) {
    h += `<div class="plan"><div><b>Installé :</b> ${esc(e.variant)} <span class="mono small">${esc(e.version)}</span><br><span class="muted small mono">${esc(e.bin)}</span></div>
      <div><button class="btn small" onclick="toggle('sd-more-${kind}')">changer / mettre à jour</button></div></div><div id="sd-more-${kind}" class="hidden" style="margin-top:10px">`;
  }
  h += `<div class="plan"><div><b>Conseillé pour cette machine :</b> ${esc(p.label)}<br><span class="muted small">${esc(p.reason)}</span></div>
    <div><button class="btn primary" onclick="installSd('${p.method}','${p.variant}','${esc(p.label)}')">${e.installed ? 'Réinstaller (dernière version)' : 'Installer automatiquement'}</button></div></div>`;
  if (e.installed) h += '</div>';
  h += `<div id="sd-job-${kind}"></div>`;
  box.innerHTML = h;
}
async function installSd(method, variant, label) {
  if (!confirm(`Installer stable-diffusion.cpp « ${label} » ?\n${method === 'build' ? 'Compilation locale CUDA : 10 à 20 minutes.' : 'Téléchargement de quelques centaines de Mo.'}`)) return;
  try {
    const r = await api('/api/gen/engine/install', {method, variant, label});
    watchJob(r.job.id, 'sd-job-' + G.mode, refreshGenStatus);
  } catch (e) { toast(e.message); }
}

function renderServiceLine(kind) {
  const box = $('gsvc-' + kind); if (!box || !G.status) return;
  const s = kind === 'music' ? G.status.music.service : G.status.sd.service;
  const lbl = {stopped: 'aucun modèle chargé', starting: 'chargement en cours…', ready: 'modèle chargé en mémoire', error: 'erreur'}[s.state];
  box.innerHTML = `<div class="row-inline"><span class="pill ${s.state === 'ready' ? 'ok' : s.state === 'error' ? 'err' : s.state === 'starting' ? 'busy' : ''}">${kind === 'music' ? 'ACE-Step' : 'sd-server'} : ${lbl}</span>
    ${s.state !== 'stopped' && s.info && s.info.model ? `<span class="mono">${esc(s.info.model)}</span>` : ''}
    ${s.state !== 'stopped' ? `<button class="btn small" onclick="stopService('${kind === 'music' ? 'music' : 'sd'}')" title="décharge le modèle et libère la VRAM">libérer la carte graphique</button>` : ''}
    <span class="muted">Un seul gros modèle tient sur la carte : lancer une génération arrête le serveur de texte et l'autre générateur.</span></div>
    ${s.state === 'error' ? `<div class="warn-item error">${esc(s.error)}</div>` : ''}
    ${s.log && s.log.length > 1 ? `<details class="log"><summary>Journal de ${kind === 'music' ? 'ACE-Step' : 'sd-server'}</summary><pre>${esc(s.log.join('\n'))}</pre></details>` : ''}`;
}
async function stopService(svc) { await api('/api/gen/stop', {service: svc}); refreshGenStatus(); }

// ------------------------------------------------------------------ modèles
async function loadModels(kind) {
  try { const r = await api('/api/gen/models?kind=' + kind); G.models[kind] = r.models; G.tags = r.tags; G.webui = r.webui; G.families = r.families; }
  catch (e) { $('gm-' + kind).innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; return; }
  if (!G.node[kind]) G.node[kind] = LS.get('node.' + kind);
  if (!G.sel[kind]) {
    const saved = LS.get('model.' + kind);
    const m = G.models[kind].find(x => x.id === saved) || G.models[kind].find(x => x.installed && !x.local) || null;
    if (m && m.installed) selectGenModel(kind, m.id, true);
  }
  renderModels(kind);
  if (!G.sel[kind]) renderForm(kind);
  loadPreviews(kind);
}
// Images et identité Civitai (nom, base) des modèles, checkpoints et LoRA : cherchées une fois, gardées en cache
async function loadPreviews(kind) {
  try { G.prev[kind] = await api('/api/gen/previews?kind=' + kind); } catch (e) { return; }
  renderModels(kind);
}
const prevF = (kind, path) => ((G.prev[kind] || {}).files || {})[path];
const prevB = (kind, base) => ((G.prev[kind] || {}).bases || {})[base];
function pimg(kind, info) {
  const adult = G.nsfw, im = info && (info.images || []).find(i => adult || i.nsfw <= 1);
  return im ? im.url : '';
}
const baseOf = (kind, m) => m.base || (prevF(kind, m.path) || {}).baseModel || '';
const ckImg = (kind, m) => m.local ? (m.image || pimg(kind, prevF(kind, m.path))) : pimg(kind, prevB(kind, m.base));

// Arborescence en trois niveaux : modèle (famille) → checkpoint (les poids utilisés) → LoRA.
// Un checkpoint Civitai ou de la WebUI se range sous le modèle dont il dérive ; un LoRA vaut
// pour toute la famille, et d'abord pour les checkpoints de la même base que lui.
function treeNodes(kind) {
  const list = G.models[kind] || [];
  const nodes = list.filter(m => !m.local).map(m => ({key: m.id, entry: m, family: m.family, name: m.name}));
  const fams = new Set(nodes.map(n => n.family)), extra = {};
  for (const m of list.filter(x => x.local)) {
    const f = m.family || m.arch || 'autre';
    if (!fams.has(f)) (extra[f] = extra[f] || []).push(m);
  }
  if (kind === 'image') ['sdxl', 'sd15'].forEach(f => { extra[f] = extra[f] || []; });
  for (const f of Object.keys(extra)) nodes.push({key: 'fam:' + f, family: f, name: famLabel(f) || 'Autres checkpoints'});
  // d'abord ce qui est utilisable tout de suite
  const ready = n => n.entry ? n.entry.installed : nodeCkpts(kind, n).some(m => m.installed);
  return nodes.map((n, i) => [n, i]).sort((a, b) => (ready(b[0]) - ready(a[0])) || a[1] - b[1]).map(x => x[0]);
}
function nodeCkpts(kind, n) {
  return (G.models[kind] || []).filter(m => m.local && (m.family || m.arch || 'autre') === n.family);
}
function nodeOf(kind, m, nodes) {
  if (!m) return null;
  return nodes.find(n => n.key === m.id) || nodes.find(n => m.local && n.family === (m.family || m.arch || 'autre'));
}
// Carrousel horizontal (‹ ›, flèches du clavier) : `step` = début d'appel complété par « ,-1) » ou « ,1) »
function carHtml(id, cards, step) {
  return `<div class="carwrap"><button class="carbtn" onclick="${step},-1)" title="précédent">‹</button>
    <div class="carousel" id="${id}" tabindex="0" onkeydown="if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();${step},event.key==='ArrowLeft'?-1:1)}">${cards}</div>
    <button class="carbtn" onclick="${step},1)" title="suivant">›</button></div>`;
}
const carLeft = (id) => { const c = $(id); return c ? c.scrollLeft : 0; };
// remet le défilement d'avant le redessin, puis amène la carte choisie au centre si elle est cachée
function carRestore(id, left, center) {
  const car = $(id); if (!car) return;
  car.scrollLeft = left;
  const sc = center && car.querySelector('.mcard.sel');
  if (sc && (sc.offsetLeft < car.scrollLeft || sc.offsetLeft + sc.offsetWidth > car.scrollLeft + car.clientWidth)) {
    car.scrollTo({left: sc.offsetLeft - (car.clientWidth - sc.offsetWidth) / 2, behavior: 'smooth'});
  }
}
function scrollCar(id, d) { const c = $(id); if (c) c.scrollBy({left: d * c.clientWidth * 0.8, behavior: 'smooth'}); }
function renderModels(kind) {
  const nodes = treeNodes(kind), sel = G.sel[kind];
  G.node = G.node || {};
  const cur = nodes.find(n => n.key === G.node[kind]) || nodeOf(kind, sel, nodes) || nodes[0];
  if (!cur) { $('gm-' + kind).innerHTML = '<p class="muted">Aucun modèle.</p>'; return; }
  G.node[kind] = cur.key;
  const fam = G.families[cur.family] || {};
  const card = (n) => {
    const e = n.entry, ck = nodeCkpts(kind, n);
    const nInst = ck.filter(m => m.installed).length;
    const state = e ? (e.installed ? `<span class="badge ok">✓ téléchargé</span> <span class="muted small">${fmtGB(e.size)}</span>`
      : `<button class="btn primary small" onclick="event.stopPropagation();downloadGenModel('${kind}','${e.id}')">⬇ Télécharger ${fmtGB(e.missing)}</button>`)
      : `<span class="muted small">${ck.length} checkpoint${ck.length > 1 ? 's' : ''} sur ce PC</span>`;
    const img = e ? ckImg(kind, e) : ck.map(m => ckImg(kind, m)).find(Boolean);
    return `<div class="mcard ${n.key === cur.key ? 'sel' : ''} ${(e ? e.installed : nInst) ? '' : 'dim'}" data-key="${esc(n.key)}" onclick="pickNode('${kind}', this.dataset.key)">
      <div class="mimg">${img ? `<img src="${esc(img)}" loading="lazy" alt="">` : `<span>${esc(n.name.slice(0, 2))}</span>`}</div>
      <div class="n">${esc(n.name)}${e ? ` <span class="badge">${esc(e.license)}</span>` : ''}${e && e.added ? ` <span class="badge acc" title="ajouté ou validé dans l’onglet Mises à jour / Civitai">nouveau · ${esc(e.added)}</span>` : ''}</div>
      ${e ? `<div class="s">${e.tags.map(t => `<span class="badge" title="${esc((G.tags || {})[t] || '')}">${esc(t)}</span>`).join('')}</div>` : ''}
      <div class="blurb">${esc(e ? e.blurb : n.family === 'autre' ? 'Checkpoints dont l’architecture n’a pas été reconnue : sd.cpp la détectera au chargement.' : 'Checkpoints « tout-en-un » (un seul fichier) : ceux de votre WebUI, de vos dossiers et de Civitai.')}</div>
      ${e && e.notice ? `<div class="warn-item warn small">⚠ ${esc(e.notice)}</div>` : ''}
      <div class="mfoot">${state}${nInst && e ? ` <span class="muted small">+ ${nInst} checkpoint${nInst > 1 ? 's' : ''}</span>` : ''}</div>
      ${e ? `<div id="gdl-${e.id}" onclick="event.stopPropagation()"></div>` : ''}
    </div>`;
  };
  let h = `<div class="tpanel"><div class="thead"><span class="tstep">niveau 1</span><b>Modèle</b><span class="muted small">${nodes.length} familles · ← → pour parcourir</span></div>
    ${carHtml('car-' + kind, nodes.map(card).join(''), `scrollCar('car-${kind}'`)}</div>`;
  // niveau 2 : checkpoints du modèle choisi
  const items = (cur.entry ? [cur.entry] : []).concat(nodeCkpts(kind, cur));
  const civFam = fam.civitai && fam.civitai.length && G.families[cur.family] ? cur.family : '';
  h += `<div class="tpanel"><div class="thead"><span class="tstep">niveau 2</span><b>Checkpoint</b><span class="muted small">de ${esc(cur.name)} — les poids utilisés pour générer${cur.entry ? ' : ceux d’origine, ou une version retravaillée par la communauté' : ''}</span>
    ${civFam && kind === 'image' ? `<button class="link small tact" onclick="civOpen('${kind}','Checkpoint','${civFam}')">🔎 chercher des checkpoints ${esc(fam.label)} sur Civitai</button>` : ''}</div>`;
  h += items.length ? carHtml('ckcar-' + kind, items.map(m => {
    const official = !m.local, on = sel && sel.id === m.id, pv = official ? null : prevF(kind, m.path), img = ckImg(kind, m), base = baseOf(kind, m);
    return `<div class="mcard ck ${on ? 'sel' : ''} ${m.installed ? '' : 'dim'}" data-id="${esc(m.id)}" onclick="pickCkpt('${kind}', this.dataset.id)" title="${esc((m.path || '') + (m.blurb ? '\n' + m.blurb : ''))}">
      <div class="mimg">${img ? `<img src="${esc(img)}" loading="lazy" alt="">` : `<span>${official ? '★' : '◆'}</span>`}</div>
      <div class="n">${official ? 'Poids d’origine' : esc(m.name)}${m.version ? ` <span class="muted small">${esc(m.version)}</span>` : ''}</div>
      ${pv && pv.found && m.source !== 'Civitai' ? `<div class="small muted">Civitai : <a href="${esc(pv.url)}" target="_blank" onclick="event.stopPropagation()">${esc(pv.name)}${pv.version ? ' · ' + esc(pv.version) : ''} ↗</a></div>` : ''}
      <div class="s">${base ? `<span class="badge acc">${esc(base)}</span>` : ''}<span>${fmtGB(m.size)}</span><span>${official ? esc(m.name) : esc(m.source)}</span></div>
      ${m.installed ? '' : `<div><span class="badge warn">${official ? 'à télécharger (carte du modèle)' : 'modèle de base manquant'}</span></div>`}
      ${official ? `<details class="small muted mfoot" onclick="event.stopPropagation()"><summary>fichiers</summary>${m.files.map(f => `<div>${f.present ? '✓' : '·'} ${esc(f.label)} : <span class="mono">${esc(f.path.split('/').pop())}</span> (${fmtGB(f.size)})</div>`).join('')}</details>` : ''}
    </div>`;
  }).join(''), `scrollCar('ckcar-${kind}'`) : '<p class="muted small">Aucun checkpoint de cette famille pour l’instant.</p>';
  h += '</div>';
  // niveau 3 : LoRA
  h += `<div class="tpanel"><div class="thead"><span class="tstep">niveau 3</span><b>LoRA</b><span class="muted small" id="glorah-${kind}"></span>
    ${civFam ? `<button class="link small tact" onclick="civOpen('${kind}','LORA','${civFam}')">🔎 chercher des LoRA ${esc(fam.label)} sur Civitai</button>` : ''}</div>
    <div class="muted small">Un LoRA ajoute un style, un personnage ou un concept. Il est entraîné sur un modèle de base : il marche avec les checkpoints de sa famille, d’abord ceux de la même base. Cliquez pour l’ajouter au prompt, recliquez pour l’en retirer.</div>
    <div id="glora-${kind}"></div></div>`;
  if (kind === 'image') {
    h += `<div class="row-inline muted small" style="margin-top:12px"><span>Checkpoints déjà sur ce PC ${G.webui && G.webui.length ? '(repris de ' + esc(G.webui.map(w => w.split('/').pop()).join(', ')) + ')' : ''} — ajouter un dossier :</span><input id="gen-add-dir" placeholder="/chemin/vers/dossier" size="34"><button class="btn small" onclick="addGenDir()">ajouter</button></div>`;
  }
  h += `<div class="small muted" style="margin-top:6px">${S.hw ? fmtGB(S.hw.disk.free) + ' libres sur le disque · ' : ''}<button class="link" onclick="showMode('storage')">voir et libérer l’espace occupé →</button></div>`;
  h += civitaiPanel(kind);
  const left = carLeft('car-' + kind), ckLeft = carLeft('ckcar-' + kind);
  $('gm-' + kind).innerHTML = h;
  carRestore('car-' + kind, left, true);
  carRestore('ckcar-' + kind, ckLeft, true);
  renderLoras(kind);
  renderCivitai(kind);
}
function pickNode(kind, key) {
  if (G.node[kind] === key) return;
  G.node[kind] = key; LS.set('node.' + kind, key);
  const nodes = treeNodes(kind), n = nodes.find(x => x.key === key), sel = G.sel[kind];
  const inNode = sel && nodeOf(kind, sel, nodes) === n;
  if (!inNode) {
    const first = n.entry && n.entry.installed ? n.entry : nodeCkpts(kind, n).find(m => m.installed);
    if (first) { selectGenModel(kind, first.id); return; }
  }
  renderModels(kind);
}
// Les carrousels se parcourent sans rien changer (‹ ›, clavier, défilement) : seul un clic sur
// un autre checkpoint le sélectionne, et recharge ses LoRA et ses réglages conseillés.
function pickCkpt(kind, id) {
  if (G.sel[kind] && G.sel[kind].id === id) return;
  selectGenModel(kind, id);
}
const fmtGB = (b) => (b / 1e9).toFixed(b < 1e9 ? 2 : 1).replace('.', ',') + ' Go';
async function addGenDir() {
  try { await api('/api/gen/dirs', {path: $('gen-add-dir').value.trim()}); toast('Dossier ajouté'); loadModels('image'); } catch (e) { toast(e.message); }
}
async function downloadGenModel(kind, id) {
  const m = G.models[kind].find(x => x.id === id);
  if (m.notice && !confirm(m.notice + '\n\nConfirmez-vous avoir le droit d’utiliser ce modèle ?')) return;
  if (!confirm(`Télécharger ${m.name} (${fmtGB(m.missing)}) ?`)) return;
  try {
    const r = await api('/api/gen/download', {id});
    watchJob(r.job.id, 'gdl-' + id, async (j) => { await loadModels(kind); if (j.state === 'done') selectGenModel(kind, id); });
  } catch (e) { toast(e.message); }
}
function selectGenModel(kind, id, silent) {
  const m = (G.models[kind] || []).find(x => x.id === id); if (!m) return;
  if (!m.installed) { toast(m.companion ? m.blurb : 'Téléchargez d’abord ce modèle', 6000); return; }
  G.sel[kind] = m;
  LS.set('model.' + kind, id);
  const nd = nodeOf(kind, m, treeNodes(kind)); if (nd) { G.node[kind] = nd.key; LS.set('node.' + kind, nd.key); }
  const d = m.defaults || {};
  const f = G.form[kind] = Object.assign({prompt: '', negative: '', seed: -1, batch: 1, hires: false, hires_scale: 1.5, hires_denoise: 0.5, hires_steps: 0, hires_upscaler: 'Latent',
    strength: 0.75, offload: 'auto', vae_tiling: false, flash_attn: true, clip_skip: -1, input_mode: 'init'}, LS.get('form.' + kind, {}));
  // réglages propres au modèle : ceux conseillés par ses auteurs
  Object.assign(f, {width: d.width, height: d.height, steps: d.steps, cfg: d.cfg, sampler: d.sampler || '', scheduler: d.scheduler || '',
    high_noise_steps: d.high_noise_steps || 0, frames: d.frames || 0, fps: d.fps || 0, flow_shift: d.flow_shift || 0, clip_skip: d.clip_skip ?? -1});
  if (d.negative_prompt && !f.negative.includes(d.negative_prompt)) f.negative = d.negative_prompt + (f.negative ? ', ' + f.negative : '');
  if (!silent) renderModels(kind);
  renderForm(kind);
  loadLoras(kind);
}

// ------------------------------------------------------------------ formulaire
function renderForm(kind) {
  const box = $('gf-' + kind); if (!box) return;
  const m = G.sel[kind];
  if (!m) { box.innerHTML = '<p class="muted">Choisissez un modèle ci-dessus (téléchargé, ou un checkpoint de votre WebUI).</p>'; return; }
  const f = G.form[kind], d = m.defaults || {}, isV = kind === 'video';
  const feats = m.features || [];
  const num = (k, min, max, step, w) => `<input type="number" min="${min}" max="${max}" step="${step || 1}" value="${f[k]}" style="width:${w || 90}px" onchange="setGen('${kind}','${k}', +this.value)">`;
  const opt = (arr, cur, dflt) => (dflt ? `<option value="">${dflt}</option>` : '') + arr.map(v => `<option value="${v}" ${v === cur ? 'selected' : ''}>${v}</option>`).join('');
  let h = `<label class="glab">Prompt <span class="muted small">${m.local ? '— les balises <span class="mono">&lt;lora:nom:0.8&gt;</span> marchent comme dans la WebUI' : ''}</span></label>
    <textarea id="gp-${kind}" rows="4" placeholder="${isV ? 'Décrivez la scène ET le mouvement : sujet, action, caméra (travelling, plan fixe…), lumière, style.' : 'Décrivez l’image : sujet, composition, lumière, style…'}" oninput="setGen('${kind}','prompt',this.value,true)">${esc(f.prompt)}</textarea>`;
  if (d.negative) h += `<label class="glab">Prompt négatif <span class="muted small">— ce qu’on ne veut pas voir</span></label><textarea rows="2" oninput="setGen('${kind}','negative',this.value,true)">${esc(f.negative)}</textarea>`;
  else h += '<div class="muted small">Modèle distillé (CFG 1) : il n’utilise pas de prompt négatif.</div>';
  // taille
  const base = Math.sqrt(d.width * d.height);
  h += `<div class="grow"><label>Format</label><div>${RATIOS.map(([n, a, b]) => {
    const w = Math.round(base * Math.sqrt(a / b) / 32) * 32, hh = Math.round(base * Math.sqrt(b / a) / 32) * 32;
    return `<button class="btn small ${f.width === w && f.height === hh ? 'on' : ''}" onclick="setSize('${kind}',${w},${hh})">${n}</button>`;
  }).join(' ')} ${num('width', 64, 4096, 16, 80)} × ${num('height', 64, 4096, 16, 80)} px</div></div>`;
  if (isV) {
    h += `<div class="grow"><label>Durée</label><div>${num('frames', 5, 241, 1, 70)} images à ${num('fps', 1, 60, 1, 60)} i/s ≈ <b>${(f.frames / Math.max(1, f.fps)).toFixed(1).replace('.', ',')} s</b> <span class="muted small">(${m.id.startsWith('wan') ? 'Wan arrondit à 4n+1 images' : m.id === 'minimax-h3' ? 'MiniMax-H3 : 17n+5 images, 24 i/s fixes' : ''})</span></div></div>`;
  }
  h += `<div class="grow"><label>Étapes</label><div>${num('steps', 1, 150)} ${f.high_noise_steps ? `+ ${num('high_noise_steps', 1, 150)} <span class="muted small">étapes « bruit fort »</span>` : ''} <span class="muted small">conseillé : ${d.steps}${d.high_noise_steps ? ' + ' + d.high_noise_steps : ''}</span></div></div>`;
  h += `<div class="grow"><label>CFG</label><div>${num('cfg', 0, 30, 0.5, 70)} <span class="muted small">force du prompt · conseillé : ${d.cfg}${d.cfg <= 1 ? ' (modèle distillé : ne pas monter)' : ''}</span></div></div>`;
  h += `<div class="grow"><label>Sampler</label><div><select onchange="setGen('${kind}','sampler',this.value)">${opt(SAMPLERS, f.sampler, 'défaut du modèle')}</select>
    <select onchange="setGen('${kind}','scheduler',this.value)">${opt(SCHEDULERS, f.scheduler, 'planning par défaut')}</select></div></div>`;
  if (isV && d.flow_shift) h += `<div class="grow"><label>Flow shift</label><div>${num('flow_shift', 0, 20, 0.5, 70)} <span class="muted small">plus haut = plus de mouvement global, moins de détails</span></div></div>`;
  h += `<div class="grow"><label>Graine</label><div>${num('seed', -1, 2147483647, 1, 130)} <button class="btn small" onclick="setGen('${kind}','seed',-1)" title="aléatoire">🎲</button>
    ${!isV ? ` &nbsp; Lot ${num('batch', 1, 16, 1, 60)} images` : ''}</div></div>`;
  if (m.local) h += `<div class="grow"><label>Clip skip</label><div>${num('clip_skip', -1, 12, 1, 60)} <span class="muted small">−1 = défaut ; 2 pour la plupart des modèles Pony / anime</span></div></div>`;
  // images d'entrée
  const inputs = [];
  if (!isV) inputs.push(['init', feats.includes('ref_images') ? 'Image d’entrée' : 'Image de départ (img2img)']);
  if (isV && feats.includes('init_image')) inputs.push(['init', 'Image de départ (image→vidéo)']);
  if (isV && feats.includes('end_image')) inputs.push(['end', 'Image de fin']);
  for (const [slot, label] of inputs) {
    const cur = G.inputs[kind][slot];
    h += `<div class="grow"><label>${label}</label><div><input type="file" accept="image/*" onchange="pickInput('${kind}','${slot}',this)">
      ${cur ? `<img src="${cur}" class="thumbsm"> <button class="link small" onclick="clearInput('${kind}','${slot}')">retirer</button>` : ''}</div></div>`;
  }
  if (!isV && G.inputs.image.init) {
    h += `<div class="grow"><label>Utilisation</label><div>${feats.includes('ref_images') ? `<select onchange="setGen('image','input_mode',this.value)"><option value="ref" ${f.input_mode === 'ref' ? 'selected' : ''}>référence à retoucher (décrivez la modification)</option><option value="init" ${f.input_mode === 'init' ? 'selected' : ''}>point de départ (img2img)</option></select>` : 'img2img'}
      ${f.input_mode === 'init' || !feats.includes('ref_images') ? ` · force ${num('strength', 0, 1, 0.05, 70)} <span class="muted small">0 = identique, 1 = ignore l’image</span>` : ''}</div></div>`;
  }
  if (!isV) h += `<div class="grow"><label>Hires fix</label><div><label class="switch"><input type="checkbox" ${f.hires ? 'checked' : ''} onchange="setGen('image','hires',this.checked)"> agrandir puis repasser</label>
    ${f.hires ? ` ×${num('hires_scale', 1, 4, 0.25, 60)} débruitage ${num('hires_denoise', 0, 1, 0.05, 60)} <select onchange="setGen('image','hires_upscaler',this.value)">${opt(['Latent', 'Latent (bicubic)', 'Lanczos', 'Nearest'], f.hires_upscaler)}</select>` : ''}</div></div>`;
  if (!isV) h += xyzHtml(kind);
  h += `<details class="gadv"><summary>Mémoire et avancé</summary>
    <div class="grow"><label>Poids</label><div><select onchange="setGen('${kind}','offload',this.value)">
      <option value="auto" ${f.offload === 'auto' ? 'selected' : ''}>auto — sd.cpp place les poids (VRAM, puis RAM, puis disque)</option>
      <option value="cpu" ${f.offload === 'cpu' ? 'selected' : ''}>en RAM, copiés sur le GPU à la demande (--offload-to-cpu)</option>
      <option value="disk" ${f.offload === 'disk' ? 'selected' : ''}>relus depuis le disque (économise la RAM, plus lent)</option></select></div></div>
    <div class="grow"><label>VAE par tuiles</label><div><label class="switch"><input type="checkbox" ${f.vae_tiling ? 'checked' : ''} onchange="setGen('${kind}','vae_tiling',this.checked)"> décoder par morceaux</label> <span class="muted small">moins de VRAM pour les grandes images / vidéos</span></div></div>
    <div class="grow"><label>Flash attention</label><div><label class="switch"><input type="checkbox" ${f.flash_attn ? 'checked' : ''} onchange="setGen('${kind}','flash_attn',this.checked)"> activée</label></div></div>
    <div class="muted small">Changer ces options relance sd-server (rechargement du modèle).</div></details>`;
  h += `<div class="row-inline" style="margin-top:12px"><button class="btn primary" id="grun-${kind}" onclick="runGen('${kind}')">▶ Générer</button>
    <button class="btn" onclick="resetGenDefaults('${kind}')">réglages conseillés</button></div>`;
  box.innerHTML = h;
  if (!isV) updateXyz(kind);
}
function setGen(kind, k, v, quiet) {
  G.form[kind][k] = v;
  if (k === 'prompt') markLoras(kind);
  LS.set('form.' + kind, {prompt: G.form[kind].prompt, negative: G.form[kind].negative, offload: G.form[kind].offload, vae_tiling: G.form[kind].vae_tiling, batch: G.form[kind].batch, xyz: G.form[kind].xyz});
  if (!quiet) renderForm(kind);
}
function setSize(kind, w, h) { G.form[kind].width = w; G.form[kind].height = h; renderForm(kind); }
function resetGenDefaults(kind) { const f = G.form[kind]; const p = f.prompt, n = f.negative; selectGenModel(kind, G.sel[kind].id, true); Object.assign(G.form[kind], {prompt: p, negative: n}); renderForm(kind); }
function pickInput(kind, slot, el) {
  const file = el.files[0]; if (!file) return;
  const r = new FileReader();
  r.onload = () => { G.inputs[kind][slot] = r.result; if (kind === 'image' && (G.sel.image.features || []).includes('ref_images')) G.form.image.input_mode = 'ref'; renderForm(kind); };
  r.readAsDataURL(file);
}
function clearInput(kind, slot) { delete G.inputs[kind][slot]; renderForm(kind); }

async function loadLoras(kind) {
  const m = G.sel[kind]; if (!m) return;
  try { G.loras[m.id] = await api('/api/gen/loras?model=' + encodeURIComponent(m.id)); } catch (e) { return; }
  renderLoras(kind);
}
// LoRA complétés par leur fiche Civitai (base exacte, mots déclencheurs, image)
function loraList(kind, m) {
  return ((G.loras[m.id] || {}).loras || []).map((x, i) => {
    const pv = prevF(kind, x.path) || {};
    return {...x, i, pv, family: x.family || pv.family || '', base: x.base || pv.baseModel || '', words: x.words.length ? x.words : (pv.words || []).slice(0, 3)};
  });
}
function renderLoras(kind) {
  const m = G.sel[kind], box = $('glora-' + kind); if (!box) return;
  const nodes = treeNodes(kind);
  if (!m || nodeOf(kind, m, nodes) !== nodes.find(n => n.key === G.node[kind])) { box.innerHTML = '<p class="muted small">Choisissez d’abord un checkpoint téléchargé ci-dessus.</p>'; return; }
  const l = G.loras[m.id]; if (!l) { box.innerHTML = '<p class="muted small">…</p>'; return; }
  const fam = m.family || l.family, base = baseOf(kind, m);
  const all = loraList(kind, m);
  const same = all.filter(x => fam && x.family === fam && base && x.base === base);
  const near = all.filter(x => fam && x.family === fam && !(base && x.base === base));
  const other = all.filter(x => !fam || x.family !== fam);
  const prompt = (G.form[kind] || {}).prompt || '';
  const card = (x, dim) => {
    const img = pimg(kind, x.pv);
    return `<div class="mcard ck ${prompt.includes('<lora:' + x.name + ':') ? 'sel' : ''} ${dim ? 'dim' : ''}" data-lora="${esc(x.name)}" onclick="toggleLora('${kind}', ${x.i})" title="${esc(x.path)}">
      <div class="mimg">${img ? `<img src="${esc(img)}" loading="lazy" alt="">` : '<span>◇</span>'}</div>
      <div class="n">${esc(x.pv.found ? x.pv.name : x.name)}</div>
      ${x.pv.found && x.pv.url ? `<div class="small muted">Civitai : <a href="${esc(x.pv.url)}" target="_blank" onclick="event.stopPropagation()">${x.pv.version ? esc(x.pv.version) : 'fiche'} ↗</a></div>` : ''}
      <div class="s">${x.base ? `<span class="badge acc">${esc(x.base)}</span>` : `<span class="badge">${esc(famLabel(x.family) || 'famille inconnue')}</span>`}<span>${esc(x.source)}</span></div>
      ${x.words.length ? `<div class="small muted">mots déclencheurs : ${esc(x.words.join(', '))}</div>` : ''}</div>`;
  };
  // même base d'abord, puis le reste de la famille, séparés par une étiquette verticale
  const sep = (t) => `<div class="cardsep">${t}</div>`;
  const left = carLeft('glcar-' + kind), left2 = carLeft('glcar2-' + kind), open2 = !!document.querySelector(`#glora-${kind} details[open]`);
  let h = '';
  if (same.length || near.length) {
    h += carHtml('glcar-' + kind, (same.length ? sep('Faits pour ' + esc(base)) + same.map(x => card(x)).join('') : '')
      + (near.length ? sep((same.length ? 'Autres de la famille — à essayer' : 'Famille ' + esc(famLabel(fam)))) + near.map(x => card(x)).join('') : ''), `scrollCar('glcar-${kind}'`);
  } else h += `<p class="muted small">Aucun LoRA ${esc(famLabel(fam) || '')} sur ce PC pour l’instant${G.families[fam] && G.families[fam].civitai.length ? ' : « 🔎 chercher des LoRA » ci-dessus' : ''}.</p>`;
  if (other.length) h += `<details class="small" ${open2 ? 'open' : ''}><summary class="muted">autres familles / famille inconnue (${other.length}) — sans effet ou imprévisibles sur ce modèle</summary>${carHtml('glcar2-' + kind, other.map(x => card(x, true)).join(''), `scrollCar('glcar2-${kind}'`)}</details>`;
  box.innerHTML = h + `<div class="muted small">Dossier : <span class="mono">${esc(l.dir)}</span> (y compris ceux de votre WebUI, par liens).</div>`;
  carRestore('glcar-' + kind, left);
  carRestore('glcar2-' + kind, left2);
  markLoras(kind);
}
function markLoras(kind) {
  const prompt = (G.form[kind] || {}).prompt || '';
  let n = 0;
  document.querySelectorAll(`#glora-${kind} [data-lora]`).forEach(c => { const on = prompt.includes('<lora:' + c.dataset.lora + ':'); c.classList.toggle('sel', on); n += on; });
  const m = G.sel[kind], hd = $('glorah-' + kind);
  if (hd) hd.textContent = m ? `pour ${m.name}${n ? ` — ${n} dans le prompt` : ''}` : '';
}
function toggleLora(kind, i) {
  const m = G.sel[kind], x = loraList(kind, m)[i], f = G.form[kind];
  const rx = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const tag = new RegExp('\\s*<lora:' + rx(x.name) + ':[^>]*>', 'g');
  // retirer aussi les mots déclencheurs ajoutés avec lui (juste avant la balise)
  const withWords = x.words.length ? new RegExp('(^|,\\s*)' + x.words.map(rx).join(',\\s*') + '\\s*<lora:' + rx(x.name) + ':[^>]*>') : null;
  if (tag.test(f.prompt)) f.prompt = (withWords && withWords.test(f.prompt) ? f.prompt.replace(withWords, '') : f.prompt.replace(tag, '')).trim();
  else f.prompt = (f.prompt.trim() + (x.words.length ? ', ' + x.words.join(', ') : '') + ` <lora:${x.name}:0.8>`).replace(/^, /, '').trim();
  const ta = $('gp-' + kind); if (ta) ta.value = f.prompt;
  setGen(kind, 'prompt', f.prompt, true);
}

const famLabel = (f) => (G.families[f] || {}).label || '';

// ------------------------------------------------------------------ Civitai
function civitaiPanel(kind) {
  const c = civState(kind);
  const fams = Object.keys(G.families).filter(f => G.families[f].civitai.length && (G.families[f].kind === 'video') === (kind === 'video'));
  return `<details class="civ" id="civ-${kind}" ${c.open ? 'open' : ''} ontoggle="civState('${kind}').open = this.open">
    <summary>🔎 Chercher sur Civitai — modèles de base, checkpoints et LoRA partagés par la communauté</summary>
    <div class="small muted" style="margin:6px 0">Seuls les modèles que stable-diffusion.cpp sait charger sont proposés. « Modèles de base » : les familles qui ne sont pas encore dans le launcher (ex. MOSS Image) ; leurs fichiers viennent de Hugging Face, dans les versions conseillées par la documentation de stable-diffusion.cpp, et une fiche pré-remplie vous est montrée avant tout téléchargement.${kind === 'video' ? ' Pour la vidéo, Civitai propose surtout des LoRA.' : ' Pour Z-Image, FLUX.2, Qwen-Image, Anima et Krea 2, un checkpoint Civitai ne contient que le modèle de diffusion : le modèle de base du catalogue doit être téléchargé (il fournit l’encodeur de texte et le VAE).'}</div>
    <div id="civtok-${kind}"></div>
    <div class="row-inline">
      <input id="civq-${kind}" placeholder="nom, style, personnage…" size="26" value="${esc(c.q || '')}" onkeydown="if(event.key==='Enter')civSearch('${kind}')">
      <select id="civt-${kind}" onchange="$('civf-${kind}').disabled = this.value === 'Base'">${(kind === 'video' ? [['LORA', 'LoRA'], ['Base', 'Modèles de base']] : [['Checkpoint', 'Checkpoints'], ['LORA', 'LoRA'], ['Base', 'Modèles de base']]).map(([v, l]) => `<option value="${v}" ${c.type === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <select id="civf-${kind}" ${c.type === 'Base' ? 'disabled' : ''}><option value="">toutes les familles compatibles</option>${fams.map(f => `<option value="${f}" ${c.family === f ? 'selected' : ''}>${esc(G.families[f].label)}</option>`).join('')}</select>
      <select id="civs-${kind}">${['Most Downloaded', 'Highest Rated', 'Newest'].map((v, i) => `<option value="${v}" ${c.sort === v ? 'selected' : ''}>${['les plus téléchargés', 'les mieux notés', 'les plus récents'][i]}</option>`).join('')}</select>
      <button class="btn" onclick="civSearch('${kind}')">chercher</button>
    </div>
    <div id="civr-${kind}"></div><div id="civfiche-${kind}"></div></details>`;
}
function civState(kind) { G.civ = G.civ || {}; return G.civ[kind] = G.civ[kind] || {}; }
function setNsfw(v) {
  G.nsfw = v; LS.set('nsfw', v);
  document.querySelectorAll('.nsfw-box').forEach(b => { b.checked = v; });
  for (const k of ['image', 'video']) {
    if ($('gm-' + k) && $('gm-' + k).innerHTML) renderModels(k);
    if (civState(k).items && civState(k).items.length) civSearch(k);   // relancer avec le nouveau filtre
  }
}
async function civSearch(kind, more) {
  const c = civState(kind);
  if (!more) Object.assign(c, {q: $('civq-' + kind).value.trim(), type: $('civt-' + kind).value, family: $('civf-' + kind).value,
    sort: $('civs-' + kind).value, items: [], next: ''});
  $('civr-' + kind).insertAdjacentHTML('beforeend', '<p class="muted small" id="civwait">Recherche sur Civitai… (quelques secondes)</p>');
  try {
    const r = await api(`/api/civitai/search?q=${encodeURIComponent(c.q)}&type=${c.type}&family=${c.type === 'Base' ? kind : c.family}&sort=${encodeURIComponent(c.sort)}&nsfw=${G.nsfw ? 1 : 0}&cursor=${more ? c.next : ''}`);
    c.items = (c.items || []).concat(r.items); c.next = r.next; c.hasToken = r.has_token;
  } catch (e) { toast('Civitai : ' + e.message, 6000); }
  renderCivitai(kind);
}
function renderCivitai(kind) {
  const c = G.civ && G.civ[kind]; const box = $('civr-' + kind); if (!box || !c || !c.items) return;
  $('civtok-' + kind).innerHTML = c.hasToken ? '' : `<div class="warn-item info small">Civitai exige un compte (gratuit) pour télécharger : créez une clé sur <a href="https://civitai.com/user/account" target="_blank">civitai.com/user/account</a> → « API Keys », puis collez-la ici : <input type="password" id="civkey-${kind}" size="30"> <button class="btn small" onclick="civSaveToken('${kind}')">enregistrer</button> <span class="muted">(gardée dans ~/.ialauncher/config.json)</span></div>`;
  box.innerHTML = (c.items.length ? '<div class="civgrid">' + c.items.map((m, i) => {
    const vi = m._v || 0, v = m.versions[vi], f = v.files.find(x => x.id === m._f) || v.files.find(x => x.primary) || v.files[0];
    return `<div class="mitem civitem">
      ${v.image ? `<img src="${esc(v.image)}" loading="lazy">` : '<div class="noimg">pas d’aperçu</div>'}
      <div class="n">${c.type === 'Base' ? `${esc(v.baseModel)} <span class="muted small">— le plus téléchargé : <a href="${esc(m.url)}" target="_blank">${esc(m.name)}</a></span>` : `<a href="${esc(m.url)}" target="_blank">${esc(m.name)}</a>`}</div>
      <div class="s"><span class="badge acc">${esc(v.baseModel)}</span><span>⬇ ${fmtN(m.downloads)}</span><span>par ${esc(m.creator)}</span>${m.nsfw ? '<span class="badge warn">adulte</span>' : ''}</div>
      ${m.versions.length > 1 ? `<select onchange="civPick('${kind}',${i},'v',+this.value)">${m.versions.map((x, j) => `<option value="${j}" ${j === vi ? 'selected' : ''}>${esc(x.name)} (${esc(x.baseModel)})</option>`).join('')}</select>` : `<div class="small muted">${esc(v.name)}</div>`}
      ${v.files.length > 1 ? `<select onchange="civPick('${kind}',${i},'f',+this.value)">${v.files.map(x => `<option value="${x.id}" ${f && x.id === f.id ? 'selected' : ''}>${esc(x.name)} — ${fmtGB(x.size)}</option>`).join('')}</select>` : ''}
      ${v.words.length ? `<div class="small muted">mots déclencheurs : ${esc(v.words.slice(0, 4).join(', '))}</div>` : ''}
      ${c.type === 'Base' ? (v.doc ? `<div class="small muted">doc sd.cpp : ${esc(v.doc)}</div><button class="btn small primary" onclick="civBase('${kind}',${i})">Préparer la fiche « ${esc(v.baseModel)} »</button>` : '<div class="small warn">famille non prise en charge</div>')
        : !v.family ? '<div class="small warn">famille non prise en charge</div>' : f ? `<button class="btn small primary" onclick="civDownload('${kind}',${i})">⬇ ${fmtGB(f.size)}</button>` : (v.pickle_only ? '<div class="small muted" title="le format pickle (.ckpt/.pt) peut exécuter du code à l’ouverture : le launcher ne le télécharge pas">seulement en .ckpt (format non sûr)</div>' : '<div class="small muted">aucun fichier téléchargeable</div>')}
      <div id="civdl-${kind}-${i}"></div></div>`;
  }).join('') + '</div>' : '<p class="muted small">Aucun résultat.</p>') + (c.next ? `<button class="btn small" onclick="civSearch('${kind}', true)">plus de résultats</button>` : '');
}
function civOpen(kind, type, family) {
  Object.assign(civState(kind), {open: true, type, family});
  $('civt-' + kind).value = type; $('civf-' + kind).value = family; $('civf-' + kind).disabled = type === 'Base';
  const d = $('civ-' + kind); d.open = true; d.scrollIntoView({behavior: 'smooth', block: 'start'});
  civSearch(kind);
}
// Modèle de base (nouvelle famille) : fiche pré-remplie d'après la doc de sd.cpp, validée puis téléchargée
async function civBase(kind, i) {
  const m = G.civ[kind].items[i], v = m.versions[m._v || 0], box = $('civfiche-' + kind);
  box.innerHTML = `<p class="muted small">Lecture de la documentation de stable-diffusion.cpp et des dépôts Hugging Face pour « ${esc(v.baseModel)} »…</p>`;
  box.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  let f; try { f = await api(`/api/civitai/base-prefill?model=${m.id}&version=${v.id}`); }
  catch (e) { box.innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; return; }
  const id = 'civ-' + kind, todo = f.files.filter(x => !x.source.startsWith('déjà')).reduce((a, x) => a + x.size, 0);
  U.fiches[id] = f;
  box.innerHTML = `<h3 style="margin:12px 0 4px">Fiche : ${esc(f.name)}</h3>` + ficheHtml(id, f,
    `<button class="btn primary" onclick="civBaseAccept('${kind}')">✓ Ajouter au launcher et télécharger (${fmtGB(todo)})</button>
     <button class="btn" onclick="$('civfiche-${kind}').innerHTML=''">annuler</button>`);
}
async function civBaseAccept(kind) {
  const id = 'civ-' + kind, entry = readFiche(id);
  let r; try { r = await api('/api/updates/accept', {kind: 'gen', entry}); } catch (e) { toast(e.message, 8000); return; }
  const box = $('civfiche-' + kind);
  box.innerHTML = `<div class="small">« ${esc(entry.name)} » ajouté au launcher : téléchargement…</div><div id="civbase-job-${kind}"></div>`;
  const k = entry.kind === 'video' ? 'video' : 'image';
  G.node[k] = r.id; LS.set('node.' + k, r.id);
  G.models = {}; if (k === kind) await loadModels(k);
  try {
    const j = await api('/api/gen/download', {id: r.id});
    watchJob(j.job.id, 'civbase-job-' + kind, async (jj) => { await loadModels(k); if (jj.state === 'done') { selectGenModel(k, r.id); toast(entry.name + ' est prêt'); } });
  } catch (e) { toast(e.message, 8000); }
}
function civPick(kind, i, what, val) { const m = G.civ[kind].items[i]; if (what === 'v') { m._v = val; m._f = null; } else m._f = val; renderCivitai(kind); }
async function civSaveToken(kind) { await api('/api/civitai/token', {token: $('civkey-' + kind).value}); G.civ[kind].hasToken = true; renderCivitai(kind); toast('Clé Civitai enregistrée'); }
async function civDownload(kind, i) {
  const c = G.civ[kind], m = c.items[i], v = m.versions[m._v || 0], f = v.files.find(x => x.id === m._f) || v.files.find(x => x.primary) || v.files[0];
  if (!c.hasToken) { toast('Enregistrez d’abord votre clé API Civitai'); return; }
  if (!confirm(`Télécharger « ${m.name} » (${f.name}, ${fmtGB(f.size)}) ?${m.commercial === 'None' ? '\nLicence : usage commercial interdit.' : ''}`)) return;
  try {
    const r = await api('/api/civitai/download', {type: c.type, model: {id: m.id, name: m.name, url: m.url}, version: v, file: f.id});
    watchJob(r.job.id, `civdl-${kind}-${i}`, async (j) => { if (j.state === 'done') { toast('Téléchargé : ' + m.name); await loadModels(kind); if (G.sel[kind]) loadLoras(kind); } });
  } catch (e) { toast(e.message); }
}

// ------------------------------------------------------------------ tout décharger
async function unloadAll() {
  const busy = Object.keys(JOBS).length;
  if (!confirm('Décharger tous les modèles (texte, image, vidéo, musique) et libérer la VRAM et la RAM ?' +
    (busy ? '\nUne tâche est en cours : une génération en cours sera annulée.' : ''))) return;
  try {
    const r = await api('/api/unload', {});
    toast(r.stopped.length ? 'Déchargé : ' + r.stopped.join(', ') : 'Aucun modèle n’était chargé');
  } catch (e) { toast(e.message); return; }
  pollServer(); refreshGenStatus();
  setTimeout(liveTick, 1500);   // la VRAM se libère une fois les processus terminés
}

// ------------------------------------------------------------------ stockage
async function loadStorage() {
  const root = $('mode-storage');
  root.innerHTML = '<section class="card"><h1>Espace disque</h1><p class="muted">Mesure en cours…</p></section>';
  let d; try { d = await api('/api/storage'); } catch (e) { root.innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; return; }
  const max = Math.max(1, ...d.groups.flatMap(g => g.items.map(i => i.size)), ...d.external.map(i => i.size));
  const row = (it) => `<div class="srow"><div><b>${esc(it.label)}</b><div class="small muted">${esc(it.note || '')}</div></div>
    <div class="sbar"><i style="width:${(it.size / max * 100).toFixed(2)}%"></i></div><div class="sval">${fmtGB(it.size)}</div>
    <div>${it.deletable && it.id ? `<button class="btn small" onclick='delStorage(${esc(JSON.stringify(it.id))}, ${esc(JSON.stringify(it.label))}, ${it.size})'>supprimer</button>` : ''}</div></div>`;
  root.innerHTML = `<section class="card"><h1>Espace disque</h1>
    <p class="lead">Tout ce que le launcher a installé ou téléchargé vit dans <span class="mono">${esc(d.home)}</span>. Supprimer un modèle ne casse rien : il se retélécharge d’un clic s’il vous manque plus tard.</p>
    <div class="stotal"><b>${fmtGB(d.total)}</b> occupés par le launcher · <b>${fmtGB(d.disk.free)}</b> libres sur ${fmtGB(d.disk.total)}</div>
    <div class="bar" style="height:12px"><div style="width:${(d.total / d.disk.total * 100).toFixed(2)}%;background:var(--acc)"></div><div style="width:${((d.disk.total - d.disk.free - d.total) / d.disk.total * 100).toFixed(2)}%;background:var(--c-system)"></div></div>
    <div class="small muted">bleu : le launcher · gris : le reste du disque · vide : libre</div></section>` +
    d.groups.map(g => `<section class="card"><h1>${esc(g.label)} <span class="muted small">${fmtGB(g.total)}</span></h1>${g.items.length ? g.items.map(row).join('') : '<p class="muted small">Rien.</p>'}</section>`).join('') +
    `<section class="card"><h1>Hors du dossier du launcher</h1><p class="lead small">Ces fichiers ne sont pas comptés au-dessus. Les modèles de vos autres programmes sont lus en place et ne sont jamais supprimés d’ici.</p>${d.external.map(row).join('')}</section>`;
}
async function delStorage(id, label, size) {
  if (!confirm(`Supprimer « ${label} » (${fmtGB(size)}) ?${id === 'uvcache' ? '\nCe cache est partagé avec vos autres projets Python gérés par uv : ils retéléchargeront leurs paquets au besoin.' : ''}`)) return;
  try { await api('/api/storage/delete', {id}); toast('Supprimé'); } catch (e) { toast(e.message, 6000); }
  loadStorage(); G.models = {};
  if (typeof refreshModels === 'function') refreshModels();
}

// ------------------------------------------------------------------ génération
async function runGen(kind) {
  const m = G.sel[kind], f = G.form[kind];
  if (!f.prompt.trim()) { toast('Écrivez d’abord un prompt'); return; }
  if (!await confirmGpu('sd-server')) return;
  const req = Object.assign({}, f, {kind, model: m.id, opts: {offload: f.offload, flash_attn: f.flash_attn}});
  if (!m.defaults.negative) req.negative = '';
  const inp = G.inputs[kind];
  if (kind === 'image' && inp.init) {
    if (f.input_mode === 'ref' && (m.features || []).includes('ref_images')) req.ref_images = [inp.init]; else req.init_image = inp.init;
  }
  if (kind === 'video') { if (inp.init) req.init_image = inp.init; if (inp.end) req.end_image = inp.end; }
  if (!f.hires) delete req.hires;
  delete req.xyz;
  if (kind === 'image' && xyzState(kind).some(a => a.p)) {
    let axes; try { axes = xyzAxes(kind); } catch (e) { toast('Tableau : ' + e.message, 8000); return; }
    const n = axes.reduce((a, x) => a * x.values.length, 1);
    if (n > 200) { toast(`${n} images : le tableau est limité à 200`, 6000); return; }
    if (n > 40 && !confirm(`Générer ${n} images ?`)) return;
    req.grid = axes.map(a => ({param: a.param, values: a.values, ...(a.search ? {search: a.search} : {})}));
  }
  try {
    const r = await api('/api/gen/run', req);
    $('grun-' + kind).disabled = true;
    queued(r);
    watchGen(kind, r.job.id);
  } catch (e) { toast(e.message, 6000); }
}
// ------------------------------------------------------------------ tableau X/Y/Z
// (le « X/Y/Z plot » de la WebUI : mêmes écritures, « 1-5 (+2) », « 1-10 [4] »…)
const XYZ = [['', '—'], ['seed', 'Graine'], ['steps', 'Étapes'], ['cfg', 'CFG'], ['sampler', 'Sampler'], ['scheduler', 'Planning'],
  ['checkpoint', 'Checkpoint'], ['prompt_sr', 'Prompt : chercher / remplacer'], ['width', 'Largeur'], ['height', 'Hauteur'],
  ['clip_skip', 'Clip skip'], ['strength', 'Force (img2img)']];
const XYZ_INT = ['seed', 'steps', 'width', 'height', 'clip_skip'], XYZ_NUM = XYZ_INT.concat(['cfg', 'strength']);
const XYZ_HINT = {seed: '1, 2, 3', steps: '10-40 (+10)', cfg: '1-9 (+2)', sampler: 'euler, dpm++2m', scheduler: 'karras, simple',
  checkpoint: '＋ ajouter… à droite', prompt_sr: ':-2>, :3>', width: '768, 1024', height: '768, 1024', clip_skip: '1, 2', strength: '0.3-0.9 [4]'};
function xyzState(kind) {
  const f = G.form[kind];
  if (!Array.isArray(f.xyz) || f.xyz.length !== 3) f.xyz = [{p: '', t: '', s: ''}, {p: '', t: '', s: ''}, {p: '', t: '', s: ''}];
  return f.xyz;
}
const ckptList = (kind) => (G.models[kind] || []).filter(m => m.installed);
function xyzHtml(kind) {
  const ax = xyzState(kind);
  const en = (p) => p === 'sampler' ? SAMPLERS : p === 'scheduler' ? SCHEDULERS : p === 'checkpoint' ? ckptList(kind).map(m => m.name) : null;
  return `<details class="gadv xyz" ${ax.some(a => a.p) ? 'open' : ''}><summary>Tableau de variations X/Y/Z <span id="xyzn-${kind}"></span></summary>
    <div class="muted small" style="margin:6px 0">Une image par combinaison : X en colonnes, Y en lignes, Z = un tableau par valeur. Les autres réglages restent ceux ci-dessus ; une graine aléatoire est tirée une seule fois pour tout le tableau, pour ne comparer que le paramètre.</div>
    ${['X (colonnes)', 'Y (lignes)', 'Z (tableaux)'].map((lab, i) => {
      const a = ax[i], e = en(a.p);
      return `<div class="grow"><label>${lab}</label><div>
        <select onchange="setAxis('${kind}',${i},'p',this.value)">${XYZ.map(([v, l]) => `<option value="${v}" ${v === a.p ? 'selected' : ''}>${l}</option>`).join('')}</select>
        ${a.p === 'prompt_sr' ? `<span class="small muted">chercher</span><input value="${esc(a.s || '')}" placeholder="texte du prompt, ex. :-2>" style="flex:1;min-width:120px" oninput="setAxis('${kind}',${i},'s',this.value)"><span class="small muted">remplacer par</span>` : ''}
        ${a.p ? `<input id="xyzt-${kind}-${i}" value="${esc(a.t)}" placeholder="${esc(XYZ_HINT[a.p])}" style="flex:1;min-width:150px" oninput="setAxis('${kind}',${i},'t',this.value)">` : ''}
        ${e ? `<select onchange="addAxisValue('${kind}',${i},this.value);this.value=''"><option value="">＋ ajouter…</option>${e.map(v => `<option>${esc(v)}</option>`).join('')}</select>` : ''}
        <span class="small" id="xyzp-${kind}-${i}"></span></div></div>`;
    }).join('')}
    <div class="small muted" style="margin-top:6px"><b>Écrire les valeurs :</b> liste <span class="mono">1, 3, 5</span> · de 1 en 1 <span class="mono">1-5</span> · avec un pas <span class="mono">1-5 (+2)</span> → 1, 3, 5, ou <span class="mono">10-4 (-3)</span> → 10, 7, 4 · N valeurs régulières <span class="mono">1-10 [4]</span> → 1, 4, 7, 10. Décimales avec un point (<span class="mono">3.5</span>). Textes séparés par des virgules, entre guillemets s’ils en contiennent. <b>Chercher / remplacer :</b> 1<sup>er</sup> champ = un texte du prompt (ou du négatif), 2<sup>e</sup> = ce qui le remplace, une image par valeur : chercher <span class="mono">chat</span>, remplacer par <span class="mono">chat, chien, renard</span>. Pour le poids d’un LoRA <span class="mono">&lt;lora:nom:-2&gt;</span> : chercher <span class="mono">:-2&gt;</span>, remplacer par <span class="mono">:-2&gt;, :3&gt;</span>. 1<sup>er</sup> champ vide = écriture de la WebUI (la 1<sup>re</sup> valeur est cherchée).</div>
  </details>`;
}
function setAxis(kind, i, k, v) {
  const ax = xyzState(kind);
  ax[i][k] = v;
  if (k === 'p') { ax[i].t = ''; ax[i].s = ''; setGen(kind, 'xyz', ax); return; }
  setGen(kind, 'xyz', ax, true);
  updateXyz(kind);
}
function addAxisValue(kind, i, v) {
  if (!v) return;
  const ax = xyzState(kind), q = v.includes(',') ? `"${v}"` : v;
  ax[i].t = ax[i].t.trim() ? ax[i].t.replace(/[,\s]*$/, '') + ', ' + q : q;
  const inp = $(`xyzt-${kind}-${i}`); if (inp) inp.value = ax[i].t;
  setGen(kind, 'xyz', ax, true);
  updateXyz(kind);
}
// Valeurs d'un axe à partir de son écriture ; lève une erreur lisible sinon.
function parseAxis(kind, p, t, search) {
  // virgules hors guillemets
  const toks = t.split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/).map(x => x.trim()).filter(Boolean).map(x => x.replace(/^"(.*)"$/, '$1'));
  if (!toks.length) throw new Error('aucune valeur');
  let vals = [];
  if (XYZ_NUM.includes(p)) {
    const N = '(-?\\d+(?:\\.\\d+)?)', rx = (s) => new RegExp('^' + s + '$');
    for (const tok of toks) {
      let m;
      if ((m = tok.match(rx(`${N}\\s*-\\s*${N}\\s*\\(\\s*([+-]?\\d+(?:\\.\\d+)?)\\s*\\)`)))) {
        const [a, b, st] = [+m[1], +m[2], +m[3]];
        if (!st || (b - a) * st < 0) throw new Error(`« ${tok} » : le pas ne mène pas de ${a} à ${b}`);
        for (let v = a, k = 0; (st > 0 ? v <= b + 1e-9 : v >= b - 1e-9) && k < 500; v = a + st * ++k) vals.push(v);
      } else if ((m = tok.match(rx(`${N}\\s*-\\s*${N}\\s*\\[\\s*(\\d+)\\s*\\]`)))) {
        const [a, b, n] = [+m[1], +m[2], +m[3]];
        if (n < 1) throw new Error(`« ${tok} » : au moins 1 valeur`);
        for (let k = 0; k < n; k++) vals.push(n === 1 ? a : a + (b - a) * k / (n - 1));
      } else if ((m = tok.match(rx(`${N}\\s*-\\s*${N}`)))) {
        const [a, b] = [+m[1], +m[2]], st = b >= a ? 1 : -1;
        for (let v = a, k = 0; (st > 0 ? v <= b : v >= b) && k < 500; v += st, k++) vals.push(v);
      } else if ((m = tok.match(rx(N)))) vals.push(+m[1]);
      else throw new Error(`« ${tok} » : écriture non reconnue`);
    }
    vals = vals.map(v => XYZ_INT.includes(p) ? Math.round(v) : +v.toFixed(4));
  } else {
    vals = toks;
    const known = p === 'sampler' ? SAMPLERS : p === 'scheduler' ? SCHEDULERS : p === 'checkpoint' ? ckptList(kind).map(m => m.name) : null;
    const bad = known ? vals.filter(v => !known.includes(v)) : [];
    if (bad.length) throw new Error(`inconnu : ${bad.join(', ')}`);
    const where = (G.form[kind].prompt || '') + '\n' + (G.form[kind].negative || ''), find = search || vals[0];
    if (p === 'prompt_sr' && !where.includes(find)) throw new Error(`« ${find} » n’est ni dans le prompt ni dans le négatif`);
  }
  if (vals.length > 50) throw new Error(`${vals.length} valeurs : 50 au plus par axe`);
  return vals;
}
// axes renseignés, avec leurs valeurs (les checkpoints passent de leur nom à leur id)
function xyzAxes(kind) {
  return xyzState(kind).filter(a => a.p).map(a => {
    const search = a.p === 'prompt_sr' ? (a.s || '') : '';
    const shown = parseAxis(kind, a.p, a.t, search);
    const values = a.p === 'checkpoint' ? shown.map(n => ckptList(kind).find(m => m.name === n).id) : shown;
    return search ? {param: a.p, values, shown, search} : {param: a.p, values, shown};
  });
}
function updateXyz(kind) {
  let n = 1, any = false, ok = true;
  xyzState(kind).forEach((a, i) => {
    const el = $(`xyzp-${kind}-${i}`); if (!el || !a.p) return;
    any = true;
    try {
      const v = parseAxis(kind, a.p, a.t, a.p === 'prompt_sr' ? a.s : ''); n *= v.length;
      el.className = 'small muted'; el.textContent = `→ ${v.length > 12 ? v.slice(0, 12).join(', ') + '…' : v.join(', ')} (${v.length})`;
    } catch (e) { ok = false; el.className = 'small warn'; el.textContent = a.t.trim() ? e.message : ''; }
  });
  const s = $('xyzn-' + kind), b = $('grun-' + kind);
  if (s) s.textContent = any ? (ok ? `— ${n} image${n > 1 ? 's' : ''}` : '— à corriger') : '';
  if (b && !b.disabled) b.textContent = any && ok ? `▶ Générer le tableau (${n} image${n > 1 ? 's' : ''})` : '▶ Générer';
}
const xyzFmt = (kind, a, v) => a.param === 'checkpoint' ? ((G.models[kind] || []).find(m => m.id === v) || {}).name || v : v;
function showGrid(kind, g) {
  G.grid = G.grid || {}; G.grid[kind] = g;
  const out = $('go-' + kind); if (!out) return;
  const [X, Y, Z] = g.axes;
  let h = '';
  Z.values.forEach((zv, zi) => {
    if (Z.param) h += `<div class="glab">${esc(Z.label)} : ${esc(xyzFmt(kind, Z, zv))}</div>`;
    h += `<div class="xyzwrap"><table class="xyzgrid"><tr><th></th>${X.values.map(v => `<th>${X.param ? esc(X.label + ' : ' + xyzFmt(kind, X, v)) : ''}</th>`).join('')}</tr>`;
    Y.values.forEach((yv, yi) => {
      h += `<tr><th>${Y.param ? esc(Y.label + ' : ' + xyzFmt(kind, Y, yv)) : ''}</th>` + X.values.map((xv, xi) => {
        const c = g.cells.find(c => c.x === xi && c.y === yi && c.z === zi);
        return `<td>${c && c.file ? `<a href="${c.file.url}" target="_blank" title="${esc(c.label)}"><img src="${c.file.url}" loading="lazy" alt=""></a>` : `<div class="xyzerr" title="${esc(c ? c.error || '' : '')}">${esc(c ? (c.error || '').slice(0, 80) : 'pas générée')}</div>`}</td>`;
      }).join('') + '</tr>';
    });
    h += '</table></div>';
  });
  out.innerHTML = h + `<div class="row-inline small muted" style="margin-top:6px"><button class="btn small" onclick="downloadGrid('${kind}')">⬇ grille en une image PNG</button>
    <span>${g.seed != null ? `Graine commune : <span class="mono">${g.seed}</span> · ` : ''}la grille va dans la galerie ; cliquez sur une image pour l’ouvrir en grand.</span></div>`;
}
// La grille titrée (tous les tableaux Z l'un sous l'autre) dessinée dans un canvas → PNG.
async function gridBlob(kind, g) {
  const [X, Y, Z] = g.axes, im = {};
  await Promise.all(g.cells.filter(c => c.file).map(c => new Promise(r => { const i = new Image(); i.onload = () => { im[c.x + ',' + c.y + ',' + c.z] = i; r(); }; i.onerror = r; i.src = c.file.url; })));
  const all = Object.values(im); if (!all.length) return null;
  const n = X.values.length * Y.values.length * Z.values.length;
  const side = Math.max(256, Math.min(1024, Math.sqrt(16e6 / n)));
  const nat = Math.max(...all.map(i => Math.max(i.naturalWidth, i.naturalHeight))), k = Math.min(1, side / nat);
  const cw = Math.round(Math.max(...all.map(i => i.naturalWidth)) * k), ch = Math.round(Math.max(...all.map(i => i.naturalHeight)) * k);
  const fs = Math.max(14, Math.round(cw / 22)), head = Z.param ? Math.round(fs * 2.4) : 0, top = X.param ? Math.round(fs * 2.2) : 0, left = Y.param ? Math.round(cw * 0.5) : 0;
  const blockH = head + top + ch * Y.values.length;
  const cv = document.createElement('canvas');
  cv.width = left + cw * X.values.length; cv.height = blockH * Z.values.length;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.fillStyle = '#111'; ctx.font = `${fs}px sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  Z.values.forEach((zv, zi) => {
    const y0 = blockH * zi;
    if (Z.param) { ctx.font = `bold ${fs}px sans-serif`; ctx.fillText(`${Z.label} : ${xyzFmt(kind, Z, zv)}`, cv.width / 2, y0 + head / 2, cv.width - 20); ctx.font = `${fs}px sans-serif`; }
    if (X.param) X.values.forEach((v, i) => ctx.fillText(`${X.label} : ${xyzFmt(kind, X, v)}`, left + cw * i + cw / 2, y0 + head + top / 2, cw - 10));
    if (Y.param) Y.values.forEach((v, i) => {             // titre de ligne sur deux lignes : la colonne est étroite
      const yc = y0 + head + top + ch * i + ch / 2;
      ctx.fillText(Y.label + ' :', left / 2, yc - fs * 0.7, left - 10);
      ctx.fillText(String(xyzFmt(kind, Y, v)), left / 2, yc + fs * 0.7, left - 10);
    });
    X.values.forEach((xv, xi) => Y.values.forEach((yv, yi) => {
      const i = im[xi + ',' + yi + ',' + zi]; if (!i) return;
      const w = i.naturalWidth * k, h = i.naturalHeight * k;
      ctx.drawImage(i, left + cw * xi + (cw - w) / 2, y0 + head + top + ch * yi + (ch - h) / 2, w, h);
    }));
  });
  return new Promise(r => cv.toBlob(r, 'image/png'));
}
async function downloadGrid(kind) {
  const g = (G.grid || {})[kind]; if (!g) return;
  const b = await gridBlob(kind, g); if (!b) { toast('Aucune image dans ce tableau'); return; }
  const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = `tableau-${g.seed ?? 'xyz'}.png`; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
// Envoie la grille au launcher, qui la range dans la galerie. Par morceaux de 700 Ko : le proxy
// de YunoHost refuse les requêtes de plus de 1 Mo.
const gridSending = new Set();
async function uploadGrid(kind, dir, g) {
  if (gridSending.has(dir)) return;
  gridSending.add(dir);
  try {
    const b = await gridBlob(kind, g); if (!b) return;
    const data = new Uint8Array(await b.arrayBuffer()), token = Math.random().toString(36).slice(2, 12), CH = 700000;
    for (let o = 0; o < data.length; o += CH) {
      const last = o + CH >= data.length ? 1 : 0;
      const r = await fetch(`/api/gen/grid/png?kind=${kind}&dir=${encodeURIComponent(dir)}&token=${token}&offset=${o}&last=${last}`,
        {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: data.subarray(o, o + CH)});
      const d = await r.json(); if (!r.ok || d.error) throw new Error(d.error || 'HTTP ' + r.status);
    }
    toast('Grille ajoutée à la galerie');
  } catch (e) { toast('Grille non enregistrée : ' + e.message, 8000); }
  finally { gridSending.delete(dir); }
}
// tableaux finis pendant qu'aucun navigateur ne regardait : ce navigateur dessine leur grille
async function sendPendingGrids(kind) {
  let l; try { l = await api('/api/gen/grids/pending?kind=' + kind); } catch (e) { return false; }
  for (const x of l) await uploadGrid(kind, x.dir, x.grid);
  return l.length > 0;
}

// Une génération à la fois sur la carte graphique : celle-ci attend peut-être son tour (autre navigateur).
// Ce navigateur retient ses propres générations pour les reprendre après un rechargement de page.
function queued(r) {
  if (r.ahead) toast(`Une autre génération est en cours : la vôtre démarrera ensuite (${r.ahead} avant elle)`, 6000);
  LS.set('genjobs', [r.job.id, ...(LS.get('genjobs') || [])].slice(0, 20));
}
function watchGen(kind, id) {
  const out = $('go-' + kind);
  if (out) out.innerHTML = `<div id="gjob-${kind}"></div>`;
  const svc = setInterval(refreshGenStatus, 3000);
  watchJob(id, 'gjob-' + kind, (j) => {
    clearInterval(svc); refreshGenStatus();
    const b = $('grun-' + kind); if (b) b.disabled = false;
    if (j.state === 'done' && j.result) {
      if (!j.result.grid) { showResult(kind, j.result.files); loadOutputs(kind); return; }
      showGrid(kind, j.result.grid);
      if (j.result.dir) uploadGrid(kind, j.result.dir, j.result.grid).then(() => loadOutputs(kind));
    }
  });
}
// La carte graphique ne porte qu'un gros modèle : prévenir si le serveur de texte tourne.
async function confirmGpu(owner) {
  if (S.server && (S.server.state === 'ready' || S.server.state === 'starting')) {
    if (!confirm('Le serveur de texte (llama-server) occupe la carte graphique : il va être arrêté pour cette génération. Continuer ?')) return false;
    setTimeout(pollServer, 1500);
  }
  return true;
}
function showResult(kind, files) {
  const out = $('go-' + kind); if (!out || !files.length) return;
  out.innerHTML = files.map(f => mediaTag(kind, f.url, true)).join('') + resultInfo(kind, files[0]);
}
function mediaTag(kind, url, big) {
  if (kind === 'video') return `<video src="${url}" controls ${big ? 'autoplay loop' : 'muted preload="metadata"'} class="${big ? 'resmedia' : ''}"></video>`;
  if (kind === 'music') return `<audio src="${url}" controls preload="metadata"></audio>`;
  return `<a href="${url}" target="_blank"><img src="${url}" class="${big ? 'resmedia' : ''}" loading="lazy"></a>`;
}
function resultInfo(kind, f) {
  const m = f.meta || {};
  return `<div class="small muted" style="margin-top:6px">${esc(m.model_name || '')} · graine <span class="mono">${m.seed}</span> · ${m.width ? m.width + '×' + m.height + ' · ' : ''}${m.steps ? m.steps + ' étapes · ' : ''}${m.gen_seconds != null ? m.gen_seconds + ' s de calcul' + (m.seconds > m.gen_seconds + 1 ? ` (+ ${(m.seconds - m.gen_seconds).toFixed(0)} s de chargement)` : '') : ''}</div>
    <div class="small">${esc(m.prompt || '')}</div>
    <div class="row-inline">${kind === 'music' ? '' : `<button class="btn small gkeep ${m.kept ? 'primary' : ''}" data-name="${esc(f.name)}" data-kept="${m.kept ? 1 : ''}" onclick="keepOutput('${kind}', this.dataset.name, !this.dataset.kept)" title="afficher son prompt et ses réglages dans la galerie">${m.kept ? '★ gardée' : '☆ garder avec ses réglages'}</button>`}
      ${m.grid ? `<button class="btn small" onclick='showGridOf("${kind}", ${esc(JSON.stringify(f.name))})'>voir les images séparées</button>` : ''}
      <button class="btn small" onclick='reuseMeta("${kind}", ${esc(JSON.stringify(f.name))})'>reprendre ces réglages</button>
      ${kind === 'image' ? `<button class="btn small" onclick='toInput("${f.url}")'>utiliser comme image d’entrée</button>` : ''}
      <a class="btn small" href="${f.url}" download>télécharger</a>
      <button class="btn small" onclick='deleteOutput("${kind}", ${esc(JSON.stringify(f.name))})'>supprimer</button></div>`;
}

// ------------------------------------------------------------------ galerie
async function loadOutputs(kind, again) {
  try { G['out_' + kind] = await api('/api/gen/outputs?kind=' + kind); } catch (e) { return; }
  if (kind === 'image' && !again) sendPendingGrids(kind).then(sent => { if (sent) loadOutputs(kind, true); });
  renderOutDir(kind);
  const box = $('gg-' + kind); if (!box) return;
  const items = G['out_' + kind];
  if (kind === 'music') {
    box.innerHTML = items.length ? items.map(f => `<div class="track"><div><b>${esc((f.meta.prompt || '').slice(0, 80) || f.name)}</b> <span class="muted small">${esc(f.meta.model || '')} · ${f.meta.duration ? f.meta.duration + ' s' : ''} · graine ${f.meta.seed}</span></div>${mediaTag('music', f.url)}<div class="row-inline small"><button class="link" onclick='reuseMeta("music", ${esc(JSON.stringify(f.name))})'>reprendre</button> <a href="${f.url}" download>télécharger</a> <button class="link" onclick='deleteOutput("music", ${esc(JSON.stringify(f.name))})'>supprimer</button></div></div>`).join('') : '<p class="muted small">Rien encore.</p>';
    return;
  }
  box.innerHTML = items.length ? items.map(f => {
    const n = esc(JSON.stringify(f.name)), m = f.meta;
    const th = `<div class="gthumb" onclick='viewOutput("${kind}", ${n})' title="${esc(m.prompt || '')}">${kind === 'video' ? `<video src="${f.url}" muted preload="metadata"></video>` : `<img src="${f.url}" loading="lazy">`}${m.kept ? '<span class="gstar">★</span>' : ''}</div>`;
    if (!m.kept) return th;
    return `<div class="gkept">${th}<div class="gcap small"><div class="gprompt">${esc(m.prompt || '(sans prompt)')}</div>
      ${m.negative ? `<div class="muted"><b>négatif :</b> ${esc(m.negative)}</div>` : ''}
      <div class="muted">${esc(paramsLine(m))}</div>
      <div class="row-inline"><button class="link" onclick='reuseMeta("${kind}", ${n})'>reprendre</button><button class="link" onclick='keepOutput("${kind}", ${n}, false)'>ne plus garder</button></div></div></div>`;
  }).join('') : '<p class="muted small">Rien encore.</p>';
}
function paramsLine(m) {
  const p = [m.model_name || m.model];
  if (m.width) p.push(m.width + '×' + m.height);
  if (m.steps) p.push(m.steps + ' étapes' + (m.high_noise_steps ? ` (+ ${m.high_noise_steps} bruit fort)` : ''));
  if (m.cfg != null && m.cfg !== '') p.push('CFG ' + m.cfg);
  if (m.sampler) p.push(m.sampler + (m.scheduler ? ' / ' + m.scheduler : ''));
  else if (m.scheduler) p.push(m.scheduler);
  if (m.clip_skip > 0) p.push('clip skip ' + m.clip_skip);
  if (m.frames) p.push(m.frames + ' images' + (m.fps ? ' à ' + m.fps + ' i/s' : ''));
  if (m.flow_shift) p.push('flow shift ' + m.flow_shift);
  if (m.hires) p.push(`hires ×${m.hires_scale} (${m.hires_upscaler}, débruitage ${m.hires_denoise})`);
  if (m.seed != null) p.push('graine ' + m.seed);
  if (m.grid) p.unshift('Tableau ' + m.grid.axes.filter(a => a.param).map(a => `${a.label} (${a.values.length})`).join(' × '));
  return p.filter(Boolean).join(' · ');
}
async function keepOutput(kind, name, keep) {
  try { await api('/api/gen/outputs/keep', {kind, name, keep}); } catch (e) { toast(e.message); return; }
  document.querySelectorAll(`#go-${kind} .gkeep`).forEach(b => {
    if (b.dataset.name !== name) return;
    b.dataset.kept = keep ? 1 : ''; b.classList.toggle('primary', keep); b.textContent = keep ? '★ gardée' : '☆ garder avec ses réglages';
  });
  toast(keep ? 'Gardée : prompt et réglages affichés dans la galerie' : 'N’est plus gardée');
  loadOutputs(kind);
}
async function renderOutDir(kind) {
  const box = $('gd-' + kind); if (!box) return;
  let d; try { d = await api('/api/gen/outputs/dir'); } catch (e) { return; }
  const sep = d.windows ? '\\' : '/';
  box.innerHTML = `<div class="row-inline">Dossier : <span class="mono">${esc((d.windows || d.path) + sep + kind)}</span>
      <button class="btn small" onclick="openOutDir('${kind}')">📂 ouvrir</button>
      <button class="link" onclick="this.parentNode.nextElementSibling.classList.toggle('hidden')">changer</button></div>
    <div class="row-inline hidden"><input id="gdi-${kind}" size="50" value="${esc(d.custom ? (d.windows || d.path) : '')}" placeholder="${d.windows ? 'ex. C:\\Users\\vous\\Pictures\\ialauncher' : 'ex. ~/Images/ialauncher'}">
      <button class="btn small" onclick="setOutDir('${kind}')">enregistrer</button>
      <span class="muted">vide = dossier par défaut ; les fichiers déjà générés ne sont pas déplacés</span></div>`;
}
async function openOutDir(kind) {
  try { await api('/api/gen/outputs/open', {kind}); toast('Dossier ouvert sur la machine qui fait tourner le launcher'); } catch (e) { toast(e.message); }
}
async function setOutDir(kind) {
  try { await api('/api/gen/outputs/dir', {path: $('gdi-' + kind).value}); } catch (e) { toast(e.message); return; }
  toast('Dossier de la galerie changé'); loadOutputs(kind);
}
function viewOutput(kind, name) {
  const f = (G['out_' + kind] || []).find(x => x.name === name); if (!f) return;
  $('go-' + kind).innerHTML = mediaTag(kind, f.url, true) + resultInfo(kind, f);
  $('go-' + kind).scrollIntoView({behavior: 'smooth', block: 'nearest'});
}
function reuseMeta(kind, name) {
  const f = (G['out_' + kind] || []).find(x => x.name === name) || {meta: {}};
  const m = f.meta;
  if (kind === 'music') { G.music = Object.assign(G.music || {}, pick(m, MUSIC_KEYS)); renderMusicForm(); toast('Réglages repris (graine comprise)'); return; }
  if (m.model && (!G.sel[kind] || G.sel[kind].id !== m.model)) selectGenModel(kind, m.model);
  if (!G.sel[kind]) { toast('Le modèle de cette génération n’est plus disponible'); return; }
  Object.assign(G.form[kind], pick(m, ['prompt', 'negative', 'width', 'height', 'steps', 'high_noise_steps', 'cfg', 'sampler', 'scheduler', 'seed', 'clip_skip', 'frames', 'fps', 'flow_shift', 'hires', 'hires_scale', 'hires_denoise', 'hires_upscaler', 'strength']));
  renderForm(kind); toast('Réglages repris (graine comprise)');
}
function showGridOf(kind, name) {
  const f = (G['out_' + kind] || []).find(x => x.name === name);
  if (f && f.meta.grid) showGrid(kind, f.meta.grid);
}
function pick(o, keys) { const r = {}; keys.forEach(k => { if (o[k] !== undefined) r[k] = o[k]; }); return r; }
async function toInput(url) {
  const b = await (await fetch(url)).blob();
  const r = new FileReader(); r.onload = () => { G.inputs.image.init = r.result; G.form.image.input_mode = (G.sel.image.features || []).includes('ref_images') ? 'ref' : 'init'; renderForm('image'); toast('Image placée en entrée'); }; r.readAsDataURL(b);
}
async function deleteOutput(kind, name) {
  if (!confirm('Supprimer ce fichier ?')) return;
  await api('/api/gen/outputs/delete', {kind, name}); loadOutputs(kind);
  $('go-' + kind).innerHTML = '';
}

// ------------------------------------------------------------------ musique
const MUSIC_KEYS = ['model', 'lm', 'prompt', 'lyrics', 'language', 'duration', 'bpm', 'key', 'time_signature', 'steps', 'cfg', 'seed', 'batch', 'format'];
async function loadMusic() {
  const root = $('mode-music');
  if (!root.dataset.ready) {
    root.innerHTML = `
    <section class="card"><h1>Moteur : ACE-Step 1.5</h1>
      <p class="lead">Modèle de musique ouvert (licence MIT) : chansons complètes avec paroles dans plus de 50 langues, de 10 s à 10 min, en quelques secondes sur une carte récente. C’est un programme Python : le launcher l’installe dans son propre environnement (via <code>uv</code>), sans toucher au reste de votre système, puis le pilote par son API.</p>
      <div id="music-engine"></div></section>
    <section class="card"><h1><span class="num">1</span> Composer</h1>
      <div id="gsvc-music" class="small muted"></div>
      <div class="genwrap"><div id="gf-music" class="genform"></div><div id="go-music" class="genout"><p class="muted">La musique s’affichera ici.</p></div></div></section>
    <section class="card"><h1>Morceaux générés <button class="link small" onclick="loadOutputs('music')">↻</button></h1>
      <div id="gd-music" class="small"></div><div id="gg-music"></div></section>`;
    root.dataset.ready = '1';
  }
  await refreshGenStatus();
  if (!G.music) G.music = Object.assign({model: 'acestep-v15-turbo', lm: 'acestep-5Hz-lm-1.7B', prompt: '', lyrics: '', language: 'fr', duration: 90, bpm: '', key: '', time_signature: '', steps: 8, cfg: 7, seed: -1, batch: 1, format: 'flac'}, LS.get('music', {}));
  renderMusicForm();
  loadOutputs('music');
}
function renderMusicEngine() {
  const box = $('music-engine'); if (!box || !G.status) return;
  const s = G.status.music;
  box.innerHTML = `<div class="plan"><div>${s.installed ? `<b>Installé</b> <span class="muted small mono">${esc(s.dir)}</span><br><span class="small">Poids présents : ${s.downloaded.length ? s.downloaded.map(esc).join(', ') : 'aucun encore — ils se téléchargent au premier lancement (plusieurs Go)'}</span>`
      : `<b>Pas encore installé.</b><br><span class="muted small">Clone le code d’ACE-Step, puis <code>uv sync</code> installe Python 3.12, PyTorch CUDA et les dépendances (≈ 6 Go) dans <span class="mono">${esc(s.dir)}</span>.${s.uv ? '' : ' uv (gestionnaire Python d’Astral) sera installé d’abord.'}</span>`}</div>
    <div><button class="btn ${s.installed ? '' : 'primary'}" onclick="installMusic()">${s.installed ? 'Mettre à jour' : 'Installer automatiquement'}</button></div></div><div id="music-job"></div>`;
}
async function installMusic() {
  if (!confirm('Installer / mettre à jour ACE-Step (plusieurs Go de dépendances Python) ?')) return;
  try { const r = await api('/api/music/install', {}); watchJob(r.job.id, 'music-job', async () => { await refreshGenStatus(); renderMusicForm(); }); } catch (e) { toast(e.message); }
}
function renderMusicForm() {
  const box = $('gf-music'); if (!box || !G.status) return;
  const f = G.music, st = G.status.music;
  if (!st.installed) { box.innerHTML = '<p class="muted">Installez d’abord ACE-Step (ci-dessus).</p>'; return; }
  const mdl = st.models.find(x => x.id === f.model) || st.models[0];
  const num = (k, min, max, step, w, ph) => `<input type="number" min="${min}" max="${max}" step="${step || 1}" value="${f[k]}" placeholder="${ph || ''}" style="width:${w || 90}px" onchange="setMusic('${k}', this.value === '' ? '' : +this.value)">`;
  box.innerHTML = `
    <div class="grow"><label>Modèle</label><div><select onchange="setMusicModel(this.value)">${st.models.map(x => `<option value="${x.id}" ${x.id === f.model ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select> <span class="muted small">${esc(mdl.blurb)}${mdl.download && !st.downloaded.includes(mdl.id) ? ` Premier usage : ${String(mdl.download).replace('.', ',')} Go à télécharger.` : ''}</span></div></div>
    <div class="grow"><label>Planificateur</label><div><select onchange="setMusic('lm', this.value)">${st.lms.map(x => `<option value="${x.id}" ${x.id === f.lm ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select> <span class="muted small">un petit LLM compose d’abord la structure (couplets, refrains, tempo) : chansons mieux construites</span></div></div>
    <label class="glab">Style <span class="muted small">— genre, instruments, voix, ambiance, tempo (en anglais de préférence)</span></label>
    <textarea rows="3" placeholder="ex. french chanson, accordion and upright bass, warm male vocal, melancholic, 90 bpm" oninput="setMusic('prompt', this.value, true)">${esc(f.prompt)}</textarea>
    <label class="glab">Paroles <span class="muted small">— balises de structure : [verse], [chorus], [bridge], [outro] ; « [instrumental] » pour un morceau sans voix</span></label>
    <textarea rows="9" placeholder="[verse]\nSous le ciel de Paris…\n\n[chorus]\n…" oninput="setMusic('lyrics', this.value, true)">${esc(f.lyrics)}</textarea>
    <div class="grow"><label>Langue</label><div><select onchange="setMusic('language', this.value)">${LANGS.map(([c, n]) => `<option value="${c}" ${c === f.language ? 'selected' : ''}>${n}</option>`).join('')}</select></div></div>
    <div class="grow"><label>Durée</label><div><input type="range" min="10" max="600" step="5" value="${f.duration}" style="width:220px" oninput="setMusic('duration', +this.value, true); this.nextElementSibling.textContent = fmtDur(+this.value)"> <b>${fmtDur(f.duration)}</b></div></div>
    <div class="grow"><label>Musique</label><div>tempo ${num('bpm', 30, 300, 1, 70, 'auto')} bpm · tonalité <input value="${esc(f.key)}" placeholder="auto (ex. A minor)" size="12" onchange="setMusic('key', this.value)"> · mesure <select onchange="setMusic('time_signature', this.value)">${[['', 'auto'], ['4', '4/4'], ['3', '3/4'], ['2', '2/4'], ['6', '6/8']].map(([v, n]) => `<option value="${v}" ${String(f.time_signature) === v ? 'selected' : ''}>${n}</option>`).join('')}</select></div></div>
    <div class="grow"><label>Calcul</label><div>${num('steps', 1, 200, 1, 60)} étapes${f.model.includes('sft') ? ` · CFG ${num('cfg', 1, 15, 0.5, 60)}` : ''} · graine ${num('seed', -1, 2147483647, 1, 120)} <button class="btn small" onclick="setMusic('seed', -1)">🎲</button> · variantes ${num('batch', 1, 4, 1, 50)} · <select onchange="setMusic('format', this.value)">${['flac', 'wav', 'mp3'].map(x => `<option ${x === f.format ? 'selected' : ''} ${x === 'mp3' && !st.ffmpeg ? 'disabled' : ''}>${x}</option>`).join('')}</select>${st.ffmpeg ? '' : ' <span class="muted small" title="installez ffmpeg (sudo apt install ffmpeg) pour exporter en MP3">MP3 : ffmpeg requis</span>'}</div></div>
    <div class="row-inline" style="margin-top:12px"><button class="btn primary" id="grun-music" onclick="runMusic()">▶ Composer</button><span class="muted small">Premier lancement : téléchargement des poids du modèle choisi (plusieurs Go), suivez le journal ci-dessus.</span></div>`;
}
const fmtDur = (s) => `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')}`;
function setMusic(k, v, quiet) { G.music[k] = v; LS.set('music', pick(G.music, MUSIC_KEYS.filter(x => x !== 'seed'))); if (!quiet) renderMusicForm(); }
function setMusicModel(id) { const m = G.status.music.models.find(x => x.id === id); G.music.model = id; G.music.steps = m.steps; G.music.cfg = m.cfg; setMusic('model', id); }
async function runMusic() {
  const f = G.music;
  if (!f.prompt.trim() && !f.lyrics.trim()) { toast('Décrivez au moins un style ou des paroles'); return; }
  if (!await confirmGpu('ace-step')) return;
  try {
    const r = await api('/api/gen/run', Object.assign({kind: 'music'}, f));
    $('grun-music').disabled = true;
    queued(r);
    watchGen('music', r.job.id);
  } catch (e) { toast(e.message, 6000); }
}

// ------------------------------------------------------------------ démarrage
(async function genInit() {
  // reprend une génération / un téléchargement en cours après un rechargement de page
  try {
    const jobs = await api('/api/jobs');
    const mine = LS.get('genjobs') || [];
    for (const j of jobs.filter(x => x.state === 'running')) {
      if (j.kind === 'gen' && mine.includes(j.id)) { const k = j.label.startsWith('Musique') ? 'music' : j.label.startsWith('Vidéo') ? 'video' : 'image'; showMode(k); setTimeout(() => watchGen(k, j.id), 800); return; }
    }
  } catch (e) { /* */ }
  const m = LS.get('mode', 'text');
  if (m !== 'text') showMode(m);
})();
