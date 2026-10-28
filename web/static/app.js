// TagPilot Web Application - 50/50 Split View with Synchronized Scrolling
let currentTaskId = null;
let currentResults = null;
let totalPages = 1;
let pollInterval = null;
let syncScrollEnabled = true;

const uploadCard = document.getElementById('upload-card');
const fileInput = document.getElementById('pdf-file-input');
const browseBtn = document.getElementById('browse-btn');
const progressCard = document.getElementById('progress-card');
const resultsCard = document.getElementById('results-card');

const progressBarFill = document.getElementById('progress-bar-fill');
const progressPercent = document.getElementById('progress-percent');
const progressStageMsg = document.getElementById('progress-stage-msg');
const liveLogBox = document.getElementById('live-log-box');

const pdfScrollContainer = document.getElementById('pdf-scroll-container');
const tagsScrollContainer = document.getElementById('tags-scroll-container');
const syncBadge = document.getElementById('sync-badge');

// Sync Scroll Toggle
if (syncBadge) {
  syncBadge.addEventListener('click', () => {
    syncScrollEnabled = !syncScrollEnabled;
    syncBadge.style.opacity = syncScrollEnabled ? '1' : '0.4';
    syncBadge.querySelector('span').innerText = syncScrollEnabled ? 'Sync Scroll: ON' : 'Sync Scroll: OFF';
  });
}

// Upload & Drag-and-drop triggers
uploadCard.addEventListener('click', () => fileInput.click());
if (browseBtn) {
  browseBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    fileInput.click();
  });
}

['dragenter', 'dragover'].forEach(eventName => {
  uploadCard.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    uploadCard.classList.add('drag-over');
  });
});

['dragleave', 'drop'].forEach(eventName => {
  uploadCard.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    uploadCard.classList.remove('drag-over');
  });
});

uploadCard.addEventListener('click', (e) => {
  if (!geminiEnabled) {
    e.preventDefault();
    e.stopPropagation();
    openGeminiModalDialog();
    showGeminiMsg('Google Gemini API Key is mandatory. Please enter and verify your key to proceed.', 'error');
  }
});

uploadCard.addEventListener('drop', (e) => {
  const dt = e.dataTransfer;
  const files = dt.files;
  if (files && files.length > 0) {
    handleFileUpload(files[0]);
  }
});

fileInput.addEventListener('change', (e) => {
  if (e.target.files && e.target.files.length > 0) {
    handleFileUpload(e.target.files[0]);
  }
});

async function handleFileUpload(file) {
  if (!geminiEnabled) {
    alert('Google Gemini API Key is mandatory! Please configure and verify a valid Gemini API Key before uploading.');
    openGeminiModalDialog();
    showGeminiMsg('Google Gemini API Key is mandatory. Please enter and verify your key to proceed.', 'error');
    if (fileInput) fileInput.value = '';
    return;
  }

  if (!file.name.toLowerCase().endsWith('.pdf')) {
    alert('Please upload a valid PDF document.');
    return;
  }

  uploadCard.style.display = 'none';
  progressCard.style.display = 'block';
  resultsCard.style.display = 'none';

  const progressTitle = document.querySelector('#progress-card .progress-title-block h3');
  if (progressTitle) {
    progressTitle.innerText = 'Automated Remediation in Progress';
    progressTitle.style.color = '';
  }
  progressBarFill.style.background = '';
  progressPercent.style.color = '';

  updateStepState(1, 'active');
  progressBarFill.style.width = '10%';
  progressPercent.innerText = '10%';
  progressStageMsg.innerText = `Uploading '${file.name}'...`;
  logMessage(`Uploading '${file.name}' (${Math.round(file.size / 1024)} KB)...`);

  const formData = new FormData();
  formData.append('file', file);

  try {
    const uploadRes = await fetch('/api/upload', {
      method: 'POST',
      body: formData
    });

    if (!uploadRes.ok) {
      let errDetail = 'Upload failed';
      try {
        const err = await uploadRes.json();
        errDetail = err.detail || errDetail;
      } catch (_) {}
      throw new Error(errDetail);
    }

    const data = await uploadRes.json();
    currentTaskId = data.task_id;
    totalPages = data.page_count;

    logMessage(`Document uploaded successfully. Detected ${totalPages} page(s). Starting remediation...`);
    
    // Trigger tagging process
    const procRes = await fetch(`/api/process/${currentTaskId}`, { method: 'POST' });
    if (!procRes.ok) {
      logMessage(`[WARN] Process trigger response code: ${procRes.status}`);
    }

    // Start polling status
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(checkStatus, 800);
  } catch (err) {
    uploadCard.style.display = 'block';
    progressCard.style.display = 'none';
    progressStageMsg.innerText = `Error: ${err.message}`;
    logMessage(`[ERROR] ${err.message}`);
    alert(`Could not upload document: ${err.message}. Please check if server is running on http://127.0.0.1:8000.`);
  }
}

