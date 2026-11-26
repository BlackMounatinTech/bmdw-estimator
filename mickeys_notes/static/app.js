const APP_BASE = document.querySelector('meta[name=app-base]')?.content || '';
const $ = selector => document.querySelector(selector);
const priorities = ['High', 'Medium', 'Low'];
const uid = () => crypto.randomUUID();
const dateKey = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const localDate = () => dateKey(new Date());
const dateObject = day => new Date(day + 'T12:00:00');
const dayOffset = document.querySelector('meta[name=day-view]').content === 'tomorrow' ? 1 : 0;
function selectedDate() { const d = new Date(); d.setDate(d.getDate() + dayOffset); return dateKey(d); }
let currentDay = '';
let state = {lists: [], note: '', habits: {}}, revision = 0;
let dirty = false, saving = null, failed = false, timer, navigation = 0;
let recognition = null, listening = false, dictationDay = null;
let stoppingRecognition = null, lastToday = localDate(), rollingDay = false;
let movingTask = false;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children) node.append(child);
  return node;
}
function clearError() { $('#error').hidden = true; $('#error').replaceChildren(); }
function showError(message, retry) {
  const error = $('#error');
  error.hidden = false;
  error.replaceChildren(document.createTextNode(message));
  if (retry) error.append(el('button', {textContent: 'Retry', onclick: retry}));
}
function changed() {
  dirty = true;
  $('#save-status').textContent = 'Unsaved';
  clearTimeout(timer);
  timer = setTimeout(save, 450);
}
async function save() {
  clearTimeout(timer);
  if (saving) { await saving; if (dirty && !failed) return save(); return; }
  if (!dirty || !currentDay) return;
  dirty = false; failed = false;
  const day = currentDay;
  const body = JSON.stringify({content: state, revision});
  $('#save-status').textContent = 'Saving…';
  saving = (async () => {
    try {
      const response = await fetch(APP_BASE + '/api/days/' + day, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': $('meta[name=csrf]').content},
        body,
      });
      const data = await response.json();
      if (!response.ok) throw Error(data.error || 'Could not save this day.');
      revision = data.revision;
      $('#save-status').textContent = dirty ? 'Unsaved' : 'Saved';
      clearError();
    } catch (error) {
      dirty = true; failed = true;
      $('#save-status').textContent = 'Not saved';
      showError(error.message + ' Your changes are still here.', save);
    } finally { saving = null; }
  })();
  await saving;
  if (dirty && !failed) return save();
}

async function loadDay(day) {
  if (movingTask) return;
  const requestId = ++navigation;
  $('#day-page').inert = true;
  if (recognition && listening) await stopDictation();
  if (requestId !== navigation) return;
  dictationDay = null;
  if (dirty || saving) {
    await save();
    if (requestId !== navigation) return;
    if (dirty) { $('#day-page').inert = false; return; }
  }
  try {
    const response = await fetch(APP_BASE + '/api/days/' + day);
    if (!response.ok) throw Error('Could not open this day. Refresh and sign in if needed.');
    const data = await response.json();
    if (requestId !== navigation) return;
    currentDay = day;
    state = data.content; state.habits ||= {}; revision = data.revision;
    $('#day-title').textContent = dateObject(day).toLocaleDateString('en-CA', {weekday: 'long', month: 'long', day: 'numeric'});
    $('#day-year').textContent = day.slice(0, 4);
    $('#capture').value = state.draft || '';
    $('#save-status').textContent = 'Saved';
    clearError(); renderTasks();
  } catch (error) {
    if (requestId === navigation) showError(error.message, () => loadDay(day));
  } finally {
    if (requestId === navigation) $('#day-page').inert = !currentDay;
  }
}

