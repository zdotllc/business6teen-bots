'use strict';

/* ================= Mission Control — voice conversation ================= */

const $ = (s) => document.querySelector(s);

let agents = [];
let currentAgent = null;
let voiceList = [];
let ttsVoice = null;
let rate = 1.7;
let handsfree = false;
let listening = false;
let speaking = false;
let groupMode = false;
let groupSelected = new Set();
let recognition = null;
// Per-agent voice overrides, persisted locally: { agentName: voiceName }.
const agentVoices = JSON.parse(localStorage.getItem('bb_agent_voices') || '{}');

function voiceFor(name) {
  const wanted = agentVoices[name];
  if (wanted) {
    const v = voiceList.find((x) => x.name === wanted);
    if (v) return v;
  }
  return ttsVoice;
}

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

const ROLE_COLORS = {
  ceo: '#f5b50a', cto: '#2dd4bf', developer: '#a78bfa', marketer: '#f472b6',
  sales: '#4ade80', finance: '#60a5fa', hr: '#fb923c', support: '#22d3ee',
  project_manager: '#818cf8', owner: '#f5b50a'
};

const ROLE_FACE = {
  ceo: '👑', cto: '🛰️', developer: '👨‍💻', marketer: '📣', sales: '🤝',
  finance: '💰', hr: '🧑‍🤝‍🧑', support: '🎧', project_manager: '📋', owner: '🦅'
};

/* ---------------- status helpers ---------------- */

function statusLine(msg) {
  $('#statusLine').textContent = msg;
}

function setVoiceState(state) {
  const el = $('#voiceState');
  el.className = 'voice-state' + (state ? ' ' + state : '');
  const labels = {
    connecting: 'connecting…', ready: 'ready', listening: 'listening',
    speaking: 'speaking', 'no-voice': 'no voice', unreachable: 'API unreachable',
  };
  el.textContent = labels[state] || 'connecting…';
}