async function checkStatus() {
  if (!currentTaskId) return;

  try {
    let res = await fetch(`/api/status/${currentTaskId}`);
    if (res.status === 404) {
      res = await fetch(`/api/jobs/${currentTaskId}`);
    }
    if (!res.ok) {
      logMessage(`Waiting for task status (HTTP ${res.status})...`);
      return;
    }

    const data = await res.json();

    progressBarFill.style.width = `${data.progress}%`;
    progressPercent.innerText = `${data.progress}%`;
    progressStageMsg.innerText = data.stage_msg;

    // Update 4 step indicators
    if (data.progress >= 25) {
      updateStepState(1, 'completed');
      updateStepState(2, 'active');
    }
    if (data.progress >= 50) {
      updateStepState(2, 'completed');
      updateStepState(3, 'active');
    }
    if (data.progress >= 75) {
      updateStepState(3, 'completed');
      updateStepState(4, 'active');
    }
    if (data.progress === 100) {
      updateStepState(4, 'completed');
    }

    // Refresh live logs
    if (data.logs && data.logs.length > 0) {
      liveLogBox.innerHTML = data.logs.map(l => `<div>&gt; ${escapeHtml(l)}</div>`).join('');
      liveLogBox.scrollTop = liveLogBox.scrollHeight;
    }

    if (data.status === 'completed') {
      clearInterval(pollInterval);
      loadResults();
    } else if (data.status === 'error') {
      clearInterval(pollInterval);
      const progressTitle = document.querySelector('#progress-card .progress-title-block h3');
      if (progressTitle) {
        progressTitle.innerText = 'Remediation Failed';
        progressTitle.style.color = '#ef4444';
      }
      progressBarFill.style.background = '#ef4444';
      progressPercent.style.color = '#ef4444';
      progressStageMsg.innerText = `Pipeline failed: ${data.error_message || 'Unknown error'}`;
      logMessage(`[ERROR] ${data.error_message}`);
      const retryBlock = document.getElementById('progress-retry-block');
      if (retryBlock) retryBlock.style.display = 'block';
    }
  } catch (err) {
    console.error('Polling error:', err);
  }
}

const retryBtn = document.getElementById('retry-upload-btn');
if (retryBtn) {
  retryBtn.addEventListener('click', () => {
    currentTaskId = null;
    fileInput.value = '';
    progressCard.style.display = 'none';
    uploadCard.style.display = 'block';
    const progressTitle = document.querySelector('#progress-card .progress-title-block h3');
    if (progressTitle) {
      progressTitle.innerText = 'Automated Remediation in Progress';
      progressTitle.style.color = '';
    }
    progressBarFill.style.background = '';
    progressPercent.style.color = '';
    const retryBlock = document.getElementById('progress-retry-block');
    if (retryBlock) retryBlock.style.display = 'none';
  });
}

function updateStepState(stepNum, state) {
  const stepEl = document.getElementById(`step-${stepNum}`);
  if (!stepEl) return;
  stepEl.classList.remove('active', 'completed');
  if (state === 'active') stepEl.classList.add('active');
  if (state === 'completed') stepEl.classList.add('completed');
}

function logMessage(msg) {
  const div = document.createElement('div');
  div.innerText = `> ${msg}`;
  liveLogBox.appendChild(div);
  liveLogBox.scrollTop = liveLogBox.scrollHeight;
}

async function loadResults() {
  progressCard.style.display = 'none';
  resultsCard.style.display = 'block';

  try {
    const res = await fetch(`/api/results/${currentTaskId}`);
    currentResults = await res.json();
    totalPages = currentResults.page_count || 1;

    // Populate summary statistics
    const stats = currentResults.stats || {};
    document.getElementById('stat-pages').innerText = currentResults.page_count;
    document.getElementById('stat-elements').innerText = stats.elements_count || 0;
    document.getElementById('stat-headings').innerText = stats.headings_count || 0;
    document.getElementById('stat-paragraphs').innerText = stats.paragraphs_count || 0;
    document.getElementById('stat-lists').innerText = (stats.lists_count || 0) + (stats.tables_count || 0) + (stats.figures_count || 0);

    // Save active task to browser URL
    try {
      window.history.replaceState(null, '', `?task=${currentTaskId}`);
    } catch (_) {}

    // Badges in panel headers
    const pageBadge = document.getElementById('page-count-badge');
    if (pageBadge) pageBadge.innerText = `${currentResults.page_count} Page${currentResults.page_count > 1 ? 's' : ''}`;
    
    const tagsBadge = document.getElementById('tags-count-badge');
    if (tagsBadge) tagsBadge.innerText = `${stats.elements_count || 0} Tags`;

    // Download links
    document.getElementById('download-pdf-btn').href = `/api/download/${currentTaskId}`;
    document.getElementById('download-json-btn').href = `/api/download_json/${currentTaskId}`;

    // Render left panel: continuous feed of all pages
    renderAllPagesFeed();

    // Render right panel: complete tag hierarchy
    renderTagTree();

    // Setup smooth synchronized scrolling
    setupSynchronizedScrolling();
  } catch (err) {
    alert(`Failed to load results: ${err.message}`);
  }
}

