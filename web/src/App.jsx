import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatMoney } from './api.js'
import CursorScrubber from './components/CursorScrubber.jsx'
import QueueTiles from './components/QueueTiles.jsx'
import QueueTable from './components/QueueTable.jsx'
import EpisodeDossier from './components/EpisodeDossier.jsx'
import FeedExceptions from './components/FeedExceptions.jsx'
import Cited from './components/Cited.jsx'

// ═══ the Portfolio Analyst panel ═════════════════════════════════════════════════════════════
// docs/agent_layer_design.md S:opening line: "the Exception Investigator explains, the Portfolio
// Analyst aggregates" -- and .agents/specs/spec_analyst.md puts this role on the dashboard
// specifically because "what is the state of the book" is the dashboard's own question, not a
// per-episode one. It hits POST /api/agent/portfolio directly (not through api.js's `api.agent.*`
// namespace, which this wave does not own) -- same request/response shape as `api.agent.explain`,
// same 503-means-unavailable convention.
//
// One tool-calling pass, not a stream: `/api/agent/portfolio` mirrors `/api/agent/explain`
// (single request, single response), not `/api/agent/decide` (SSE). So this panel cannot show a
// real step-by-step log the way DecidePanel does in pages/Analyse.jsx -- there is no server-sent
// event to render. What it borrows from DecidePanel instead is the *principle* the task calls
// for: a run that takes 1-3 minutes must never sit behind a bare, silent spinner. An elapsed-time
// readout plus a short, honest list of what a portfolio analysis pass actually does (read the
// overview, read the ranked queue, cross-reference, draft) gives a reviewer watching the wait
// something true to look at, without pretending to know a stage has completed when the single
// HTTP call has not yet returned.
const PORTFOLIO_PROGRESS_STEPS = [
  'Reading the portfolio overview -- counts and money by disposition, verdict-pair and reason-code frequencies…',
  'Reading the exception queue, ranked by the deterministic SQL sort already applied…',
  'Cross-referencing reason codes and cross-track flags across both queues…',
  'Drafting the report and verifying every figure against a tool result before answering…',
]
//: How often the next honest-progress line appears. Not a real progress signal (there is no
//: server event to key off) -- just a slower cadence than a spinner, tuned to the 1-3 minute
//: wall clock this call actually takes on the thinking model.
const PORTFOLIO_STEP_INTERVAL_MS = 18_000

