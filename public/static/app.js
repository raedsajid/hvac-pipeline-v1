/* Small browser enhancements. All page rendering and application logic live in Python. */
let processing = false;
for (const form of document.querySelectorAll('form[data-job]')) {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (processing) return;
    processing = true;
    const buttons = [...document.querySelectorAll('form[data-job] button')];
    const states = buttons.map(button => button.disabled);
    buttons.forEach(button => button.disabled = true);
    const status = form.closest('.panel')?.querySelector('.job-status') || document.querySelector('.job-status');
    status.classList.remove('error');
    status.textContent = 'Processing… You can leave this page and resume saved progress later.';
    try {
      for (let step = 0; step < 600; step++) {
        const response = await fetch(form.dataset.job, { method:'POST', headers:{'Content-Type':'application/json'}, body:'{}', credentials:'same-origin' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Processing could not finish. Retry to resume.');
        status.textContent = `${result.message || 'Progress saved'} · ${result.progress || 0}%`;
        const progress = form.closest('.panel')?.querySelector('progress');
        if (progress) progress.value = result.progress || 0;
        if (result.done || result.status === 'prepared') { window.location.reload(); return; }
      }
      status.textContent = 'Progress saved. Click again to continue.';
    } catch (error) { status.textContent = error.message; status.classList.add('error'); }
    finally { processing = false; buttons.forEach((button, i) => button.disabled = states[i]); }
  });
}
for (const form of document.querySelectorAll('form[data-upload-project]')) {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const file = form.elements.file.files[0];
    const status = form.querySelector('.upload-status');
    const button = form.querySelector('button');
    if (!file || button.disabled) return;
    if (file.size > 10 * 1024 * 1024) { status.textContent = 'Choose a PDF up to 10 MB.'; return; }
    button.disabled = true;
    const endpoint = `/api/projects/${form.dataset.uploadProject}`;
    const post = async (action, body) => {
      const response = await fetch(`${endpoint}/${action}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Upload could not finish.');
      return result;
    };
    try {
      status.textContent = 'Preparing secure upload…';
      const ticket = await post('upload-url', {filename:file.name,size:file.size,docKind:form.elements.docKind.value});
      status.textContent = 'Uploading PDF…';
      const uploaded = await fetch(ticket.url, {method:'PUT',headers:ticket.headers,body:file,credentials:'omit'});
      if (!uploaded.ok) throw new Error('R2 upload failed. Check bucket CORS configuration and retry.');
      status.textContent = 'Validating PDF…';
      const result = await post('upload-complete', {uploadId:ticket.uploadId});
      window.location.assign(`/documents/${result.docId}${form.elements.docKind.value === 'drawing' ? '?tab=crops' : ''}`);
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
}

// Legacy form redirects still use anchors. Keep them working with sidebar routes.
if (/^\/projects\/[^/]+$/.test(location.pathname) && ['#register','#equipment'].includes(location.hash)) {
  const url = new URL(location.href); url.searchParams.set('tab', location.hash.slice(1)); url.hash = '';
  location.replace(url);
}
const mobileNavigation = matchMedia('(max-width: 900px)');
function updateNavigation(expanded) {
  if (mobileNavigation.matches) {
    document.body.classList.toggle('sidebar-open', expanded);
    document.body.classList.remove('sidebar-collapsed');
  } else {
    document.body.classList.toggle('sidebar-collapsed', !expanded);
    document.body.classList.remove('sidebar-open');
  }
  document.querySelectorAll('.sidebar-toggle').forEach(button => button.setAttribute('aria-expanded', String(expanded)));
  document.querySelector('#app-sidebar')?.toggleAttribute('inert', !expanded);
}
updateNavigation(!mobileNavigation.matches);
mobileNavigation.addEventListener('change', () => updateNavigation(!mobileNavigation.matches));
for (const toggle of document.querySelectorAll('.sidebar-toggle')) toggle.addEventListener('click', () => {
  const expanded = toggle.getAttribute('aria-expanded') === 'true';
  if (expanded && toggle.closest('.app-sidebar')) document.querySelector('.app-topbar .sidebar-toggle')?.focus();
  updateNavigation(!expanded);
});
document.querySelector('.sidebar-scrim')?.addEventListener('click', () => updateNavigation(false));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && document.body.classList.contains('sidebar-open')) {
    updateNavigation(false); document.querySelector('.app-topbar .sidebar-toggle')?.focus();
  }
});
for (const trigger of document.querySelectorAll('[data-open-dialog]')) trigger.addEventListener('click', () => {
  document.getElementById(trigger.dataset.openDialog)?.showModal();
});
for (const close of document.querySelectorAll('[data-close-dialog]')) close.addEventListener('click', () => close.closest('dialog').close());
for (const time of document.querySelectorAll('[data-timestamp]')) {
  const date = new Date(Number(time.dataset.timestamp));
  if (!Number.isNaN(date.getTime())) { time.dateTime = date.toISOString(); time.textContent = date.toLocaleString(); }
}
for (const select of document.querySelectorAll('[data-document-choice]')) select.addEventListener('change', () => {
  location.assign(`/documents/${encodeURIComponent(select.value)}?tab=${encodeURIComponent(select.dataset.tab)}`);
});
for (const button of document.querySelectorAll('[data-show-pane]')) {
  const activate = () => {
    const buttons = [...button.parentElement.querySelectorAll('[data-show-pane]')];
    buttons.forEach(item => {
      const selected = item === button;
      item.classList.toggle('active', selected); item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.dataset.showPane).hidden = !selected;
    });
  };
  button.tabIndex = button.classList.contains('active') ? 0 : -1;
  button.addEventListener('click', activate);
  button.addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const siblings = [...button.parentElement.querySelectorAll('[data-show-pane]')];
    const offset = event.key === 'ArrowLeft' ? -1 : 1;
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? siblings.length - 1 : (siblings.indexOf(button) + offset + siblings.length) % siblings.length;
    siblings[index].click(); siblings[index].focus();
  });
}
const sourceImage = document.querySelector('#source-page');
for (const card of document.querySelectorAll('[data-highlight]')) {
  let timer;
  const highlight = () => {
    if (!sourceImage) return;
    sourceImage.src = card.dataset.highlight;
    sourceImage.alt = `Page ${card.dataset.page} with source evidence highlighted`;
    document.querySelector('#source-page-label').textContent = `Page ${card.dataset.page}`;
  };
  card.addEventListener('mouseenter', () => { timer = setTimeout(highlight, 180); });
  card.addEventListener('mouseleave', () => clearTimeout(timer));
  card.addEventListener('focus', highlight);
}
for (const input of document.querySelectorAll('[data-filter-cards]')) input.addEventListener('input', () => {
  for (const card of document.querySelectorAll('.finding-card')) card.hidden = !card.textContent.toLowerCase().includes(input.value.toLowerCase());
});
for (const button of document.querySelectorAll('[data-chat-prompt]')) button.addEventListener('click', () => {
  const field = document.querySelector('#question'); if (!field || field.disabled) return;
  field.value = button.textContent.trim(); field.focus();
});
const chatForm = document.querySelector('.chat-input');
chatForm?.querySelector('textarea').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); chatForm.requestSubmit(); }
});
chatForm?.addEventListener('submit', () => {
  chatForm.querySelector('button').disabled = true;
  document.querySelector('.chat-status').textContent = 'Reading the source and preparing an answer…';
});
// Filter, sort and select without changing export endpoint semantics.
const register = document.querySelector('#register-table');
const exportForm = document.querySelector('#register-export');
const selectedRows = () => [...document.querySelectorAll('[data-select-row]:checked')];
function updateSelection() {
  const selected = selectedRows();
  const count = document.querySelector('[data-selection-count]');
  if (count) count.textContent = `${selected.length} ${selected.length === 1 ? 'row' : 'rows'} selected`;
  const exportSelected = document.querySelector('[data-export-selected]');
  if (exportSelected) exportSelected.disabled = !selected.length;
  const all = document.querySelector('[data-select-all]');
  if (all) {
    const visible = [...document.querySelectorAll('[data-select-row]')].filter(input => !input.closest('tr').hidden);
    all.checked = visible.length > 0 && visible.every(input => input.checked);
    all.indeterminate = visible.some(input => input.checked) && !all.checked;
  }
}
function filterRegister() {
  const query = document.querySelector('[data-filter="register-table"]')?.value.toLowerCase() || '';
  const status = document.querySelector('#register-status')?.value || '';
  register?.querySelectorAll('tbody tr').forEach(row => row.hidden = !row.textContent.toLowerCase().includes(query) || Boolean(status && row.dataset.status !== status));
  updateSelection();
}
for (const search of document.querySelectorAll('[data-filter]')) search.addEventListener('input', filterRegister);
document.querySelector('#register-status')?.addEventListener('change', filterRegister);
document.querySelector('#register-sort')?.addEventListener('change', event => {
  if (!register) return;
  const rows = [...register.querySelectorAll('tbody tr')];
  const key = event.target.value;
  rows.sort((a,b) => key === 'source' ? Number(a.dataset.order)-Number(b.dataset.order) : a.dataset[key].localeCompare(b.dataset[key]));
  rows.forEach(row => register.tBodies[0].append(row));
});
document.querySelector('[data-select-all]')?.addEventListener('change', event => {
  document.querySelectorAll('[data-select-row]').forEach(input => { if (!input.closest('tr').hidden) input.checked = event.target.checked; }); updateSelection();
});
for (const input of document.querySelectorAll('[data-select-row]')) input.addEventListener('change', updateSelection);
exportForm?.addEventListener('submit', event => {
  const selected = event.submitter?.hasAttribute('data-export-selected');
  const field = exportForm.elements.ids;
  field.disabled = !selected; field.value = selected ? selectedRows().map(input => input.value).join(',') : '';
  if (selected && !field.value) event.preventDefault();
});

// Crisp red overlays use the existing normalized crop coordinates on the clean PDF.
const surface = document.querySelector('.crop-surface');
if (surface) {
  const canvas = surface.querySelector('canvas');
  const image = surface.querySelector('img');
  const form = document.querySelector('#crop-form');
  const feedback = document.querySelector('[data-crop-feedback]');
  const regions = JSON.parse(surface.dataset.regions);
  const page = Number(surface.dataset.page);
  const rejectionKey = `builderp-rejected:${surface.dataset.docId}:${surface.dataset.detectionRevision}`;
  let rejected = new Set();
  try { rejected = new Set(JSON.parse(sessionStorage.getItem(rejectionKey) || '[]')); } catch (_) { /* Optional tab-local state. */ }
  let start = null, dragBox = null, selected = surface.dataset.selected, dragged = false;
  const point = event => {
    const rect = surface.getBoundingClientRect();
    return [Math.max(0, Math.min(1,(event.clientX-rect.left)/rect.width)), Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];
  };
  const draw = () => {
    const width = surface.clientWidth, height = surface.clientHeight, scale = window.devicePixelRatio || 1;
    canvas.width = Math.round(width*scale); canvas.height = Math.round(height*scale);
    const context = canvas.getContext('2d'); context.scale(scale,scale);
    const box = (coordinates, label, focused) => {
      const [x,y,w,h] = coordinates;
      context.strokeStyle = focused ? '#b91c1c' : '#ef4444'; context.lineWidth = focused ? 3 : 2;
      context.fillStyle = focused ? 'rgba(220,38,38,.12)' : 'rgba(220,38,38,.04)';
      context.fillRect(x*width,y*height,w*width,h*height); context.strokeRect(x*width,y*height,w*width,h*height);
      if (label) {
        context.font = '600 12px Arial'; const labelWidth = context.measureText(label).width+12;
        const top = Math.max(0,y*height-21);
        context.fillStyle = '#dc2626'; context.fillRect(x*width,top,labelWidth,20);
        context.fillStyle = '#fff'; context.fillText(label,x*width+6,top+14);
      }
    };
    regions.forEach((region,index) => { if (region.page === page && !rejected.has(region.id)) box(region.box, String(index+1),region.id === selected); });
    if (dragBox) box(dragBox, 'Crop', true);
  };
  const syncCandidates = () => {
    document.querySelectorAll('[data-candidate]').forEach(row => {
      const isRejected = rejected.has(row.dataset.candidate) && row.dataset.accepted !== 'true';
      row.classList.toggle('is-rejected', isRejected); row.classList.toggle('is-selected', row.dataset.candidate === selected);
      const check = row.querySelector('input[type=checkbox]');
      check.disabled = isRejected || row.dataset.accepted === 'true';
      if (isRejected) check.checked = false;
      const button = row.querySelector('[data-reject-candidate]');
      if (button) { button.textContent = isRejected ? 'Undo' : 'Reject'; button.setAttribute('aria-pressed',String(isRejected)); }
    });
    const accept = document.querySelector('[data-accept-candidates]');
    if (accept) accept.disabled = !document.querySelector('[data-candidate] input:checked:not(:disabled)');
    draw();
  };
  for (const row of document.querySelectorAll('[data-candidate]')) {
    if (row.dataset.accepted === 'true') rejected.delete(row.dataset.candidate);
    row.querySelector('input').addEventListener('change', syncCandidates);
    row.querySelector('[data-reject-candidate]')?.addEventListener('click', () => {
      const id = row.dataset.candidate;
      if (rejected.has(id)) rejected.delete(id); else rejected.add(id);
      try { sessionStorage.setItem(rejectionKey, JSON.stringify([...rejected])); } catch (_) { /* Rejection remains in memory. */ }
      syncCandidates();
    });
  }
  surface.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    start = point(event); dragged = false; surface.setPointerCapture(event.pointerId);
  });
  surface.addEventListener('pointermove', event => {
    if (!start) return;
    const end = point(event);
    if (Math.hypot((end[0]-start[0])*surface.clientWidth,(end[1]-start[1])*surface.clientHeight) < 5 && !dragged) return;
    dragged = true;
    dragBox = [Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.abs(start[0]-end[0]),Math.abs(start[1]-end[1])];
    draw();
  });
  surface.addEventListener('pointerup', event => {
    if (!start) return;
    const end = point(event);
    if (dragged && dragBox && dragBox[2] > 0.002 && dragBox[3] > 0.002) {
      ['x','y','w','h'].forEach((key,index) => form.elements[key].value = dragBox[index].toFixed(6));
      feedback.textContent = 'Crop drawn. Check its borders, then save the region.';
    } else if (!dragged) {
      const hit = [...regions].reverse().find(region => region.page === page && !rejected.has(region.id) && end[0]>=region.box[0] && end[0]<=region.box[0]+region.box[2] && end[1]>=region.box[1] && end[1]<=region.box[1]+region.box[3]);
      if (hit) {
        selected = hit.id;
        const row = [...document.querySelectorAll('[data-candidate]')].find(item => item.dataset.candidate === selected);
        const check = row?.querySelector('input'); if (check && !check.disabled) check.focus({preventScroll:true});
        feedback.textContent = `Region ${regions.indexOf(hit)+1} selected. Use the review controls to accept or edit it.`;
        syncCandidates();
      }
    }
    start = null;
  });
  surface.addEventListener('pointercancel', () => { start=null; dragBox=null; draw(); });
  for (const key of ['x','y','w','h']) form.elements[key].addEventListener('input', () => {
    const values = ['x','y','w','h'].map(name => Number(form.elements[name].value));
    if (values.every(Number.isFinite)) { dragBox=values; draw(); }
  });
  image.addEventListener('load', draw);
  new ResizeObserver(draw).observe(surface);
  syncCandidates();
}
// Browser-only draft equipment export; source and review data remain unchanged.
document.querySelector('[data-export-equipment]')?.addEventListener('click', event => {
  const items = JSON.parse(event.currentTarget.dataset.equipment);
  const keys = ['unitTag','scheduleTitle','sheetNumber','proposedCsi','proposedDescription','selectedCsi','selectedDescription','status'];
  const cell = value => {
    let text = String(value ?? '');
    if (/^[\s]*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"','""') + '"';
  };
  const csv = ['Unit tag,Schedule,Sheet,Proposed CSI,Proposed description,Selected CSI,Selected description,Review status', ...items.map(item => keys.map(key=>cell(item[key])).join(','))].join('\r\n');
  const url = URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}));
  const link = document.createElement('a'); link.href=url; link.download='equipment-log-draft.csv'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
