const APP_BASE = document.querySelector('meta[name=app-base]')?.content || '';
const $ = selector => document.querySelector(selector);
const priorities = ['High', 'Medium', 'Low'];
const uid = () => crypto.randomUUID();
const dateKey = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const localDate = () => dateKey(new Date());
const dateObject = day => new Date(day + 'T12:00:00');
let currentDay = '', monthView = localDate().slice(0, 7);
let state = {lists: [], note: '', habits: {}}, revision = 0;
let routines = {morning: [], night: []};
let dirty = false, saving = null, failed = false, timer, navigation = 0;
let recognition = null, listening = false, dictationDay = null;
let stoppingRecognition = null, lastToday = localDate(), rollingDay = false;
const savedDays = new Set();

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
      savedDays.add(day);
      $('#save-status').textContent = dirty ? 'Unsaved' : 'Saved';
      clearError();
      renderCalendar();
    } catch (error) {
      dirty = true; failed = true;
      $('#save-status').textContent = 'Not saved';
      showError(error.message + ' Your changes are still here.', save);
    } finally { saving = null; }
  })();
  await saving;
  if (dirty && !failed) return save();
}

function renderCalendar() {
  const first = dateObject(monthView + '-01');
  $('#month-title').textContent = first.toLocaleDateString('en-CA', {month: 'long', year: 'numeric'});
  const offset = (first.getDay() + 6) % 7;
  const lastDay = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  const cells = Math.ceil((offset + lastDay) / 7) * 7;
  const grid = $('#calendar'); grid.replaceChildren();
  for (let i = 0; i < cells; i++) {
    const d = new Date(first.getFullYear(), first.getMonth(), i - offset + 1, 12);
    const day = dateKey(d);
    const classes = [d.getMonth() !== first.getMonth() ? 'outside' : '', day === currentDay ? 'selected' : '', day === localDate() ? 'is-today' : '', savedDays.has(day) ? 'has-entry' : ''].filter(Boolean).join(' ');
    const button = el('button', {type: 'button', textContent: d.getDate(), className: classes, onclick: () => loadDay(day)});
    button.setAttribute('aria-label', d.toLocaleDateString('en-CA', {weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'}));
    button.setAttribute('aria-pressed', String(day === currentDay));
    if (day === localDate()) button.setAttribute('aria-current', 'date');
    grid.append(button);
  }
}
async function loadMonthEntries() {
  const month = monthView;
  try {
    const response = await fetch(APP_BASE + '/api/calendar/' + month);
    if (!response.ok) return;
    const data = await response.json();
    for (const day of data.days) savedDays.add(day);
    if (month === monthView) renderCalendar();
  } catch (_) { /* The calendar still works if saved-day markers cannot load. */ }
}
async function loadDay(day) {
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
    currentDay = day; monthView = day.slice(0, 7);
    state = data.content; state.habits ||= {}; revision = data.revision;
    $('#day-title').textContent = dateObject(day).toLocaleDateString('en-CA', {weekday: 'long', month: 'long', day: 'numeric'});
    $('#day-year').textContent = day.slice(0, 4);
    $('#capture').value = state.draft || '';
    $('#save-status').textContent = 'Saved';
    clearError(); renderRoutines(); renderTasks(); renderCalendar();
    loadMonthEntries();
  } catch (error) {
    if (requestId === navigation) showError(error.message, () => loadDay(day));
  } finally {
    if (requestId === navigation) $('#day-page').inert = !currentDay;
  }
}

function renderRoutines() {
  for (const [key, title] of [['morning', 'Morning'], ['night', 'Evening']]) {
    const root = $('#' + key + '-routine'); root.replaceChildren();
    const card = el('section', {className: 'routine'}, el('h2', {textContent: title}));
    for (const habit of routines[key]) {
      const checkbox = el('input', {type: 'checkbox', checked: !!state.habits[habit.id]});
      const row = el('label', {className: checkbox.checked ? 'done' : ''}, checkbox, el('span', {textContent: habit.text}));
      checkbox.onchange = () => {
        state.habits[habit.id] = checkbox.checked;
        row.classList.toggle('done', checkbox.checked);
        changed();
      };
      card.append(row);
    }
    root.append(card);
  }
}
// Kept in the order added so line numbers never jump around.
function taskEntries() {
  return state.lists.flatMap(list => list.tasks.map(task => ({list, task})));
}
function renderTasks() {
  const root = $('#tasks'); root.replaceChildren();
  const entries = taskEntries();
  for (const [index, {list, task}] of entries.entries()) {
    const number = el('span', {className: 'task-number', textContent: index + 1 + '.'});
    const checkbox = el('input', {type: 'checkbox', checked: task.done});
    checkbox.setAttribute('aria-label', 'Complete ' + task.text);
    checkbox.onchange = () => { task.done = checkbox.checked; changed(); renderTasks(); };
    const input = el('input', {className: 'task-text', value: task.text});
    input.setAttribute('aria-label', 'Task');
    input.oninput = () => { task.text = input.value; checkbox.setAttribute('aria-label', 'Complete ' + task.text); changed(); };
    const star = el('button', {type: 'button', className: 'star' + (task.priority === 'High' ? ' important' : ''), textContent: task.priority === 'High' ? '★' : '☆'});
    star.setAttribute('aria-label', task.priority === 'High' ? 'Remove priority' : 'Mark as priority');
    star.setAttribute('aria-pressed', String(task.priority === 'High'));
    star.onclick = () => { task.priority = task.priority === 'High' ? 'Medium' : 'High'; changed(); renderTasks(); };
    const remove = el('button', {type: 'button', className: 'remove', textContent: '×', onclick: () => { list.tasks = list.tasks.filter(t => t.id !== task.id); changed(); renderTasks(); }});
    remove.setAttribute('aria-label', 'Remove ' + task.text);
    root.append(el('div', {className: 'task' + (task.done ? ' done' : '')}, number, checkbox, input, star, remove));
  }
  if (!entries.length) root.append(el('p', {className: 'empty', textContent: 'A blank day. Add whatever you need to do.'}));
  const complete = entries.filter(({task}) => task.done).length;
  $('#progress').textContent = entries.length ? `${complete}/${entries.length} done` : '';
  $('#priority-hint').hidden = !entries.length;
}
const ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen'];
const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];
function spokenNumber(words) {
  if (/^\d+$/.test(words)) return Number(words);
  const [tens, ones, extra] = words.toLowerCase().split(/[\s-]+/);
  if (extra) return null;
  if (!ones) return ONES.includes(tens) ? ONES.indexOf(tens) : TENS.indexOf(tens) > 1 ? TENS.indexOf(tens) * 10 : null;
  return TENS.indexOf(tens) > 1 && ONES.indexOf(ones) > 0 && ONES.indexOf(ones) < 10 ? TENS.indexOf(tens) * 10 + ONES.indexOf(ones) : null;
}
// "seven load wood", "number 12, call Bob" -> {line: 7, text: 'load wood'}.
function lineEdit(item) {
  const prefix = item.match(/^(?:(?:number|line|item|no\.?)\s+|#\s*)/i);
  const words = item.slice(prefix ? prefix[0].length : 0).split(/\s+/);
  for (const size of [2, 1]) {
    const line = spokenNumber(words.slice(0, size).join(' ').replace(/[.:)]$/, ''));
    if (!line) continue;
    const text = words.slice(size).join(' ').replace(/^[\s:.,-]+/, '');
    return text ? {line, text, explicit: !!prefix} : null;
  }
  return null;
}
const bareNumber = item => !lineEdit(item) && !!lineEdit(item + ' x');
// Splits a brain dump on pauses, punctuation, "then" and "new line"; a number said on its own joins the next item.
function dumpItems(text) {
  const items = [];
  for (const piece of text.split(/\r?\n|[,;!?]|\.(?=\s|$)|\b(?:new\s+line|and\s+then|then)\b/gi)) {
    const item = piece.replace(/^\s*[-*•]\s*/, '').trim();
    if (!item) continue;
    const last = items[items.length - 1];
    if (last && bareNumber(last)) items[items.length - 1] = (/^\d|^[a-z]/i.test(last) && !/^(number|line|item|no)\b|^#/i.test(last) ? 'number ' : '') + last + ' ' + item;
    else items.push(item);
  }
  return items;
}
function addTasks() {
  const items = dumpItems($('#capture').value);
  if (!items.length) { $('#capture').focus(); return; }
  let list = state.lists.find(l => l.id === 'daily-' + currentDay);
  if (!list) { list = {id: 'daily-' + currentDay, title: 'Daily list', priority: 'Medium', tasks: []}; state.lists.push(list); }
  const before = JSON.stringify(state.lists), day = currentDay, notes = [];
  let added = 0;
  for (const item of items) {
    const entries = taskEntries(), edit = lineEdit(item);
    if (edit && edit.line <= entries.length) {
      entries[edit.line - 1].task.text = edit.text[0].toUpperCase() + edit.text.slice(1);
      notes.push('Changed line ' + edit.line);
    } else if (edit && edit.explicit) {
      list.tasks.push({id: uid(), text: edit.text[0].toUpperCase() + edit.text.slice(1), done: false, priority: 'Medium'});
      notes.push(`No line ${edit.line} yet, added it as line ${entries.length + 1}`);
    } else {
      list.tasks.push({id: uid(), text: item[0].toUpperCase() + item.slice(1), done: false, priority: 'Medium'});
      added++;
    }
  }
  if (added) notes.unshift(`Added ${added} item${added === 1 ? '' : 's'}`);
  const draft = $('#capture').value;
  const undo = el('button', {type: 'button', className: 'undo', textContent: 'Undo', onclick: () => {
    if (currentDay !== day) return;
    state.lists = JSON.parse(before); $('#capture').value = state.draft = draft;
    $('#voice-status').textContent = 'Undone.'; changed(); renderTasks();
  }});
  $('#voice-status').replaceChildren(notes.join('. ') + '. ', undo);
  $('#capture').value = ''; state.draft = ''; changed(); renderTasks();
}
$('#add-tasks').onclick = addTasks;
$('#capture').oninput = event => { state.draft = event.target.value; changed(); };
$('#capture').onkeydown = event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); addTasks(); } };
$('#today').onclick = () => loadDay(localDate());
for (const [id, step] of [['previous-month', -1], ['next-month', 1]]) {
  $('#' + id).onclick = () => {
    const d = dateObject(monthView + '-01'); d.setMonth(d.getMonth() + step);
    monthView = dateKey(d).slice(0, 7); renderCalendar(); loadMonthEntries();
  };
}

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
  recognition.onstart = () => { listening = true; $('#dictate').textContent = '■ Stop'; $('#dictate').classList.add('listening'); $('#voice-status').textContent = 'Listening. Pause between items. Say a number first to change that line.'; };
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
  if (today === lastToday || rollingDay) return;
  const previous = lastToday; lastToday = today; renderCalendar();
  if (currentDay === previous) {
    rollingDay = true; await loadDay(today); rollingDay = false;
    if (currentDay === previous) lastToday = previous;
  }
}
document.addEventListener('visibilitychange', () => { if (document.hidden && dirty) save(); else if (!document.hidden) checkDayChange(); });
window.addEventListener('focus', checkDayChange);
setInterval(checkDayChange, 60000);
async function init() {
  renderCalendar();
  try {
    const response = await fetch(APP_BASE + '/api/routines');
    if (!response.ok) throw Error('Could not load your routines. Refresh and sign in if needed.');
    routines = await response.json(); await loadDay(localDate());
  } catch (error) { showError(error.message, init); }
}
init();