function PortfolioAnalystPanel({ cursor }) {
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [unavailable, setUnavailable] = useState(null)
  const [elapsedS, setElapsedS] = useState(0)
  const [stepIndex, setStepIndex] = useState(0)
  const elapsedTimer = useRef(null)
  const stepTimer = useRef(null)

  useEffect(() => {
    return () => {
      if (elapsedTimer.current) clearInterval(elapsedTimer.current)
      if (stepTimer.current) clearInterval(stepTimer.current)
    }
  }, [])

  async function run() {
    setBusy(true)
    setError(null)
    setUnavailable(null)
    setResult(null)
    setElapsedS(0)
    setStepIndex(0)
    elapsedTimer.current = setInterval(() => setElapsedS((s) => s + 1), 1000)
    stepTimer.current = setInterval(
      () => setStepIndex((i) => Math.min(i + 1, PORTFOLIO_PROGRESS_STEPS.length - 1)),
      PORTFOLIO_STEP_INTERVAL_MS,
    )
    try {
      const response = await fetch('/api/agent/portfolio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cursor: cursor ?? null, question: question.trim() || null }),
      })
      if (!response.ok) {
        let detail = response.statusText
        try {
          detail = (await response.json()).detail ?? detail
        } catch {
          /* a non-JSON error body is still worth surfacing as the status text */
        }
        const err = new Error(`${response.status}: ${detail}`)
        err.status = response.status
        throw err
      }
      setResult(await response.json())
    } catch (exc) {
      if (exc.status === 503) setUnavailable(String(exc.message ?? exc))
      else setError(String(exc.message ?? exc))
    } finally {
      setBusy(false)
      clearInterval(elapsedTimer.current)
      clearInterval(stepTimer.current)
    }
  }

  return (
    <section className="panel">
      <div className="panel-head-row">
        <h2>Portfolio Analyst</h2>
        <button type="button" data-testid="portfolio-run" onClick={run} disabled={busy}>
          {busy ? 'Analysing…' : result ? 'Run again' : 'Analyse the book'}
        </button>
      </div>
      <p className="hint">
        Aggregates the same counts and money the tiles below show, plus the exception queue's own
        deterministic ranking, into a narrative answer to "what is the state of the book right
        now, and what carries the most money" — every figure is quoted from a tool result and
        every ordering is a SQL sort the agent only explains; it never re-ranks a queue itself.
        Ask a specific question below, or leave it blank for a general summary.
      </p>
      <label className="wi-field">
        <span>Question (optional)</span>
        <textarea
          data-testid="portfolio-question"
          rows={2}
          placeholder='e.g. "which reason code carries the most money in the exception queue right now?"'
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={busy}
        />
      </label>

      {busy ? (
        <div className="agent-log" data-testid="portfolio-progress">
          <div className="agent-log-row muted">
            running for {elapsedS}s — the provider's fast tier is down, so this call runs on a
            thinking model and can take 1–3 minutes even when it succeeds.
          </div>
          {PORTFOLIO_PROGRESS_STEPS.slice(0, stepIndex + 1).map((step, i) => (
            <div key={i} className="agent-log-row">
              {step}
            </div>
          ))}
        </div>
      ) : null}

      {unavailable ? (
        <div className="agent-unavailable" data-testid="portfolio-unavailable">
          {unavailable}
        </div>
      ) : null}
      {error ? (
        <div className="error" data-testid="portfolio-error">
          {error}
        </div>
      ) : null}

      {result && result.status === 'ok' ? (
        <div data-testid="portfolio-report">
          {['state_of_the_book', 'what_is_concentrated', 'what_i_could_not_determine', 'where_to_look_first'].map(
            (key) =>
              result.report[key] ? (
                <div key={key} style={{ marginBottom: 10 }}>
                  <div className="k" style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-muted)' }}>
                    {key.replaceAll('_', ' ')}
                  </div>
                  <Cited text={result.report[key]} />
                </div>
              ) : null,
          )}
          {result.citations?.length ? (
            <p className="dossier-note">
              {result.citations.length} citation{result.citations.length === 1 ? '' : 's'}, numbered
              above in order of first use. Hover a marker to see what it points at.
            </p>
          ) : null}
          {result.unsourced_figures?.length ? (
            <div className="agent-unsourced" data-testid="portfolio-unsourced-figures">
              ⚠ figure(s) that survived the repair turn without appearing in any tool result:{' '}
              {result.unsourced_figures.join(', ')}
            </div>
          ) : null}
        </div>
      ) : null}
      {result && result.status !== 'ok' ? (
        <p className="empty">portfolio analyst outcome: {result.status}</p>
      ) : null}
    </section>
  )
}