/** fetch + JSON with a hard timeout so the UI never hangs on 'connecting…'. */
async function fetchJson(url, ms = 8000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

/* ---------------- deck + robot faces ---------------- */

function renderDeck() {
  const deck = $('#agentDeck');
  if (!deck) return;
  deck.innerHTML = '';

  if (!agents.length) {
    deck.innerHTML = '<div class="deck-empty">No agents hired yet — hire your team from the main dashboard, then come back.</div>';
    return;
  }

  for (const a of agents) {
    const accent = ROLE_COLORS[a.role] || '#94a3b8';
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'robot idle';
    card.dataset.agent = a.name;
    card.style.setProperty('--accent', accent);
    card.innerHTML = `
      <div class="robot-head">
        <span class="robot-ear left"></span>
        <span class="robot-ear right"></span>
        <span class="robot-eyes"><span class="eye"></span><span class="eye"></span></span>
        <span class="robot-mouth"></span>
      </div>
      <span class="robot-name">${esc(a.name)}</span>
      <span class="robot-role">${esc(a.title || a.role)}</span>
      <select class="agent-voice" data-voice-for="${esc(a.name)}" title="Voice for ${esc(a.name)}"><option value="">(default)</option></select>`;
    card.addEventListener('click', () => {
      if (groupMode) toggleGroupMember(a.name);
      else selectAgent(a.name);
    });
    deck.appendChild(card);
  }
  if (agents.length) selectAgent(agents[0].name);
}

function selectAgent(name) {
  currentAgent = name;
  document.querySelectorAll('.robot').forEach((r) =>
    r.classList.toggle('selected', r.dataset.agent === name));
  setFace(name, 'idle');
  statusLine(`Talking to ${name}.`);
}

function setFace(name, state) {
  document.querySelectorAll('.robot').forEach((r) => {
    r.classList.remove('idle', 'thinking', 'speaking');
    r.classList.add(r.dataset.agent === name ? state : 'idle');
  });
}

function esc(v) {
  return String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

/* ---------------- chat bubbles ---------------- */

function addBubble(who, text, agentName) {
  const log = $('#chatLog');
  const welcome = log.querySelector('.chat-welcome');
  if (welcome) welcome.remove();

  const b = document.createElement('div');
  b.className = 'bubble ' + who;

  const face = document.createElement('span');
  face.className = 'mini-face';
  const whoName = who === 'agent' ? (agentName || currentAgent) : null;
  const role = whoName ? (agents.find((a) => a.name === whoName) || {}).role : null;
  face.textContent = who === 'user' ? '🧑' : (ROLE_FACE[role] || '🤖');

  const txt = document.createElement('div');
  txt.className = 'bubble-text';
  if (who === 'agent' && agentName) {
    const cap = document.createElement('div');
    cap.className = 'bubble-who';
    cap.textContent = agentName;
    txt.appendChild(cap);
  }
  const body = document.createElement('div');
  body.textContent = text;
  txt.appendChild(body);

  b.appendChild(face);
  b.appendChild(txt);
  log.appendChild(b);
  log.scrollTop = log.scrollHeight;
}

/* ---------------- chat ---------------- */

async function sendMessage(text) {
  text = String(text || '').trim();
  if (!text) return;
  addBubble('user', text);
  $('#textInput').value = '';
  if (groupMode) {
    await sendGroup(text);
    return;
  }
  if (!currentAgent) return;
  setFace(currentAgent, 'thinking');
  statusLine(`${currentAgent} is thinking…`);
  setVoiceState('ready');

  try {
    const res = await fetch('/api/chat/' + encodeURIComponent(currentAgent), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) throw new Error(data.error || 'Request failed (' + res.status + ')');
    addBubble('agent', data.reply);
    speak(data.reply);
  } catch (err) {
    setFace(currentAgent, 'idle');
    setVoiceState('ready');
    statusLine('Error: ' + err.message);
    addBubble('error', '⚠ ' + err.message);
  }
}

/* ---------------- text-to-speech (agents talk) ---------------- */

/** Strip markdown/formatting so TTS reads clean sentences (no **, #, pipes…). */
function cleanSpeech(text) {
  return String(text)
    .replace(/```[\s\S]*?```/g, ' ')                 // code blocks
    .replace(/`([^`]*)`/g, '$1')                     // inline code
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, ' ')         // images
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')         // links -> link text
    .replace(/\*\*([^*]+)\*\*/g, '$1')               // **bold** -> bold
    .replace(/__([^_]+)__/g, '$1')                   // __bold__ -> bold
    .replace(/\*([^*\s][^*]*)\*/g, '$1')             // *italic* -> italic
    .replace(/_([^_\s][^_]*)_/g, '$1')               // _italic_ -> italic
    .replace(/~~([^~]+)~~/g, '$1')                   // strikethrough
    .replace(/^#{1,6}\s*/gm, '')                     // headings
    .replace(/^\s*([-*+])\s+/gm, '')                 // list bullets
    .replace(/^\s*>\s?/gm, '')                       // blockquotes
    .replace(/\|/g, ', ')                            // table pipes -> commas
    .replace(/[`*_~]/g, '')                          // any leftover markers
    .replace(/[ \t]{2,}/g, ' ')                      // collapse spaces
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function chunkText(text) {
  // Chrome cuts utterances around 15s/200 chars — split into safe chunks.
  const sentences = String(text).match(/[^.!?…]+[.!?…]*\s*/g) || [String(text)];
  const chunks = [];
  let cur = '';
  for (const s of sentences) {
    if ((cur + s).length > 180 && cur) { chunks.push(cur.trim()); cur = s; }
    else cur += s;
  }
  if (cur.trim()) chunks.push(cur.trim());
  return chunks.length ? chunks : [String(text)];
}

function speak(text) {
  const synth = window.speechSynthesis;
  if (!synth) { setFace(currentAgent, 'idle'); return; }
  // Make sure the mic is OFF while the agent talks — otherwise it hears
  // itself and responds to its own speech (feedback loop).
  stopListening();
  synth.cancel();
  speaking = true;
  setVoiceState('speaking');
  const parts = chunkText(cleanSpeech(text));
  let i = 0;
  const next = () => {
    if (i >= parts.length) {
      speaking = false;
      setFace(currentAgent, 'idle');
      setVoiceState('ready');
      statusLine('Ready.');
      // Brief pause so the tail of the agent's voice isn't caught by the mic.
      if (handsfree) setTimeout(() => startListening(), 400);
      return;
    }
    const u = new SpeechSynthesisUtterance(parts[i++]);
    const v = voiceFor(currentAgent);
    if (v) u.voice = v;
    u.rate = rate;
    u.onend = next;
    u.onerror = next;
    synth.speak(u);
  };
  next();
}

