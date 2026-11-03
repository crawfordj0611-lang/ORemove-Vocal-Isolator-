// ORemove interface logic.
//
// Talks to Python through window.pywebview.api (see the Api class in app.py).
// Python never calls into the page. Instead the page waits for events
// (progress, stage, log, done, ...) on the app's local server (/events): one
// request that returns as soon as something happens, so idle costs nothing.

const $ = id => document.getElementById(id);
let api = null;
let state = null;            // settings, models, version (from api.get_state)
let selectedFiles = [];      // paths picked with Browse; shown as a summary in the input
let filesSummary = '';
let running = false;
let startedAt = 0, tickTimer = null;
let separating = false;      // true while the model is separating (not downloading or loading)
let rateStart = null;        // {t, v}: first progress sample of the current stage, for the time-left estimate
let sepStarted = 0;          // when the current separating stage began
let etaText = '';
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
  $('about').textContent = `ORemove ${state.version} - newmoneymadeit™`;
  renderSettings();
  $('ytdlpVersion').textContent = `yt-dlp ${state.ytdlp}`;
  if (state.welcome) showWelcome();
  const prof = state.engine_profile;
  if (prof && prof.results) {
    const best = prof.results.find(r => r.fp16 === prof.fp16 && r.compile === prof.compile);
    if (best) { $('speedStatus').textContent = `Using the fastest setting found: ${best.setting}`; $('speedRun').textContent = 'Run again'; }
  }
  if (!state.ffmpeg) showStartError('FFmpeg is missing, so audio can\'t be read or written. Reinstall ORemove.');
  listenForEvents();
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
  if (e.key === 'Escape') { closeSheets(); closePicker(); }
  if (e.key === ' ' && $('results').classList.contains('active') && document.activeElement.tagName !== 'INPUT') {
    e.preventDefault(); togglePlay(abKind);
  }
});

// ---------- settings ----------
function renderSettings() {
  const s = state.settings;
  const m = state.models.find(x => x.filename === s.model) || state.models[0];
  $('pickerName').textContent = m.name;
  $('picker').title = `${m.name} · ${m.best_for}`;
  setSeg('qualitySeg', s.quality, v => saveSettings({ quality: v }));
  setSeg('formatSeg', s.format, v => saveSettings({ format: v }));
  $('outDir').textContent = s.output_dir;
  $('outDir').title = s.output_dir;
  const sw = $('ytdlpSwitch');
  sw.classList.toggle('on', !!s.ytdlp_auto);
  sw.setAttribute('aria-checked', String(!!s.ytdlp_auto));
  const installed = state.models.filter(x => x.installed);
  $('modelsCount').textContent = `${installed.length} of ${state.models.length} installed`;
  $('modelsInstalled').innerHTML = installed.length
    ? '<span class="okdot"></span>' + installed.map(x => esc(x.name)).join(' · ')
    : 'Models install with your first song';
  renderModelList();
}
function renderModelList() {
  const s = state.settings;
  const row = x => `
    <div class="opt ${x.filename === s.model ? 'on' : ''}" data-model="${x.filename}">
      <svg class="ck" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-9"/></svg>
      <div><div class="n">${esc(x.name)}</div><div class="d">${esc(x.best_for)}</div></div>
      <div class="s">${x.bundled ? 'Built in' : esc(x.size)}${x.installed ? '<em>Installed</em>'
        : state.installing === x.filename ? '<i>Installing…</i>' : '<i>Not installed</i>'}</div>
    </div>`;
  const main = state.models.filter(x => !x.bundled), fast = state.models.filter(x => x.bundled);
  $('modelList').innerHTML = main.map(row).join('') + (fast.length ? '<div class="pop-sep"></div>' + fast.map(row).join('') : '');
  $('modelList').querySelectorAll('.opt').forEach(el => el.onclick = () => { closePicker(); saveSettings({ model: el.dataset.model }); });
}
function openPicker() {
  if (running) return;
  closeSheets();
  renderModelList();
  const pop = $('modelPop'), r = $('picker').getBoundingClientRect();
  pop.classList.remove('hidden'); $('popScrim').classList.remove('hidden'); $('picker').classList.add('open');
  const h = pop.offsetHeight, w = pop.offsetWidth;
  // Open upward when there's no room below (the usual case: the picker sits low in the window).
  const top = r.bottom + 8 + h < innerHeight ? r.bottom + 8 : Math.max(10, r.top - h - 8);
  pop.style.top = top + 'px';
  pop.style.left = Math.min(Math.max(10, r.left), innerWidth - w - 10) + 'px';
}
function closePicker() {
  $('modelPop').classList.add('hidden'); $('popScrim').classList.add('hidden'); $('picker').classList.remove('open');
}
$('picker').onclick = () => $('modelPop').classList.contains('hidden') ? openPicker() : closePicker();
$('popScrim').onclick = closePicker;
$('chooseModel').onclick = () => openPicker();
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
$('ytdlpCheck').onclick = () => api.check_ytdlp();
$('ytdlpSwitch').onclick = () => saveSettings({ ytdlp_auto: !state.settings.ytdlp_auto });
// ---------- speed test ----------
$('speedRun').onclick = async () => {
  if (running) return;
  const r = await api.speed_test();
  if (!r.ok) { $('speedStatus').textContent = r.error; return; }
  $('speedRun').disabled = true; $('speedRun').textContent = 'Testing…';
  $('speedStatus').textContent = 'Starting the speed test…';
};
function onSpeedTest(ev) {
  if (ev.status) $('speedStatus').textContent = ev.status;
  if (ev.done) {
    $('speedStatus').textContent = ev.summary;
    $('speedRun').disabled = false; $('speedRun').textContent = 'Run again';
  }
}
$('openLogs').onclick = () => api.open_logs();
function setYtdlp(ev) {
  $('ytdlpVersion').textContent = `yt-dlp ${ev.version}`;
  $('ytdlpStatus').textContent = ev.status;
  document.querySelector('#ytdlpStatus').previousElementSibling.classList.toggle('off', /off|couldn/i.test(ev.status));
}

