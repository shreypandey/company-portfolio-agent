# Portfolio Research Agent

A live-streaming company research agent. Submit a company name → the agent
orchestrates ~10 data sources via tool use on an LLM (Anthropic or Gemini),
streams every tool call to the browser, and emits a markdown portfolio + a
multi-sheet Excel workbook.

## What it produces

For each run, in `outputs/<run_id>/`:
- `portfolio.md` — overview, financials, historical performance, earnings track,
  analyst view, market position, competitors, ownership & insider activity,
  recent events, sector & macro context, risks (every non-trivial claim cited
  inline with the source name and URL)
- `<Company>_financials.xlsx` — up to 15 sheets: Overview, Income Statement
  (annual + quarterly), Balance Sheet (annual + quarterly), Cash Flow (annual +
  quarterly), Key Ratios History, Earnings Track, Analyst Coverage,
  Ownership & Insider, Peers Comparison, Macro Context, Source Log, Data Gaps

Plus, in `runs/<run_id>/`:
- `state.json` — full message history checkpointed after every tool turn
- `archive.json` — full original tool results when context-window archival fires

## Architecture

```
Browser ─GET /research?company=X─► FastAPI ──► Agent loop ──► LLM Client
   ▲                                  │              │            │
   │                                  │              ├─► 13 semantic tools
   │                                  │              │     each with internal
   │                                  │              │     source-chain fallback
   │                                  │              │
   └──── SSE event stream ────────────┘              ▼
        (started, iteration, agent_text,        runs/{id}/state.json
         tool_start, tool_input_delta,          runs/{id}/archive.json
         tool_executing, tool_result,           outputs/{id}/portfolio.md
         source_try/ok/miss/error/cache_hit,    outputs/{id}/<co>_financials.xlsx
         turn_done, retry, context_trim,
         error, done)
```

Single-process. One Agent class works against any LLM provider (factory in
`backend/llm/`). State on disk. No database.

## LLM providers

Switch via `LLM_PROVIDER` in `.env`. Same Agent loop for both — provider-specific
format conversion (Anthropic content blocks vs Gemini Parts, thought signatures
on Gemini, etc.) lives in `backend/llm/<provider>_client.py`.

| Provider | Default model | Context window | Notes |
|---|---|---|---|
| `anthropic` | `claude-sonnet-4-6` | 200K | full streaming + parallel tool calls |
| `gemini` | `gemini-3.1-pro-preview` | 1M | native Google `google-genai` SDK (the OpenAI-compat layer corrupts parallel tool calls during streaming) |

Tested and working on `gemini-3.1-pro-preview`, `gemini-3.1-flash-lite`,
`gemini-2.5-pro`, and `claude-sonnet-4-6`.

## The 13 LLM-facing tools

The model never sees raw APIs. Each tool is **semantic** — it expresses
*what the LLM wants to know*, and internally tries a chain of sources, returning
a uniform `{ok, data, sources_used, sources_attempted}` envelope.

| Tool | Primary source | Fallback chain |
|---|---|---|
| `get_company_overview` | Finnhub profile+quote | yfinance .info → Wikipedia (narrative) |
| `get_financial_statements` | yfinance (clean, global) | Finnhub `/stock/financials-reported` (raw XBRL) |
| `get_key_metrics_history` | Finnhub `/stock/metric` | yfinance .info snapshot |
| `get_earnings_history` | Finnhub `/stock/earnings` | Alpha Vantage `EARNINGS` |
| `get_analyst_coverage` | Finnhub `/stock/recommendation` | — (price targets paid-only) |
| `get_ownership_activity` | Finnhub insider transactions + sentiment | — (13F holders paid-only) |
| `get_competitors` | Finnhub `/stock/peers` + enrich each in parallel | Brave web search |
| `get_recent_news_and_events` | Tavily news + Finnhub news + AV NEWS_SENTIMENT + SEC 8-Ks (parallel) | Brave news (if no Tavily) |
| `get_filings_narrative` | SEC EDGAR + trafilatura | — |
| `get_macro_context` | FRED (sector-curated series) | — |
| `search_web` | Tavily (JS-rendered, LLM-tuned) | Brave web/news → Wikipedia opensearch |
| `fetch_page` | trafilatura (fast static HTML) | Tavily `/extract` (JS-rendered) |
| `build_excel_workbook` | openpyxl (local) | — |