// --------------------------------------------------------------------------
// Left Panel: Render ALL pages in continuous vertical feed
// --------------------------------------------------------------------------
function renderAllPagesFeed() {
  pdfScrollContainer.innerHTML = '';
  if (!currentResults || !currentResults.doc_json) return;

  const pages = currentResults.doc_json.pages || [];

  pages.forEach(pageData => {
    const pNum = pageData.page_number;

    // Card wrapper for the page
    const cardWrapper = document.createElement('div');
    cardWrapper.className = 'page-card-wrapper';
    cardWrapper.id = `page-card-${pNum}`;

    // Header label: "Page X of Y"
    const labelTag = document.createElement('div');
    labelTag.className = 'page-label-tag';
    labelTag.innerHTML = `<span>Page ${pNum} of ${totalPages}</span>`;
    cardWrapper.appendChild(labelTag);

    // Canvas container
    const canvasWrapper = document.createElement('div');
    canvasWrapper.className = 'page-canvas-wrapper';

    // Page image
    const img = document.createElement('img');
    img.className = 'page-preview-img';
    img.alt = `Page ${pNum}`;
    img.src = `/api/page_preview/${currentTaskId}/${pNum}`;

    // SVG overlay for this page
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'page-overlay-svg');
    svg.setAttribute('id', `page-svg-${pNum}`);

    img.onload = () => {
      drawPageBoundingBoxes(pageData, img, svg);
    };

    canvasWrapper.appendChild(img);
    canvasWrapper.appendChild(svg);
    cardWrapper.appendChild(canvasWrapper);

    // Page End Split Divider
    const splitDivider = document.createElement('div');
    splitDivider.className = 'page-split-divider';
    splitDivider.innerHTML = `
      <div class="divider-line"></div>
      <div class="divider-chip">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
        <span>End of Page ${pNum}</span>
      </div>
      <div class="divider-line"></div>
    `;
    cardWrapper.appendChild(splitDivider);

    pdfScrollContainer.appendChild(cardWrapper);
  });
}

function getElementDomKey(pNum, el) {
  const page = pNum || 1;
  const idVal = (el && el.id != null) ? el.id : (el && (el.uuid || el.node_id) ? (el.uuid || el.node_id) : 'el');
  return `${page}-${idVal}`;
}

function drawPageBoundingBoxes(pageData, imgEl, svgEl) {
  svgEl.innerHTML = '';
  const pNum = pageData.page_number || 1;
  const allElements = [];
  function collect(els) {
    (els || []).forEach(el => {
      if ((el.tag || '').toUpperCase() === 'ARTIFACT' || (el.type || '').toLowerCase() === 'artifact') return;
      allElements.push(el);
      if (el.kids) collect(el.kids);
    });
  }
  collect(pageData.elements || []);
  if (allElements.length === 0) return;

  const imgW = imgEl.clientWidth || 580;
  const imgH = imgEl.clientHeight || 750;

  // Use exact page dimensions if present in doc_json, otherwise find max bounds
  let maxPdfX = pageData.width || 612;
  let maxPdfY = pageData.height || 792;
  if (!pageData.width || !pageData.height) {
    allElements.forEach(el => {
      const b = el.bounding_box || [];
      if (b.length === 4) {
        if (b[2] > maxPdfX) maxPdfX = b[2];
        if (b[3] > maxPdfY) maxPdfY = b[3];
      }
    });
  }

  const scaleX = imgW / maxPdfX;
  const scaleY = imgH / maxPdfY;

  allElements.forEach(el => {
    const b = el.bounding_box;
    if (!b || b.length !== 4) return;

    // If this is a wrapper P whose only child is a Formula, skip drawing duplicate wrapper rect
    // so the Formula rect with distinct formula styling is directly visible and interactive
    if (el.tag === 'P' && el.kids && el.kids.length === 1 && (el.kids[0].tag === 'Formula' || el.kids[0].type === 'formula')) {
      return;
    }

    // PDF coords (bottom-up): [x0, y0, x1, y1]
    const x = b[0] * scaleX;
    const w = (b[2] - b[0]) * scaleX;
    const y = (maxPdfY - b[3]) * scaleY;
    const h = (b[3] - b[1]) * scaleY;

    const elKey = getElementDomKey(pNum, el);

    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', x);
    rect.setAttribute('y', y);
    rect.setAttribute('width', Math.max(w, 4));
    rect.setAttribute('height', Math.max(h, 4));
    rect.setAttribute('rx', '2');
    rect.setAttribute('id', `bbox-node-${elKey}`);
    rect.setAttribute('class', `bbox-rect tag-${getTagColorClass(el)}`);
    rect.style.fill = 'rgba(99, 102, 241, 0.12)';
    rect.style.stroke = getTagStrokeColor(el);
    rect.style.strokeWidth = '1.5';
    rect.style.cursor = 'pointer';

    rect.addEventListener('mouseenter', () => highlightElement(elKey));
    rect.addEventListener('mouseleave', () => unhighlightElement(elKey));
    rect.addEventListener('click', (e) => {
      e.stopPropagation();
      scrollToTreeNode(elKey);
    });

    svgEl.appendChild(rect);
  });
}

