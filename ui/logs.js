/* IA Launcher — onglet Journaux : launcher.log (qui a demandé quoi, refus, erreurs, tâches) et
   la sortie des serveurs (sd-server, llama-server, ace-step), suivis en direct.
   Réutilise les utilitaires d'app.js et gen.js ($, api, esc, LS). */
'use strict';

const L = {file: LS.get('logs.file', 'launcher.log'), size: -1, timer: null};

async function loadLogs() {
  const root = $('mode-logs');
  if (!root.dataset.ready) {
    root.innerHTML = `<section class="card"><h1>Journaux</h1>
      <p class="lead">Ce qui se passe dans le launcher, en direct. <b>launcher.log</b> : chaque action demandée, par quelle adresse et quel appareil, les refus, les erreurs et les tâches (début, attente de leur tour, fin). Les autres : la sortie des serveurs de génération et de texte, un bloc par lancement.</p>
      <div class="row-inline"><span id="logs-files"></span></div>
      <div class="row-inline small" style="margin-top:8px">
        <input id="logs-filter" placeholder="filtrer (ex. erreur, Android, tâche)" size="30" oninput="L.size = -1; pollLogs()">
        <label><input type="checkbox" id="logs-live" checked onchange="pollLogs()"> en direct</label>
        <span id="logs-where" class="muted"></span></div>
      <pre id="logs-view" class="logview"></pre></section>`;
    root.dataset.ready = '1';
  }
  await listLogs();
  L.size = -1;
  pollLogs();
}

async function listLogs() {
  let d; try { d = await api('/api/logs'); } catch (e) { $('logs-files').innerHTML = esc(e.message); return; }
  if (!d.files.some(f => f.name === L.file) && d.files.length) L.file = d.files.some(f => f.name === 'launcher.log') ? 'launcher.log' : d.files[0].name;
  $('logs-files').innerHTML = d.files.map(f => `<button class="chip ${f.name === L.file ? 'on' : ''}" onclick="pickLog(${esc(JSON.stringify(f.name))})" title="${Math.ceil(f.size / 1024)} ko · ${new Date(f.mtime * 1000).toLocaleString()}">${esc(f.name)}</button>`).join(' ') || '<span class="muted">aucun journal pour l’instant</span>';
  $('logs-where').textContent = `${d.dir}${d.rotated ? ' · roulement par logrotate' : ' · taille plafonnée par le launcher'}`;
}

function pickLog(name) {
  L.file = name; LS.set('logs.file', name); L.size = -1;
  listLogs(); pollLogs();
}

async function pollLogs() {
  clearTimeout(L.timer);
  if (G.mode !== 'logs') return;           // onglet quitté : on arrête de suivre
  const view = $('logs-view');
  try {
    const d = await api('/api/logs/' + encodeURIComponent(L.file) + '?lines=1500');
    if (d.size !== L.size) {
      L.size = d.size;
      const atEnd = view.scrollHeight - view.scrollTop - view.clientHeight < 30;
      const f = $('logs-filter').value.trim().toLowerCase();
      const lines = f ? d.lines.filter(x => x.toLowerCase().includes(f)) : d.lines;
      view.innerHTML = lines.map(x => {
        const low = x.toLowerCase();
        const c = /\berr|erreur|failed|traceback|exception|refus| → [45]\d\d/.test(low) ? 'le'
          : /\bwarn|\[w\]|attente/.test(low) ? 'lw' : x.startsWith('=====') ? 'ls' : '';
        return c ? `<span class="${c}">${esc(x)}</span>` : esc(x);
      }).join('\n');
      if (atEnd || L.first !== L.file) view.scrollTop = view.scrollHeight;
      L.first = L.file;
    }
  } catch (e) { view.textContent = e.message; }
  if ($('logs-live').checked) L.timer = setTimeout(pollLogs, 2000);
}
