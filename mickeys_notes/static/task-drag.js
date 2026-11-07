// Small, dependency-free drag handles for mouse, touch and keyboard.
window.taskDragHandle = function (row, root, commit) {
  const handle = document.createElement('button');
  handle.type = 'button'; handle.className = 'drag-handle'; handle.textContent = '⠿';
  handle.setAttribute('aria-label', 'Drag to reorder. Use arrow keys to move.');
  handle.title = 'Drag to reorder';
  let active = false, moved = false, y = 0, frame;
  const order = () => [...root.querySelectorAll('.task')].map(node => node.dataset.taskId);
  const move = () => {
    const others = [...root.querySelectorAll('.task')].filter(node => node !== row);
    const next = others.find(node => y < node.getBoundingClientRect().top + node.getBoundingClientRect().height / 2);
    root.insertBefore(row, next || null);
  };
  const scroll = () => {
    if (!active) return;
    const speed = y < 80 ? -12 : y > innerHeight - 80 ? 12 : 0;
    if (speed && moved) { window.scrollBy(0, speed); move(); }
    frame = requestAnimationFrame(scroll);
  };
  handle.onpointerdown = event => {
    if (event.button !== 0) return;
    event.preventDefault();
    handle.focus({preventScroll: true});
    active = true; moved = false; y = event.clientY;
    // Listen on the document: moving a row in the DOM can release pointer capture.
    document.addEventListener('pointermove', pointerMove);
    document.addEventListener('pointerup', finish);
    document.addEventListener('pointercancel', finish);
    row.classList.add('dragging');
    frame = requestAnimationFrame(scroll);
  };
  const pointerMove = event => {
    if (!active) return;
    y = event.clientY; moved = true; move();
  };
  const finish = () => {
    if (!active) return;
    active = false; cancelAnimationFrame(frame); row.classList.remove('dragging');
    document.removeEventListener('pointermove', pointerMove);
    document.removeEventListener('pointerup', finish);
    document.removeEventListener('pointercancel', finish);
    if (moved) commit(order());
  };
  handle.onkeydown = event => {
    if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'ArrowUp' && row.previousElementSibling?.classList.contains('task')) root.insertBefore(row, row.previousElementSibling);
    if (event.key === 'ArrowDown' && row.nextElementSibling?.classList.contains('task')) root.insertBefore(row.nextElementSibling, row);
    commit(order());
    root.querySelector(`[data-task-id="${CSS.escape(row.dataset.taskId)}"] .drag-handle`)?.focus();
  };
  return handle;
};