// ---------- first run ----------
function showWelcome() {
  const rec = state.models[0];
  $('wModelName').textContent = rec.installed ? `${rec.name} is installed` : 'Install the recommended model now?';
  $('wModelInfo').textContent = rec.installed ? 'You\'re ready to go.' : `${rec.name} · ${rec.size} · otherwise it downloads with your first song`;
  $('wInstallBtn').classList.toggle('hidden', rec.installed);
  $('welcome').classList.remove('hidden');
}
$('wInstallBtn').onclick = async () => {
  const rec = state.models[0];
  const r = await api.install_model(rec.filename);
  if (!r.ok) { $('wModelInfo').textContent = r.error; return; }
  state.installing = rec.filename;
  $('wInstallBtn').disabled = true; $('wInstallBtn').textContent = 'Installing…';
  $('wBar').classList.remove('hidden');
  $('wModelInfo').textContent = 'Downloading… you can start using ORemove meanwhile.';
};
$('wDone').onclick = () => { $('welcome').classList.add('hidden'); api.welcome_done(); input.focus(); };
function onInstall(ev) {
  $('wBarFill').style.width = ev.percent + '%';
  $('wModelInfo').textContent = `Downloading… ${ev.percent}% of ${ev.mb} MB`;
}
function onInstalled(ev) {
  state.installing = null; state.models = ev.models; renderSettings();
  const m = state.models.find(x => x.filename === ev.model);
  $('wBarFill').style.width = '100%';
  $('wModelName').textContent = `${m ? m.name : 'Model'} is installed`;
  $('wModelInfo').textContent = 'Ready: your first song starts straight away.';
  $('wInstallBtn').classList.add('hidden');
  appendLog(`Installed ${m ? m.name : ev.model}.`);
}
function onInstallError(ev) {
  state.installing = null; renderModelList();
  $('wInstallBtn').disabled = false; $('wInstallBtn').textContent = 'Try again';
  $('wModelInfo').textContent = `Couldn't install: ${ev.message}`;
}

// ---------- new version ----------
let update = null;
function showUpdate(ev) {
  update = ev;
  $('updateText').textContent = `ORemove ${ev.version} is available.`;
  $('updateBanner').classList.remove('hidden');
}
$('updateGet').onclick = () => update && api.open_release(update.url);
$('updateDismiss').onclick = () => { $('updateBanner').classList.add('hidden'); if (update) api.dismiss_update(update.version); };

