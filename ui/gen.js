/* IA Launcher — onglets Image, Vidéo et Musique.
   Réutilise les utilitaires d'app.js ($, api, esc, fmtG, toast, watchJob, renderJob).
   Image et vidéo passent par stable-diffusion.cpp (sd-server), la musique par ACE-Step. */
'use strict';

const G = {
  mode: 'text', status: null, models: {}, sel: {}, loras: {}, form: {}, inputs: {image: {}, video: {}}, viewing: {},
};
const SAMPLERS = ['euler', 'euler_a', 'heun', 'dpm2', 'dpm++2s_a', 'dpm++2m', 'dpm++2mv2', 'dpm++2m_sde', 'ipndm', 'ipndm_v', 'lcm', 'ddim_trailing', 'tcd', 'res_multistep', 'res_2s', 'er_sde', 'lms'];
const SCHEDULERS = ['discrete', 'karras', 'exponential', 'ays', 'gits', 'sgm_uniform', 'simple', 'smoothstep', 'kl_optimal', 'beta'];
const RATIOS = [['1:1', 1, 1], ['4:3', 4, 3], ['3:4', 3, 4], ['16:9', 16, 9], ['9:16', 9, 16], ['3:2', 3, 2], ['2:3', 2, 3]];
const LANGS = [['fr', 'français'], ['en', 'anglais'], ['es', 'espagnol'], ['de', 'allemand'], ['it', 'italien'], ['pt', 'portugais'], ['ja', 'japonais'], ['ko', 'coréen'], ['zh', 'chinois'], ['ru', 'russe'], ['ar', 'arabe'], ['hi', 'hindi']];
const LS = {
  get(k, d) { try { const v = localStorage.getItem('ialauncher.' + k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('ialauncher.' + k, JSON.stringify(v)); } catch (e) { /* stockage indisponible */ } },
};

// ------------------------------------------------------------------ navigation
function showMode(m) {
  G.mode = m;
  document.querySelectorAll('.mode').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
  ['text', 'image', 'video', 'music', 'storage'].forEach(x => $('mode-' + x).classList.toggle('hidden', x !== m));
  LS.set('mode', m);
  if (m === 'image' || m === 'video') loadVisual(m);
  if (m === 'music') loadMusic();
  if (m === 'storage') loadStorage();
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
  <section class="card"><h1><span class="num">1</span> Modèle</h1>
    <p class="lead">${isV ? 'Un modèle vidéo = un transformer de diffusion + un encodeur de texte + un VAE (qui décode les images latentes en pixels). Le launcher télécharge les trois.' : 'Tous ces modèles tournent dans le même moteur (stable-diffusion.cpp, ci-dessus) : choisissez-en un, il est chargé à la première génération.'}</p>
    <div id="gm-${kind}"></div></section>
  <section class="card"><h1><span class="num">2</span> ${isV ? 'Générer une vidéo' : 'Générer'}</h1>
    <div id="gsvc-${kind}" class="small muted"></div>
    <div class="genwrap"><div id="gf-${kind}" class="genform"></div><div id="go-${kind}" class="genout"><p class="muted">Le résultat s'affichera ici.</p></div></div></section>
  <section class="card"><h1>Galerie <button class="link small" onclick="loadOutputs('${kind}')">↻</button></h1>
    <p class="lead small">Tout est enregistré dans <span class="mono">~/.ialauncher/outputs/${kind}/</span>, avec les réglages à côté (.json). Cliquez sur une ${isV ? 'vidéo' : 'image'} pour la revoir et reprendre ses réglages.</p>
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
  try { const r = await api('/api/gen/models?kind=' + kind); G.models[kind] = r.models; G.tags = r.tags; G.webui = r.webui; }
  catch (e) { $('gm-' + kind).innerHTML = `<div class="warn-item error">${esc(e.message)}</div>`; return; }
  if (!G.sel[kind]) {
    const saved = LS.get('model.' + kind);
    const m = G.models[kind].find(x => x.id === saved) || G.models[kind].find(x => x.installed && !x.local) || null;
    if (m && m.installed) selectGenModel(kind, m.id, true);
  }
  renderModels(kind);
  if (!G.sel[kind]) renderForm(kind);
}
function renderModels(kind) {
  const list = G.models[kind] || [], sel = G.sel[kind];
  const cat = list.filter(m => !m.local), loc = list.filter(m => m.local);
  let h = `<h3 class="small muted" style="margin:4px 0 6px">CATALOGUE — MODÈLES À TÉLÉCHARGER</h3>
    <p class="small muted" style="margin:0 0 8px">Chacun se compose de plusieurs fichiers, téléchargés ensemble : le modèle de diffusion, un encodeur de texte (petit LLM) et un VAE. Ils servent aussi de base aux checkpoints Civitai de leur famille.</p>` + '<div class="cat">' + cat.map(m => `
    <div class="mitem ${sel && sel.id === m.id ? 'sel' : ''}">
      <div class="n">${esc(m.name)} <span class="badge">${esc(m.license)}</span></div>
      <div class="s">${m.tags.map(t => `<span class="badge" title="${esc((G.tags || {})[t] || '')}">${esc(t)}</span>`).join('')}<span>${fmtGB(m.size)} au total</span></div>
      <div class="blurb">${esc(m.blurb)}</div>
      ${m.notice ? `<div class="warn-item warn small">⚠ ${esc(m.notice)}</div>` : ''}
      <details class="small muted"><summary>fichiers</summary>${m.files.map(f => `<div>${f.present ? '✓' : '·'} ${esc(f.label)} : <span class="mono">${esc(f.path.split('/').pop())}</span> (${fmtGB(f.size)})</div>`).join('')}</details>
      <div class="row-inline">${m.installed
        ? `<button class="btn ${sel && sel.id === m.id ? '' : 'primary'} small" onclick="selectGenModel('${kind}','${m.id}')">${sel && sel.id === m.id ? '✓ sélectionné' : 'Utiliser'}</button>`
        : `<button class="btn primary small" onclick="downloadGenModel('${kind}','${m.id}')">⬇ Télécharger ${fmtGB(m.missing)}</button>`}</div>
      <div id="gdl-${m.id}"></div>
    </div>`).join('') + '</div>';
  if (kind === 'image') {
    h += `<h3 class="small muted" style="margin:16px 0 6px">DÉJÀ SUR CE PC — CHECKPOINTS${G.webui && G.webui.length ? ' (' + esc(G.webui.map(w => w.split('/').pop()).join(', ')) + ', Civitai)' : ''}</h3>
      <p class="small muted" style="margin:0 0 8px">Checkpoints SD 1.5 / SDXL / Pony tout-en-un (un seul fichier), lus en place, et ceux téléchargés depuis Civitai. Le programme Stable Diffusion WebUI n’est pas utilisé, seulement ses fichiers.</p>`;
    h += loc.length ? '<div class="mlist compact">' + loc.map(m => `
      <div class="mitem ${sel && sel.id === m.id ? 'sel' : ''} ${m.installed ? '' : 'dim'}" onclick="selectGenModel('${kind}','${m.id}')" title="${esc(m.path + '\n' + (m.blurb || ''))}">
        <div class="n">${esc(m.name)}${m.version ? ` <span class="muted small">${esc(m.version)}</span>` : ''}</div>
        <div class="s"><span class="badge acc">${esc(famLabel(m.family) || m.arch || '?')}</span><span>${fmtGB(m.size)}</span><span>${esc(m.source)}</span>${m.installed ? '' : '<span class="badge warn">modèle de base manquant</span>'}</div>
      </div>`).join('') + '</div>' : '<p class="muted small">Aucun checkpoint : cherchez-en sur Civitai ci-dessous, ou utilisez le catalogue. (Les dossiers de Stable Diffusion WebUI et Forge sont repris automatiquement s’ils existent, mais ne sont pas nécessaires.)</p>';
    h += `<div class="row-inline muted small"><span>Ajouter un dossier de checkpoints :</span><input id="gen-add-dir" placeholder="/chemin/vers/dossier" size="40"><button class="btn small" onclick="addGenDir()">ajouter</button></div>`;
  }
  h += `<div class="small muted" style="margin-top:6px">${S.hw ? fmtGB(S.hw.disk.free) + ' libres sur le disque · ' : ''}<button class="link" onclick="showMode('storage')">voir et libérer l’espace occupé →</button></div>`;
  h += civitaiPanel(kind);
  $('gm-' + kind).innerHTML = h;
  renderCivitai(kind);
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
  h += `<div id="glora-${kind}"></div>`;
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
}
function setGen(kind, k, v, quiet) {
  G.form[kind][k] = v;
  LS.set('form.' + kind, {prompt: G.form[kind].prompt, negative: G.form[kind].negative, offload: G.form[kind].offload, vae_tiling: G.form[kind].vae_tiling, batch: G.form[kind].batch});
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
function renderLoras(kind) {
  const m = G.sel[kind], box = $('glora-' + kind); if (!box || !m) return;
  const l = G.loras[m.id]; if (!l) return;
  const fam = m.family || l.family;
  const ok = l.loras.filter(x => x.family === fam), other = l.loras.filter(x => x.family !== fam);
  const chip = (x) => `<button class="chip ${x.family && x.family !== fam ? 'dim' : ''}" title="${esc((famLabel(x.family) || 'famille inconnue') + (x.words.length ? ' · mots déclencheurs : ' + x.words.join(', ') : '') + ' · ' + x.source)}" onclick='insertLora("${kind}", ${esc(JSON.stringify(x))})'>${esc(x.name)}${x.family ? ` <span class="muted">${esc(famLabel(x.family))}</span>` : ''}</button>`;
  box.innerHTML = l.loras.length ? `<details class="small" ${ok.length ? 'open' : ''}><summary>${ok.length} LoRA pour ce modèle (${esc(famLabel(fam) || '?')})${other.length ? `, ${other.length} autres` : ''} — cliquez pour insérer</summary>
      <div class="chips">${ok.map(chip).join('')}</div>
      ${other.length ? `<details><summary class="muted">autres familles / famille inconnue (${other.length})</summary><div class="chips">${other.map(chip).join('')}</div></details>` : ''}
      <div class="muted">Un LoRA ne fonctionne qu’avec un modèle de la même famille. Les mots déclencheurs sont ajoutés avec lui. Dossier : <span class="mono">${esc(l.dir)}</span> (y compris ceux de votre WebUI, par liens).</div></details>`
    : `<div class="muted small">Aucun LoRA : cherchez-en sur Civitai (type « LoRA ») ou déposez des .safetensors dans <span class="mono">${esc(l.dir)}</span>.</div>`;
}
function insertLora(kind, x) {
  const ta = $('gp-' + kind);
  ta.value = (ta.value.trim() + (x.words.length ? ', ' + x.words.join(', ') : '') + ` <lora:${x.name}:0.8>`).replace(/^, /, '').trim();
  setGen(kind, 'prompt', ta.value, true);
}

const FAMS = {sd15: 'SD 1.5', sdxl: 'SDXL / Pony / Illustrious', zimage: 'Z-Image', flux2klein9: 'FLUX.2 klein 9B', qwen21: 'Qwen-Image 2.1',
  anima: 'Anima', krea2: 'Krea 2', wan5b: 'Wan 2.2 5B', wan14b: 'Wan 2.2 A14B', minimax: 'MiniMax-H3', flux: 'FLUX.1 (non pris en charge)'};
const famLabel = (f) => FAMS[f] || '';
const VIDEO_FAMS = ['wan5b', 'wan14b', 'minimax'];

// ------------------------------------------------------------------ Civitai
function civitaiPanel(kind) {
  const c = G.civ && G.civ[kind] || {};
  const fams = Object.keys(FAMS).filter(f => f !== 'flux' && (kind === 'video') === VIDEO_FAMS.includes(f));
  return `<details class="civ" id="civ-${kind}" ${c.open ? 'open' : ''} ontoggle="civState('${kind}').open = this.open">
    <summary>🔎 Chercher sur Civitai — des milliers de checkpoints et de LoRA partagés par la communauté</summary>
    <div class="small muted" style="margin:6px 0">Seuls les modèles des familles que stable-diffusion.cpp sait charger sont proposés.${kind === 'video' ? ' Pour la vidéo, Civitai propose surtout des LoRA.' : ' Pour Z-Image, FLUX.2, Qwen-Image, Anima et Krea 2, un checkpoint Civitai ne contient que le modèle de diffusion : le modèle de base du catalogue doit être téléchargé (il fournit l’encodeur de texte et le VAE).'}</div>
    <div id="civtok-${kind}"></div>
    <div class="row-inline">
      <input id="civq-${kind}" placeholder="nom, style, personnage…" size="26" value="${esc(c.q || '')}" onkeydown="if(event.key==='Enter')civSearch('${kind}')">
      <select id="civt-${kind}">${(kind === 'video' ? [['LORA', 'LoRA']] : [['Checkpoint', 'Checkpoints'], ['LORA', 'LoRA']]).map(([v, l]) => `<option value="${v}" ${c.type === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <select id="civf-${kind}"><option value="">toutes les familles compatibles</option>${fams.map(f => `<option value="${f}" ${c.family === f ? 'selected' : ''}>${FAMS[f]}</option>`).join('')}</select>
      <select id="civs-${kind}">${['Most Downloaded', 'Highest Rated', 'Newest'].map((v, i) => `<option value="${v}" ${c.sort === v ? 'selected' : ''}>${['les plus téléchargés', 'les mieux notés', 'les plus récents'][i]}</option>`).join('')}</select>
      <label class="switch small"><input type="checkbox" id="civn-${kind}" ${c.nsfw ? 'checked' : ''}> contenu adulte</label>
      <button class="btn" onclick="civSearch('${kind}')">chercher</button>
    </div>
    <div id="civr-${kind}"></div></details>`;
}
function civState(kind) { G.civ = G.civ || {}; return G.civ[kind] = G.civ[kind] || {}; }
async function civSearch(kind, more) {
  const c = civState(kind);
  if (!more) Object.assign(c, {q: $('civq-' + kind).value.trim(), type: $('civt-' + kind).value, family: $('civf-' + kind).value,
    sort: $('civs-' + kind).value, nsfw: $('civn-' + kind).checked, items: [], next: ''});
  $('civr-' + kind).insertAdjacentHTML('beforeend', '<p class="muted small" id="civwait">Recherche sur Civitai… (quelques secondes)</p>');
  try {
    const r = await api(`/api/civitai/search?q=${encodeURIComponent(c.q)}&type=${c.type}&family=${c.family}&sort=${encodeURIComponent(c.sort)}&nsfw=${c.nsfw ? 1 : 0}&cursor=${more ? c.next : ''}`);
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
      <div class="n"><a href="${esc(m.url)}" target="_blank">${esc(m.name)}</a></div>
      <div class="s"><span class="badge acc">${esc(v.baseModel)}</span><span>⬇ ${fmtN(m.downloads)}</span><span>par ${esc(m.creator)}</span>${m.nsfw ? '<span class="badge warn">adulte</span>' : ''}</div>
      ${m.versions.length > 1 ? `<select onchange="civPick('${kind}',${i},'v',+this.value)">${m.versions.map((x, j) => `<option value="${j}" ${j === vi ? 'selected' : ''}>${esc(x.name)} (${esc(x.baseModel)})</option>`).join('')}</select>` : `<div class="small muted">${esc(v.name)}</div>`}
      ${v.files.length > 1 ? `<select onchange="civPick('${kind}',${i},'f',+this.value)">${v.files.map(x => `<option value="${x.id}" ${f && x.id === f.id ? 'selected' : ''}>${esc(x.name)} — ${fmtGB(x.size)}</option>`).join('')}</select>` : ''}
      ${v.words.length ? `<div class="small muted">mots déclencheurs : ${esc(v.words.slice(0, 4).join(', '))}</div>` : ''}
      ${!v.family ? '<div class="small warn">famille non prise en charge</div>' : f ? `<button class="btn small primary" onclick="civDownload('${kind}',${i})">⬇ ${fmtGB(f.size)}</button>` : (v.pickle_only ? '<div class="small muted" title="le format pickle (.ckpt/.pt) peut exécuter du code à l’ouverture : le launcher ne le télécharge pas">seulement en .ckpt (format non sûr)</div>' : '<div class="small muted">aucun fichier téléchargeable</div>')}
      <div id="civdl-${kind}-${i}"></div></div>`;
  }).join('') + '</div>' : '<p class="muted small">Aucun résultat.</p>') + (c.next ? `<button class="btn small" onclick="civSearch('${kind}', true)">plus de résultats</button>` : '');
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
  try {
    const r = await api('/api/gen/run', req);
    $('grun-' + kind).disabled = true;
    watchGen(kind, r.job.id);
  } catch (e) { toast(e.message, 6000); }
}
function watchGen(kind, id) {
  const out = $('go-' + kind);
  if (out) out.innerHTML = `<div id="gjob-${kind}"></div>`;
  const svc = setInterval(refreshGenStatus, 3000);
  watchJob(id, 'gjob-' + kind, (j) => {
    clearInterval(svc); refreshGenStatus();
    const b = $('grun-' + kind); if (b) b.disabled = false;
    if (j.state === 'done' && j.result) { showResult(kind, j.result.files); loadOutputs(kind); }
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
    <div class="row-inline"><button class="btn small" onclick='reuseMeta("${kind}", ${esc(JSON.stringify(f.name))})'>reprendre ces réglages</button>
      ${kind === 'image' ? `<button class="btn small" onclick='toInput("${f.url}")'>utiliser comme image d’entrée</button>` : ''}
      <a class="btn small" href="${f.url}" download>télécharger</a>
      <button class="btn small" onclick='deleteOutput("${kind}", ${esc(JSON.stringify(f.name))})'>supprimer</button></div>`;
}

// ------------------------------------------------------------------ galerie
async function loadOutputs(kind) {
  try { G['out_' + kind] = await api('/api/gen/outputs?kind=' + kind); } catch (e) { return; }
  const box = $('gg-' + kind); if (!box) return;
  const items = G['out_' + kind];
  if (kind === 'music') {
    box.innerHTML = items.length ? items.map(f => `<div class="track"><div><b>${esc((f.meta.prompt || '').slice(0, 80) || f.name)}</b> <span class="muted small">${esc(f.meta.model || '')} · ${f.meta.duration ? f.meta.duration + ' s' : ''} · graine ${f.meta.seed}</span></div>${mediaTag('music', f.url)}<div class="row-inline small"><button class="link" onclick='reuseMeta("music", ${esc(JSON.stringify(f.name))})'>reprendre</button> <a href="${f.url}" download>télécharger</a> <button class="link" onclick='deleteOutput("music", ${esc(JSON.stringify(f.name))})'>supprimer</button></div></div>`).join('') : '<p class="muted small">Rien encore.</p>';
    return;
  }
  box.innerHTML = items.length ? items.map(f => `<div class="gthumb" onclick='viewOutput("${kind}", ${esc(JSON.stringify(f.name))})' title="${esc(f.meta.prompt || '')}">${kind === 'video' ? `<video src="${f.url}" muted preload="metadata"></video>` : `<img src="${f.url}" loading="lazy">`}</div>`).join('') : '<p class="muted small">Rien encore.</p>';
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
      <p class="lead small">Enregistrés dans <span class="mono">~/.ialauncher/outputs/music/</span>.</p><div id="gg-music"></div></section>`;
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
    watchGen('music', r.job.id);
  } catch (e) { toast(e.message, 6000); }
}

// ------------------------------------------------------------------ démarrage
(async function genInit() {
  // reprend une génération / un téléchargement en cours après un rechargement de page
  try {
    const jobs = await api('/api/jobs');
    for (const j of jobs.filter(x => x.state === 'running')) {
      if (j.kind === 'gen') { const k = j.label.startsWith('Musique') ? 'music' : j.label.startsWith('Vidéo') ? 'video' : 'image'; showMode(k); setTimeout(() => watchGen(k, j.id), 800); return; }
    }
  } catch (e) { /* */ }
  const m = LS.get('mode', 'text');
  if (m !== 'text') showMode(m);
})();
