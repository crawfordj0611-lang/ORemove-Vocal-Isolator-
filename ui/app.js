// ORemove interface logic.
//
// Talks to Python through window.pywebview.api (see the Api class in app.py).
// Python never calls into the page: the page polls api.poll() for events
// (progress, stage, log, done, ...) every 100 ms.

const $ = id => document.getElementById(id);
let api = null;
let state = null;            // settings, models, version (from api.get_state)
let selectedFiles = [];      // paths picked with Browse; shown as a summary in the input
let filesSummary = '';
let running = false;
let startedAt = 0, tickTimer = null;
let songs = [], songIndex = 0;
let abKind = 'Acapella';
const players = {};          // kind -> {audio, card}

// ---------- startup ----------
function whenApiReady(fn) {
  if (window.pywebview && window.pywebview.api) return fn();
  window.addEventListener('pywebviewready', fn, { once: true });
}

whenApiReady(async () => {
  api = window.pywebview.api;
  state = await api.get_state();
  $('about').textContent = `ORemove ${state.version} · AI Powered Vocal Isolator`;
  renderSettings();
  if (!state.ffmpeg) showStartError('FFmpeg is missing, so audio can\'t be read or written. Reinstall ORemove.');
  setInterval(pollEvents, 100);
  api.ready();
});

// ---------- screens & sheets ----------
function show(name) {
  for (const s of ['start', 'processing', 'results']) $(s).classList.toggle('active', s === name);
}
function openSheet(id) {
  closeSheets();
  $(id).classList.add('on'); $('scrim').classList.add('on');
  if (id === 'history') renderHistory();
}
function closeSheets() {
  document.querySelectorAll('.sheet').forEach(s => s.classList.remove('on'));
  $('scrim').classList.remove('on');
}
$('settingsBtn').onclick = () => openSheet('settings');
$('historyBtn').onclick = () => openSheet('history');
$('scrim').onclick = closeSheets;
document.querySelectorAll('[data-close]').forEach(b => b.onclick = closeSheets);
document.querySelectorAll('[data-open]').forEach(b => b.onclick = () => openSheet(b.dataset.open));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeSheets();
  if (e.key === ' ' && $('results').classList.contains('active') && document.activeElement.tagName !== 'INPUT') {
    e.preventDefault(); togglePlay(abKind);
  }
});

// ---------- settings ----------
function renderSettings() {
  const s = state.settings;
  $('modelList').innerHTML = state.models.map(m => `
    <div class="model ${m.filename === s.model ? 'on' : ''}" data-model="${m.filename}">
      <div class="radio"></div>
      <div><div class="n">${esc(m.name)}</div><div class="d">${esc(m.best_for)}</div></div>
      <div class="s">${esc(m.size)}</div>
    </div>`).join('');
  document.querySelectorAll('.model').forEach(el => el.onclick = () => saveSettings({ model: el.dataset.model }));
  setSeg('qualitySeg', s.quality, v => saveSettings({ quality: v }));
  setSeg('formatSeg', s.format, v => saveSettings({ format: v }));
  $('outDir').textContent = s.output_dir;
  const m = state.models.find(x => x.filename === s.model) || state.models[0];
  $('chipModel').textContent = m.name; $('chipBest').textContent = m.best_for;
  $('chipQuality').textContent = s.quality; $('chipFormat').textContent = s.format;
}
function setSeg(id, value, onPick) {
  $(id).querySelectorAll('span').forEach(sp => {
    sp.classList.toggle('on', sp.dataset.v === value);
    sp.onclick = () => onPick(sp.dataset.v);
  });
}
async function saveSettings(change) {
  if (running) return;
  state.settings = await api.save_settings(change);
  renderSettings();
}
$('changeDir').onclick = async () => {
  if (running) return;
  const dir = await api.choose_folder();
  if (dir) saveSettings({ output_dir: dir });
};
$('openOut').onclick = () => api.open_output();

