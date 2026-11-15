/* IA Launcher — onglet YouTube : résumé d'une vidéo en une phrase (paragraphe dépliable,
   un point par sujet), questions libres sur la vidéo, historique.
   Réutilise les utilitaires d'app.js et gen.js ($, api, esc, toast, watchJob, renderJob, LS, G). */
'use strict';

const Y = {status: null, item: null, asking: false};

const ytDuree = (s) => s ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')}` : '';

async function loadYoutube() {
  const root = $('mode-youtube');
  if (!root.dataset.ready) {
    root.innerHTML = `<section class="card"><h1>Résumer une vidéo YouTube</h1>
      <p class="lead">Collez l'adresse d'une vidéo : le launcher lit les sous-titres écrits par son auteur (pour une vidéo ni française ni anglaise, sa traduction française ou anglaise s'il en a fait une), sinon il transcrit l'audio avec Whisper — jamais les sous-titres automatiques de YouTube, trop fautifs. Le modèle de texte lancé en tire <b>l'essentiel en une phrase</b>, un point par sujet si la vidéo en aborde plusieurs. Cliquez une phrase pour l'explication détaillée ; posez vos propres questions en dessous.</p>
      <div id="yt-tools"></div>
      <div class="row-inline">
        <input id="yt-url" placeholder="https://www.youtube.com/watch?v=…" size="50" onkeydown="if(event.key==='Enter')ytRun()">
        <button class="btn primary" id="yt-go" onclick="ytRun()">Résumer</button>
        <select id="yt-lang" onchange="ytLang(this.value)" title="langue des résumés et des réponses, quelle que soit celle de la vidéo (réglage du launcher, valable sur tous vos appareils)">
          <option value="fr">en français</option><option value="en">in English</option><option value="es">en español</option>
          <option value="de">auf Deutsch</option><option value="it">in italiano</option><option value="pt">em português</option></select>
      </div>
      <div id="yt-llm" class="small"></div>
      <div id="yt-job"></div></section>
      <section class="card hidden" id="yt-result"></section>
      <section class="card"><h1>Déjà résumées <span class="muted small" id="yt-count"></span></h1>
        <div class="row-inline small"><span class="muted">Faire le ménage :</span>
          <select id="yt-clean"><option value="7">résumés de plus de 7 jours</option><option value="30">résumés de plus de 30 jours</option><option value="0">tous les résumés</option></select>
          <button class="btn small" onclick="ytClean()">Supprimer</button></div>
        <div id="yt-history" class="yt-gal"></div></section>`;
    root.dataset.ready = '1';
  }
  await ytRefresh();
  // page rechargée pendant un résumé : reprendre son suivi
  if (!Object.values(JOBS).some(x => x.boxId === 'yt-job')) {
    try {
      const j = (await api('/api/jobs')).find(x => x.kind === 'youtube' && x.state === 'running' && x.label.startsWith('Résumé'));
      if (j) { $('yt-go').disabled = true; ytWatch(j.id); }
    } catch (e) { /* pas de suivi à reprendre */ }
  }
  const last = LS.get('yt.last', '');
  if (last && !Y.item) ytOpen(last);
}

async function ytRefresh() {
  try { Y.status = await api('/api/youtube'); } catch (e) { toast(e.message); return; }
  const t = Y.status.tools;
  $('yt-lang').value = Y.status.lang;
  $('yt-tools').innerHTML = t.ytdlp && t.deno ? '' : `<div class="row-inline"><span>Il faut d'abord <b>yt-dlp</b> (lit YouTube) et <b>Deno</b> (moteur JavaScript que YouTube impose) : ~100 Mo, dans le dossier des moteurs.</span>
      <button class="btn" onclick="ytInstall()">Installer</button></div><div id="yt-install-job"></div>`;
  if (t.ytdlp && t.deno && !t.whisper) $('yt-tools').innerHTML = `
      <div class="row-inline"><span>Il faut aussi <b>Whisper</b> : la plupart des vidéos n'ont pas de sous-titres écrits par leur auteur, et whisper.cpp transcrit alors l'audio. Le launcher le compile ${t.nvcc ? 'pour votre carte NVIDIA (CUDA, ~5 à 10 min)' : 'pour le processeur (pas de CUDA Toolkit trouvé : transcription lente)'}, puis télécharge le modèle large-v3-turbo (575 Mo) et, s'il n'est pas déjà installé, ffmpeg (160 Mo).</span>
      <button class="btn" onclick="ytInstall('install-whisper')">Installer</button></div><div id="yt-install-job"></div>`;
  const llm = Y.status.llm;
  $('yt-llm').innerHTML = `<div class="row-inline"><span class="muted">Modèle des résumés :</span>
      <select id="yt-model" onchange="ytSetModel(this.value)" title="chargé tout seul (configuration conseillée, contexte 64k ≈ 4 h de vidéo) quand aucun modèle de texte ne tourne, ou relancé ainsi si celui qui tourne a trop peu de contexte pour la vidéo"></select>
      <span class="muted">${!llm ? 'chargé au premier résumé (~30 s)' : llm.id === Y.status.model ? 'lancé' : `c’est <b>${esc(llm.name)}</b>, déjà lancé, qui sera utilisé`}</span></div>`;
  ytModels();
  const h = Y.status.history;
  $('yt-count').textContent = h.length ? `(${h.length})` : '';
  $('yt-history').innerHTML = h.length ? h.map(x => `<div class="yt-card ${Y.item && Y.item.meta.id === x.id ? 'on' : ''}" onclick="ytOpen('${esc(x.id)}', true)" title="${esc(x.phrase)}">
      <div class="yt-thumb"><img loading="lazy" alt="" src="/api/youtube/thumb?id=${esc(x.id)}" onerror="this.remove()">
        <span class="yt-dur">${ytMinSec(x.duration)}</span>
        <button class="yt-del" title="supprimer ce résumé" onclick="event.stopPropagation(); ytDelete(['${esc(x.id)}'])">✕</button></div>
      <div class="yt-ct"><b>${esc(x.title)}</b><span class="muted small">${esc(x.channel || '')} · ${new Date(x.at * 1000).toLocaleDateString('fr-FR')}</span></div>
    </div>`).join('') : '<span class="muted">aucune pour l’instant</span>';
}