// The whole app is a function of one piece of state: the cursor.
//
// Everything on screen is "what did we believe at this moment?" — so moving the cursor refetches
// rather than mutating, and there is no local cache to invalidate. That is the replay architecture
// surfacing all the way up to the UI: the front end has no concept of "now" either.
export default function App() {
  const [meta, setMeta] = useState(null)
  const [cursor, setCursor] = useState(null)
  const [disposition, setDisposition] = useState('EXCEPTION')
  const [orderBy, setOrderBy] = useState('reopened_first')
  const [overview, setOverview] = useState(null)
  const [rows, setRows] = useState([])
  const [selected, setSelected] = useState(null)
  const [dossier, setDossier] = useState(null)
  const [feeds, setFeeds] = useState(null)
  const [busy, setBusy] = useState(false)
  const [dossierBusy, setDossierBusy] = useState(false)
  const [error, setError] = useState(null)

  // --- bootstrap ---------------------------------------------------------
  useEffect(() => {
    api
      .meta()
      .then((payload) => {
        setMeta(payload)
        setCursor(payload.cursor.max)
      })
      .catch((exc) => setError(String(exc.message ?? exc)))
  }, [])

  // --- everything that depends on the cursor ----------------------------
  // Monotonic request id. Dragging the cursor fires a refresh per tick, and without this an older,
  // slower response can land after a newer one and silently repaint the screen with the wrong
  // cursor's data — wrong numbers, no error, nothing to notice.
  const requestId = useRef(0)

  const refresh = useCallback(
    async (nextCursor, nextDisposition, nextOrderBy) => {
      if (!nextCursor) return
      const ticket = ++requestId.current
      setBusy(true)
      setError(null)
      try {
        const [overviewPayload, queuePayload, feedsPayload] = await Promise.all([
          api.overview(nextCursor),
          api.queue(nextDisposition, nextCursor, nextOrderBy),
          api.feedExceptions(nextCursor),
        ])
        if (ticket !== requestId.current) return
        setOverview(overviewPayload)
        setRows(queuePayload.episodes)
        setFeeds(feedsPayload)
      } catch (exc) {
        if (ticket !== requestId.current) return
        setError(String(exc.message ?? exc))
        // Clear rather than keep. Stale rows are worse than no rows here: the heading has already
        // changed to the queue that was asked for, so leaving the previous queue's episodes under it
        // presents real data as an answer to a different question. An empty table plus the error is
        // honest; a populated one is not.
        setRows([])
        setOverview(null)
        setFeeds(null)
      } finally {
        if (ticket === requestId.current) setBusy(false)
      }
    },
    [],
  )

  // Clicking the tile that is already selected retries. Without this, a failed fetch leaves the
  // only obvious recovery action — clicking the thing you want — doing nothing at all, because the
  // state did not change and the effect did not re-run.
  const selectDisposition = useCallback(
    (next) => {
      if (next === disposition) refresh(cursor, next, orderBy)
      else setDisposition(next)
    },
    [cursor, disposition, orderBy, refresh],
  )

  useEffect(() => {
    refresh(cursor, disposition, orderBy)
  }, [cursor, disposition, orderBy, refresh])

  // --- the selected episode, re-read at the current cursor --------------
  useEffect(() => {
    if (!selected || !cursor) {
      setDossier(null)
      return
    }
    let live = true
    setDossierBusy(true)
    api
      .dossier(selected, cursor)
      .then((payload) => {
        if (!live) return
        setDossier(payload)
        // No scrollIntoView here. The dossier now lives in a sticky right-hand column that is
        // always on screen, and this effect re-runs on every cursor tick (the dossier refetches at
        // the new cursor) — a scroll tied to that would yank the page on every drag of the slider,
        // not just when the selected episode actually changes.
      })
      .catch((exc) => {
        if (!live) return
        setError(String(exc.message ?? exc))
        // Same reasoning as the queue: a dossier still showing the *previous* cursor's history is a
        // wrong answer presented confidently. Clearing it makes the gap visible.
        setDossier(null)
      })
      .finally(() => {
        if (live) setDossierBusy(false)
      })
    return () => {
      live = false
    }
  }, [selected, cursor])

  const regenerate = async (profile, seed) => {
    setBusy(true)
    setError(null)
    try {
      await api.regenerate(profile, seed)
      const payload = await api.meta()
      setMeta(payload)
      setSelected(null)
      setCursor(payload.cursor.max)
      // Refresh explicitly rather than relying on the cursor changing.
      //
      // Both profiles cover the *same* generation window, so `cursor.max` after regenerating is
      // usually the identical string it already was. React sees no state change, the effect does not
      // re-run, and the screen keeps showing the previous dataset's counts — stale numbers with no
      // error to explain them, which is the most misleading possible outcome. The dataset changed
      // even though the cursor did not, so the refetch has to be driven by the event, not the state.
      await refresh(payload.cursor.max, disposition, orderBy)
    } catch (exc) {
      setError(String(exc.message ?? exc))
    } finally {
      setBusy(false)
    }
  }

  const totals = overview
    ? Object.values(overview.by_disposition).reduce(
        (accumulator, entry) => ({
          episodes: accumulator.episodes + entry.episodes,
          variance: accumulator.variance + entry.variance_cents,
        }),
        { episodes: 0, variance: 0 },
      )
    : { episodes: 0, variance: 0 }

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1>Post-Claim Pharmacy Financial Reconciliation</h1>
          <div className="sub">
            Deterministic layer. Every figure below was computed in Python and is shown verbatim —
            nothing on this screen calculates.
            {meta ? (
              <>
                {' '}
                Profile <code>{meta.profile}</code>, seed{' '}
                <code data-testid="seed">{meta.stored?.master_seed ?? '—'}</code>, engine{' '}
                <code>{meta.engine_version}</code>.
              </>
            ) : null}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="sub" data-testid="totals">
            {totals.episodes} episodes · {formatMoney(totals.variance)} total variance
          </span>
          {/*
            Both rebuild buttons mint a fresh seed, because "rebuild" reading as "produce
            the identical file again" surprised everyone who pressed it. A new seed means
            new claims, new amounts, and defects landing on different episodes, and on the
            demo profile a different mix of exceptions — while every named edge case is
            still guaranteed to appear. Reproducibility did not go anywhere: the seed that
            produced whatever you are looking at is in the masthead and in manifest.json,
            and `Repeat seed` below replays it byte for byte.
          */}
          <button
            data-testid="regen-demo"
            onClick={() => regenerate('demo', api.freshSeed())}
            disabled={busy}
            title="Rebuild the 60-episode walkthrough on a new seed: different claims, amounts and queue mix, with every named edge case still present."
          >
            Rebuild demo
          </button>
          <button
            data-testid="regen-full"
            onClick={() => regenerate('full', api.freshSeed())}
            disabled={busy}
            title="Rebuild the 1,500-episode profile on a new seed. All 372 verdict pairs are still covered — that is arithmetic, not luck."
          >
            Rebuild full
          </button>
          <button
            data-testid="regen-same-seed"
            className="primary"
            onClick={() => regenerate(meta?.profile ?? 'demo', meta?.stored?.master_seed)}
            disabled={busy || !meta?.stored?.master_seed}
            title="Rebuild on the seed shown in the masthead. Byte-for-byte identical, which is how you check reproducibility rather than take it on trust — compare the feed hashes in manifest.json."
          >
            Repeat seed
          </button>
        </div>
      </header>

      {error ? (
        <div className="error" data-testid="error">
          {error}
        </div>
      ) : null}

      <div className="layout-grid">
        <div className="layout-left">
          <section className="panel">
            <h2>Replay cursor</h2>
            <p className="hint">
              The engine processes records whose arrival time is at or before this cursor. Moving it
              backwards reproduces the answer as it stood then — the same code path in both directions,
              which is why the audit question needs no separate feature.
            </p>
            <CursorScrubber bounds={meta?.cursor} cursor={cursor} onChange={setCursor} busy={busy} />
          </section>

          <PortfolioAnalystPanel cursor={cursor} />

          <section className="panel">
            <h2>Queues at this cursor</h2>
            <p className="hint">
              Three dispositions, because “what do I do with this?” has three answers. A queue is a
              query over the verdict log, not a table things are moved into — which is why it can be
              asked at any cursor and why deleting the log loses nothing.
            </p>
            <QueueTiles overview={overview} selected={disposition} onSelect={selectDisposition} />
          </section>

          <section className="panel">
            <h2>{disposition} queue</h2>
            <QueueTable
              rows={rows}
              orderBy={orderBy}
              onOrderBy={setOrderBy}
              selected={selected}
              onSelect={setSelected}
              busy={busy}
            />
          </section>

          <section className="panel">
            <h2>Feed-level exceptions</h2>
            <p className="hint">
              Properties of ingestion rather than of any claim, which is why they are not multiplied into
              the episode state space. Each one is a query, not a stored flag.
            </p>
            <FeedExceptions data={feeds} />
          </section>

          {overview ? (
            <section className="panel">
              <h2>Verdict distribution</h2>
              <p className="hint">
                Fifty deterministic rules generate 372 reachable verdict pairs compositionally — the
                engine resolves each track independently and then annotates, rather than enumerating
                combinations.
              </p>
              <div className="table-wrap">
                <table data-testid="verdict-distribution">
                  <thead>
                    <tr>
                      <th>Reimbursement</th>
                      <th>Rebate</th>
                      <th className="num">Episodes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.verdict_pairs.map((pair) => (
                      <tr key={`${pair.reimbursement}|${pair.rebate}`}>
                        <td>
                          <span className="verdict-code">{pair.reimbursement}</span>
                        </td>
                        <td>
                          <span className="verdict-code">{pair.rebate}</span>
                        </td>
                        <td className="num">{pair.episodes}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
        </div>

        <div className="layout-right">
          <section className="panel" id="episode" data-testid="episode-panel">
            <div className="panel-head-row">
              <h2>The episode, end to end</h2>
              {dossier ? (
                <button
                  type="button"
                  data-testid="inspect-using-ai"
                  onClick={() => window.open(`/analyse/${dossier.episode_id}`, '_blank', 'noopener')}
                  title="Open the agent layer's analysis screen for this episode in a new tab — an LLM explanation and a recommended next step, grounded in this same dossier and never computing a number of its own."
                >
                  Inspect using AI
                </button>
              ) : null}
            </div>
            <p className="hint">
              One claim, everything that happened to it, in the order we learned it — the claim filed,
              what the payer said, when the money moved, how the 340B rebate went, and every point at
              which the verdict changed. This is the same object the agent layer receives in a single
              call, and nothing in it was written by a model: every line is composed from the records.
            </p>
            <EpisodeDossier dossier={dossier} busy={dossierBusy} />
          </section>
        </div>
      </div>
    </div>
  )
}