// Redraw bounding boxes when window or panel size changes
window.addEventListener('resize', () => {
  if (!currentResults || !currentResults.doc_json) return;
  (currentResults.doc_json.pages || []).forEach(pageData => {
    const pNum = pageData.page_number;
    const card = document.getElementById(`page-card-${pNum}`);
    if (card) {
      const img = card.querySelector('.page-preview-img');
      const svg = card.querySelector('.page-overlay-svg');
      if (img && svg && img.clientWidth > 0) {
        drawPageBoundingBoxes(pageData, img, svg);
      }
    }
  });
});

// --------------------------------------------------------------------------
// Right Panel: Render structural tags split page by page
// --------------------------------------------------------------------------
function renderTagTree() {
  tagsScrollContainer.innerHTML = '';
  if (!currentResults || !currentResults.doc_json) return;

  const pages = currentResults.doc_json.pages || [];

  pages.forEach(p => {
    const pNum = p.page_number;
    const elements = p.elements || [];

    // Count all elements on this page recursively
    let totalTagsOnPage = 0;
    function countNodes(els) {
      (els || []).forEach(e => {
        if ((e.tag || '').toUpperCase() === 'ARTIFACT' || (e.type || '').toLowerCase() === 'artifact') return;
        totalTagsOnPage++;
        if (e.kids) countNodes(e.kids);
      });
    }
    countNodes(elements);

    // Discrete card wrapper for this page's tags
    const pageCard = document.createElement('div');
    pageCard.className = 'tags-page-card';
    pageCard.id = `tags-page-card-${pNum}`;

    // Page header badge matching left side
    const headerEl = document.createElement('div');
    headerEl.className = 'tags-page-header';
    headerEl.innerHTML = `
      <div class="page-label-tag">
        <span>Page ${pNum} of ${totalPages}</span>
      </div>
      <span class="tags-count-pill">${totalTagsOnPage} Tag${totalTagsOnPage === 1 ? '' : 's'}</span>
    `;
    pageCard.appendChild(headerEl);

    // Box holding all tag nodes on this page
    const boxEl = document.createElement('div');
    boxEl.className = 'tags-page-box';

    if (elements.length === 0) {
      const emptyEl = document.createElement('div');
      emptyEl.className = 'tags-empty-state';
      emptyEl.innerHTML = `<span>No tags detected on Page ${pNum}</span>`;
      boxEl.appendChild(emptyEl);
    } else {
      elements.forEach(el => {
        renderElementTreeNode(el, pNum, boxEl);
      });
    }

    pageCard.appendChild(boxEl);

    // Matching Page End Split Divider
    const splitDivider = document.createElement('div');
    splitDivider.className = 'page-split-divider';
    splitDivider.innerHTML = `
      <div class="divider-line"></div>
      <div class="divider-chip">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
        <span>End of Page ${pNum}</span>
      </div>
      <div class="divider-line"></div>
    `;
    pageCard.appendChild(splitDivider);

    tagsScrollContainer.appendChild(pageCard);
  });
}

function getElementSummaryText(el) {
  if (el.actual_text && el.actual_text.trim()) {
    return el.actual_text.trim();
  }
  if (el.content && el.content.trim()) {
    return el.content.trim();
  }
  if (el.text && el.text.trim()) {
    return el.text.trim();
  }
  if (el.alt_text && el.alt_text.trim()) {
    return el.alt_text.trim();
  }
  
  // For LI (list_item), extract Lbl and LBody text from kids
  if (tag === 'LI' || el.type === 'list_item') {
    if (el.kids && el.kids.length > 0) {
      let lblTxt = '';
      let bodyTxt = '';
      el.kids.forEach(k => {
        const kTag = (k.tag || k.type || '').toUpperCase();
        if (kTag === 'LBL' || k.type === 'list_label') {
          if (k.content && k.content.trim()) {
            lblTxt = k.content.trim();
          } else if (k.kids && k.kids.length > 0) {
            lblTxt = (k.kids[0].content || k.kids[0].actual_text || '').trim();
          }
        } else if (kTag === 'LBODY' || k.type === 'list_body') {
          bodyTxt = getElementSummaryText(k);
        }
      });
      if (lblTxt || bodyTxt) {
        return `${lblTxt} ${bodyTxt}`.trim();
      }
    }
  }

  // For L (list)
  if (tag === 'L' || el.type === 'list') {
    const count = (el.kids || []).length;
    return `List (${count} item${count === 1 ? '' : 's'})`;
  }

  // For Table
  if (tag === 'TABLE' || el.type === 'table') {
    const rows = (el.kids || []).length;
    return `Table (${rows} row${rows === 1 ? '' : 's'})`;
  }

  // For TR
  if (tag === 'TR' || el.type === 'table_row') {
    const cells = (el.kids || []).length;
    return `Row (${cells} cell${cells === 1 ? '' : 's'})`;
  }

  // For Figure
  if (tag === 'FIGURE' || el.type === 'figure') {
    return el.alt_text || 'Figure (Image)';
  }

  // For Formula
  if (tag === 'FORMULA' || el.type === 'formula') {
    return el.content || el.alt_text || 'Formula';
  }

  // If node has kids, concatenate a preview of text
  if (el.kids && el.kids.length > 0) {
    if (tag === 'LBL' || el.type === 'list_label') {
      return '';
    }
    let combined = '';
    for (const kid of el.kids) {
      const kt = getElementSummaryText(kid);
      if (kt && !kt.startsWith('List (') && !kt.startsWith('Table (')) {
        combined += (combined ? ' ' : '') + kt;
        if (combined.length > 120) break;
      }
    }
    if (combined) return combined;
    return `${el.kids.length} items`;
  }

  return '';
}

