/* Small browser enhancements. All page rendering and application logic live in Python. */
for (const search of document.querySelectorAll('[data-filter]')) {
  search.addEventListener('input', () => {
    const query = search.value.toLowerCase();
    for (const row of document.querySelectorAll(`#${search.dataset.filter} tbody tr`)) row.hidden = !row.textContent.toLowerCase().includes(query);
  });
}
let processing = false;
for (const form of document.querySelectorAll('form[data-job]')) {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (processing) return;
    processing = true;
    const buttons = [...document.querySelectorAll('form[data-job] button')];
    const states = buttons.map(button => button.disabled);
    buttons.forEach(button => button.disabled = true);
    const status = form.closest('.panel').querySelector('.job-status') || document.querySelector('.job-status');
    status.classList.remove('error');
    status.textContent = 'Processing… You can leave this page and resume saved progress later.';
    try {
      for (let step = 0; step < 600; step++) {
        const response = await fetch(form.dataset.job, { method:'POST', headers:{'Content-Type':'application/json'}, body:'{}', credentials:'same-origin' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Processing could not finish. Retry to resume.');
        status.textContent = `${result.message || 'Progress saved'} · ${result.progress || 0}%`;
        if (result.done || result.status === 'prepared') { window.location.reload(); return; }
      }
      status.textContent = 'Progress saved. Click again to continue.';
    } catch (error) { status.textContent = error.message; status.classList.add('error'); }
    finally { processing = false; buttons.forEach((button, i) => button.disabled = states[i]); }
  });
}
const surface = document.querySelector('.crop-surface');
if (surface) {
  const canvas = surface.querySelector('canvas');
  const form = document.querySelector('#crop-form');
  let start = null;
  const point = event => {
    const rect = surface.getBoundingClientRect();
    return [Math.max(0, Math.min(1,(event.clientX-rect.left)/rect.width)), Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];
  };
  const show = (a,b) => {
    canvas.width = surface.clientWidth; canvas.height = surface.clientHeight;
    const context = canvas.getContext('2d');
    const x=Math.min(a[0],b[0]), y=Math.min(a[1],b[1]), w=Math.abs(a[0]-b[0]), h=Math.abs(a[1]-b[1]);
    context.fillStyle = 'rgba(225,158,56,.20)'; context.strokeStyle='#d87b28'; context.lineWidth=2;
    context.fillRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);
    context.strokeRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);
    for (const [key,value] of Object.entries({x,y,w,h})) form.elements[key].value = value.toFixed(6);
  };
  surface.addEventListener('pointerdown',event=>{start=point(event);surface.setPointerCapture(event.pointerId);});
  surface.addEventListener('pointermove',event=>{if(start)show(start,point(event));});
  surface.addEventListener('pointerup',event=>{if(start)show(start,point(event));start=null;});
  surface.addEventListener('pointercancel',()=>{start=null;});
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
      window.location.assign(`/documents/${result.docId}`);
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
}
