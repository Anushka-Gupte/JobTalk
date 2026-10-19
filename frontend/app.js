// JobTalk Frontend Application Logic
let items = [];
let unpricedCount = 0;
let taxRate = 0.18;
let currencySymbol = '₹';
let gstSplit = false;
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
    const savedBiz = localStorage.getItem('jobtalk_biz_name');
    const bizInput = document.getElementById('bizName');
    if (bizInput && !bizInput.value) {
      if (savedBiz !== null) {
        bizInput.value = savedBiz;
      } else if (data.business && data.business !== "Dave's Plumbing & Electrical") {
        bizInput.value = data.business;
      } else {
        bizInput.value = '';
      }
    }

    const savedTax = localStorage.getItem('jobtalk_tax_rate');
    const gstSelect = document.getElementById('gstRateSelect');
    if (savedTax !== null) {
      taxRate = parseFloat(savedTax) || 0.0;
    } else if (typeof data.tax === 'number') {
      taxRate = data.tax;
    }
    if (gstSelect && (!gstSelect.value || gstSelect.value === "0")) {
      gstSelect.value = String(taxRate);
      if (!gstSelect.value) gstSelect.value = "0";
    }

    const savedSplit = localStorage.getItem('jobtalk_gst_split');
    const splitChk = document.getElementById('gstSplitCheck');
    if (savedSplit !== null) {
      gstSplit = savedSplit === 'true';
    } else if (typeof data.gst_split === 'boolean') {
      gstSplit = data.gst_split;
    }
    if (splitChk) {
      splitChk.checked = gstSplit;
    }

    const taxLbl = document.getElementById('taxRateDisplay');
    if (taxLbl) taxLbl.textContent = Math.round(taxRate * 100) + '%';
    if (data.currency_symbol) {
      currencySymbol = data.currency_symbol;
    }
    if (data.tax_label) {
      const lbl = document.getElementById('taxLabelDisplay');
      if (lbl) lbl.textContent = data.tax_label;
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
      if (blob.size === 0) {
        alert('Recording was empty. Please try recording again.');
        stream.getTracks().forEach(t => t.stop());
        return;
      }
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
    mediaRecorder.start(250);
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
          if (data.job.items && data.job.items.length > 0) {
            items = data.job.items;
          }
        }
        if (data.missing_items_reminder !== undefined) {
          missingReminderText = data.missing_items_reminder;
        }
        if (data.unmatched_number_phrases !== undefined) {
          renderTranscriptHighlights(data.unmatched_number_phrases);
        }
        hideProgress();
        renderTable();
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

let missingReminderText = null;
let unmatchedPhrases = [];