function renderElementTreeNode(el, pNum, container) {
  if ((el.tag || '').toUpperCase() === 'ARTIFACT' || (el.type || '').toLowerCase() === 'artifact') return;

  const nodeEl = document.createElement('div');
  nodeEl.className = 'tree-node';
  nodeEl.id = `tree-node-${elKey}`;
  nodeEl.dataset.page = pNum;

  const itemEl = document.createElement('div');
  itemEl.className = 'tree-item';

  const tagLabel = getTagDisplayLabel(el);
  const colorClass = getTagColorClass(el);
  const isLbl = (tagLabel === 'Lbl' || (el.tag || '').toUpperCase() === 'LBL' || el.type === 'list_label');
  const summaryText = getElementSummaryText(el);
  const displayContent = (isLbl && el.kids && el.kids.length > 0) ? '' : (summaryText || (el.kids && el.kids.length > 0 ? `${el.kids.length} items` : 'Content'));
  const tooltipText = (el.actual_text && el.actual_text.trim() && el.actual_text.trim() !== displayContent)
    ? `${displayContent} (ActualText: ${el.actual_text.trim()})`
    : displayContent;

  itemEl.innerHTML = `
    <span class="tag-badge tag-${colorClass}">&lt;${tagLabel}&gt;</span>
    ${displayContent ? `<span class="tree-content" title="${escapeHtml(tooltipText)}">${escapeHtml(displayContent)}</span>` : ''}
  `;

  itemEl.addEventListener('mouseenter', () => highlightElement(elKey));
  itemEl.addEventListener('mouseleave', () => unhighlightElement(elKey));
  itemEl.addEventListener('click', (e) => {
    e.stopPropagation();
    scrollToBBox(elKey, pNum);
  });

  nodeEl.appendChild(itemEl);

  if (el.kids && el.kids.length > 0) {
    const childrenContainer = document.createElement('div');
    childrenContainer.className = 'tree-children';
    el.kids.forEach(kid => renderElementTreeNode(kid, pNum, childrenContainer));
    nodeEl.appendChild(childrenContainer);
  }

  container.appendChild(nodeEl);
}

// --------------------------------------------------------------------------
// Page-Anchored Synchronized Scrolling (Left <-> Right)
// --------------------------------------------------------------------------
let activePanel = null; // 'left' | 'right' | null
let idleTimer = null;

function setActivePanel(panel, delay = 250) {
  activePanel = panel;
  if (idleTimer) clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    activePanel = null;
  }, delay);
}

function setupSynchronizedScrolling() {

  // Pointer & Wheel listeners to track which panel user is interacting with
  pdfScrollContainer.addEventListener('mouseenter', () => { activePanel = 'left'; });
  pdfScrollContainer.addEventListener('wheel', () => { setActivePanel('left'); }, { passive: true });
  pdfScrollContainer.addEventListener('pointerdown', () => { setActivePanel('left'); });
  pdfScrollContainer.addEventListener('touchstart', () => { setActivePanel('left'); }, { passive: true });

  tagsScrollContainer.addEventListener('mouseenter', () => { activePanel = 'right'; });
  tagsScrollContainer.addEventListener('wheel', () => { setActivePanel('right'); }, { passive: true });
  tagsScrollContainer.addEventListener('pointerdown', () => { setActivePanel('right'); });
  tagsScrollContainer.addEventListener('touchstart', () => { setActivePanel('right'); }, { passive: true });

  function syncScroll(sourceContainer, targetContainer, sourcePrefix, targetPrefix) {
    if (!syncScrollEnabled) return;
    if (!currentResults || !currentResults.doc_json) return;
    const pages = currentResults.doc_json.pages || [];
    if (pages.length === 0) return;

    const scrollTop = sourceContainer.scrollTop;
    const maxSource = sourceContainer.scrollHeight - sourceContainer.clientHeight;
    const maxTarget = targetContainer.scrollHeight - targetContainer.clientHeight;

    if (maxSource <= 0 || maxTarget <= 0) return;

    if (scrollTop <= 5) {
      targetContainer.scrollTop = 0;
      return;
    }
    if (scrollTop >= maxSource - 8) {
      targetContainer.scrollTop = maxTarget;
      return;
    }

    // Match based on card offsetTop positions
    let targetScrollTop = null;
    for (let i = 0; i < pages.length; i++) {
      const pNum = pages[i].page_number;
      const sEl = document.getElementById(`${sourcePrefix}-${pNum}`);
      const tEl = document.getElementById(`${targetPrefix}-${pNum}`);
      if (!sEl || !tEl) continue;

      const sTop = sEl.offsetTop;
      const sH = Math.max(1, sEl.offsetHeight);

      if (scrollTop >= sTop && (scrollTop < sTop + sH || i === pages.length - 1)) {
        const progress = Math.max(0, Math.min(1, (scrollTop - sTop) / sH));
        targetScrollTop = tEl.offsetTop + progress * tEl.offsetHeight;
        break;
      }
    }

    if (targetScrollTop !== null) {
      targetContainer.scrollTop = Math.round(targetScrollTop);
    } else {
      targetContainer.scrollTop = Math.round((scrollTop / maxSource) * maxTarget);
    }
  }

  pdfScrollContainer.addEventListener('scroll', () => {
    if (!syncScrollEnabled) return;
    // Discard programmatic scrolls triggered by right panel
    if (activePanel === 'right') return;
    setActivePanel('left');
    syncScroll(pdfScrollContainer, tagsScrollContainer, 'page-card', 'tags-page-card');
  });

  tagsScrollContainer.addEventListener('scroll', () => {
    if (!syncScrollEnabled) return;
    // Discard programmatic scrolls triggered by left panel
    if (activePanel === 'left') return;
    setActivePanel('right');
    syncScroll(tagsScrollContainer, pdfScrollContainer, 'tags-page-card', 'page-card');
  });
}