/** Like speak(), but resolves when the agent finishes talking (for group chat). */
function speakAsync(text, agentName) {
  return new Promise((resolve) => {
    const synth = window.speechSynthesis;
    if (!synth) { setFace(agentName, 'idle'); resolve(); return; }
    stopListening();
    synth.cancel();
    speaking = true;
    setVoiceState('speaking');
    setFace(agentName, 'speaking');
    const parts = chunkText(cleanSpeech(text));
    let i = 0;
    const next = () => {
      if (i >= parts.length) {
        speaking = false;
        setFace(agentName, 'idle');
        resolve();
        return;
      }
      const u = new SpeechSynthesisUtterance(parts[i++]);
      const v = voiceFor(agentName);
      if (v) u.voice = v;
      u.rate = rate;
      u.onend = next;
      u.onerror = next;
      synth.speak(u);
    };
    next();
  });
}

/* ---------------- group chat ---------------- */

async function sendGroup(text) {
  const names = [...groupSelected];
  if (!names.length) { statusLine('Select at least one agent for group chat.'); return; }
  names.forEach((n) => setFace(n, 'thinking'));
  statusLine('Broadcasting to ' + names.length + ' agents…');
  setVoiceState('ready');
  try {
    const res = await fetch('/api/chat/group', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, agents: names }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) throw new Error(data.error || 'Group chat failed (' + res.status + ')');
    const replies = Array.isArray(data.replies) ? data.replies : [];
    for (const r of replies) {
      if (r.error) { addBubble('error', '⚠ ' + r.agent + ': ' + r.error); continue; }
      addBubble('agent', r.reply, r.agent);
      await speakAsync(r.reply, r.agent);
    }
    setVoiceState('ready');
    statusLine('Ready.');
    if (handsfree) setTimeout(() => startListening(), 400);
  } catch (err) {
    setVoiceState('ready');
    statusLine('Error: ' + err.message);
    addBubble('error', '⚠ ' + err.message);
  } finally {
    names.forEach((n) => setFace(n, 'idle'));
  }
}

function setMode(group) {
  groupMode = group;
  $('#modeDirect').classList.toggle('active', !group);
  $('#modeGroup').classList.toggle('active', group);
  $('#groupAll').style.display = group ? '' : 'none';
  if (group) {
    groupSelected = new Set(agents.map((a) => a.name));
    refreshGroupVisuals();
  } else {
    document.querySelectorAll('.robot').forEach((r) => r.classList.remove('group-active', 'group-excluded'));
    statusLine(currentAgent ? 'Talking to ' + currentAgent + '.' : 'Ready.');
  }
}

/** Toggle one agent in/out of the group chat. */
function toggleGroupMember(name) {
  if (groupSelected.has(name)) groupSelected.delete(name);
  else groupSelected.add(name);
  refreshGroupVisuals();
}

function refreshGroupVisuals() {
  document.querySelectorAll('.robot').forEach((r) => {
    const on = groupSelected.has(r.dataset.agent);
    r.classList.toggle('group-active', groupMode && on);
    r.classList.toggle('group-excluded', groupMode && !on);
  });
  if (groupMode) {
    statusLine(`Group chat — ${groupSelected.size}/${agents.length} selected. Click robots to include/exclude.`);
  }
}

function stopAll() {
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  stopListening();
  speaking = false;
  setFace(currentAgent, 'idle');
  setVoiceState('ready');
  statusLine('Stopped.');
}

/* ---------------- speech recognition (you talk) ---------------- */

function setupRecognition() {
  recognition = new SR();
  recognition.lang = 'en-US';
  recognition.continuous = true;
  recognition.interimResults = true;

  recognition.onresult = (e) => {
    let interim = '';
    let final = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      if (e.results[i].isFinal) final += t; else interim += t;
    }
    const box = $('#textInput');
    if (final) {
      box.value = '';
      sendMessage(final);
    } else if (interim) {
      box.value = interim;
    }
  };

  recognition.onend = () => {
    listening = false;
    $('#micBtn').classList.remove('active');
    if (handsfree && !speaking) startListening();
    else { setVoiceState('ready'); statusLine('Mic off.'); }
  };

  recognition.onerror = (e) => {
    listening = false;
    $('#micBtn').classList.remove('active');
    setVoiceState('ready');
    statusLine('Mic error: ' + (e.error || 'unknown') + ' (allow microphone access)');
  };
}

function startListening() {
  if (!recognition || listening || speaking) return;
  listening = true;
  $('#micBtn').classList.add('active');
  setVoiceState('listening');
  statusLine('🎤 listening…');
  try { recognition.start(); } catch { /* already started */ }
}

function stopListening() {
  listening = false;
  $('#micBtn').classList.remove('active');
  if (recognition) { try { recognition.stop(); } catch { /* noop */ } }
}

/* ---------------- voices ---------------- */

