/* IA Launcher — onglet Podcasts : épisodes transcrits par Whisper puis résumés comme les vidéos
   de l'onglet YouTube (même modèle, même langue, mêmes questions libres). Podcasts suivis :
   chaque nouvel épisode est résumé tout seul.
   Réutilise app.js, gen.js et youtube.js ($, api, esc, toast, watchJob, LS, ytText, ytDetailHtml, ytDuree, ytMinSec, ytMsgs). */
'use strict';

const P = {status: null, item: null, asking: false, open: null, eps: {}};   // eps : derniers épisodes lus, par podcast

const pcDate = (t) => t ? new Date(t * 1000).toLocaleDateString('fr-FR') : '';

async function loadPodcasts() {
  const root = $('mode-podcasts');
  if (!root.dataset.ready) {
    root.innerHTML = `<section class="card"><h1>Résumer un podcast</h1>
      <p class="lead">Collez le <b>flux RSS</b> d'un podcast, son lien <b>Apple Podcasts</b> ou l'adresse de sa page web : le launcher en liste les derniers épisodes. Chaque épisode est transcrit par Whisper puis résumé en une phrase, comme une vidéo de l'onglet YouTube. Cochez « suivre » pour que chaque nouvel épisode soit résumé tout seul (le launcher vérifie toutes les heures quand il tourne ; le portier le réveille toutes les 6 h pour ça).</p>
      <div id="pc-tools"></div>
      <div class="row-inline">
        <input id="pc-url" placeholder="https://…/rss  ou  https://podcasts.apple.com/…" size="50" onkeydown="if(event.key==='Enter')pcAdd()">
        <label title="résumer tout seul chaque épisode publié à partir de maintenant"><input type="checkbox" id="pc-follow" checked> suivre</label>
        <button class="btn primary" id="pc-go" onclick="pcAdd()">Ajouter</button>
      </div>
      <div id="pc-llm" class="small"></div>
      <div id="pc-job"></div></section>
    <section class="card"><h1>Mes podcasts</h1><div id="pc-feeds"></div></section>
    <section class="card hidden" id="pc-result"></section>
    <section class="card"><h1>Déjà résumés <span class="muted small" id="pc-count"></span></h1>
      <div class="row-inline small"><span class="muted">Faire le ménage :</span>
        <select id="pc-clean"><option value="7">résumés de plus de 7 jours</option><option value="30">résumés de plus de 30 jours</option><option value="0">tous les résumés</option></select>
        <button class="btn small" onclick="pcClean()">Supprimer</button></div>
      <div id="pc-history" class="yt-gal"></div></section>`;
    root.dataset.ready = '1';
  }
  await pcRefresh();
  const last = LS.get('pc.last', '');
  if (last && !P.item) pcOpen(last);
}

async function pcRefresh() {
  try { P.status = await api('/api/podcasts'); } catch (e) { toast(e.message); return; }
  const s = P.status, t = s.tools;
  $('pc-tools').innerHTML = t.whisper ? '' : `<div class="row-inline"><span>Il faut d'abord <b>Whisper</b> pour transcrire l'audio des épisodes : le launcher compile whisper.cpp ${t.nvcc ? 'pour votre carte NVIDIA (CUDA, ~5 à 10 min)' : 'pour le processeur (pas de CUDA Toolkit trouvé : transcription lente)'}, puis télécharge le modèle large-v3-turbo (575 Mo) et, s'il manque, ffmpeg (160 Mo).</span>
      <button class="btn" onclick="pcInstall()">Installer</button></div><div id="pc-install-job"></div>`;
  const llm = s.llm, lang = {fr: 'en français', en: 'en anglais', es: 'en espagnol', de: 'en allemand', it: 'en italien', pt: 'en portugais'}[s.lang];
  $('pc-llm').innerHTML = `<span class="muted">Résumés ${lang}, par ${!llm || llm.id === s.model ? 'le modèle des résumés' : `<b>${esc(llm.name)}</b> (déjà lancé)`} : réglages communs avec l’onglet <button class="link" onclick="showMode('youtube')">YouTube</button>.</span>`;
  // suivi des résumés en cours (lancés ici, par un autre appareil ou par le suivi automatique)
  Object.values(s.running).forEach(pcWatch);
  pcFeeds();
  const h = s.history;
  $('pc-count').textContent = h.length ? `(${h.length})` : '';
  $('pc-history').innerHTML = h.length ? h.map(x => `<div class="yt-card ${P.item && P.item.meta.id === x.id ? 'on' : ''}" data-id="${esc(x.id)}" onclick="pcOpen('${esc(x.id)}', true)" title="${esc(x.phrase)}">
      <div class="yt-thumb"><img loading="lazy" alt="" src="/api/podcasts/thumb?id=${esc(x.id)}" onerror="this.remove()">
        <span class="yt-dur">${ytMinSec(x.duration)}</span>
        <button class="yt-del" title="supprimer ce résumé" onclick="event.stopPropagation(); pcDelete(['${esc(x.id)}'])">✕</button></div>
      <div class="yt-ct"><b>${esc(x.title)}</b><span class="muted small">${esc(x.channel || '')} · ${pcDate(x.date || x.at)}</span></div>
    </div>`).join('') : '<span class="muted">aucun pour l’instant</span>';
}

