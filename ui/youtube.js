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
      <p class="lead">Collez l'adresse d'une vidéo : le launcher lit ses sous-titres (ceux de l'auteur, sinon ceux générés par YouTube dans la langue d'origine) et le modèle de texte lancé en tire <b>l'essentiel en une phrase</b>, un point par sujet si la vidéo en aborde plusieurs. Cliquez une phrase pour l'explication détaillée ; posez vos propres questions en dessous.</p>
      <div id="yt-tools"></div>
      <div class="row-inline">
        <input id="yt-url" placeholder="https://www.youtube.com/watch?v=…" size="50" onkeydown="if(event.key==='Enter')ytRun()">
        <button class="btn primary" id="yt-go" onclick="ytRun()">Résumer</button>
      </div>
      <div id="yt-llm" class="small"></div>
      <div id="yt-job"></div></section>
      <section class="card hidden" id="yt-result"></section>
      <section class="card"><h1>Déjà résumées</h1><div id="yt-history"></div></section>`;
    root.dataset.ready = '1';
  }
  await ytRefresh();
  const last = LS.get('yt.last', '');
  if (last && !Y.item) ytOpen(last);
}

async function ytRefresh() {
  try { Y.status = await api('/api/youtube'); } catch (e) { toast(e.message); return; }
  const t = Y.status.tools;
  $('yt-tools').innerHTML = t.ytdlp && t.deno ? '' : `<div class="row-inline"><span>Il faut d'abord <b>yt-dlp</b> (lit YouTube) et <b>Deno</b> (moteur JavaScript que YouTube impose) : ~100 Mo, dans le dossier des moteurs.</span>
      <button class="btn" onclick="ytInstall()">Installer</button></div><div id="yt-install-job"></div>`;
  $('yt-llm').innerHTML = Y.status.llm ? `<span class="muted">Modèle : ${esc(Y.status.llm.name)}</span>`
    : `<span class="warn-item">Aucun modèle de texte lancé : lancez-en un dans l'onglet <a href="#" onclick="showMode('text');return false">💬 Texte</a> (un modèle de 20 à 30 milliards de paramètres et un contexte de 16k suffisent pour 30 min de vidéo).</span>`;
  const h = Y.status.history;
  $('yt-history').innerHTML = h.length ? h.map(x => `<div class="yt-hist" onclick="ytOpen('${esc(x.id)}')">
      <div><b>${esc(x.title)}</b> <span class="muted small">${esc(x.channel || '')} · ${ytDuree(x.duration)}</span></div>
      <div class="small">${esc(x.phrase)}</div></div>`).join('') : '<span class="muted">aucune pour l’instant</span>';
}

async function ytInstall() {
  try {
    const d = await api('/api/youtube/install', {});
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
    watchJob(d.job.id, 'yt-job', (j) => {
      $('yt-go').disabled = false;
      if (j.state === 'done') { $('yt-job').innerHTML = ''; ytShow(j.result); ytRefresh(); }
    });
  } catch (e) { toast(e.message, 8000); $('yt-go').disabled = false; }
}

async function ytOpen(id) {
  try { ytShow(await api('/api/youtube/item?id=' + encodeURIComponent(id))); }
  catch (e) { LS.set('yt.last', ''); }
}

function ytShow(item) {
  Y.item = item;
  LS.set('yt.last', item.meta.id);
  const m = item.meta, s = item.summary;
  const subs = m.subs ? `sous-titres ${m.subs.kind} (${esc(m.subs.lang)})` : '';
  const box = $('yt-result');
  box.classList.remove('hidden');
  box.innerHTML = `<h1><a href="${esc(m.webpage_url)}" target="_blank" rel="noopener">${esc(m.title)}</a></h1>
    <p class="muted small">${esc(m.channel || '')} · ${ytDuree(m.duration)} · ${subs} · ${fmtN(item.words)} mots lus en ${item.seconds} s
      <button class="link" onclick="ytRun(true)" title="relit les sous-titres et redemande un résumé au modèle lancé">refaire le résumé</button></p>
    ${s.points.map(p => `<details class="yt-point"><summary>${esc(p.phrase)}</summary><p>${esc(p.detail)}</p></details>`).join('')}
    <h2>Une question sur la vidéo ?</h2>
    <div id="yt-chat" class="chat">${item.questions.map(ytMsgs).join('') || '<span class="muted small">Le modèle répond d’après ce qui est dit dans la vidéo, en citant le moment (mm:ss). La première question est la plus longue : il relit la transcription.</span>'}</div>
    <div class="composer">
      <textarea id="yt-q" rows="2" placeholder="Votre question… (Entrée pour envoyer, Maj+Entrée = nouvelle ligne)" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();ytAsk()}"></textarea>
      <button class="btn primary" id="yt-send" onclick="ytAsk()">Envoyer</button>
    </div>`;
  const c = $('yt-chat'); c.scrollTop = c.scrollHeight;
}

const ytMsgs = (q) => `<div class="msg user">${esc(q.q)}</div><div class="msg assistant">${esc(q.a)}</div>`;

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
    item.questions.push({q: question, a: d.answer});
    $('yt-wait').outerHTML = `<div class="msg assistant">${esc(d.answer)}</div>`;
  } catch (e) {
    $('yt-wait').outerHTML = `<div class="msg assistant warn-item">${esc(e.message)}</div>`;
  }
  Y.asking = false; $('yt-send').disabled = false;
  c.scrollTop = c.scrollHeight;
}