## Resilience model

The assignment called this out specifically. Layer by layer:

- **Per-tool source chain**: each semantic tool tries sources in order. On a
  source error/miss, the chain logs it and moves on. Only when every source in
  the chain fails does the tool return `ok: false` — and even then with a hint
  telling the LLM what to try.
- **Per-source retry**: shared `request()` helper with 2-attempt exponential
  backoff on 408/425/5xx (including 522/524/529); 429 → `RateLimitError`, also
  retried. 404 → clean `SourceMiss` (not retried). Auth errors not retried.
- **LLM API retry**: Anthropic client uses `tenacity` 4-attempt retry on
  transient errors with exponential backoff + jitter, emitting a `retry` SSE
  event so the UI shows it. Gemini client relies on the `google-genai` SDK's
  built-in behavior.
- **Tool-call dedupe (Gemini)**: Gemini's streaming repeats some function calls
  across chunks. Deduped by `(name, args)` signature in `gemini_client`.
- **Finnhub rate limit**: in-process token bucket (60/min) prevents self-429s on
  the free tier when peer enrichment fans out 5+ profile calls in parallel.
- **Per-run HTTP cache**: `(source, endpoint, args)` keyed dict on `RunContext`.
  SEC ticker index, Finnhub profile, etc. fetched once even if multiple tools
  need them.
- **Ticker resolution is deterministic**: SEC EDGAR's `company_tickers.json`
  (~10K records, in-memory after first fetch) is the source of truth. Falls
  back to Finnhub `/search` for non-US. Drives both the typeahead and the
  agent's internal `_resolver`.
- **JS-page detection**: when trafilatura extracts < 200 chars from > 8KB of
  HTML (or zero chars), `page_fetcher` raises `SourceMiss` with a `js_suspected`
  signal. `fetch_page` automatically escalates to Tavily `/extract`.
- **Context window archival**: if input_tokens crosses 80% of the model's
  context window, older `tool_result` payloads get replaced with a 1-line
  summary referencing the on-disk archive. Last 4 (assistant, tool_result)
  pairs are preserved intact. Emits a `context_trim` event.
- **Loop runaway cap**: `MAX_ITERATIONS = 25`. System prompt explicitly tells
  the model "if a tool returns `ok: false`, do NOT retry the same call".
- **Crash recovery**: full message history written to
  `runs/<id>/state.json` after every tool turn. The disk is the truth.
- **SSE heartbeats**: `:ping` comments every 15s prevent proxy timeouts.
- **Graceful degradation everywhere**: if Excel generation fails, the markdown
  still ships. If Finnhub 403s a foreign ticker, the agent falls through to
  yfinance + Wikipedia + Tavily. If Tavily quota runs out, Brave takes over.
  No single failure aborts a run.
- **Fatal-error tagging**: if the LLM call dies terminally after retries, the
  agent emits `done` with `aborted: true, error: "..."` so the UI shows red,
  not green.

## UI features (frontend/)

Single page, vanilla JS, SSE consumer:

- **Typeahead** backed by `/symbols/search` (~10K SEC tickers, instant
  in-memory match). Arrow keys + Enter to select.
- **Live tool cards** for each tool call: name, status, JSON input, result
  preview (full result still goes to the LLM — 600-char cap is UI only).
- **Nested source badges** under each tool card showing `try → ok / miss /
  error / cache_hit` per underlying source.
- **Sticky-to-bottom scroll**: auto-follows new content only if you're already
  near the bottom. Scroll up to read earlier output, a floating
  "↓ new updates" pill appears.
- **Auto-collapse on `done`**: both Live Stream and Final Portfolio sections
  collapse with click-to-expand toggles, showing tool count + file size in the
  header.
- **Empty-sheets warning** on the portfolio: surfaces which Excel tabs ended up
  empty (gracefully — usually means the LLM had no data for that section).