function pcFeeds() {
  const f = P.status.feeds;
  $('pc-feeds').innerHTML = f.length ? f.map(x => `<div class="pc-feed">
      <div class="pc-head" onclick="pcToggle('${x.id}')">
        <img loading="lazy" alt="" src="/api/podcasts/thumb?id=${x.id}" onerror="this.style.visibility='hidden'">
        <div class="pc-name"><b>${esc(x.title)}</b><span class="muted small">${esc(x.author || '')}${x.follow && x.checked ? ` · vérifié le ${new Date(x.checked * 1000).toLocaleString('fr-FR', {dateStyle: 'short', timeStyle: 'short'})}` : ''}</span>
          ${x.error ? `<span class="small warn-item">flux injoignable : ${esc(x.error)}</span>` : ''}</div>
        <label class="small" onclick="event.stopPropagation()" title="résumer tout seul chaque nouvel épisode"><input type="checkbox" ${x.follow ? 'checked' : ''} onchange="pcFollow('${x.id}', this.checked)"> suivi</label>
        <button class="link" onclick="event.stopPropagation(); pcRemove('${x.id}')" title="retirer ce podcast (les résumés restent)">✕</button>
      </div>
      <div class="pc-eps" id="pc-eps-${x.id}">${P.open === x.id ? '<span class="muted small">…</span>' : ''}</div></div>`).join('')
    : '<span class="muted">aucun podcast pour l’instant</span>';
  if (P.open) pcEpisodes(P.open);
}

function pcToggle(fid) {
  P.open = P.open === fid ? null : fid;
  if (P.open) delete P.eps[fid];   // ouvert à nouveau : relire le flux (nouvel épisode ?)
  pcFeeds();
}

async function pcEpisodes(fid) {
  const box = $('pc-eps-' + fid); if (!box) return;
  if (!P.eps[fid]) {
    box.innerHTML = '<span class="muted small">lecture du flux…</span>';
    try { P.eps[fid] = (await api('/api/podcasts/episodes?feed=' + fid)).episodes; } catch (e) { box.innerHTML = `<div class="warn-item">${esc(e.message)}</div>`; return; }
  }
  const run = P.status.running, done = new Set(P.status.history.map(h => h.id));
  box.innerHTML = P.eps[fid].map(e => `<div class="pc-ep"><div><b>${esc(e.title)}</b> <span class="muted small">${pcDate(e.date)} · ${ytDuree(e.duration)}</span></div>
      ${done.has(e.id) ? `<button class="btn small" onclick="pcOpen('${e.id}', true)">voir le résumé</button>`
        : run[e.id] ? '<span class="muted small">résumé en cours…</span>'
        : `<button class="btn small primary" onclick="pcRun('${fid}', '${e.id}')">Résumer</button>`}</div>`).join('') || '<span class="muted small">aucun épisode avec un fichier audio</span>';
}

async function pcAdd() {
  const url = $('pc-url').value.trim();
  if (!url) return;
  $('pc-go').disabled = true;
  try {
    const d = await api('/api/podcasts/add', {url, follow: $('pc-follow').checked});
    $('pc-url').value = '';
    toast(`${d.feed.title} ajouté${d.feed.follow ? ' et suivi : ses prochains épisodes seront résumés tout seuls' : ''}`, 5000);
    P.open = d.feed.id; P.eps[d.feed.id] = d.episodes;
    await pcRefresh();
  } catch (e) { toast(e.message, 8000); }
  $('pc-go').disabled = false;
}

async function pcFollow(fid, follow) {
  try {
    await api('/api/podcasts/follow', {feed: fid, follow});
    toast(follow ? 'Suivi : les épisodes publiés à partir de maintenant seront résumés tout seuls' : 'Plus suivi');
  } catch (e) { toast(e.message); }
  pcRefresh();
}

async function pcRemove(fid) {
  if (!confirm('Retirer ce podcast de la liste ? Les résumés déjà faits restent.')) return;
  try { await api('/api/podcasts/remove', {feed: fid}); } catch (e) { toast(e.message); }
  if (P.open === fid) P.open = null;
  pcRefresh();
}

async function pcInstall() {
  try { watchJob((await api('/api/youtube/install-whisper', {})).job.id, 'pc-install-job', pcRefresh); }
  catch (e) { toast(e.message); }
}

async function pcRun(fid, eid, again) {
  try {
    const ep = (P.eps[fid] || []).find(x => x.id === eid);   // titre : pour la file d'attente
    const d = await api('/api/podcasts/run', {feed: fid, id: eid, again: !!again, title: ep ? ep.title : ''});
    if (d.item) { pcShow(d.item); return; }
    P.status.running[eid] = d.job.id;
    if (P.open) pcEpisodes(P.open);
    pcWatch(d.job.id);
  } catch (e) { toast(e.message, 8000); }
}

