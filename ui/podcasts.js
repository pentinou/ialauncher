/* IA Launcher — onglet Podcasts : épisodes transcrits par Whisper puis résumés comme les vidéos
   de l'onglet YouTube (même modèle, même langue, mêmes questions libres). Deux galeries : les
   abonnements (importables de Podcast Addict ; nouveaux épisodes signalés chaque jour, résumés au
   clic, ou tout seuls en « résumé auto ») et les épisodes isolés (résumés sans abonnement).
   Réutilise app.js, gen.js et youtube.js ($, api, esc, toast, watchJob, LS, ytText, ytDetailHtml, ytDuree, ytMinSec, ytMsgs). */
'use strict';

const P = {status: null, item: null, asking: false, open: null, eps: {}, pick: null};   // eps : derniers épisodes lus, par podcast ; pick : choix d'un épisode isolé

const pcDate = (t) => t ? new Date(t * 1000).toLocaleDateString('fr-FR') : '';

async function loadPodcasts() {
  const root = $('mode-podcasts');
  if (!root.dataset.ready) {
    root.innerHTML = `<section class="card"><h1>Résumer un podcast</h1>
      <p class="lead">Collez le <b>flux RSS</b> d'un podcast, son lien <b>Apple Podcasts</b> ou l'adresse de sa page web, puis <b>abonnez-vous</b> (il rejoint vos abonnements, dont les nouveaux épisodes sont signalés chaque jour) ou résumez <b>un seul épisode</b> sans vous abonner. Chaque épisode est transcrit par Whisper puis résumé en une phrase, comme une vidéo de l'onglet YouTube.</p>
      <div id="pc-tools"></div>
      <div class="row-inline">
        <input id="pc-url" placeholder="https://…/rss  ou  https://podcasts.apple.com/…" size="50" onkeydown="if(event.key==='Enter')pcAdd()">
        <button class="btn primary" id="pc-go" onclick="pcAdd()">S’abonner</button>
        <button class="btn" id="pc-one" onclick="pcOne()" title="lien d'un épisode (Apple Podcasts, page de l'épisode) : il est résumé ; lien d'un podcast : choisissez l'épisode">Un seul épisode</button>
      </div>
      <div class="row-inline small">
        <label title="Podcast Addict : Paramètres → Sauvegarde/Restauration → Sauvegarder (fichier .backup), ou Exporter en OPML">
          <input type="file" id="pc-file" accept=".backup,.opml,.xml,.db,.zip" class="hidden" onchange="pcImport(this)">
          <span class="btn small" onclick="$('pc-file').click()">Importer de Podcast Addict…</span></label>
        <span class="muted">sauvegarde <span class="mono">.backup</span> ou export OPML</span></div>
      <div id="pc-llm" class="small"></div>
      <div id="pc-pick"></div>
      <div id="pc-job"></div></section>
    <section class="card hidden" id="pc-result"></section>
    <section class="card hidden" id="pc-feed"></section>
    <section class="card"><h1>Mes abonnements <span class="muted small" id="pc-nsubs"></span></h1>
      <div class="row-inline small"><span class="muted">Trier :</span>
        <select id="pc-sort" onchange="LS.set('pc.sort', this.value); pcFeeds()">
          <option value="new">nouveaux épisodes d’abord</option><option value="last">épisode le plus récent</option>
          <option value="count">plus d’épisodes publiés</option><option value="added">derniers abonnements</option>
          <option value="title">nom (A → Z)</option></select></div>
      <div id="pc-feeds" class="yt-gal pc-gal"></div></section>
    <section class="card"><h1>Épisodes isolés <span class="muted small" id="pc-count"></span></h1>
      <p class="muted small">Résumés d'épisodes dont vous ne suivez pas le podcast.</p>
      <div id="pc-history" class="yt-gal"></div></section>`;
    root.dataset.ready = '1';
    $('pc-sort').value = LS.get('pc.sort', 'new');
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
  // suivi des résumés en cours (lancés ici, par un autre appareil ou par le résumé auto)
  Object.values(s.running).forEach(pcWatch);
  pcFeeds();
  const h = s.singles;
  $('pc-count').textContent = h.length ? `(${h.length})` : '';
  $('pc-history').innerHTML = h.length ? h.map(x => `<div class="yt-card ${P.item && P.item.meta.id === x.id ? 'on' : ''}" data-id="${esc(x.id)}" onclick="pcOpen('${esc(x.id)}', true)" title="${esc(x.phrase)}">
      <div class="yt-thumb"><img loading="lazy" alt="" src="/api/podcasts/thumb?id=${esc(x.id)}" onerror="this.remove()">
        <span class="yt-dur">${ytMinSec(x.duration)}</span>
        <button class="yt-del" title="supprimer ce résumé" onclick="event.stopPropagation(); pcDelete(['${esc(x.id)}'])">✕</button></div>
      <div class="yt-ct"><b>${esc(x.title)}</b><span class="muted small">${esc(x.channel || '')} · ${pcDate(x.date || x.at)}</span></div>
    </div>`).join('') : '<span class="muted">aucun pour l’instant</span>';
}

// galerie des abonnements : pochette et nouveaux épisodes, dans l'ordre choisi (mémorisé sur cet appareil)
const PC_SORT = {
  new: (a, b) => (b.new > 0) - (a.new > 0) || b.last - a.last,
  last: (a, b) => b.last - a.last,
  count: (a, b) => b.count - a.count || b.last - a.last,
  added: (a, b) => b.added - a.added,
  title: (a, b) => a.title.localeCompare(b.title, 'fr', {sensitivity: 'base'}),
};
function pcFeeds() {
  const f = [...P.status.feeds].sort(PC_SORT[$('pc-sort').value] || PC_SORT.new);
  $('pc-nsubs').textContent = f.length ? `(${f.length})` : '';
  $('pc-feeds').innerHTML = f.length ? f.map(x => `<div class="yt-card ${P.open === x.id ? 'on' : ''}" onclick="pcToggle('${x.id}')" title="${esc(x.title)}">
      <div class="yt-thumb"><img loading="lazy" alt="" src="/api/podcasts/thumb?id=${x.id}" onerror="this.remove()">
        ${x.new ? `<span class="pc-new">${x.new} nouveau${x.new > 1 ? 'x' : ''}</span>` : ''}
        ${x.follow ? '<span class="yt-dur" title="nouveaux épisodes résumés tout seuls">auto</span>' : ''}</div>
      <div class="yt-ct"><b>${esc(x.title)}</b><span class="muted small">${x.error ? '⚠ flux injoignable' : x.last ? 'dernier : ' + pcDate(x.last) : esc(x.author || '')}${x.count ? ` · ${x.count} ép.` : ''}</span></div>
    </div>`).join('') : '<span class="muted">aucun pour l’instant : abonnez-vous ci-dessus ou importez vos abonnements de Podcast Addict</span>';
  pcFeedPanel();
}

// podcast ouvert : ses réglages et ses épisodes, au-dessus des galeries (qui restent en bas de page)
function pcFeedPanel() {
  const box = $('pc-feed'), x = P.status.feeds.find(f => f.id === P.open);
  box.classList.toggle('hidden', !x);
  if (!x) { P.open = null; return; }
  box.innerHTML = `<div class="pc-head">
      <img alt="" src="/api/podcasts/thumb?id=${x.id}" onerror="this.style.visibility='hidden'">
      <div class="pc-name"><b>${esc(x.title)}</b><span class="muted small">${esc(x.author || '')}${x.checked ? ` · vérifié le ${new Date(x.checked * 1000).toLocaleString('fr-FR', {dateStyle: 'short', timeStyle: 'short'})}` : ''}</span>
        ${x.error ? `<span class="small warn-item">flux injoignable : ${esc(x.error)}</span>` : ''}</div>
      <label class="small" title="résumer tout seul chaque nouvel épisode"><input type="checkbox" ${x.follow ? 'checked' : ''} onchange="pcFollow('${x.id}', this.checked)"> résumé auto</label>
      <button class="link" onclick="pcRemove('${x.id}')" title="se désabonner (les résumés restent, parmi les épisodes isolés)">se désabonner</button>
    </div><div class="pc-eps" id="pc-eps-${x.id}"><span class="muted small">…</span></div>`;
  pcEpisodes(x.id);
}

function pcToggle(fid) {
  P.open = P.open === fid ? null : fid;
  if (P.open) delete P.eps[fid];   // ouvert à nouveau : relire le flux (nouvel épisode ?)
  pcFeeds();
  if (P.open) $('pc-feed').scrollIntoView({behavior: 'smooth', block: 'start'});
}

async function pcEpisodes(fid) {
  const box = $('pc-eps-' + fid); if (!box) return;
  if (!P.eps[fid]) {
    box.innerHTML = '<span class="muted small">lecture du flux…</span>';
    try { P.eps[fid] = (await api('/api/podcasts/episodes?feed=' + fid)).episodes; } catch (e) { box.innerHTML = `<div class="warn-item">${esc(e.message)}</div>`; return; }
    const f = P.status.feeds.find(x => x.id === fid);
    if (f && f.new) { f.new = 0; pcFeeds(); return; }   // ouvert : ses nouveautés sont vues
  }
  box.innerHTML = pcEpList(P.eps[fid], `pcRun('${fid}', '§')`);
}

// liste d'épisodes : un clic ouvre le résumé, ou le lance s'il n'existe pas
function pcEpList(eps, run) {
  const going = P.status.running;
  return eps.map(e => `<div class="pc-ep${e.new ? ' new' : ''}"><div><b>${esc(e.title)}</b> <span class="muted small">${e.new ? '<span class="pc-new">nouveau</span> ' : ''}${pcDate(e.date)} · ${ytDuree(e.duration)}</span></div>
      ${e.done ? `<button class="btn small" onclick="pcOpen('${e.id}', true)">voir le résumé</button>`
        : going[e.id] ? '<span class="muted small">résumé en cours…</span>'
        : `<button class="btn small primary" onclick="${run.replace('§', e.id)}">Résumer</button>`}</div>`).join('') || '<span class="muted small">aucun épisode avec un fichier audio</span>';
}

async function pcAdd() {
  const url = $('pc-url').value.trim();
  if (!url) return;
  $('pc-go').disabled = true;
  try {
    const d = await api('/api/podcasts/add', {url, follow: false});
    $('pc-url').value = '';
    toast(`Abonné à ${d.feed.title}`, 4000);
    P.open = d.feed.id; P.eps[d.feed.id] = d.episodes;
    await pcRefresh();
    $('pc-feed').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (e) { toast(e.message, 8000); }
  $('pc-go').disabled = false;
}

// un seul épisode, sans abonnement : résumé tout de suite si le lien le désigne, sinon choix dans le flux
async function pcOne() {
  const url = $('pc-url').value.trim();
  if (!url) return;
  $('pc-one').disabled = true;
  try {
    const d = await api('/api/podcasts/lookup', {url});
    $('pc-url').value = '';
    if (d.episode) { P.pick = null; $('pc-pick').innerHTML = ''; pcRun('', d.episode, false, d.feed.url, (d.episodes.find(e => e.id === d.episode) || {}).title); }
    else { P.pick = d; pcPick(); }
  } catch (e) { toast(e.message, 8000); }
  $('pc-one').disabled = false;
}

function pcPick() {
  const d = P.pick;
  $('pc-pick').innerHTML = d ? `<div class="pc-feed"><div class="pc-head"><div class="pc-name"><b>Quel épisode de ${esc(d.feed.title)} ?</b>
      <span class="muted small">résumé sans abonnement : il rejoint les épisodes isolés</span></div>
      <button class="link" onclick="P.pick = null; pcPick()">fermer</button></div>
    <div class="pc-eps">${pcEpList(d.episodes, `pcRun('', '§', false, P.pick.feed.url)`)}</div></div>` : '';
}

async function pcImport(input) {
  const file = input.files[0]; input.value = '';
  if (!file) return;
  try {
    const r = await fetch('/api/podcasts/import', {method: 'POST', body: file});
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || r.statusText);
    toast(d.added ? `${d.added} abonnement${d.added > 1 ? 's' : ''} importé${d.added > 1 ? 's' : ''} (sur ${d.found}) : lecture de leurs flux…`
      : `Les ${d.found} abonnements de ce fichier sont déjà là`, 6000);
    if (d.job) {
      $('pc-job').insertAdjacentHTML('afterbegin', `<div id="pc-imp-${d.job.id}"></div>`);
      watchJob(d.job.id, 'pc-imp-' + d.job.id, (j) => { if (j.state === 'done') $('pc-imp-' + j.id).remove(); pcRefresh(); });
    }
    pcRefresh();
  } catch (e) { toast('Import : ' + e.message, 10000); }
}

async function pcFollow(fid, follow) {
  try {
    await api('/api/podcasts/follow', {feed: fid, follow});
    toast(follow ? 'Résumé auto : les épisodes publiés à partir de maintenant seront résumés tout seuls' : 'Résumé auto désactivé');
  } catch (e) { toast(e.message); }
  pcRefresh();
}

async function pcRemove(fid) {
  if (!confirm('Se désabonner de ce podcast ? Ses résumés déjà faits restent, parmi les épisodes isolés.')) return;
  try { await api('/api/podcasts/remove', {feed: fid}); } catch (e) { toast(e.message); }
  if (P.open === fid) P.open = null;
  pcRefresh();
}

async function pcInstall() {
  try { watchJob((await api('/api/youtube/install-whisper', {})).job.id, 'pc-install-job', pcRefresh); }
  catch (e) { toast(e.message); }
}

// fid : abonnement ; url : flux d'un épisode isolé (ni l'un ni l'autre : « refaire le résumé »)
async function pcRun(fid, eid, again, url, title) {
  try {
    const ep = [...(P.eps[fid] || []), ...(P.pick ? P.pick.episodes : [])].find(x => x.id === eid);   // titre : pour la file d'attente
    const d = await api('/api/podcasts/run', {feed: fid, url: url || '', id: eid, again: !!again, title: title || (ep ? ep.title : '')});
    if (d.item) { pcShow(d.item); $('pc-result').scrollIntoView({behavior: 'smooth', block: 'start'}); return; }
    P.status.running[eid] = d.job.id;
    if (P.open) pcEpisodes(P.open);
    pcPick();
    pcWatch(d.job.id);
  } catch (e) { toast(e.message, 8000); }
}

// un bloc de suivi par résumé (lancé ici, par un autre appareil ou par le résumé auto)
function pcWatch(id) {
  const box = 'pc-job-' + id;
  if ($(box)) return;
  $('pc-job').insertAdjacentHTML('beforeend', `<div id="${box}"></div>`);
  watchJob(id, box, (j) => {
    if (j.state === 'done') {
      $(box).remove();
      const id = j.result.meta.id;   // listes d'épisodes déjà lues : celui-ci est résumé
      [...Object.values(P.eps), P.pick ? P.pick.episodes : []].flat().forEach(e => { if (e.id === id) e.done = true; });
      if (!P.asking) pcShow(j.result);
    }
    pcRefresh().then(pcPick);
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
    Object.values(P.eps).flat().forEach(e => { if (ids.includes(e.id)) e.done = false; });
    toast(`${d.deleted} résumé${d.deleted > 1 ? 's' : ''} supprimé${d.deleted > 1 ? 's' : ''}`);
  } catch (e) { toast(e.message); }
  pcRefresh();
}
