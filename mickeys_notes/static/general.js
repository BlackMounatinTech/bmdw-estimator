const base = document.querySelector('meta[name=app-base]').content;
const $ = selector => document.querySelector(selector);
let content = {tasks: [], draft: ''}, revision = 0, dirty = false, saving = null, failed = false, timer;
function element(tag, props = {}, ...children) {
  const node = document.createElement(tag); Object.assign(node, props); node.append(...children); return node;
}
function error(message) { $('#error').textContent = message; $('#error').hidden = false; }
function changed() {
  dirty = true; $('#save-status').textContent = 'Unsaved'; clearTimeout(timer); timer = setTimeout(save, 450);
}
async function save() {
  clearTimeout(timer);
  if (saving) { await saving; if (dirty && !failed) return save(); return; }
  if (!dirty) return;
  dirty = false; failed = false; $('#save-status').textContent = 'Saving…';
  const body = JSON.stringify({content, revision});
  saving = (async () => {
    try {
      const response = await fetch(base + '/api/general', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': $('meta[name=csrf]').content}, body});
      const data = await response.json();
      if (!response.ok) throw Error(data.error || 'Could not save your list.');
      revision = data.revision; $('#save-status').textContent = dirty ? 'Unsaved' : 'Saved'; $('#error').hidden = true;
    } catch (e) {
      dirty = true; failed = true; $('#save-status').textContent = 'Not saved';
      error(e.message + ' Your changes are still here.');
      const retry = element('button', {type: 'button', textContent: 'Retry', onclick: save}); $('#error').append(retry);
    } finally { saving = null; }
  })();
  await saving; if (dirty && !failed) return save();
}
function orderedTasks(tasks) { return [...tasks.filter(t => !t.done), ...tasks.filter(t => t.done)]; }
function toggleTask(task, done) {
  content.tasks = content.tasks.filter(t => t.id !== task.id); task.done = done;
  if (done) content.tasks.push(task); else content.tasks.unshift(task);
  changed(); render();
}
function render() {
  const root = $('#general-tasks'); root.replaceChildren();
  content.tasks = orderedTasks(content.tasks);
  for (const task of content.tasks) {
    const check = element('input', {type: 'checkbox', checked: task.done});
    check.setAttribute('aria-label', 'Complete ' + task.text); check.onchange = () => toggleTask(task, check.checked);
    const text = element('span', {className: 'task-text', textContent: task.text});
    const remove = element('button', {type: 'button', className: 'remove', textContent: '×', onclick: () => { content.tasks = content.tasks.filter(t => t.id !== task.id); changed(); render(); }});
    remove.setAttribute('aria-label', 'Remove ' + task.text);
    root.append(element('div', {className: 'task' + (task.done ? ' done' : '')}, element('label', {className: 'task-toggle'}, check, text), remove));
  }
  if (!content.tasks.length) root.append(element('p', {className: 'empty', textContent: 'Add anything you need to get done.'}));
  $('#general-progress').textContent = content.tasks.length ? `${content.tasks.filter(t => t.done).length}/${content.tasks.length} done` : '';
}
function addTasks() {
  const items = $('#general-capture').value.split(/\r?\n|[,;!?]|\.(?=\s|$)|\b(?:new\s+line|and\s+then|then)\b/gi).map(t => t.replace(/^\s*[-*•]\s*/, '').trim()).filter(Boolean);
  if (!items.length) return;
  // Each addition goes above earlier tasks. Numbers are ordinary task text here.
  for (const text of items) content.tasks.unshift({id: crypto.randomUUID(), text: text[0].toUpperCase() + text.slice(1), done: false});
  content.draft = ''; $('#general-capture').value = ''; changed(); render();
}
$('#general-add').onclick = addTasks;
$('#general-capture').oninput = e => { content.draft = e.target.value; changed(); };
$('#general-capture').onkeydown = e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addTasks(); } };
let speech, listening = false, keepListening = false;
const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
if (Speech) {
  speech = new Speech(); speech.lang = 'en-CA'; speech.continuous = false; speech.interimResults = false;
  speech.onstart = () => { listening = true; $('#general-dictate').textContent = '■ Stop'; $('#general-dictate').classList.add('listening'); $('#voice-status').textContent = 'Listening. Pause between tasks.'; };
  speech.onresult = e => {
    for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) {
      const field = $('#general-capture'); field.value += (field.value ? '\n' : '') + e.results[i][0].transcript;
      content.draft = field.value; changed();
    }
  };
  speech.onend = () => {
    if (keepListening) { try { speech.start(); return; } catch (_) {} }
    listening = false; keepListening = false; $('#general-dictate').textContent = '🎙 Dictate'; $('#general-dictate').classList.remove('listening');
  };
  speech.onerror = e => { if (['aborted', 'no-speech'].includes(e.error)) return; keepListening = false; $('#voice-status').textContent = 'Use your keyboard’s microphone, or type your tasks.'; };
  $('#general-dictate').onclick = () => { if (listening) { keepListening = false; speech.stop(); } else { keepListening = true; try { speech.start(); } catch (_) { keepListening = false; } } };
} else $('#general-dictate').hidden = true;
// Finish autosaves before switching between the day view and this shared list.
for (const link of document.querySelectorAll('header a')) link.onclick = async e => {
  e.preventDefault(); keepListening = false;
  if (speech && listening) speech.stop();
  await save(); if (!dirty) location.href = link.href;
};
window.addEventListener('beforeunload', e => { if (dirty || saving) { e.preventDefault(); e.returnValue = ''; } });
document.addEventListener('visibilitychange', () => { if (document.hidden && dirty) save(); });
async function init() {
  try {
    const response = await fetch(base + '/api/general');
    if (!response.ok) throw Error('Could not load your general to-do list. Refresh to try again.');
    const data = await response.json(); content = data.content; revision = data.revision;
    $('#general-capture').value = content.draft || ''; render(); $('#save-status').textContent = 'Saved'; $('#general-page').inert = false;
  } catch (e) { error(e.message); }
}
init();
