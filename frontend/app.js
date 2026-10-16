// JobTalk Frontend Application Logic
let items = [];
let unpricedCount = 0;
let taxRate = 0.08;
let mediaRecorder = null;
let audioChunks = [];
let recordStartTime = null;
let recordTimerInterval = null;
let recalcTimer = null;
let rates = {};

// Check secure context for microphone on load
window.addEventListener('DOMContentLoaded', () => {
  const isSecure = window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
  if (!isSecure) {
    const micSec = document.getElementById('micSection');
    const micNot = document.getElementById('micNotice');
    if (micSec) micSec.style.display = 'none';
    if (micNot) micNot.style.display = 'block';
  }
  checkHealth();
  setInterval(checkHealth, 15000);
  renderTable();
});

async function checkHealth() {
  try {
    const res = await fetch('/api/health');
    if (!res.ok) throw new Error('Health check error');
    const data = await res.json();
    const badge = document.getElementById('statusBadge');
    const text = document.getElementById('statusText');
    if (data.model_ready) {
      badge.className = 'status-badge status-ready';
      text.textContent = 'Model Ready (' + (data.model || 'local') + ')';
    } else {
      badge.className = 'status-badge status-loading';
      text.textContent = 'Warming up model...';
    }
    if (typeof data.tax === 'number') {
      taxRate = data.tax;
      const taxLbl = document.getElementById('taxRateDisplay');
      if (taxLbl) taxLbl.textContent = Math.round(taxRate * 100) + '%';
    }
  } catch (e) {
    const badge = document.getElementById('statusBadge');
    const text = document.getElementById('statusText');
    badge.className = 'status-badge';
    text.textContent = 'Offline / Ollama Disconnected';
  }
}

// Audio file selection
const audioInput = document.getElementById('audioFileInput');
if (audioInput) {
  audioInput.addEventListener('change', () => {
    if (audioInput.files.length > 0) {
      const file = audioInput.files[0];
      const lbl = document.getElementById('selectedFileName');
      lbl.textContent = 'Selected: ' + file.name + ' (' + Math.round(file.size / 1024) + ' KB)';
      lbl.style.display = 'block';
      document.getElementById('processBtn').disabled = false;
    }
  });
}

// MediaRecorder logic
async function toggleRecord() {
  const btn = document.getElementById('recordBtn');
  const timer = document.getElementById('recordTimer');
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    btn.textContent = 'Start Recording';
    btn.className = 'btn btn-secondary';
    clearInterval(recordTimerInterval);
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = () => {
      const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
      const file = new File([blob], 'recording.webm', { type: blob.type });
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      audioInput.files = dataTransfer.files;
      const lbl = document.getElementById('selectedFileName');
      lbl.textContent = 'Recorded audio (' + Math.round(blob.size / 1024) + ' KB)';
      lbl.style.display = 'block';
      document.getElementById('processBtn').disabled = false;
      stream.getTracks().forEach(t => t.stop());
    };
    mediaRecorder.start();
    btn.textContent = 'Stop Recording';
    btn.className = 'btn btn-danger';
    timer.style.display = 'inline';
    recordStartTime = Date.now();
    recordTimerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - recordStartTime) / 1000);
      const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
      const s = String(elapsed % 60).padStart(2, '0');
      timer.textContent = m + ':' + s;
    }, 500);
  } catch (err) {
    alert('Could not access microphone: ' + err.message);
  }
}

function showProgress(msg) {
  const el = document.getElementById('progressState');
  const txt = document.getElementById('progressMsg');
  txt.textContent = msg;
  el.style.display = 'inline-flex';
}

function hideProgress() {
  document.getElementById('progressState').style.display = 'none';
}

// Process audio file via SSE endpoint
async function processAudio() {
  if (!audioInput.files || audioInput.files.length === 0) return;
  const file = audioInput.files[0];

  showProgress('Transcribing audio with Whisper...');
  items = [];
  renderTable();

  const formData = new FormData();
  formData.append('audio', file);
  formData.append('rates', JSON.stringify(rates));

  try {
    const res = await fetch('/api/extract', {
      method: 'POST',
      body: formData
    });
    if (!res.ok) {
      const errJson = await res.json().catch(() => ({}));
      throw new Error(errJson.detail || 'Server returned HTTP ' + res.status);
    }
    await handleSseStream(res);
  } catch (err) {
    hideProgress();
    alert('Extraction failed: ' + err.message);
  }
}