const ytMinSec = (s) => s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : '';

async function ytDelete(ids, ask = true) {
  if (!ids.length) { toast('Rien à supprimer'); return; }
  if (ask && !confirm(ids.length > 1 ? `Supprimer ${ids.length} résumés (et leurs questions) ?` : 'Supprimer ce résumé (et ses questions) ?')) return;
  try {
    const d = await api('/api/youtube/delete', {ids});
    if (Y.item && ids.includes(Y.item.meta.id)) { Y.item = null; LS.set('yt.last', ''); $('yt-result').classList.add('hidden'); }
    toast(`${d.deleted} résumé${d.deleted > 1 ? 's' : ''} supprimé${d.deleted > 1 ? 's' : ''}`);
  } catch (e) { toast(e.message); }
  ytRefresh();
}

function ytClean() {
  const days = +$('yt-clean').value, limit = Date.now() / 1000 - days * 86400;
  const ids = Y.status.history.filter(x => !days || x.at < limit).map(x => x.id);
  const what = $('yt-clean').selectedOptions[0].text;
  if (!ids.length) { toast(`Aucun : ${what}`); return; }
  if (confirm(`Supprimer ${ids.length} résumé${ids.length > 1 ? 's' : ''} (${what}), avec leurs questions ?`)) ytDelete(ids, false);
}

async function ytLang(lang) {
  try { await api('/api/youtube/lang', {lang}); toast('Prochains résumés et réponses : ' + $('yt-lang').selectedOptions[0].text); }
  catch (e) { toast(e.message); }
}

async function ytModels() {
  let d; try { d = await api('/api/models'); } catch (e) { return; }
  const cur = Y.status.model;
  $('yt-model').innerHTML = (cur ? '' : '<option value="">— à choisir —</option>') + d.models.filter(m => !m.draft).map(m =>
    `<option value="${esc(m.id)}" ${m.id === cur ? 'selected' : ''}>${esc(m.name)} (${fmtG(m.size)})</option>`).join('');
}

async function ytSetModel(id) {
  try { await api('/api/youtube/model', {id}); toast('Modèle des résumés enregistré'); } catch (e) { toast(e.message); }
  ytRefresh();
}

async function ytInstall(what = 'install') {
  try {
    const d = await api('/api/youtube/' + what, {});
    watchJob(d.job.id, 'yt-install-job', ytRefresh);
  } catch (e) { toast(e.message); }
}

async function ytRun(again) {
  const url = again ? Y.item.meta.webpage_url : $('yt-url').value.trim();
  if (!url) return;
  $('yt-go').disabled = true;
  try {
    const d = await api('/api/youtube/run', {url, again: !!again});
    if (d.item) { ytShow(d.item); $('yt-go').disabled = false; return; }
    if (d.ahead) toast(`En file d’attente : ${d.ahead} tâche${d.ahead > 1 ? 's' : ''} avant celle-ci (génération en cours)`, 5000);
    ytWatch(d.job.id);
  } catch (e) { toast(e.message, 8000); $('yt-go').disabled = false; }
}

function ytWatch(id) {
  watchJob(id, 'yt-job', (j) => {
    $('yt-go').disabled = false;
    if (j.state === 'done') { $('yt-job').innerHTML = ''; ytShow(j.result); ytRefresh(); }
  });
}

// Détail d'un point → texte en lignes « - » : liste de puces (« > » = sous-puce) ; les anciens
// résumés (un texte) passent tels quels.
function ytDetail(d) {
  if (!Array.isArray(d)) return d;
  return d.map(x => x.trim().startsWith('>') ? '  - ' + x.trim().slice(1).trim() : '- ' + x.trim().replace(/^[-•*]\s+/, '')).join('\n');
}