- **Context-trim warning**: if the context-archival path fires, a banner shows
  count + bytes freed + link to `archive.json`.
- **Provider awareness**: status banner shows the active `provider · model`
  during each run.

## Setup

```bash
uv sync                # creates .venv and installs from uv.lock
cp .env.example .env   # then fill in keys
```

### Required keys (`.env`)

| Key | Used by |
|---|---|
| `LLM_API_KEY` | LLM provider key (Anthropic or Google AI Studio, depending on `LLM_PROVIDER`) |
| `LLM_PROVIDER` | `anthropic` or `gemini` |
| `LLM_MODEL` | e.g. `gemini-3.1-pro-preview` or `claude-sonnet-4-6` |
| `FINNHUB_API_KEY` | primary US data source — required |
| `TAVILY_API_KEY` | primary search + JS-extract — strongly recommended |
| `BRAVE_SEARCH_API_KEY` | fallback search |
| `ALPHAVANTAGE_API_KEY` | NEWS_SENTIMENT (25 calls/day cap) |
| `FRED_API_KEY` | macro context (free, unlimited) |
| `SEC_EDGAR_USER_AGENT` | required by SEC (your email is fine) |
| `FMP_API_KEY` | not actively used — FMP's free tier was killed Aug 2025 |

## Run

```bash
uv run python -m backend.main
# → http://127.0.0.1:8000
```

Open the URL, type a company name (typeahead resolves it), click Research.

## Try

| Category | Examples | What it exercises |
|---|---|---|
| Public US | `Apple`, `Nvidia`, `Microsoft`, `Salesforce` | Full pipeline — every tool succeeds, 12-sheet Excel |
| Global public | `Toyota` → `TM` (NYSE ADR), `ASML`, `Nestlé` | Foreign ticker resolution, yfinance fallback, partial SEC misses (foreign filers file 20-F not 10-K) |
| Private | `Stripe`, `OpenAI`, `Anthropic` | Heavy Tavily usage, Data Gaps sheet activates, narrative-first portfolio |

## Switching providers

```bash
# in .env:
LLM_PROVIDER="anthropic"          # or "gemini"
LLM_MODEL="claude-sonnet-4-6"     # or "gemini-3.1-pro-preview", "gemini-3.1-flash-lite", etc.
# then restart the server
```

The agent loop is provider-agnostic. Switching is `.env` + restart, no code change.

## Project layout

```
backend/
  agent.py              # Provider-agnostic ReAct loop + checkpoint + context archival
  config.py             # env vars + thresholds
  context.py            # RunContext (shared httpx, event sink, per-run cache)
  envelope.py           # Result envelope + SourceChain helper
  main.py               # FastAPI + SSE + /symbols/search + static frontend
  resilience.py         # (legacy decorator — current path uses chain.try_ instead)
  llm/                  # Provider abstraction
    base.py             # BaseLLMClient interface + FinalTurn
    anthropic_client.py # Anthropic streaming + tool_use mapping
    gemini_client.py    # Google native SDK + thought_signature handling
    __init__.py         # get_llm_client() factory
  sources/              # Raw API wrappers (never LLM-facing)
    _http.py            # shared request helper with retry/cache
    errors.py           # SourceError, SourceMiss, RateLimitError
    finnhub.py alpha_vantage.py fred.py tavily.py brave.py
    yahoo.py sec_edgar.py ticker_index.py wikipedia.py page_fetcher.py
    fmp.py              # kept for paid-tier upgrade path (currently dead)
  tools/                # 13 semantic LLM-facing tools
    _resolver.py        # SEC EDGAR-first ticker resolution
    overview.py statements.py metrics.py earnings.py
    analyst.py ownership.py competitors.py news_events.py
    filings.py macro.py search.py page.py excel.py
    registry.py         # schemas + dispatcher
frontend/
  index.html app.js styles.css
outputs/<run_id>/       # portfolio.md + .xlsx (and the source markdown the agent wrote)
runs/<run_id>/          # state.json checkpoint + archive.json (when context-trim fires)
```