// Extract from direct transcript text
async function extractFromTranscript() {
  const text = document.getElementById('transcriptText').value.trim();
  if (!text) {
    alert('Please enter or dictate a transcript first.');
    return;
  }
  showProgress('Extracting structured quote facts...');
  items = [];
  renderTable();

  try {
    const res = await fetch('/api/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript: text, rates: rates })
    });
    if (!res.ok) {
      const errJson = await res.json().catch(() => ({}));
      throw new Error(errJson.detail || 'Server returned HTTP ' + res.status);
    }
    await handleSseStream(res);
  } catch (err) {
    hideProgress();
    alert('Extraction failed: ' + err.message);
  }
}

// Process Server-Sent Events from response reader
async function handleSseStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split('\n\n');
    buffer = parts.pop();

    for (const block of parts) {
      if (!block.trim()) continue;
      let eventType = 'message';
      let eventData = '';
      const lines = block.split('\n');
      for (const line of lines) {
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          eventData = line.slice(5).trim();
        }
      }

      if (eventType === 'transcript') {
        const data = JSON.parse(eventData);
        document.getElementById('transcriptText').value = data.transcript || '';
        showProgress('Transcribed! Streaming quote line items...');
      } else if (eventType === 'item') {
        const data = JSON.parse(eventData);
        items.push(data.item);
        renderTable();
        triggerRecalc();
      } else if (eventType === 'done') {
        const data = JSON.parse(eventData);
        if (data.job) {
          if (data.job.customer) document.getElementById('custName').value = data.job.customer;
          if (data.job.address) document.getElementById('custAddr').value = data.job.address;
          if (data.job.job_summary) document.getElementById('jobSummary').value = data.job.job_summary;
          if (data.job.notes) document.getElementById('jobNotes').value = data.job.notes;
        }
        hideProgress();
        triggerRecalc();
      } else if (eventType === 'error') {
        const data = JSON.parse(eventData);
        hideProgress();
        alert('Error: ' + data.error);
      }
    }
  }
  hideProgress();
}

// Render line items table
function renderTable() {
  const tbody = document.getElementById('itemsBody');
  if (!tbody) return;
  tbody.innerHTML = '';
  unpricedCount = 0;

  items.forEach((it, idx) => {
    const isUnpriced = (it.unit_price === null || it.unit_price === undefined || it.unit_price === '');
    if (isUnpriced) unpricedCount++;

    const tr = document.createElement('tr');
    if (isUnpriced) tr.className = 'unpriced';

    // Description
    const tdDesc = document.createElement('td');
    const descInput = document.createElement('input');
    descInput.type = 'text';
    descInput.value = it.description || '';
    descInput.oninput = (e) => { items[idx].description = e.target.value; triggerRecalc(); };
    tdDesc.appendChild(descInput);
    if (isUnpriced) {
      const badge = document.createElement('span');
      badge.className = 'badge-warn';
      badge.textContent = 'Needs price';
      tdDesc.appendChild(badge);
    }
    tr.appendChild(tdDesc);

    // Kind (Labor / Material)
    const tdKind = document.createElement('td');
    const kindSelect = document.createElement('select');
    kindSelect.innerHTML = '<option value="labor">Labor</option><option value="material">Material</option>';
    kindSelect.value = (it.kind === 'labor') ? 'labor' : 'material';
    kindSelect.onchange = (e) => { items[idx].kind = e.target.value; triggerRecalc(); };
    tdKind.appendChild(kindSelect);
    tr.appendChild(tdKind);

    // Qty
    const tdQty = document.createElement('td');
    const qtyInput = document.createElement('input');
    qtyInput.type = 'number';
    qtyInput.step = 'any';
    qtyInput.value = it.qty !== undefined ? it.qty : 1;
    qtyInput.oninput = (e) => { items[idx].qty = parseFloat(e.target.value) || 0; triggerRecalc(); };
    tdQty.appendChild(qtyInput);
    tr.appendChild(tdQty);

    // Unit Price
    const tdPrice = document.createElement('td');
    const priceInput = document.createElement('input');
    priceInput.type = 'number';
    priceInput.step = '0.01';
    priceInput.placeholder = 'none';
    priceInput.value = it.unit_price !== null && it.unit_price !== undefined ? it.unit_price : '';
    priceInput.oninput = (e) => {
      const val = e.target.value.trim();
      items[idx].unit_price = val === '' ? null : (parseFloat(val) || 0);
      triggerRecalc();
    };
    tdPrice.appendChild(priceInput);
    tr.appendChild(tdPrice);

    // Line Total
    const tdTotal = document.createElement('td');
    tdTotal.style.textAlign = 'right';
    tdTotal.style.fontWeight = '500';
    tdTotal.textContent = it.total !== null && it.total !== undefined ? ('$' + it.total.toFixed(2)) : (isUnpriced ? '?' : '$0.00');
    tr.appendChild(tdTotal);

    // Delete button
    const tdDel = document.createElement('td');
    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'btn btn-secondary';
    delBtn.style.padding = '2px 6px';
    delBtn.style.fontSize = '0.75rem';
    delBtn.textContent = '✕';
    delBtn.onclick = () => { items.splice(idx, 1); renderTable(); triggerRecalc(); };
    tdDel.appendChild(delBtn);
    tr.appendChild(tdDel);

    tbody.appendChild(tr);
  });

  // Update unpriced warning and export button
  const alertBox = document.getElementById('unpricedAlert');
  if (alertBox) {
    if (unpricedCount > 0) {
      alertBox.style.display = 'block';
      document.getElementById('unpricedCount').textContent = unpricedCount;
    } else {
      alertBox.style.display = 'none';
    }
  }
  updateExportState();
}