// un bloc de suivi par résumé (lancé ici, par un autre appareil ou par le suivi automatique)
function pcWatch(id) {
  const box = 'pc-job-' + id;
  if ($(box)) return;
  $('pc-job').insertAdjacentHTML('beforeend', `<div id="${box}"></div>`);
  watchJob(id, box, (j) => {
    if (j.state === 'done') { $(box).remove(); if (!P.asking) pcShow(j.result); }
    pcRefresh();
  });
}

async function pcOpen(id, scroll) {
  try { pcShow(await api('/api/podcasts/item?id=' + encodeURIComponent(id))); }
  catch (e) { LS.set('pc.last', ''); return; }
  if (scroll) $('pc-result').scrollIntoView({behavior: 'smooth', block: 'start'});
}

function pcShow(item) {
  P.item = item;
  LS.set('pc.last', item.meta.id);
  document.querySelectorAll('#pc-history .yt-card').forEach(c => c.classList.toggle('on', c.dataset.id === item.meta.id));
  const m = item.meta, s = item.summary, box = $('pc-result');
  box.classList.remove('hidden');
  box.innerHTML = `<h1><a href="${esc(m.webpage_url)}" target="_blank" rel="noopener">${esc(m.title)}</a></h1>
    <p class="muted small">${esc(m.channel || '')} · ${pcDate(m.date)} · ${ytDuree(m.duration)} · transcrit par Whisper (${esc((m.subs || {}).lang || '?')}) · ${fmtN(item.words)} mots lus en ${item.seconds} s
      <button class="link" onclick="pcRun('', '${m.id}', true)" title="redemande un résumé au modèle, sur la transcription déjà faite">refaire le résumé</button></p>
    ${s.points.map(p => `<details class="yt-point"><summary>${esc(p.phrase)}</summary><div class="yt-det">${ytDetailHtml(p.detail)}</div></details>`).join('')}
    <h2>Une question sur l’épisode ?</h2>
    <div id="pc-chat" class="chat">${item.questions.map(ytMsgs).join('') || '<span class="muted small">Le modèle répond d’après ce qui est dit dans l’épisode, en citant le moment (mm:ss).</span>'}</div>
    <div class="composer">
      <textarea id="pc-q" rows="2" placeholder="Votre question… (Entrée pour envoyer, Maj+Entrée = nouvelle ligne)" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();pcAsk()}"></textarea>
      <button class="btn primary" id="pc-send" onclick="pcAsk()">Envoyer</button>
    </div>`;
  const c = $('pc-chat'); c.scrollTop = c.scrollHeight;
}

async function pcAsk() {
  const question = $('pc-q').value.trim();
  if (!question || P.asking) return;
  const item = P.item, c = $('pc-chat');
  if (!item.questions.length) c.innerHTML = '';
  c.insertAdjacentHTML('beforeend', `<div class="msg user">${esc(question)}</div><div class="msg assistant muted" id="pc-wait">…</div>`);
  c.scrollTop = c.scrollHeight;
  $('pc-q').value = ''; P.asking = true; $('pc-send').disabled = true;
  try {
    let j = (await api('/api/podcasts/ask', {id: item.meta.id, question})).job;
    for (let fails = 0; j.state === 'running';) {
      await new Promise(r => setTimeout(r, 1000));
      try { j = await api('/api/jobs/' + j.id); fails = 0; }
      catch (e) { if (++fails > 30) throw e; continue; }
      if ($('pc-wait')) $('pc-wait').textContent = j.detail || '…';
    }
    if (j.state !== 'done') throw new Error(j.error || 'annulé');
    item.questions.push({q: question, a: j.result.answer});
    $('pc-wait').outerHTML = `<div class="msg assistant yt-a">${ytText(j.result.answer)}</div>`;
  } catch (e) {
    $('pc-wait').outerHTML = `<div class="msg assistant warn-item">${esc(e.message)}</div>`;
  }
  P.asking = false; $('pc-send').disabled = false;
  c.scrollTop = c.scrollHeight;
}

async function pcDelete(ids, ask = true) {
  if (!ids.length) { toast('Rien à supprimer'); return; }
  if (ask && !confirm(ids.length > 1 ? `Supprimer ${ids.length} résumés (et leurs questions) ?` : 'Supprimer ce résumé (et ses questions) ?')) return;
  try {
    const d = await api('/api/podcasts/delete', {ids});
    if (P.item && ids.includes(P.item.meta.id)) { P.item = null; LS.set('pc.last', ''); $('pc-result').classList.add('hidden'); }
    toast(`${d.deleted} résumé${d.deleted > 1 ? 's' : ''} supprimé${d.deleted > 1 ? 's' : ''}`);
  } catch (e) { toast(e.message); }
  pcRefresh();
}

function pcClean() {
  const days = +$('pc-clean').value, limit = Date.now() / 1000 - days * 86400;
  const ids = P.status.history.filter(x => !days || x.at < limit).map(x => x.id);
  const what = $('pc-clean').selectedOptions[0].text;
  if (!ids.length) { toast(`Aucun : ${what}`); return; }
  if (confirm(`Supprimer ${ids.length} résumé${ids.length > 1 ? 's' : ''} (${what}), avec leurs questions ?`)) pcDelete(ids, false);
}