// ---------- choosing a source ----------
const input = $('source');
input.addEventListener('input', () => {
  if (selectedFiles.length && input.value !== filesSummary) selectedFiles = [];
  $('clearSource').classList.toggle('hidden', !input.value);
  hideStartError();
});
input.addEventListener('keydown', e => { if (e.key === 'Enter') start(); });
$('clearSource').onclick = () => { selectedFiles = []; input.value = ''; $('clearSource').classList.add('hidden'); input.focus(); };
$('browse').onclick = async () => {
  const paths = await api.browse_files();
  if (!paths || !paths.length) return;
  selectedFiles = paths;
  const names = paths.map(p => p.split(/[\\/]/).pop());
  filesSummary = paths.length === 1 ? paths[0] : `${paths.length} files: ${names.slice(0, 2).join(', ')}${paths.length > 2 ? '…' : ''}`;
  input.value = filesSummary;
  $('clearSource').classList.remove('hidden');
  hideStartError();
};
$('go').onclick = () => start();

// Drag and drop: the drop itself is handled in Python (which can see full file
// paths); the page only shows the hover effect.
const drop = $('drop');
let dragDepth = 0;
document.addEventListener('dragenter', e => { e.preventDefault(); if (!running) { dragDepth++; drop.classList.add('hover'); } });
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; drop.classList.remove('hover'); } });
document.addEventListener('drop', e => { e.preventDefault(); dragDepth = 0; drop.classList.remove('hover'); });

