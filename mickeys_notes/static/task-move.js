// Shared long-press and keyboard-accessible move menu for every task list.
window.taskMoveActions = function (row, task, source, move, day) {
  const label = row.querySelector('.task-toggle');
  let timer, startX, startY, suppressClick = false;
  const cancelHold = () => clearTimeout(timer);
  const make = (tag, props = {}) => Object.assign(document.createElement(tag), props);
  const open = () => {
    cancelHold();
    if (!row.isConnected) return;
    const dialog = make('dialog', {className: 'task-actions'});
    const heading = make('h2', {textContent: 'Move task to…'});
    heading.id = 'task-move-title'; dialog.setAttribute('aria-labelledby', heading.id);
    dialog.append(heading, make('p', {textContent: task.text}));
    const destinations = make('div', {className: 'task-move-destinations'});
    const nowDate = new Date(), tomorrowDate = new Date(); tomorrowDate.setDate(tomorrowDate.getDate() + 1);
    const dateKey = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const dates = {today: dateKey(nowDate), tomorrow: dateKey(tomorrowDate)};
    for (const [key, title] of [['today', 'Today'], ['tomorrow', 'Tomorrow'], ['business', 'Business'], ['week', 'This Week'], ['month', 'This Month'], ['quarter', 'This Quarter'], ['bday', 'By My Bday'], ['unfinished', 'Unfinished Tasks'], ['days', 'Another day…']]) {
      if ((key === source && key !== 'days') || (source === 'days' && dates[key] === day)) continue;
      const button = make('button', {type: 'button', textContent: title});
      button.onclick = () => {
        if (dates[key]) { dialog.close(); move({list: 'days', date: dates[key]}); return; }
        if (key !== 'days') { dialog.close(); move({list: key}); return; }
        destinations.replaceChildren();
        const dateLabel = make('label', {textContent: 'Choose a day'});
        const now = new Date();
        const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
        const input = make('input', {type: 'date', value: day || today, required: true});
        input.setAttribute('aria-label', 'Destination date'); dateLabel.append(input);
        const confirm = make('button', {type: 'button', className: 'primary', textContent: 'Move to this day'});
        confirm.onclick = () => { if (!input.reportValidity()) return; dialog.close(); move({list: 'days', date: input.value}); };
        destinations.append(dateLabel, confirm); input.focus();
      };
      destinations.append(button);
    }
    const cancel = make('button', {type: 'button', textContent: 'Cancel', onclick: () => dialog.close()});
    dialog.append(destinations, cancel); dialog.onclose = () => dialog.remove();
    document.body.append(dialog); dialog.showModal(); destinations.querySelector('button')?.focus();
  };
  label.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !event.isPrimary) return;
    cancelHold(); suppressClick = false; startX = event.clientX; startY = event.clientY;
    timer = setTimeout(() => { suppressClick = true; open(); }, 600);
  });
  label.addEventListener('pointermove', event => { if (Math.hypot(event.clientX - startX, event.clientY - startY) > 10) cancelHold(); });
  for (const event of ['pointerup', 'pointercancel', 'pointerleave']) label.addEventListener(event, cancelHold);
  label.addEventListener('contextmenu', event => { event.preventDefault(); if (!suppressClick) { suppressClick = true; open(); } });
  row.addEventListener('click', event => {
    if (suppressClick && event.target.closest('.task-toggle')) { event.preventDefault(); event.stopImmediatePropagation(); suppressClick = false; }
  }, true);
  const more = make('button', {type: 'button', className: 'task-more', textContent: '…', title: 'Move task', onclick: open});
  more.setAttribute('aria-label', 'Move ' + task.text); row.append(more);
};