function setupVoices() {
  const synth = window.speechSynthesis;
  if (!synth) { setVoiceState('no-voice'); statusLine('Speech synthesis unavailable in this browser.'); return; }

  const populate = () => {
    const voices = synth.getVoices();
    const en = voices.filter((v) => /^en/i.test(v.lang));
    voiceList = en.length ? en : voices;
    const sel = $('#voiceSelect');
    sel.innerHTML = '';
    voiceList.forEach((v, i) => {
      const opt = document.createElement('option');
      opt.value = String(i);
      opt.textContent = v.name + ' (' + v.lang + ')';
      sel.appendChild(opt);
    });
    const pref = voiceList.findIndex((v) => /neural|natural|google us english|premium/i.test(v.name));
    sel.selectedIndex = pref >= 0 ? pref : 0;
    ttsVoice = voiceList[sel.selectedIndex] || null;

    // Per-agent voice pickers on the robot cards.
    document.querySelectorAll('.agent-voice').forEach((vsel) => {
      const agentName = vsel.dataset.voiceFor || '';
      const current = agentVoices[agentName] || '';
      vsel.innerHTML = '<option value="">(default)</option>' +
        voiceList.map((v) => `<option value="${esc(v.name)}" ${v.name === current ? 'selected' : ''}>${esc(v.name)}</option>`).join('');
    });

    setVoiceState(voiceList.length ? 'ready' : 'no-voice');
  };

  populate();
  if (typeof synth.onvoiceschanged !== 'undefined') synth.onvoiceschanged = populate;
}

/** Show a visible error card in the agent deck with a retry button. */
function renderApiError(message) {
  setVoiceState('unreachable');
  const deck = $('#agentDeck');
  deck.innerHTML = '';
  const card = document.createElement('div');
  card.className = 'deck-empty';
  card.innerHTML = `
    <p>⚠️ Could not reach the dashboard API.</p>
    <p class="hint" style="font-size:12px;margin-top:4px;">${esc(message || '')}</p>
    <p class="hint" style="font-size:12px;margin-top:4px;">Open <b>http://127.0.0.1:8420/advanced</b> (use the IP, not 'localhost') and make sure the dashboard is running: <b>venv/bin/agent-company-ai dashboard</b></p>
    <p style="margin-top:10px;"><button type="button" class="btn btn-primary" data-retry>↻ Retry</button></p>`;
  deck.appendChild(card);
  card.querySelector('[data-retry]').addEventListener('click', () => init());
}

/* ---------------- wiring ---------------- */

function bindUI() {
  $('#sendBtn').addEventListener('click', () => sendMessage($('#textInput').value));
  $('#textInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendMessage($('#textInput').value); });
  $('#micBtn').addEventListener('click', () => (listening ? stopListening() : startListening()));
  $('#stopBtn').addEventListener('click', stopAll);
  $('#handsfree').addEventListener('change', (e) => { handsfree = e.target.checked; });
  $('#rateRange').addEventListener('input', (e) => { rate = Number(e.target.value); });
  $('#voiceSelect').addEventListener('change', (e) => {
    const i = Number(e.target.value);
    ttsVoice = voiceList[i] || null;
  });
  $('#modeDirect').addEventListener('click', () => setMode(false));
  $('#modeGroup').addEventListener('click', () => setMode(true));
  $('#groupAll').addEventListener('click', () => {
    groupSelected = new Set(agents.map((a) => a.name));
    refreshGroupVisuals();
  });
  // Per-agent voice changes are persisted locally.
  $('#agentDeck').addEventListener('change', (e) => {
    if (e.target.classList.contains('agent-voice')) {
      agentVoices[e.target.dataset.voiceFor] = e.target.value;
      localStorage.setItem('bb_agent_voices', JSON.stringify(agentVoices));
    }
  });
}

async function init() {
  try {
    const [status, list] = await Promise.all([
      fetchJson('/api/status'),
      fetchJson('/api/agents'),
    ]);
    if (status.name) $('#companyName').textContent = status.name;
    agents = Array.isArray(list) ? list : [];
  } catch (e) {
    console.error('[mission-control] API fetch failed:', e);
    statusLine('Could not reach the dashboard API: ' + e.message);
    agents = [];
    renderApiError(e && e.name === 'AbortError' ? 'The request timed out.' : e.message);
  }
  renderDeck();
  setupVoices();
  bindUI();
  if (SR) setupRecognition();
  else statusLine('Speech recognition unavailable in this browser — type instead (Chrome/Edge recommended for voice).');
}

init();