// ---------- choosing a source ----------
const input = $('source');
input.addEventListener('input', () => {
  if (selectedFiles.length && input.value !== filesSummary) selectedFiles = [];
  $('clearSource').classList.toggle('hidden', !input.value);
  hideStartError();
});
input.addEventListener('keydown', e => { if (e.key === 'Enter') start(); });
// Choosing a song: start loading the AI engine in the background (see Api.warm_up).
const warmUp = () => { if (api && !running) api.warm_up(); };
input.addEventListener('focus', warmUp);
$('clearSource').onclick = () => { selectedFiles = []; input.value = ''; $('clearSource').classList.add('hidden'); input.focus(); };
$('browse').onclick = async () => {
  warmUp();
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
document.addEventListener('dragenter', e => { e.preventDefault(); if (!running) { if (!dragDepth) warmUp(); dragDepth++; drop.classList.add('hover'); } });
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
  separating = false; rateStart = null; sepStarted = 0; etaText = '';
  clearInterval(tickTimer);
  tickTimer = setInterval(() => {
    $('elapsed').textContent = fmt((Date.now() - startedAt) / 1000) + ' elapsed' + (etaText ? ' · ' + etaText : '');
  }, 500);
  document.querySelectorAll('.picker, .seg, .switch').forEach(el => el.classList.add('disabled'));
  closePicker();
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
// Time left, from how fast progress is moving in the current stage.
function updateEta(v) {
  if (v === null || !separating) return;
  const now = Date.now();
  // The first chunks are slower (the GPU is warming up), so start measuring
  // a few seconds in, or the estimate starts out far too long.
  if (!sepStarted) { sepStarted = now; return; }
  if (!rateStart) { if (now - sepStarted >= 6000) rateStart = { t: now, v }; return; }
  const dv = v - rateStart.v, dt = (now - rateStart.t) / 1000;
  if (dv < 0.01 || dt < 3) return;  // wait for a steady rate
  const left = (0.97 - v) * dt / dv;
  etaText = left < 60 ? 'under a minute left' : `about ${Math.ceil(left / 60)} min left`;
}
function finishRun() {
  running = false;
  clearInterval(tickTimer);
  document.querySelectorAll('.picker, .seg, .switch').forEach(el => el.classList.remove('disabled'));
}

// ---------- events from Python ----------
async function listenForEvents() {
  const token = new URLSearchParams(location.search).get('t');
  for (;;) {
    try {
      const response = await fetch(`/events?t=${token}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(response.status);
      for (const ev of await response.json()) handle(ev);
    } catch {
      await new Promise(r => setTimeout(r, 1000));  // server busy or restarting: try again shortly
    }
  }
}
function handle(ev) {
  switch (ev.type) {
    case 'log': appendLog(ev.text); break;
    case 'device': setDevice(ev); break;
    case 'ytdlp': setYtdlp(ev); break;
    case 'update': showUpdate(ev); break;
    case 'install': onInstall(ev); break;
    case 'installed': onInstalled(ev); break;
    case 'install_error': onInstallError(ev); break;
    case 'models': state.models = ev.models; renderSettings(); break;
    case 'speedtest': onSpeedTest(ev); break;
    case 'dropped': if (!running && ev.paths.length) { selectedFiles = []; input.value = ''; start(ev.paths); } break;
    case 'title': $('trackTitle').textContent = ev.text; break;
    case 'stage': {
      // A new "Separating stems" step (or leaving it) restarts the time-left estimate.
      const now = ev.detail.includes('Separating stems');
      if (now || now !== separating) { rateStart = null; sepStarted = 0; etaText = ''; }
      separating = now;
      setStage(ev.name); $('stepText').textContent = ev.detail + (ev.detail.endsWith(')') ? '' : '…');
      break;
    }
    case 'progress': setProgress(ev.value); updateEta(ev.value); break;
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
  // Where the rest of the time went (downloads, starting the engine, ...).
  const t = info.timings, timing = $('timing');
  timing.classList.toggle('hidden', !t);
  if (t) {
    const parts = [['getting audio', t.audio], ['first-time model download', t.model_download], ['starting the AI engine', t.engine],
                   ['loading the model', t.model_load], ['separating', t.separate], ['saving', t.save]]
      .filter(([, v]) => v >= 1).map(([k, v]) => `${k} ${fmt(v)}`);
    timing.textContent = `Total ${fmt(info.total)}: ` + parts.join(' · ') + (info.gpu_memory_mb ? ` · GPU memory ${info.gpu_memory_mb} MB` : '');
  }
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
      ${state.platform === 'win' ? `<span class="drag" title="Drag this stem into your DAW, a folder or another app">${ICON_GRIP}Drag to DAW</span>` : ''}
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
  const handle = card.querySelector('.drag');
  if (handle) {
    // Hold and move: Windows takes over the drag, so the file can be dropped anywhere.
    handle.addEventListener('mousedown', down => {
      if (down.button !== 0) return;
      down.preventDefault();
      const move = e => {
        if (Math.abs(e.clientX - down.clientX) + Math.abs(e.clientY - down.clientY) < 4) return;
        done(); api.drag_out(stem.path);
      };
      const done = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', done); };
      addEventListener('mousemove', move); addEventListener('mouseup', done);
    });
  }
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
const ICON_GRIP = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>';
const ICON_PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