// Texte du modèle → HTML : lignes « - » en puces (deux espaces de retrait = sous-puce),
// les autres en paragraphes, **…** en gras. Les anciens résumés rédigés en paragraphe s'affichent tels quels.
function ytText(s) {
  let html = '', depth = 0;
  for (const line of String(s || '').split('\n')) {
    const m = line.match(/^(\s*)[-•*]\s+(.*)$/);
    if (!m && !line.trim()) continue;
    const d = m ? Math.min(3, Math.floor(m[1].length / 2) + 1) : 0;
    while (depth < d) { html += '<ul>'; depth++; }
    while (depth > d) { html += '</ul>'; depth--; }
    const t = esc(m ? m[2] : line.trim()).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');   // le modèle met parfois du gras malgré la consigne
    html += m ? `<li>${t}</li>` : `<p>${t}</p>`;
  }
  return html + '</ul>'.repeat(depth);
}

async function ytOpen(id, scroll) {
  try { ytShow(await api('/api/youtube/item?id=' + encodeURIComponent(id))); }
  catch (e) { LS.set('yt.last', ''); return; }
  document.querySelectorAll('.yt-card').forEach(c => c.classList.toggle('on', c.getAttribute('onclick').includes(`'${id}'`)));
  if (scroll) $('yt-result').scrollIntoView({behavior: 'smooth', block: 'start'});
}

function ytShow(item) {
  Y.item = item;
  LS.set('yt.last', item.meta.id);
  const m = item.meta, s = item.summary;
  const subs = !m.subs ? '' : m.subs.kind === 'whisper' ? `transcrit par Whisper (${esc(m.subs.lang)})`
    : m.subs.kind === 'auteur' ? `sous-titres de l’auteur (${esc(m.subs.lang)})` : `sous-titres ${m.subs.kind} (${esc(m.subs.lang)})`;
  const skip = m.skipped ? ` · ${ytDuree(m.skipped.seconds)} de sponsor, intro… retirées (SponsorBlock)` : '';
  const box = $('yt-result');
  box.classList.remove('hidden');
  box.innerHTML = `<h1><a href="${esc(m.webpage_url)}" target="_blank" rel="noopener">${esc(m.title)}</a></h1>
    <p class="muted small">${esc(m.channel || '')} · ${ytDuree(m.duration)} · ${subs}${skip} · ${fmtN(item.words)} mots lus en ${item.seconds} s
      <button class="link" onclick="ytRun(true)" title="relit la vidéo (sous-titres de l’auteur, sinon Whisper) et redemande un résumé au modèle lancé">refaire le résumé</button></p>
    ${s.points.map(p => `<details class="yt-point"><summary>${esc(p.phrase)}</summary><div class="yt-det">${ytText(ytDetail(p.detail))}</div></details>`).join('')}
    <h2>Une question sur la vidéo ?</h2>
    <div id="yt-chat" class="chat">${item.questions.map(ytMsgs).join('') || '<span class="muted small">Le modèle répond d’après ce qui est dit dans la vidéo, en citant le moment (mm:ss). La première question est la plus longue : il relit la transcription.</span>'}</div>
    <div class="composer">
      <textarea id="yt-q" rows="2" placeholder="Votre question… (Entrée pour envoyer, Maj+Entrée = nouvelle ligne)" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();ytAsk()}"></textarea>
      <button class="btn primary" id="yt-send" onclick="ytAsk()">Envoyer</button>
    </div>`;
  const c = $('yt-chat'); c.scrollTop = c.scrollHeight;
}

const ytMsgs = (q) => `<div class="msg user">${esc(q.q)}</div><div class="msg assistant yt-a">${ytText(q.a)}</div>`;

async function ytAsk() {
  const question = $('yt-q').value.trim();
  if (!question || Y.asking) return;
  const item = Y.item, c = $('yt-chat');
  if (!item.questions.length) c.innerHTML = '';
  c.insertAdjacentHTML('beforeend', `<div class="msg user">${esc(question)}</div><div class="msg assistant muted" id="yt-wait">…</div>`);
  c.scrollTop = c.scrollHeight;
  $('yt-q').value = ''; Y.asking = true; $('yt-send').disabled = true;
  try {
    const d = await api('/api/youtube/ask', {id: item.meta.id, question});
    let j = d.job;
    for (let fails = 0; j.state === 'running';) {     // file d'attente, chargement du modèle, réponse
      await new Promise(r => setTimeout(r, 1000));
      try { j = await api('/api/jobs/' + j.id); fails = 0; }
      catch (e) { if (++fails > 30) throw e; continue; }   // téléphone en veille, réseau coupé : on réessaie
      if ($('yt-wait')) $('yt-wait').textContent = j.detail || '…';
    }
    if (j.state !== 'done') throw new Error(j.error || 'annulé');
    item.questions.push({q: question, a: j.result.answer});
    $('yt-wait').outerHTML = `<div class="msg assistant yt-a">${ytText(j.result.answer)}</div>`;
  } catch (e) {
    $('yt-wait').outerHTML = `<div class="msg assistant warn-item">${esc(e.message)}</div>`;
  }
  Y.asking = false; $('yt-send').disabled = false;
  c.scrollTop = c.scrollHeight;
}