function getTagDisplayLabel(el) {
  if (el.tag) return el.tag;
  const t = el.type || '';
  if (t === 'heading') return `H${el.heading_level || 1}`;
  if (t === 'paragraph') return 'P';
  if (t === 'formula') return 'Formula';
  if (t === 'list') return 'L';
  if (t === 'list_item') return 'LI';
  if (t === 'list_label') return 'Lbl';
  if (t === 'list_body') return 'LBody';
  if (t === 'figure') return 'Figure';
  if (t === 'table') return 'Table';
  if (t === 'table_row') return 'TR';
  if (t === 'table_header_cell') return 'TH';
  if (t === 'table_data_cell') return 'TD';
  if (t === 'span') return 'Span';
  return t ? t.toUpperCase() : 'TAG';
}

function getTagColorClass(el) {
  const tg = (el.tag || '').toUpperCase();
  if (tg.startsWith('H') && tg.length === 2 && !isNaN(tg[1])) {
    return tg.toLowerCase();
  }
  if (tg === 'P') return 'p';
  if (tg === 'FORMULA') return 'formula';
  if (tg === 'L' || tg === 'LI' || tg === 'LBL' || tg === 'LBODY') return 'li';
  if (tg === 'FIGURE') return 'figure';
  if (tg === 'TABLE' || tg === 'TR' || tg === 'TH' || tg === 'TD') return 'table';
  if (tg === 'SPAN') return 'span';

  const t = el.type || '';
  if (t === 'heading') return `h${el.heading_level || 1}`;
  if (t === 'paragraph') return 'p';
  if (t === 'formula') return 'formula';
  if (t === 'list' || t === 'list_item' || t === 'list_label' || t === 'list_body') return 'li';
  if (t === 'figure') return 'figure';
  if (t === 'table' || t === 'table_row' || t === 'table_header_cell' || t === 'table_data_cell') return 'table';
  if (t === 'span') return 'span';
  return 'p';
}

function getTagStrokeColor(el) {
  const label = getTagDisplayLabel(el).toUpperCase();
  if (label === 'H1') return '#f59e0b';
  if (label === 'H2') return '#3b82f6';
  if (label === 'H3' || label.startsWith('H')) return '#06b6d4';
  if (label === 'P') return '#818cf8';
  if (label === 'FORMULA') return '#ec4899';
  if (label === 'L' || label === 'LI' || label === 'LBL' || label === 'LBODY') return '#10b981';
  if (label === 'FIGURE') return '#f43f5e';
  if (label === 'TABLE' || label === 'TR' || label === 'TH' || label === 'TD') return '#fb923c';
  if (label === 'SPAN') return '#a855f7';
  return '#94a3b8';
}

function clearAllSelections() {
  document.querySelectorAll('.tree-item.selected-tree-item').forEach(el => {
    el.classList.remove('selected-tree-item');
    el.classList.remove('highlighted');
  });
  document.querySelectorAll('.bbox-rect.selected-bbox').forEach(b => {
    b.classList.remove('selected-bbox');
    b.style.strokeWidth = '1.5';
    b.style.fill = 'rgba(99, 102, 241, 0.12)';
  });
}

function getResolvedBBox(elKey) {
  let bbox = document.getElementById(`bbox-node-${elKey}`);
  if (!bbox && elKey.endsWith('-P')) {
    bbox = document.getElementById(`bbox-node-${elKey.slice(0, -2)}`);
  }
  return bbox;
}

function highlightElement(elKey) {
  const bbox = getResolvedBBox(elKey);
  if (bbox && !bbox.classList.contains('selected-bbox')) {
    bbox.style.strokeWidth = '3.5';
    bbox.style.fill = 'rgba(99, 102, 241, 0.35)';
  }
  const treeNode = document.getElementById(`tree-node-${elKey}`);
  if (treeNode) {
    const treeItem = treeNode.querySelector('.tree-item');
    if (treeItem && !treeItem.classList.contains('selected-tree-item')) {
      treeItem.classList.add('highlighted');
    }
  }
}

