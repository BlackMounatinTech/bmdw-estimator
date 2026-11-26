const base = document.querySelector('meta[name=app-base]').content;
async function loadFinished() {
  try {
    const response = await fetch(base + '/api/lists/finished');
    if (!response.ok) throw Error('Could not load finished tasks.');
    const data = await response.json(), root = document.querySelector('#finished-tasks');
    root.replaceChildren();
    for (const entry of data.tasks) {
      const row = document.createElement('div'); row.className = 'task finished-task';
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = true; check.disabled = true; check.setAttribute('aria-label', 'Finished');
      const details = document.createElement('div'); details.className = 'task-text';
      const text = document.createElement('div'); text.textContent = entry.task.text;
      const meta = document.createElement('small'); meta.textContent = entry.source + ' · ' + (entry.task.completion_date_unknown ? 'Completion date not recorded' : entry.completed_on);
      details.append(text, meta); row.append(check, details); root.append(row);
    }
    if (!data.tasks.length) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = 'Completed tasks will appear here after midnight.'; root.append(p); }
    document.querySelector('#finished-count').textContent = data.tasks.length + ' finished';
    document.querySelector('#save-status').textContent = 'Read-only'; document.querySelector('#error').hidden = true;
  } catch (e) { const node = document.querySelector('#error'); node.textContent = e.message; node.hidden = false; }
}
loadFinished(); setInterval(loadFinished, 60000); window.addEventListener('focus', loadFinished);