function currentSources() {
  if (selectedFiles.length && input.value === filesSummary) return selectedFiles;
  const text = input.value.trim().replace(/^["']|["']$/g, '');
  return text ? [text] : [];
}

async function start(sources) {
  if (running) return;
  sources = sources || currentSources();
  if (!sources.length) { showStartError('Paste a YouTube link, or browse for audio files.'); input.focus(); return; }
  const result = await api.start(sources);
  if (!result.ok) { showStartError(result.error); return; }
  running = true;
  closeSheets();
  resetProcessing(sources);
  show('processing');
}

// ---------- processing ----------
function resetProcessing(sources) {
  setProgress(0);
  $('trackTitle').textContent = sources.length > 1 ? `${sources.length} songs` : (sources[0].split(/[\\/]/).pop() || 'Preparing…');
  $('stepText').textContent = 'Starting…';
  setStage(null);
  $('cancel').disabled = false; $('cancel').textContent = 'Stop';
  startedAt = Date.now();
  clearInterval(tickTimer);
  tickTimer = setInterval(() => { $('elapsed').textContent = fmt((Date.now() - startedAt) / 1000) + ' elapsed'; }, 500);
  document.querySelectorAll('.model, .seg').forEach(el => el.classList.add('disabled'));
}
function setProgress(v) {
  const wrap = $('ringWrap');
  if (v === null) { wrap.classList.add('busy'); $('ring').style.strokeDashoffset = 552.9 * 0.72; return; }
  wrap.classList.remove('busy');
  $('ring').style.strokeDashoffset = 552.9 * (1 - v);
  $('pct').textContent = Math.floor(v * 100);
}
function setStage(name) {
  const order = ['download', 'separate', 'save'];
  const at = name === 'done' ? 3 : order.indexOf(name);
  document.querySelectorAll('.step').forEach((el, i) => el.className = 'step' + (i < at ? ' done' : i === at ? ' now' : ''));
}
$('cancel').onclick = () => {
  api.cancel();
  $('cancel').disabled = true;
  $('cancel').textContent = 'Stopping after the current step…';
};
function finishRun() {
  running = false;
  clearInterval(tickTimer);
  document.querySelectorAll('.model, .seg').forEach(el => el.classList.remove('disabled'));
}

// ---------- events from Python ----------
async function pollEvents() {
  let events;
  try { events = await api.poll(); } catch { return; }
  for (const ev of events) handle(ev);
}
function handle(ev) {
  switch (ev.type) {
    case 'log': appendLog(ev.text); break;
    case 'device': setDevice(ev); break;
    case 'dropped': if (!running && ev.paths.length) { selectedFiles = []; input.value = ''; start(ev.paths); } break;
    case 'title': $('trackTitle').textContent = ev.text; break;
    case 'stage': setStage(ev.name); $('stepText').textContent = ev.detail + '…'; break;
    case 'progress': setProgress(ev.value); break;
    case 'done':
      finishRun();
      setStage('done'); setProgress(1);
      showResults(ev.songs, ev.failures, ev);
      break;
    case 'cancelled':
      finishRun(); show('start');
      showStartError('Stopped. Nothing was saved for the unfinished song.', true);
      break;
    case 'error':
      finishRun(); show('start');
      showStartError(ev.message);
      break;
  }
}
function setDevice(ev) {
  const dot = document.querySelector('#device .dot');
  dot.className = 'dot' + ({ gpu: '', cpu: ' cpu', nonvidia: ' cpu', error: ' error' }[ev.kind] ?? '');
  $('deviceName').textContent = ev.name;
  $('device').title = ev.detail || 'What the AI engine runs on';
  if (ev.kind === 'nonvidia') showStartError(ev.detail, true);
}
function appendLog(text) {
  const log = $('log');
  const stamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  log.textContent += `[${stamp}] ${text}\n`;
  if (log.textContent.length > 60000) log.textContent = log.textContent.slice(-50000);
  log.scrollTop = log.scrollHeight;
}

// ---------- results ----------
function showResults(list, failures, info) {
  songs = list; songIndex = 0;
  $('doneText').textContent = info.seconds != null ? `Separated in ${fmtDuration(info.seconds)}${info.device ? ' on ' + info.device : ''}` : 'From history';
  $('failures').classList.toggle('hidden', !(failures && failures.length));
  if (failures && failures.length) $('failures').textContent = `${failures.length} file${failures.length > 1 ? 's' : ''} couldn't be processed: ` + failures.join(' · ');
  const tabs = $('songTabs');
  tabs.classList.toggle('hidden', songs.length < 2);
  tabs.innerHTML = songs.map((s, i) => `<button data-i="${i}" title="${esc(s.title)}">${esc(s.title)}</button>`).join('');
  tabs.querySelectorAll('button').forEach(b => b.onclick = () => selectSong(+b.dataset.i));
  selectSong(0);
  show('results');
  input.value = ''; selectedFiles = []; $('clearSource').classList.add('hidden');
}
function selectSong(i) {
  stopAll();
  songIndex = i;
  const song = songs[i];
  $('songTabs').querySelectorAll('button').forEach((b, j) => b.classList.toggle('on', j === i));
  $('songTitle').textContent = song.title;
  $('songTitle').title = song.title;
  $('songMeta').textContent = song.meta;
  const cards = $('cards');
  cards.innerHTML = '';
  for (const k of Object.keys(players)) delete players[k];
  for (const stem of song.stems) cards.appendChild(makeCard(stem));
  const kinds = song.stems.map(s => s.kind);
  abKind = kinds.includes(abKind) ? abKind : kinds[0];
  $('ab').querySelectorAll('span').forEach(sp => {
    sp.classList.toggle('hidden', !kinds.includes(sp.dataset.kind));
    sp.classList.toggle('on', sp.dataset.kind === abKind);
  });
}
function makeCard(stem) {
  const card = document.createElement('div');
  card.className = 'card ' + stem.kind;
  const title = { Acapella: 'Vocals', Instrumental: 'Instrumental' }[stem.kind] || stem.kind;
  const sub = { Acapella: 'Acapella', Instrumental: 'Beat' }[stem.kind] || 'Stem';
  card.innerHTML = `
    <div class="card-top">
      <button class="play" title="Play">${ICON_PLAY}</button>
      <div><div class="card-title">${esc(title)}</div><div class="card-sub">${sub} · ${esc(stem.size)}</div></div>
    </div>
    <div class="wave"><svg viewBox="0 0 400 72" preserveAspectRatio="none"><g class="bars"></g><g class="prog"></g></svg></div>
    <div class="time"><span class="cur">0:00</span><span>${fmt(stem.duration || 0)}</span></div>
    <div class="card-actions">
      <button class="btn btn-ghost" data-act="reveal">Show in folder</button>
      <button class="btn btn-ghost" data-act="open">Open in player</button>
    </div>`;
  const bars = barsSvg(stem.peaks);
  card.querySelector('.bars').innerHTML = bars;
  card.querySelector('.prog').innerHTML = bars;
  const audio = new Audio(stem.url);
  audio.preload = 'metadata';
  const prog = card.querySelector('.prog'), cur = card.querySelector('.cur'), btn = card.querySelector('.play');
  const duration = () => audio.duration || stem.duration || 1;
  audio.addEventListener('timeupdate', () => {
    prog.style.clipPath = `inset(0 ${100 - (audio.currentTime / duration()) * 100}% 0 0)`;
    cur.textContent = fmt(audio.currentTime);
  });
  audio.addEventListener('play', () => btn.innerHTML = ICON_PAUSE);
  audio.addEventListener('pause', () => btn.innerHTML = ICON_PLAY);
  audio.addEventListener('ended', () => { btn.innerHTML = ICON_PLAY; });
  btn.onclick = () => togglePlay(stem.kind);
  card.querySelector('.wave').onclick = e => {
    const r = e.currentTarget.getBoundingClientRect();
    const t = ((e.clientX - r.left) / r.width) * duration();
    for (const p of Object.values(players)) p.audio.currentTime = t;  // keep stems in sync for A/B
  };
  card.querySelector('[data-act="reveal"]').onclick = () => api.reveal(stem.path);
  card.querySelector('[data-act="open"]').onclick = () => { stopAll(); api.open_path(stem.path); };
  players[stem.kind] = { audio, card };
  return card;
}
function togglePlay(kind) {
  const p = players[kind];
  if (!p) return;
  if (!p.audio.paused) { p.audio.pause(); return; }
  for (const [k, other] of Object.entries(players)) if (k !== kind) other.audio.pause();
  abKind = kind;
  $('ab').querySelectorAll('span').forEach(sp => sp.classList.toggle('on', sp.dataset.kind === kind));
  p.audio.play();
}
function stopAll() { for (const p of Object.values(players)) p.audio.pause(); }
// A/B: switch to the other stem at the same position, keeping playback going.
$('ab').querySelectorAll('span').forEach(sp => sp.onclick = () => {
  const target = players[sp.dataset.kind];
  if (!target) return;
  const playing = Object.values(players).find(p => !p.audio.paused);
  if (playing && playing !== target) { target.audio.currentTime = playing.audio.currentTime; playing.audio.pause(); target.audio.play(); }
  abKind = sp.dataset.kind;
  $('ab').querySelectorAll('span').forEach(x => x.classList.toggle('on', x === sp));
});
$('newSong').onclick = () => { stopAll(); hideStartError(); show('start'); input.focus(); };

// ---------- history ----------
async function renderHistory() {
  const items = await api.history();
  $('historyList').innerHTML = items.length ? items.map(h => `
    <div class="hist" data-id="${h.id}"><div class="t">${esc(h.title)}</div><div class="m">${esc(h.when)} · ${esc(h.model)}</div></div>`).join('')
    : '<div class="empty">Songs you separate will appear here.</div>';
  document.querySelectorAll('.hist').forEach(el => el.onclick = async () => {
    if (running) return;
    const song = await api.history_item(el.dataset.id);
    closeSheets();
    if (!song) { showStartError('Those files have been moved or deleted.', true); show('start'); return; }
    showResults([song], [], { seconds: null });
  });
}

// ---------- helpers ----------
function showStartError(text, soft) {
  const b = $('startError');
  b.textContent = text;
  b.className = 'banner ' + (soft ? 'warn' : 'error');
}
function hideStartError() { $('startError').className = 'banner error hidden'; }
function barsSvg(peaks) {
  const n = 100;
  const src = peaks && peaks.length ? peaks : Array(n).fill(0.08);
  let out = '';
  for (let i = 0; i < n; i++) {
    const v = src[Math.floor(i * src.length / n)];
    const h = Math.max(3, 68 * Math.min(1, v));
    out += `<rect x="${i * 4 + 0.5}" y="${36 - h / 2}" width="2.6" height="${h}" rx="1.3"/>`;
  }
  return out;
}
function fmt(sec) { sec = Math.max(0, Math.floor(sec)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; }
function fmtDuration(sec) { return sec < 60 ? `${Math.round(sec)} s` : `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`; }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
const ICON_PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