function unhighlightElement(elKey) {
  const bbox = getResolvedBBox(elKey);
  if (bbox && !bbox.classList.contains('selected-bbox')) {
    bbox.style.strokeWidth = '1.5';
    bbox.style.fill = 'rgba(99, 102, 241, 0.12)';
  }
  const treeNode = document.getElementById(`tree-node-${elKey}`);
  if (treeNode) {
    const treeItem = treeNode.querySelector('.tree-item');
    if (treeItem && !treeItem.classList.contains('selected-tree-item')) {
      treeItem.classList.remove('highlighted');
    }
  }
}

function scrollToTreeNode(elKey) {
  setActivePanel('left', 800);
  clearAllSelections();

  const nodeEl = document.getElementById(`tree-node-${elKey}`);
  if (nodeEl && tagsScrollContainer) {
    const containerRect = tagsScrollContainer.getBoundingClientRect();
    const nodeRect = nodeEl.getBoundingClientRect();
    const targetScrollTop = tagsScrollContainer.scrollTop + (nodeRect.top - containerRect.top) - (containerRect.height / 2) + (nodeRect.height / 2);
    tagsScrollContainer.scrollTo({
      top: Math.max(0, targetScrollTop),
      behavior: 'smooth'
    });
    const treeItem = nodeEl.querySelector('.tree-item');
    if (treeItem) {
      treeItem.classList.add('selected-tree-item');
      treeItem.classList.add('highlighted');
    }
  }

  const bbox = getResolvedBBox(elKey);
  if (bbox) {
    bbox.classList.add('selected-bbox');
    bbox.style.strokeWidth = '3.5';
    bbox.style.fill = 'rgba(99, 102, 241, 0.45)';
  }
}

function scrollToBBox(elKey, pageNum) {
  setActivePanel('right', 800);
  clearAllSelections();

  const bbox = getResolvedBBox(elKey);
  if (bbox && pdfScrollContainer) {
    const containerRect = pdfScrollContainer.getBoundingClientRect();
    const bboxRect = bbox.getBoundingClientRect();
    const targetScrollTop = pdfScrollContainer.scrollTop + (bboxRect.top - containerRect.top) - (containerRect.height / 2) + (bboxRect.height / 2);
    pdfScrollContainer.scrollTo({
      top: Math.max(0, targetScrollTop),
      behavior: 'smooth'
    });
    bbox.classList.add('selected-bbox');
    bbox.style.strokeWidth = '3.5';
    bbox.style.fill = 'rgba(99, 102, 241, 0.45)';
  } else {
    const pageCard = document.getElementById(`page-card-${pageNum}`);
    if (pageCard && pdfScrollContainer) {
      const containerRect = pdfScrollContainer.getBoundingClientRect();
      const cardRect = pageCard.getBoundingClientRect();
      const targetScrollTop = pdfScrollContainer.scrollTop + (cardRect.top - containerRect.top) - 20;
      pdfScrollContainer.scrollTo({
        top: Math.max(0, targetScrollTop),
        behavior: 'smooth'
      });
    }
  }

  const nodeEl = document.getElementById(`tree-node-${elKey}`);
  if (nodeEl) {
    const treeItem = nodeEl.querySelector('.tree-item');
    if (treeItem) {
      treeItem.classList.add('selected-tree-item');
      treeItem.classList.add('highlighted');
    }
  }
}

// Reset / New Document
document.getElementById('new-doc-btn').addEventListener('click', () => {
  currentTaskId = null;
  currentResults = null;
  fileInput.value = '';
  resultsCard.style.display = 'none';
  progressCard.style.display = 'none';
  uploadCard.style.display = 'block';
  pdfScrollContainer.innerHTML = '';
  tagsScrollContainer.innerHTML = '';
});

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

window.addEventListener('DOMContentLoaded', () => {
  checkGeminiStatus();
  const params = new URLSearchParams(window.location.search);
  const taskFromParam = params.get('task') || window.location.hash.replace('#', '').trim();
  if (taskFromParam) {
    currentTaskId = taskFromParam;
    uploadCard.style.display = 'none';
    progressCard.style.display = 'block';
    progressStageMsg.innerText = 'Loading document tags...';
    checkStatus();
  }
});

// --------------------------------------------------------------------------
// Gemini AI Key Modal & Integration
// --------------------------------------------------------------------------
const geminiKeyBtn = document.getElementById('gemini-key-btn');
const geminiBtnText = document.getElementById('gemini-btn-text');
const geminiStatusPill = document.getElementById('gemini-status-pill');

const summaryGeminiBtn = document.getElementById('summary-gemini-btn');
const summaryGeminiBtnText = document.getElementById('summary-gemini-btn-text');
const summaryGeminiStatusPill = document.getElementById('summary-gemini-status-pill');

const geminiModal = document.getElementById('gemini-modal');
const closeGeminiModal = document.getElementById('close-gemini-modal');
const cancelGeminiBtn = document.getElementById('cancel-gemini-btn');
const saveGeminiKeyBtn = document.getElementById('save-gemini-key-btn');
const removeGeminiKeyBtn = document.getElementById('remove-gemini-key-btn');
const geminiApiKeyInput = document.getElementById('gemini-api-key-input');
const toggleKeyVisibility = document.getElementById('toggle-key-visibility');
const geminiStatusMsg = document.getElementById('gemini-status-msg');