function addEmptyRow() {
  items.push({
    description: '',
    kind: 'material',
    qty: 1.0,
    unit_price: null,
    total: null
  });
  renderTable();
  const inputs = document.querySelectorAll('#itemsBody tr:last-child input');
  if (inputs.length > 0) inputs[0].focus();
}

function updateExportState() {
  const chk = document.getElementById('allowUnpricedCheck');
  if (!chk) return;
  const allowUnpriced = chk.checked;
  const exportBtn = document.getElementById('exportBtn');
  if (exportBtn) {
    if (unpricedCount > 0 && !allowUnpriced) {
      exportBtn.style.opacity = '0.5';
    } else {
      exportBtn.style.opacity = '1.0';
    }
  }
}

// Trigger recalculation on Python backend
function triggerRecalc() {
  clearTimeout(recalcTimer);
  recalcTimer = setTimeout(async () => {
    try {
      const res = await fetch('/api/recalculate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: items,
          rates: rates,
          tax_rate: taxRate
        })
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data.items) {
        data.items.forEach((it, idx) => {
          if (items[idx]) {
            items[idx].total = it.total;
            items[idx].unit_price = it.unit_price;
          }
        });
        updateTotalsDisplay(data.totals);
        updateTableTotalsOnly();
      }
    } catch (e) {
      console.error('Recalc error:', e);
    }
  }, 150);
}

function updateTableTotalsOnly() {
  const rows = document.querySelectorAll('#itemsBody tr');
  unpricedCount = 0;
  rows.forEach((tr, idx) => {
    const it = items[idx];
    if (!it) return;
    const isUnpriced = (it.unit_price === null || it.unit_price === undefined || it.unit_price === '');
    if (isUnpriced) unpricedCount++;
    if (isUnpriced) tr.classList.add('unpriced');
    else tr.classList.remove('unpriced');

    const totalCell = tr.children[4];
    if (totalCell) {
      totalCell.textContent = it.total !== null && it.total !== undefined ? ('$' + it.total.toFixed(2)) : (isUnpriced ? '?' : '$0.00');
    }
  });
  const alertBox = document.getElementById('unpricedAlert');
  if (alertBox) {
    if (unpricedCount > 0) {
      alertBox.style.display = 'block';
      document.getElementById('unpricedCount').textContent = unpricedCount;
    } else {
      alertBox.style.display = 'none';
    }
  }
  updateExportState();
}

function updateTotalsDisplay(tot) {
  if (!tot) return;
  document.getElementById('totLabor').textContent = '$' + (tot.labor || 0).toFixed(2);
  document.getElementById('totMaterials').textContent = '$' + (tot.materials || 0).toFixed(2);
  document.getElementById('totSubtotal').textContent = '$' + (tot.subtotal || 0).toFixed(2);
  document.getElementById('totTax').textContent = '$' + (tot.tax || 0).toFixed(2);
  document.getElementById('totGrand').textContent = '$' + (tot.total || 0).toFixed(2);
}

// Export quote to printable HTML
async function exportQuote() {
  const allowUnpriced = document.getElementById('allowUnpricedCheck').checked;
  if (unpricedCount > 0 && !allowUnpriced) {
    alert('Cannot export: ' + unpricedCount + ' item(s) have no price set. Please enter prices or check "Accept unpriced items on quote" to proceed.');
    return;
  }

  const payload = {
    customer: document.getElementById('custName').value.trim(),
    address: document.getElementById('custAddr').value.trim(),
    job_summary: document.getElementById('jobSummary').value.trim(),
    notes: document.getElementById('jobNotes').value.trim(),
    items: items,
    rates: rates,
    tax_rate: taxRate,
    allow_unpriced: allowUnpriced
  };

  try {
    const res = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json();
      alert('Export blocked: ' + (err.error || err.detail || 'Server error'));
      return;
    }
    const htmlText = await res.text();
    const blob = new Blob([htmlText], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank');
  } catch (err) {
    alert('Export failed: ' + err.message);
  }
}
