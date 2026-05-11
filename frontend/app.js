(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    form: $('research-form'),
    input: $('company-input'),
    suggestions: $('suggestions'),
    btn: $('submit-btn'),
    status: $('status'),
    streamWrap: $('stream-wrap'),
    streamToggle: $('stream-toggle'),
    streamHint: document.querySelector('.stream-hint'),
    stream: $('stream'),
    agentText: $('agent-text'),
    finalWrap: $('final-wrap'),
    finalToggle: $('final-toggle'),
    finalHint: document.querySelector('.final-hint'),
    final: $('final'),
    scrollPill: $('scroll-pill'),
  };

  const STATE = {
    es: null,
    runId: null,
    toolCards: new Map(),   // tool_use_id -> { card, inputBuf }
    agentTextBuf: '',
    userScrolledUp: false,
  };

  const EVENT_TYPES = [
    'started', 'iteration', 'agent_text',
    'tool_start', 'tool_input_delta', 'tool_input_done',
    'tool_executing', 'tool_result',
    'source_try', 'source_ok', 'source_miss', 'source_error', 'source_cache_hit',
    'turn_done', 'retry', 'context_trim', 'error', 'done',
  ];

  function setStatus(text, kind = 'running') {
    els.status.textContent = text;
    els.status.className = `status status-${kind}`;
  }

  function resetUI() {
    if (STATE.es) { STATE.es.close(); STATE.es = null; }
    STATE.toolCards.clear();
    STATE.agentTextBuf = '';
    STATE.userScrolledUp = false;
    els.stream.innerHTML = '';
    els.agentText.textContent = '';
    els.final.innerHTML = '';
    els.finalWrap.hidden = true;
    els.streamWrap.classList.remove('collapsed');
    els.streamToggle.classList.remove('collapsed');
    els.finalWrap.classList.remove('collapsed');
    els.finalToggle.classList.remove('collapsed');
    if (els.streamHint) els.streamHint.textContent = '';
    if (els.finalHint) els.finalHint.textContent = '';
    els.scrollPill.hidden = true;
  }

  // Manual toggle of the live-stream section
  els.streamToggle.addEventListener('click', () => {
    els.streamWrap.classList.toggle('collapsed');
    els.streamToggle.classList.toggle('collapsed');
  });

  // Manual toggle of the final portfolio section
  els.finalToggle.addEventListener('click', () => {
    els.finalWrap.classList.toggle('collapsed');
    els.finalToggle.classList.toggle('collapsed');
  });

  // ───── Sticky-to-bottom scroll behaviour ─────
  // Auto-scroll only when the user is already near the bottom. Once they
  // scroll up to read earlier content, we stop yanking the viewport.
  // A floating pill appears to let them re-enable follow-mode.
  const NEAR_BOTTOM_PX = 80;
  function isNearBottom() {
    return (window.innerHeight + window.scrollY) >= (document.documentElement.scrollHeight - NEAR_BOTTOM_PX);
  }
  window.addEventListener('scroll', () => {
    const near = isNearBottom();
    STATE.userScrolledUp = !near;
    if (near) els.scrollPill.hidden = true;
  }, { passive: true });
  els.scrollPill.addEventListener('click', () => {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
    STATE.userScrolledUp = false;
    els.scrollPill.hidden = true;
  });
  function followIfPossible() {
    if (STATE.userScrolledUp) {
      // We have new content but user is reading above — surface the pill instead of yanking.
      if (STATE.es) els.scrollPill.hidden = false;
      return;
    }
    // Defer to next frame so the layout has settled.
    requestAnimationFrame(() => {
      window.scrollTo({ top: document.documentElement.scrollHeight });
    });
  }

  function makeToolCard(id, name) {
    const card = document.createElement('div');
    card.className = 'tool-card';
    card.dataset.id = id;
    card.innerHTML = `
      <div class="tool-header">
        <span class="tool-name">${name}</span>
        <span class="tool-status status-starting">starting</span>
      </div>
      <div class="sources-strip" hidden></div>
      <details class="tool-details" open>
        <summary>input</summary>
        <pre class="tool-input"></pre>
      </details>
      <details class="tool-details">
        <summary>result <span class="tool-size"></span></summary>
        <pre class="tool-result"></pre>
      </details>
    `;
    return card;
  }

  function updateSourceBadge(toolId, source, status, reason) {
    const t = STATE.toolCards.get(toolId);
    if (!t) return;
    const strip = t.card.querySelector('.sources-strip');
    strip.hidden = false;
    let badge = strip.querySelector(`[data-source="${CSS.escape(source)}"]`);
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'src-badge';
      badge.dataset.source = source;
      badge.innerHTML = `<span class="src-name">${source}</span><span class="src-state"></span>`;
      strip.appendChild(badge);
    }
    badge.classList.remove('src-try', 'src-ok', 'src-miss', 'src-error', 'src-cache');
    badge.classList.add(`src-${status}`);
    const stateEl = badge.querySelector('.src-state');
    stateEl.textContent = ({
      try: '⋯',
      ok: '✓',
      miss: '⊘',
      error: '✗',
      cache: '⚡',
    })[status] || status;
    if (reason) badge.title = reason;
  }

  function setToolStatus(card, status) {
    const el = card.querySelector('.tool-status');
    el.textContent = status;
    el.className = `tool-status status-${status}`;
  }

  function commitAgentTextBlock() {
    if (!STATE.agentTextBuf.trim()) {
      els.agentText.textContent = '';
      STATE.agentTextBuf = '';
      return;
    }
    const block = document.createElement('div');
    block.className = 'agent-text-block';
    block.textContent = STATE.agentTextBuf;
    els.stream.appendChild(block);
    STATE.agentTextBuf = '';
    els.agentText.textContent = '';
  }

  const handlers = {
    started(d) {
      STATE.runId = d.run_id;
      setStatus(`run ${d.run_id} · model ${d.model} · researching "${d.company}"`, 'running');
    },
    iteration(d) {
      setStatus(`iteration ${d.n}/${d.max}`, 'running');
    },
    agent_text(d) {
      STATE.agentTextBuf += d.text;
      els.agentText.textContent = STATE.agentTextBuf;
      followIfPossible();
    },
    tool_start(d) {
      const card = makeToolCard(d.id, d.name);
      STATE.toolCards.set(d.id, { card, inputBuf: '' });
      els.stream.appendChild(card);
      followIfPossible();
    },
    tool_input_delta(d) {
      const t = STATE.toolCards.get(d.id);
      if (!t) return;
      t.inputBuf += d.partial_json || '';
      t.card.querySelector('.tool-input').textContent = t.inputBuf;
    },
    tool_input_done(_d) { /* final input arrives in tool_executing */ },
    source_try(d)   { updateSourceBadge(d.tool_id, d.source, 'try'); },
    source_ok(d)    { updateSourceBadge(d.tool_id, d.source, 'ok', d.note || ''); },
    source_miss(d)  { updateSourceBadge(d.tool_id, d.source, 'miss', d.reason || ''); },
    source_error(d) { updateSourceBadge(d.tool_id, d.source, 'error', d.reason || ''); },
    source_cache_hit(d) { updateSourceBadge(d.tool_id, d.source, 'cache', d.endpoint || ''); },
    tool_executing(d) {
      const t = STATE.toolCards.get(d.id);
      if (!t) return;
      t.card.querySelector('.tool-input').textContent = JSON.stringify(d.input, null, 2);
      setToolStatus(t.card, 'running');
    },
    tool_result(d) {
      const t = STATE.toolCards.get(d.id);
      if (!t) return;
      setToolStatus(t.card, d.is_error ? 'error' : 'done');
      t.card.querySelector('.tool-result').textContent = d.preview || '(empty)';
      t.card.querySelector('.tool-size').textContent = `· ${d.size?.toLocaleString?.() ?? d.size} chars`;
    },
    turn_done(d) {
      commitAgentTextBlock();
      const usage = d.usage ? `· in=${d.usage.input_tokens} out=${d.usage.output_tokens}` : '';
      setStatus(`turn done · stop=${d.stop_reason} ${usage}`, 'running');
    },
    retry(d) {
      setStatus(`retrying ${d.target} (attempt ${d.attempt})`, 'retry');
    },
    context_trim(d) {
      // Surface that we archived older tool results so the user knows what happened.
      // Render as a compact info banner inside the stream.
      const card = document.createElement('div');
      card.className = 'context-trim-banner';
      card.innerHTML = `
        <strong>⚠ context trimmed</strong>
        archived <strong>${d.trimmed_count}</strong> older tool result${d.trimmed_count === 1 ? '' : 's'}
        (~${(d.bytes_freed_approx / 1024).toFixed(1)} KB freed)
        at <strong>${d.pct_used_before}%</strong> of ${d.window.toLocaleString()}-token window.
        Full archive preserved in <code>runs/${STATE.runId}/archive.json</code>.
      `;
      els.stream.appendChild(card);
      followIfPossible();
    },
    error(d) {
      setStatus(`error: ${d.message}`, 'error');
    },
    done(d) {
      commitAgentTextBlock();
      if (d.aborted) {
        setStatus(`aborted after ${d.iterations} iterations · ${d.error || 'fatal error'}`, 'error');
      } else {
        setStatus(`done · ${d.iterations} iterations · ${d.last_stop_reason}`, 'done');
      }
      els.btn.disabled = false;
      renderFinal(d);
      if (STATE.es) { STATE.es.close(); STATE.es = null; }
    },
  };

  async function renderFinal(d) {
    els.finalWrap.hidden = false;
    const portfolioUrl = `/outputs/${d.run_id}/portfolio.md`;
    const xlsxName = d.excel_path ? d.excel_path.split('/').pop() : null;
    const xlsxUrl = xlsxName ? `/outputs/${d.run_id}/${xlsxName}` : null;

    let md = '';
    try {
      const r = await fetch(portfolioUrl);
      if (r.ok) md = await r.text();
    } catch (_) { /* ignore */ }

    const actions = `
      <div class="actions">
        <a href="${portfolioUrl}" target="_blank" rel="noopener">📄 portfolio.md</a>
        ${xlsxUrl ? `<a href="${xlsxUrl}" download>📊 ${xlsxName}</a>` : '<span class="muted">no Excel generated</span>'}
      </div>
    `;

    // Surface any empty Excel sheets so the user knows what was thin / unavailable.
    let emptyBanner = '';
    if (d.excel_empty_sheets && d.excel_empty_sheets.length) {
      const sheets = d.excel_empty_sheets.map(s => `<code>${s}</code>`).join(', ');
      const totalSheets = (d.excel_sheets || []).length;
      emptyBanner = `
        <div class="empty-sheets-banner">
          ⚠ ${d.excel_empty_sheets.length} of ${totalSheets} Excel sheet${totalSheets === 1 ? '' : 's'} ended up empty: ${sheets}.
          The corresponding data was either not provided by the agent or unavailable on the free tier of the underlying source.
        </div>
      `;
    }

    const body = md
      ? `<article>${window.marked ? window.marked.parse(md) : `<pre>${md}</pre>`}</article>`
      : `<p class="muted">Portfolio file not readable.</p>`;
    els.final.innerHTML = actions + emptyBanner + body;

    // Auto-collapse BOTH sections so the page is compact post-run.
    // Users can expand either with a click.
    els.streamWrap.classList.add('collapsed');
    els.streamToggle.classList.add('collapsed');
    const toolCount = STATE.toolCards.size;
    if (els.streamHint) els.streamHint.textContent = `· ${toolCount} tool call${toolCount === 1 ? '' : 's'} · click to expand`;

    els.finalWrap.classList.add('collapsed');
    els.finalToggle.classList.add('collapsed');
    const portfolioBytes = (md && md.length) || 0;
    const xlsxLabel = xlsxName ? ` · ${xlsxName}` : '';
    if (els.finalHint) els.finalHint.textContent = `· ${(portfolioBytes / 1024).toFixed(1)} KB${xlsxLabel} · click to expand`;

    // Don't yank the viewport — the user might still be reading earlier output.
    // Just hide the scroll-follow pill since the run is over.
    els.scrollPill.hidden = true;
  }

  function start(company) {
    resetUI();
    setStatus(`connecting…`, 'running');
    els.btn.disabled = true;

    const url = `/research?company=${encodeURIComponent(company)}`;
    const es = new EventSource(url);
    STATE.es = es;

    for (const t of EVENT_TYPES) {
      es.addEventListener(t, (ev) => {
        try { handlers[t]?.(JSON.parse(ev.data)); }
        catch (err) { console.error('handler error', t, err); }
      });
    }
    es.onerror = () => {
      // EventSource will retry automatically. We only show an error if we haven't received `done`.
      if (els.status.classList.contains('status-done')) return;
      setStatus(`stream interrupted — retrying…`, 'retry');
    };
  }

  // ───── Typeahead: search SEC EDGAR ticker index ─────
  const TYPEAHEAD = {
    debounceMs: 150,
    timer: null,
    activeIndex: -1,
    suggestions: [],
    selectedTicker: null,
    abortCtrl: null,
  };

  function hideSuggestions() {
    els.suggestions.hidden = true;
    els.suggestions.innerHTML = '';
    TYPEAHEAD.suggestions = [];
    TYPEAHEAD.activeIndex = -1;
  }

  function renderSuggestions(items) {
    if (!items.length) {
      els.suggestions.innerHTML = `<li class="suggestion empty">No US public match — submit anyway to search globally / handle as private co.</li>`;
      els.suggestions.hidden = false;
      return;
    }
    els.suggestions.innerHTML = items.map((s, i) => `
      <li class="suggestion${i === TYPEAHEAD.activeIndex ? ' active' : ''}" data-index="${i}" role="option">
        <span class="ticker">${s.ticker}</span>
        <span class="name">${s.name}</span>
        <span class="cik">CIK ${s.cik}</span>
      </li>
    `).join('');
    els.suggestions.hidden = false;
  }

  function updateActive() {
    [...els.suggestions.querySelectorAll('.suggestion')].forEach((el, i) => {
      el.classList.toggle('active', i === TYPEAHEAD.activeIndex);
    });
    const active = els.suggestions.querySelector('.suggestion.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
  }

  function pickSuggestion(i) {
    const s = TYPEAHEAD.suggestions[i];
    if (!s) return;
    els.input.value = `${s.ticker} — ${s.name}`;
    TYPEAHEAD.selectedTicker = s.ticker;
    hideSuggestions();
  }

  async function fetchSuggestions(q) {
    if (TYPEAHEAD.abortCtrl) TYPEAHEAD.abortCtrl.abort();
    TYPEAHEAD.abortCtrl = new AbortController();
    try {
      const r = await fetch(`/symbols/search?q=${encodeURIComponent(q)}&limit=10`, { signal: TYPEAHEAD.abortCtrl.signal });
      if (!r.ok) return [];
      const d = await r.json();
      return d.results || [];
    } catch (e) {
      if (e.name !== 'AbortError') console.error('search failed', e);
      return [];
    }
  }

  els.input.addEventListener('input', () => {
    TYPEAHEAD.selectedTicker = null;  // user is editing; invalidate selection
    clearTimeout(TYPEAHEAD.timer);
    const q = els.input.value.trim();
    if (q.length < 1) { hideSuggestions(); return; }
    // Strip the " — name" suffix if user is editing a previously selected item
    const editQuery = q.split(' — ')[0].trim();
    TYPEAHEAD.timer = setTimeout(async () => {
      TYPEAHEAD.suggestions = await fetchSuggestions(editQuery);
      TYPEAHEAD.activeIndex = TYPEAHEAD.suggestions.length ? 0 : -1;
      renderSuggestions(TYPEAHEAD.suggestions);
    }, TYPEAHEAD.debounceMs);
  });

  els.input.addEventListener('keydown', (e) => {
    if (els.suggestions.hidden) return;
    if (e.key === 'ArrowDown') {
      TYPEAHEAD.activeIndex = Math.min(TYPEAHEAD.activeIndex + 1, TYPEAHEAD.suggestions.length - 1);
      updateActive();
      e.preventDefault();
    } else if (e.key === 'ArrowUp') {
      TYPEAHEAD.activeIndex = Math.max(TYPEAHEAD.activeIndex - 1, 0);
      updateActive();
      e.preventDefault();
    } else if (e.key === 'Enter' && TYPEAHEAD.activeIndex >= 0 && TYPEAHEAD.suggestions[TYPEAHEAD.activeIndex]) {
      pickSuggestion(TYPEAHEAD.activeIndex);
      e.preventDefault();
    } else if (e.key === 'Escape') {
      hideSuggestions();
    }
  });

  els.suggestions.addEventListener('click', (e) => {
    const li = e.target.closest('.suggestion');
    if (!li || li.classList.contains('empty')) return;
    const idx = parseInt(li.dataset.index, 10);
    if (!isNaN(idx)) pickSuggestion(idx);
  });

  // Hide suggestions on click outside
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-wrapper')) hideSuggestions();
  });

  els.form.addEventListener('submit', (e) => {
    e.preventDefault();
    // Prefer the selected ticker (deterministic) over the raw input.
    let company;
    if (TYPEAHEAD.selectedTicker) {
      company = TYPEAHEAD.selectedTicker;
    } else {
      // If raw input has " — name" suffix, strip it
      company = els.input.value.split(' — ')[0].trim();
    }
    if (!company) return;
    hideSuggestions();
    start(company);
  });
})();