let geminiEnabled = false;

async function checkGeminiStatus() {
  try {
    const res = await fetch('/api/gemini/status');
    const data = await res.json();
    geminiEnabled = data.enabled;
    updateGeminiUI(data);
    if (!geminiEnabled && !currentTaskId) {
      setTimeout(() => {
        openGeminiModalDialog();
        showGeminiMsg('Google Gemini API Key is mandatory. Please enter and verify your key to proceed.', 'error');
      }, 300);
    }
  } catch (err) {
    console.error('Failed to check Gemini status:', err);
  }
}

function updateGeminiUI(data) {
  const isOnline = !!data.enabled;
  const count = data.key_count || (isOnline ? 1 : 0);
  const label = isOnline ? (count > 1 ? `Gemini (${count} Keys)` : 'Gemini AI') : 'Gemini API Key';
  const pillText = isOnline ? 'Active' : 'Offline';
  const pillClass = isOnline ? 'gemini-status-pill online' : 'gemini-status-pill offline';

  if (geminiKeyBtn) {
    geminiKeyBtn.classList.toggle('active', isOnline);
    if (geminiBtnText) geminiBtnText.innerText = isOnline ? 'Gemini AI' : 'Add Gemini Key';
    if (geminiStatusPill) {
      geminiStatusPill.innerText = pillText;
      geminiStatusPill.className = pillClass;
    }
  }

  if (summaryGeminiBtn) {
    summaryGeminiBtn.classList.toggle('active', isOnline);
    if (summaryGeminiBtnText) summaryGeminiBtnText.innerText = label;
    if (summaryGeminiStatusPill) {
      summaryGeminiStatusPill.innerText = pillText;
      summaryGeminiStatusPill.className = pillClass;
    }
  }

  if (removeGeminiKeyBtn) removeGeminiKeyBtn.style.display = isOnline ? 'block' : 'none';
}

function openGeminiModalDialog() {
  geminiModal.classList.add('open');
  geminiStatusMsg.style.display = 'none';
  geminiStatusMsg.className = 'gemini-status-msg';
  geminiApiKeyInput.value = '';
  setTimeout(() => {
    geminiApiKeyInput.focus();
  }, 50);
}

function closeGeminiModalDialog() {
  geminiModal.classList.remove('open');
  geminiStatusMsg.style.display = 'none';
}

if (geminiKeyBtn) geminiKeyBtn.addEventListener('click', openGeminiModalDialog);
if (summaryGeminiBtn) summaryGeminiBtn.addEventListener('click', openGeminiModalDialog);
if (closeGeminiModal) closeGeminiModal.addEventListener('click', closeGeminiModalDialog);
if (cancelGeminiBtn) cancelGeminiBtn.addEventListener('click', closeGeminiModalDialog);

if (geminiModal) {
  geminiModal.addEventListener('click', (e) => {
    if (e.target === geminiModal) closeGeminiModalDialog();
  });
}

if (toggleKeyVisibility) {
  toggleKeyVisibility.addEventListener('click', () => {
    const isPass = geminiApiKeyInput.type === 'password';
    geminiApiKeyInput.type = isPass ? 'text' : 'password';
    toggleKeyVisibility.innerText = isPass ? '🔒' : '👁️';
  });
}

if (saveGeminiKeyBtn) {
  saveGeminiKeyBtn.addEventListener('click', async () => {
    const key = geminiApiKeyInput.value.trim();
    if (!key) {
      showGeminiMsg('Please enter or paste your Gemini API key.', 'error');
      return;
    }

    saveGeminiKeyBtn.disabled = true;
    saveGeminiKeyBtn.innerHTML = '<span class="spinner"></span> <span>Verifying...</span>';
    showGeminiMsg('Testing connection to Google Gemini API...', '');

    try {
      const res = await fetch('/api/gemini/key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: key })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || data.message || 'Verification failed.');
      }

      showGeminiMsg(data.message || 'Connected successfully!', 'success');
      updateGeminiUI(data.status);
      setTimeout(() => {
        closeGeminiModalDialog();
      }, 1200);
    } catch (err) {
      showGeminiMsg(err.message, 'error');
    } finally {
      saveGeminiKeyBtn.disabled = false;
      saveGeminiKeyBtn.innerHTML = '<span>Connect &amp; Save</span>';
    }
  });
}

if (removeGeminiKeyBtn) {
  removeGeminiKeyBtn.addEventListener('click', async () => {
    if (!confirm('Are you sure you want to remove the Gemini API key?')) return;
    try {
      const res = await fetch('/api/gemini/key', { method: 'DELETE' });
      const data = await res.json();
      updateGeminiUI(data.status);
      closeGeminiModalDialog();
    } catch (err) {
      alert('Failed to remove key: ' + err.message);
    }
  });
}

function showGeminiMsg(msg, type) {
  geminiStatusMsg.innerText = msg;
  geminiStatusMsg.className = `gemini-status-msg ${type}`;
  geminiStatusMsg.style.display = 'block';
}

// Check and sync Gemini AI status on page load
checkGeminiStatus();