function renderTranscriptHighlights(unmatched) {
  if (unmatched !== undefined) {
    unmatchedPhrases = unmatched || [];
  }
  const box = document.getElementById('transcriptHighlightBox');
  const content = document.getElementById('transcriptHighlightContent');
  if (!box || !content) return;

  if (!unmatchedPhrases || unmatchedPhrases.length === 0) {
    box.style.display = 'none';
    content.innerHTML = '';
    return;
  }

  box.style.display = 'block';
  content.innerHTML = '';

  unmatchedPhrases.forEach(phrase => {
    const pEl = document.createElement('div');
    pEl.style.marginTop = '4px';
    pEl.innerHTML = `<mark class="unmatched-phrase-highlight" title="Amount or quantity not found in any active row">${escapeHtml(phrase)}</mark>`;
    content.appendChild(pEl);
  });
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function updateMissingItemsAlert() {
  const alertEl = document.getElementById('missingItemsAlert');
  const textEl = document.getElementById('missingItemsText');
  if (!alertEl || !textEl) return;
  if (missingReminderText) {
    alertEl.style.display = 'flex';
    textEl.textContent = missingReminderText;
  } else {
    alertEl.style.display = 'none';
    textEl.textContent = '';
  }
}

function restoreItem(idx) {
  if (items[idx]) {
    items[idx].dropped_reason = null;
    renderTable();
    triggerRecalc();
  }
}

// Render line items table
function renderTable() {
  const tbody = document.getElementById('itemsBody');
  if (!tbody) return;
  tbody.innerHTML = '';
  unpricedCount = 0;

  const droppedList = document.getElementById('droppedItemsList');
  const droppedSec = document.getElementById('droppedItemsSection');
  if (droppedList) droppedList.innerHTML = '';
  let droppedCount = 0;

  items.forEach((it, idx) => {
    if (it.dropped_reason) {
      droppedCount++;
      if (droppedList) {
        const dropRow = document.createElement('div');
        dropRow.className = 'dropped-item-row';
        const priceStr = it.unit_price ? ' @ ' + (it.formatted_unit_price || it.unit_price) : '';
        dropRow.innerHTML = `
          <span>
            <strong>${escapeHtml(it.description)}</strong>
            <span style="font-size:0.75rem; color:#fde68a; margin-left:6px;">(${escapeHtml(it.dropped_reason)})</span>
            <span style="font-size:0.8rem; color:var(--text-muted); margin-left:8px;">Qty: ${it.qty || 1}${priceStr}</span>
          </span>
          <button type="button" class="btn btn-secondary" style="padding:2px 8px; font-size:0.75rem;" onclick="restoreItem(${idx})">Restore</button>
        `;
        droppedList.appendChild(dropRow);
      }
      return;
    }

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

    // Kind (Labour / Material)
    const tdKind = document.createElement('td');
    const kindSelect = document.createElement('select');
    kindSelect.innerHTML = '<option value="labor">Labour</option><option value="material">Material</option>';
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

    // Unit Price (Rate)
    const tdPrice = document.createElement('td');
    const priceInput = document.createElement('input');
    priceInput.type = 'number';
    priceInput.step = '0.01';
    priceInput.placeholder = 'none';
    priceInput.value = it.unit_price !== null && it.unit_price !== undefined ? it.unit_price : '';
    priceInput.oninput = (e) => {
      const val = e.target.value.trim();
      items[idx].unit_price = val === '' ? null : (parseFloat(val) || 0);
      items[idx].price_flag = null;
      triggerRecalc();
    };
    tdPrice.appendChild(priceInput);
    if (it.price_flag && !isUnpriced) {
      const pBadge = document.createElement('span');
      pBadge.className = 'badge-warn';
      pBadge.textContent = 'price not found in recording, confirm';
      pBadge.title = 'Click to confirm price';
      pBadge.style.cursor = 'pointer';
      pBadge.style.display = 'block';
      pBadge.onclick = () => {
        items[idx].price_flag = null;
        renderTable();
      };
      tdPrice.appendChild(pBadge);
    }
    tr.appendChild(tdPrice);

    // Line Total (Amount) — display pre-formatted string from backend
    const tdTotal = document.createElement('td');
    tdTotal.style.textAlign = 'right';
    tdTotal.style.fontWeight = '500';
    tdTotal.textContent = it.formatted_total || (isUnpriced ? '?' : (currencySymbol + '0.00'));
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

  if (droppedSec) {
    droppedSec.style.display = droppedCount > 0 ? 'block' : 'none';
  }

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
  updateMissingItemsAlert();
}

function addEmptyRow() {
  items.push({
    description: '',
    kind: 'material',
    qty: 1.0,
    unit_price: null,
    total: null,
    formatted_total: '?'
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
  const hasUnconfirmed = items.some(it => !it.dropped_reason && it.price_flag);
  if (exportBtn) {
    if ((unpricedCount > 0 || hasUnconfirmed) && !allowUnpriced) {
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
          tax_rate: taxRate,
          gst_split: gstSplit,
          currency_symbol: currencySymbol,
          transcript: document.getElementById('transcriptText')?.value || '',
          address: document.getElementById('custAddr')?.value || ''
        })
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data.missing_items_reminder !== undefined) {
        missingReminderText = data.missing_items_reminder;
        updateMissingItemsAlert();
      }
      if (data.unmatched_number_phrases !== undefined) {
        renderTranscriptHighlights(data.unmatched_number_phrases);
      }
      if (data.items) {
        data.items.forEach((it, idx) => {
          if (items[idx]) {
            items[idx].total = it.total;
            items[idx].unit_price = it.unit_price;
            items[idx].price_flag = it.price_flag;
            items[idx].dropped_reason = it.dropped_reason;
            items[idx].formatted_total = it.formatted_total;
            items[idx].formatted_unit_price = it.formatted_unit_price;
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
      totalCell.textContent = it.formatted_total || (isUnpriced ? '?' : (currencySymbol + '0.00'));
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
  // All formatted strings are returned directly from Python backend
  if (tot.formatted_labor) document.getElementById('totLabor').textContent = tot.formatted_labor;
  if (tot.formatted_materials) document.getElementById('totMaterials').textContent = tot.formatted_materials;
  if (tot.formatted_subtotal) document.getElementById('totSubtotal').textContent = tot.formatted_subtotal;
  if (tot.formatted_tax) document.getElementById('totTax').textContent = tot.formatted_tax;
  if (tot.formatted_total) document.getElementById('totGrand').textContent = tot.formatted_total;

  const cgstRow = document.getElementById('cgstRow');
  const sgstRow = document.getElementById('sgstRow');
  const singleTaxRow = document.getElementById('singleTaxRow');

  if (gstSplit && tot.formatted_cgst && tot.formatted_sgst) {
    if (singleTaxRow) singleTaxRow.style.display = 'none';
    if (cgstRow) {
      cgstRow.style.display = 'flex';
      document.getElementById('totCgst').textContent = tot.formatted_cgst;
      document.getElementById('cgstRateDisplay').textContent = (taxRate * 50).toFixed(1).replace('.0', '') + '%';
    }
    if (sgstRow) {
      sgstRow.style.display = 'flex';
      document.getElementById('totSgst').textContent = tot.formatted_sgst;
      document.getElementById('sgstRateDisplay').textContent = (taxRate * 50).toFixed(1).replace('.0', '') + '%';
    }
  } else {
    if (singleTaxRow) singleTaxRow.style.display = 'flex';
    if (cgstRow) cgstRow.style.display = 'none';
    if (sgstRow) sgstRow.style.display = 'none';
  }

  // Show round-off row only when there is a non-zero round_off
  const roundOffRow = document.getElementById('roundOffRow');
  if (roundOffRow) {
    if (tot.round_off && tot.formatted_round_off) {
      roundOffRow.style.display = 'flex';
      const el = document.getElementById('totRoundOff');
      if (el) el.textContent = tot.formatted_round_off;
    } else {
      roundOffRow.style.display = 'none';
    }
  }
}

// Export quote to printable HTML
async function exportQuote() {
  const allowUnpriced = document.getElementById('allowUnpricedCheck').checked;
  const hasUnconfirmed = items.some(it => it.price_flag);

  if (unpricedCount > 0 && !allowUnpriced) {
    alert('Cannot export: ' + unpricedCount + ' item(s) have no price set. Please enter prices or check "Accept unpriced items on quote" to proceed.');
    return;
  }

  if (hasUnconfirmed && !allowUnpriced) {
    alert('Cannot export: One or more line items have prices not found in the recording. Please confirm or edit the prices, or check "Accept unpriced items on quote" to proceed.');
    return;
  }

  const payload = {
    customer: document.getElementById('custName').value.trim(),
    address: document.getElementById('custAddr').value.trim(),
    business: document.getElementById('bizName')?.value.trim() || '',
    job_summary: document.getElementById('jobSummary').value.trim(),
    notes: document.getElementById('jobNotes').value.trim(),
    items: items,
    rates: rates,
    tax_rate: taxRate,
    gst_split: gstSplit,
    currency_symbol: currencySymbol,
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

function onBizNameChange() {
  const val = document.getElementById('bizName')?.value || '';
  localStorage.setItem('jobtalk_biz_name', val);
}

function onGstChange() {
  const sel = document.getElementById('gstRateSelect');
  if (sel) {
    taxRate = parseFloat(sel.value) || 0.0;
    localStorage.setItem('jobtalk_tax_rate', String(taxRate));
    const taxLbl = document.getElementById('taxRateDisplay');
    if (taxLbl) taxLbl.textContent = Math.round(taxRate * 100) + '%';
  }
  const splitChk = document.getElementById('gstSplitCheck');
  if (splitChk) {
    gstSplit = splitChk.checked;
    localStorage.setItem('jobtalk_gst_split', String(gstSplit));
  }
  triggerRecalc();
}

function shareOnWhatsApp() {
  const biz = document.getElementById('bizName')?.value.trim() || '';
  const cust = document.getElementById('custName')?.value.trim() || '';
  const addr = document.getElementById('custAddr')?.value.trim() || '';
  const summary = document.getElementById('jobSummary')?.value.trim() || '';
  const grandTotal = document.getElementById('totGrand')?.textContent || (currencySymbol + '0.00');
  const subtotal = document.getElementById('totSubtotal')?.textContent || (currencySymbol + '0.00');
  const tax = document.getElementById('totTax')?.textContent || '';

  let text = '';
  if (biz) text += '*' + biz + '*\n';
  text += 'Quote Estimate\n';
  if (cust) text += 'Customer: ' + cust + '\n';
  if (addr) text += 'Address: ' + addr + '\n';
  if (summary) text += 'Job: ' + summary + '\n';

  const activeItems = items.filter(it => !it.dropped_reason);
  if (activeItems.length > 0) {
    text += '\nItems:\n';
    activeItems.forEach(it => {
      const amt = it.formatted_total || (it.unit_price ? currencySymbol + (it.qty * it.unit_price).toFixed(2) : 'TBD');
      text += '• ' + it.description + ' (x' + it.qty + '): ' + amt + '\n';
    });
  }

  text += '\nSubtotal: ' + subtotal + '\n';
  if (taxRate > 0) {
    text += 'GST (' + Math.round(taxRate * 100) + '%): ' + tax + '\n';
  }
  text += '*Grand Total: ' + grandTotal + '*\n';

  const url = 'https://api.whatsapp.com/send?text=' + encodeURIComponent(text);
  window.open(url, '_blank');
}