function taskEntries() {
  const order = new Map((state.taskOrder || []).map((id, index) => [id, index]));
  return state.lists.flatMap(list => list.tasks.map(task => ({list, task})))
    .sort((a, b) => (order.get(a.task.id) ?? order.size) - (order.get(b.task.id) ?? order.size));
}
async function moveDailyTask(task, target) {
  if (movingTask) return;
  movingTask = true; $('#day-page').inert = true;
  try {
    if (recognition && listening) await stopDictation();
    await save();
    if (dirty) return;
    const response = await fetch(APP_BASE + '/api/tasks/move', {
      method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': $('meta[name=csrf]').content},
      body: JSON.stringify({task_id: task.id, revision, source: {list: 'days', date: currentDay}, target}),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Could not move this task.');
    state = data.content; state.habits ||= {}; revision = data.revision;
    renderTasks();
    $('#voice-status').textContent = 'Task moved.';
    $('#save-status').textContent = 'Saved'; clearError();
  } catch (error) { showError(error.message + ' Refresh before trying again.'); }
  finally { movingTask = false; $('#day-page').inert = false; }
}
function renderTasks() {
  const root = $('#tasks'); root.replaceChildren();
  const entries = taskEntries();
  for (const [index, {list, task}] of entries.entries()) {
    const number = el('span', {className: 'task-number', textContent: index + 1 + '.'});
    const checkbox = el('input', {type: 'checkbox', checked: task.done});
    checkbox.setAttribute('aria-label', 'Complete ' + task.text);
    checkbox.onchange = () => { task.done = checkbox.checked; if (task.done) task.completed_on = localDate(); else delete task.completed_on; changed(); renderTasks(); };
    const text = el('span', {className: 'task-text', textContent: task.text});
    const remove = el('button', {type: 'button', className: 'remove', textContent: '×', onclick: () => {
      if (!window.confirm('Confirm delete?\n\n' + task.text)) return;
      list.tasks = list.tasks.filter(t => t.id !== task.id);
      state.taskOrder = (state.taskOrder || []).filter(id => id !== task.id);
      changed(); renderTasks();
    }});
    remove.setAttribute('aria-label', 'Remove ' + task.text);
    const row = el('div', {className: 'task' + (task.done ? ' done' : '')}, el('label', {className: 'task-toggle'}, checkbox, number, text), remove);
    row.dataset.taskId = task.id;
    taskMoveActions(row, task, 'days', target => moveDailyTask(task, target), currentDay);
    row.prepend(taskDragHandle(row, root, ids => { state.taskOrder = ids; changed(); renderTasks(); }));
    root.append(row);
  }
  if (!entries.length) root.append(el('p', {className: 'empty', textContent: 'A blank day. Add whatever you need to do.'}));
  const complete = entries.filter(({task}) => task.done).length;
  $('#progress').textContent = entries.length ? `${complete}/${entries.length} done` : '';
  $('#priority-hint').hidden = true;
}
// Dictation always adds tasks; task text is read-only after adding.
function dumpItems(text) {
  return text.split(/\r?\n|[,;!?]|\.(?=\s|$)|\b(?:new\s+line|and\s+then|then)\b/gi).map(piece => piece.replace(/^\s*[-*•]\s*/, '').trim()).filter(Boolean);
}
function addTasks() {
  const items = dumpItems($('#capture').value);
  if (!items.length) { $('#capture').focus(); return; }
  let list = state.lists.find(l => l.id === 'daily-' + currentDay);
  if (!list) { list = {id: 'daily-' + currentDay, title: 'Daily list', priority: 'Medium', tasks: []}; state.lists.push(list); }
  const before = JSON.stringify(state.lists), day = currentDay;
  for (const item of items) list.tasks.push({id: uid(), text: item[0].toUpperCase() + item.slice(1), done: false, priority: 'Medium'});
  const message = `Added ${items.length} item${items.length === 1 ? '' : 's'}. `;
  const draft = $('#capture').value;
  const undo = el('button', {type: 'button', className: 'undo', textContent: 'Undo', onclick: () => {
    if (currentDay !== day) return;
    state.lists = JSON.parse(before); $('#capture').value = state.draft = draft;
    $('#voice-status').textContent = 'Undone.'; changed(); renderTasks();
  }});
  $('#voice-status').replaceChildren(message, undo);
  $('#capture').value = ''; state.draft = ''; changed(); renderTasks();
}
$('#add-tasks').onclick = addTasks;
$('#capture').oninput = event => { state.draft = event.target.value; changed(); };
$('#capture').onkeydown = event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); addTasks(); } };
const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
// One short session per phrase, restarted after each pause, so every pause becomes its own line.
let keepListening = false;
async function stopDictation() {
  keepListening = false;
  if (!recognition || !listening) return;
  if (!stoppingRecognition) {
    stoppingRecognition = new Promise(resolve => {
      const finish = () => { clearTimeout(timeout); recognition.removeEventListener('end', finish); resolve(); };
      const timeout = setTimeout(finish, 1200);
      recognition.addEventListener('end', finish, {once: true});
      try { recognition.stop(); } catch (_) { finish(); }
    });
  }
  await stoppingRecognition; stoppingRecognition = null;
}
if (Speech) {
  recognition = new Speech(); recognition.lang = 'en-CA'; recognition.continuous = false; recognition.interimResults = false;
  recognition.onstart = () => { listening = true; $('#dictate').textContent = '■ Stop'; $('#dictate').classList.add('listening'); $('#voice-status').textContent = 'Listening. Pause between items.'; };
  recognition.onresult = event => {
    if (dictationDay !== currentDay) return;
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (!event.results[i].isFinal) continue;
      const field = $('#capture');
      field.value += (field.value && !field.value.endsWith('\n') ? '\n' : '') + event.results[i][0].transcript.replace(/\bnew\s+line\b[,.]?/gi, '\n');
      state.draft = field.value; changed();
    }
  };
  recognition.onend = () => {
    if (keepListening && dictationDay === currentDay && !$('#voice-status').dataset.error) {
      try { recognition.start(); return; } catch (_) { /* Fall through and stop. */ }
    }
    keepListening = false; listening = false; $('#dictate').textContent = '🎙 Dictate'; $('#dictate').classList.remove('listening'); if (!$('#voice-status').dataset.error) $('#voice-status').textContent = 'Review your list, then tap Add to list.'; };
  recognition.onerror = event => { if (event.error === 'no-speech' || event.error === 'aborted') return; $('#voice-status').dataset.error = 'true'; $('#voice-status').textContent = event.error === 'not-allowed' ? 'Allow microphone access, or use your keyboard’s microphone.' : 'Dictation stopped. You can still type or use your keyboard’s microphone.'; };
  $('#dictate').onclick = () => {
    if (listening) { keepListening = false; recognition.stop(); return; }
    delete $('#voice-status').dataset.error;
    dictationDay = currentDay; keepListening = true;
    try { recognition.start(); } catch (_) { $('#voice-status').textContent = 'Try your keyboard’s microphone, or type your list.'; }
  };
} else {
  $('#dictate').hidden = true;
  $('#voice-status').textContent = 'Use your keyboard’s microphone to dictate.';
}
for (const link of document.querySelectorAll('header a')) link.onclick = async event => {
  event.preventDefault();
  if (recognition && listening) await stopDictation();
  await save(); if (!dirty) location.href = link.href;
};
window.addEventListener('beforeunload', event => { if (dirty || saving) { event.preventDefault(); event.returnValue = ''; } });
async function checkDayChange() {
  const today = localDate();
  if (today === lastToday || rollingDay || movingTask) return;
  rollingDay = true;
  try {
    const target = selectedDate();
    await loadDay(target);
    if (currentDay === target) lastToday = today;
  } finally { rollingDay = false; }
}
document.addEventListener('visibilitychange', () => { if (document.hidden && dirty) save(); else if (!document.hidden) checkDayChange(); });
window.addEventListener('focus', checkDayChange);
setInterval(checkDayChange, 60000);
// Switch at the Mac/phone's local midnight; the interval also catches sleep/wake.
function scheduleMidnight() {
  const now = new Date(), midnight = new Date(now);
  midnight.setHours(24, 0, 0, 0);
  setTimeout(async () => { await checkDayChange(); scheduleMidnight(); }, midnight - now + 20);
}
scheduleMidnight();
async function init() {
  try {
    await loadDay(selectedDate());
  } catch (error) { showError(error.message, init); }
}
init();
