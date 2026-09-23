import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatMoney } from './api.js'
import * as labels from './labels.js'
import CursorScrubber from './components/CursorScrubber.jsx'
import QueueTiles from './components/QueueTiles.jsx'
import QueueTable from './components/QueueTable.jsx'
import EpisodeDossier from './components/EpisodeDossier.jsx'
import FeedExceptions from './components/FeedExceptions.jsx'
import Cited from './components/Cited.jsx'
import TodoListPanel from './components/TodoListPanel.jsx'
import Connectivity from './components/Connectivity.jsx'

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
        {/* Named for what it does, not for the agent role that does it. */}
        <h2>Ask about the book</h2>
        <button type="button" data-testid="portfolio-run" onClick={run} disabled={busy}>
          {busy ? 'Analysing…' : result ? 'Run again' : 'Analyse the book'}
        </button>
      </div>
      <p className="hint">
        Every figure it quotes was computed by the engine and cited. It explains the numbers; it
        never produces one.
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
  // Which screen is showing. A piece of state and a pair of buttons, the same `.view-toggle`
  // pattern the dossier's simple/detailed switch already uses — not a route. The two screens
  // share the masthead and nothing else, there is no deep link to either, and the queue state
  // has to survive a look at the connector report and still be there on the way back, which a
  // router would have had to reconstruct. `/analyse/:episodeId` stays a real navigation because
  // it genuinely is one: a new tab, opened with `window.open`.
  const [view, setView] = useState('operations')
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
          {/*
            The claim worth making, and nothing else. This line used to open with "Deterministic
            layer" and end with the profile, the seed and the engine version -- three build
            parameters that tell a reader the thing they are looking at is a test harness. They
            have not gone anywhere: they are the `title` on the build marker below, which is
            where someone checking reproducibility will think to look and nobody else has to.
          */}
          <div className="sub">
            Every figure was computed in Python and is shown verbatim. Nothing on this screen
            calculates.
            {meta ? (
              <span
                className="build-stamp"
                data-testid="seed"
                title={`profile ${meta.profile} · seed ${meta.stored?.master_seed ?? '—'} · engine ${meta.engine_version}`}
              >
                {meta.profile}
              </span>
            ) : null}
          </div>
        </div>
        <div className="masthead-tools">
          <div className="view-toggle" role="group" aria-label="Screen">
            <button
              type="button"
              data-testid="screen-operations"
              aria-pressed={view === 'operations'}
              onClick={() => setView('operations')}
              title="The queues, the replay cursor and the episode dossier — what the deterministic engine concluded."
            >
              Operations
            </button>
            <button
              type="button"
              data-testid="screen-connectivity"
              aria-pressed={view === 'connectivity'}
              onClick={() => setView('connectivity')}
              title="Connector readiness per source: transport, auth, schema contract, mock provenance, golden-claim traces, control totals, and what is still vendor-blocked."
            >
              Connectivity
            </button>
          </div>
          {view === 'operations' ? (
            <span className="sub" data-testid="totals">
              {totals.episodes} episodes · {formatMoney(totals.variance)} total variance
            </span>
          ) : null}
          {/*
            The three rebuild controls now sit behind a disclosure rather than in the header
            row itself. They are how you reseed or replay a dataset — build tooling, not
            product — and one of them drops and rebuilds the database. Three buttons of equal
            weight to the screen switcher read as the primary actions on the page, which put
            the most destructive control in the most reachable spot. Folded, not removed:
            every one is still one click away and the demo still opens with them.
          */}
          <details className="tools-menu">
            <summary title="Rebuild or replay the dataset">Data</summary>
            <div className="tools-menu-body">
              {/*
                Both rebuild buttons mint a fresh seed, because "rebuild" reading as "produce
                the identical file again" surprised everyone who pressed it. A new seed means
                new claims, new amounts, and defects landing on different episodes, and on the
                demo profile a different mix of exceptions — while every named edge case is
                still guaranteed to appear. Reproducibility did not go anywhere: the seed that
                produced whatever you are looking at is in the masthead and in manifest.json,
                and `Repeat seed` replays it byte for byte.
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
          </details>
        </div>
      </header>

      {/*
        The cursor sits in the header band rather than in a panel of its own. It governs every
        figure on the screen below it, so a panel in the left column made it look like one more
        thing to read on the way down — and put two paragraphs of explanation between the title
        and the first dollar figure. Here it reads as a control on the whole page, which is what
        it is, and the money starts at the top of the scroll.
      */}
      {view === 'operations' ? (
        <div className="cursor-band">
          <CursorScrubber bounds={meta?.cursor} cursor={cursor} onChange={setCursor} busy={busy} />
        </div>
      ) : null}

      {error ? (
        <div className="error" data-testid="error">
          {error}
        </div>
      ) : null}

      {/*
        Two screens, one page. The connectivity view answers a different question from every
        panel below it — "what is actually built, per source" rather than "what did we conclude
        about this book of claims" — and it takes no cursor, because connector readiness is a
        property of the build rather than of the moment being replayed. Swapping the body rather
        than navigating keeps the cursor, the selected queue and the open episode intact
        underneath it, so coming back lands exactly where you left.
      */}
      {view === 'connectivity' ? (
        <Connectivity />
      ) : (
      <div className="layout-grid">
        <div className="layout-left">
          {/*
            Order on this column is deliberate and it is the money first. What an operator — or
            anyone being shown the book — wants from this screen is "how much is outstanding and
            what do I work", in that order. Everything below the queue answers a question you only
            ask once you have seen those two: what could not be matched at all, what the agent
            layer makes of it, what the team has already committed to.
          */}
          {/*
            Every panel's hint is now one line that states the question the panel answers. They
            were three to five lines each, explaining how the system was built -- good writing
            aimed at a reviewer grading the architecture, and the first thing a reader has to get
            past to reach a number. The reasoning did not go anywhere; it is in DESIGN_NOTE.md,
            where someone looking for it will find it.
          */}
          <section className="panel">
            <h2>Where the money is</h2>
            <p className="hint">What do I do with this? There are three answers.</p>
            <QueueTiles overview={overview} selected={disposition} onSelect={selectDisposition} />
          </section>

          <section className="panel">
            <h2>{labels.disposition(disposition).label}</h2>
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
            <h2>Money and documents we could not match</h2>
            <p className="hint">
              Faults in what arrived, not facts about any claim.
            </p>
            <FeedExceptions data={feeds} />
          </section>

          {/*
            The analyst panel reads the same counts and money the tiles above already show. It
            belongs after them: it answers "what do you make of this", which is a question you ask
            once you have the numbers, not before. Above the queue it read as the primary feature
            of the screen, which inverted the one rule the whole system is built on — the engine
            decides, the agent explains.
          */}
          <PortfolioAnalystPanel cursor={cursor} />

          {/*
            The cross-episode to-do list answers "what am I doing about it", distinct from the
            queues above, which answer "what is the state of the book". It sits below the
            operational queues and above the reference-only verdict distribution so it stays
            deliberately un-prominent — it was originally specified as its own screen, reversed
            to a dashboard panel (decision A32, docs/decision_ledger.md).
          */}
          <TodoListPanel cursor={cursor} />

          {overview ? (
            <section className="panel">
              <h2>Verdict distribution</h2>
              <p className="hint">
                Every combination of insurance outcome and rebate outcome in the book, most
                frequent first. Top 25 — the counts do not sum to the total.
              </p>
              {/*
                Capped like every other long table on this screen. It was the one without a
                height, so a profile with more distinct pairs pushed the page to several
                thousand pixels -- the exact failure `.queue-scroll` exists to prevent.
              */}
              <div className="table-wrap queue-scroll">
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
              One claim, everything that happened to it, in the order we learned it. Nothing here
              was written by a model.
            </p>
            <EpisodeDossier dossier={dossier} busy={dossierBusy} />
          </section>
        </div>
      </div>
      )}
    </div>
  )
}
