import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import EpisodeDossier from '../components/EpisodeDossier.jsx'
import Cited from '../components/Cited.jsx'

// The agent layer's analysis screen (docs/agent_layer_design.md S7/S8.6): one episode, the
// evidence beside the recommendation, never a rubber stamp.
//
// LEFT reuses the exact dossier the dashboard shows (identity, economics, and — inside it —
// Timeline.jsx's own event-by-event narrative), because that is the object the agent layer
// itself receives in one call, and a reviewer who cannot see it beside the recommendation is
// being asked to trust a summary rather than check it. RIGHT is everything the agent layer adds
// on top: an explanation, a streamed propose/evaluate/score/gate loop, and — the one write path
// in the whole system — an editable draft a human accepts or rejects.
//
// No router. See main.jsx's own comment: this screen is only ever reached by a fresh navigation.

const ACTIONS = ['RESUBMIT', 'APPEAL', 'WRITE_OFF', 'ESCALATE', 'INVESTIGATE_CROSSWALK', 'AWAIT_PAYER', 'ABSTAIN']

// The four real outcomes, styled distinctly — `insufficient_data` is deliberately given the
// same visual register as `complete` (no red, no warning triangle), because design S8.6 is
// explicit that it "is a successful outcome... the UI should not style it as an error."
const OUTCOME_STYLE = {
  complete: { color: 'var(--status-good)', label: 'Complete', icon: '✓' },
  insufficient_data: { color: 'var(--accent)', label: 'Insufficient data', icon: 'ⓘ' },
  stalled: { color: 'var(--status-warning)', label: 'Stalled', icon: '⚠' },
  capped: { color: 'var(--status-warning)', label: 'Capped', icon: '⚠' },
  error: { color: 'var(--status-critical)', label: 'Run failed', icon: '✕' },
  episode_not_found: { color: 'var(--status-critical)', label: 'Episode not found', icon: '✕' },
}

// What a status means for the claim, when the run carried no reason of its own. Written for the
// person holding the claim, not for the person holding the harness.
const OUTCOME_FALLBACK = {
  stalled: 'Two more rounds of review did not improve on the recommendation below, so the loop stopped rather than spend a third. Treat the draft as a starting point and check it yourself.',
  capped: 'The review ran out of its budget before it cleared the bar. The recommendation below is the best it reached, not a settled answer.',
  complete: 'The recommendation below cleared every check the reviewer applies.',
  insufficient_data: 'The records available at this cursor do not settle the question. The draft below says what is missing.',
}

function truncate(text, n) {
  if (!text) return ''
  return text.length > n ? `${text.slice(0, n)}…` : text
}

// The one artifact rule the write tool enforces (`tools.py`: at most 120 characters, no "; ",
// no newline), applied here so a reviewer never meets it as a 422 from a draft they did not
// write. "; " is the tool's own separator when it folds the list into one stored string, so a
// semicolon inside an entry would silently split it in two; a comma carries the same meaning to
// a reader and survives the round trip.
const ARTIFACT_MAX = 120

function cleanArtifact(text) {
  const flat = String(text ?? '').replace(/\s+/g, ' ').replace(/;\s/g, ', ').trim()
  return flat.length > ARTIFACT_MAX ? `${flat.slice(0, ARTIFACT_MAX - 1).trimEnd()}…` : flat
}

// Which role is working, in words. Keyed on the SSE frame kinds the harness already emits, so
// there is nothing new on the wire -- the stream is simply read for one fact instead of printed.
const PROGRESS = {
  run_started: 'reading the episode',
  iteration_started: (p) => `round ${(p.iteration ?? 0) + 1} — proposing an action`,
  tool_call: (p) => `looking up ${String(p.name ?? '').replace(/_/g, ' ')}`,
  proposal: (p) => (p.schema_repair_attempted ? 'correcting the proposal' : 'grading the proposal'),
  evaluation: 'grading the proposal',
  score: 'checking the result against the bar',
  critique: 'sending it back for another round',
}

// ═══ the outcome, as a reader needs it ══════════════════════════════════════════════════════
//
// What used to sit here was the harness's own audit trail, streamed frame by frame: every
// llm_request, every tool_call and its byte count, the proposer's full reasoning, the
// evaluator's per-criterion grades as `G6 CONTRADICTED` chips, the score to three decimals, the
// critique fed forward, and a closing "Full checklist (10 criteria graded)". All of it is real
// and all of it is still recorded -- `GET /api/agent/runs/{run_id}` returns the whole journal,
// event for event, and the JSONL on disk is untouched. None of it belongs on this screen.
//
// A criterion id is a name for an argument the engine has with itself. `G3_figures_are_sourced`
// CONTRADICTED does not tell an operator anything they can act on; it tells a maintainer that
// the number scorer fired. Rendering it beside the recommendation asks the reader to audit the
// grader instead of reading the recommendation, and the two are not the same job. The run id is
// kept, so the trail is one HTTP call away for whoever does want to audit it.
function OutcomeBanner({ outcome }) {
  const style = OUTCOME_STYLE[outcome.status] ?? { color: 'var(--border-strong)', label: outcome.status, icon: '?' }
  // One sentence, in plain words, for why the loop ended where it did. The four outcomes each
  // carry their reason on a different field, and exactly one of them is ever populated.
  //
  // Every status gets one, including the two that carry no reason field of their own. A live run
  // ended "Stalled" with nothing under it, which tells a reader the machine stopped and not what
  // that means for the claim in front of them -- a status word alone is a shrug.
  const why =
    outcome.detail ||
    outcome.blocked_reason ||
    outcome.missing_narrative ||
    outcome.budget_note ||
    OUTCOME_FALLBACK[outcome.status] ||
    null
  return (
    <div className="outcome-banner" style={{ '--oc': style.color }} data-testid="decide-outcome" data-status={outcome.status}>
      <div className="outcome-banner-head">
        <span className="outcome-banner-icon" aria-hidden="true">
          {style.icon}
        </span>
        <span className="outcome-banner-label">{style.label}</span>
      </div>
      {why ? <p className="outcome-why" data-testid="decide-why">{why}</p> : null}
      {outcome.run_id ? (
        <p className="outcome-runid">
          Full reasoning trace recorded as run <span className="mono">{outcome.run_id.slice(0, 12)}</span>
        </p>
      ) : null}
    </div>
  )
}

// ═══ the explain panel ═══════════════════════════════════════════════════════════════════════

function ExplainPanel({ episodeId, onRun }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [unavailable, setUnavailable] = useState(null)
  const [ran, setRan] = useState(false)

  async function run() {
    onRun?.()
    setBusy(true)
    setError(null)
    setUnavailable(null)
    setResult(null)
    try {
      const payload = await api.agent.explain(episodeId)
      setResult(payload)
    } catch (exc) {
      if (exc.status === 503) setUnavailable(String(exc.message ?? exc))
      else setError(String(exc.message ?? exc))
    } finally {
      setBusy(false)
      setRan(true)
    }
  }

  return (
    <section className="panel">
      <div className="panel-head-row">
        <h2>1. Explain</h2>
        <button type="button" data-testid="explain-btn" onClick={run} disabled={busy}>
          {busy ? 'Explaining…' : ran ? 'Explain again' : 'Explain'}
        </button>
      </div>
      <p className="hint">
        The Exception Investigator reads this episode's dossier and writes a narrative report —
        citing raw records, engine events and verdicts by id, never inventing a figure of its own.
      </p>
      {busy ? <p className="spinner">running the investigator…</p> : null}
      {unavailable ? (
        <div className="agent-unavailable" data-testid="explain-unavailable">
          {unavailable}
        </div>
      ) : null}
      {error ? <div className="error">{error}</div> : null}
      {result && result.status === 'episode_not_found' ? (
        <p className="empty">No episode {episodeId} exists at this cursor.</p>
      ) : null}
      {result && result.status === 'ok' ? (
        <div data-testid="explain-report">
          {['what_happened', 'why_it_is_open', 'what_i_could_not_determine', 'what_a_human_should_check_first'].map(
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
              {result.citations.length} citation{result.citations.length === 1 ? '' : 's'}, numbered above in order of
              first use. Hover a marker to see what it points at.
            </p>
          ) : null}
          {result.unsourced_figures?.length ? (
            <div className="agent-unsourced" data-testid="unsourced-figures">
              ⚠ figure(s) that survived the repair turn without appearing in any tool result:{' '}
              {result.unsourced_figures.join(', ')}
            </div>
          ) : null}
        </div>
      ) : null}
      {result && result.status !== 'ok' && result.status !== 'episode_not_found' ? (
        <p className="empty">investigator outcome: {result.status}</p>
      ) : null}
    </section>
  )
}

// ═══ the decide panel: the streamed loop, the outcome, the editable draft ══════════════════════

function DecidePanel({ episodeId, canRun }) {
  const [running, setRunning] = useState(false)
  // One line of plain progress instead of the raw frame log: which of the two roles is working
  // right now. The SSE stream still arrives in full -- this reads it and keeps one string.
  const [progress, setProgress] = useState(null)
  const [outcome, setOutcome] = useState(null)
  const [unavailable, setUnavailable] = useState(null)
  const abortRef = useRef(null)

  // Work-item draft, seeded from the proposal once an outcome carrying one arrives, then
  // freely editable — the safety property the task calls for, not a convenience: a human
  // reviewer must be able to change the action, the artifacts and the rationale before
  // anything is written, not merely rubber-stamp what a model proposed.
  const [wiAction, setWiAction] = useState('ABSTAIN')
  const [wiArtifacts, setWiArtifacts] = useState([''])
  const [wiSummary, setWiSummary] = useState('')
  const [wiBusy, setWiBusy] = useState(false)
  const [wiError, setWiError] = useState(null)
  const [wiResult, setWiResult] = useState(null)
  const [episodeWorkItems, setEpisodeWorkItems] = useState([])

  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current()
    }
  }, [])

  useEffect(() => {
    api.agent
      .workItemsForEpisode(episodeId)
      .then((payload) => setEpisodeWorkItems(payload.work_items))
      .catch(() => {
        /* the to-do list is a nicety on this screen; a 503 here is not worth its own banner */
      })
  }, [episodeId, wiResult])

  useEffect(() => {
    if (outcome?.proposal) {
      setWiAction(ACTIONS.includes(outcome.proposal.action) ? outcome.proposal.action : 'ABSTAIN')
      // Sanitised on the way in, not only on the way out. The model writes an artifact as a
      // sentence with a semicolon in it and no length discipline; the write tool rejects
      // anything over 120 characters, containing "; " (its own list separator) or a newline.
      // Seeding the form with text the server will refuse puts a 422 in front of a reviewer who
      // did nothing wrong, which is exactly what happened.
      setWiArtifacts(
        outcome.proposal.required_artifacts?.length
          ? outcome.proposal.required_artifacts.map(cleanArtifact)
          : [''],
      )
      setWiSummary((outcome.proposal.reasoning || '').slice(0, 600))
      setWiResult(null)
      setWiError(null)
    }
  }, [outcome])

  function run() {
    if (abortRef.current) abortRef.current()
    setOutcome(null)
    setUnavailable(null)
    setRunning(true)
    setProgress('reading the episode')
    abortRef.current = api.agent.decide(episodeId, null, (kind, payload) => {
      if (kind === 'client_error' && /503/.test(payload.detail ?? '')) {
        setUnavailable(payload.detail)
        setRunning(false)
        return
      }
      const step = PROGRESS[kind]
      if (step) setProgress(typeof step === 'function' ? step(payload) : step)
      if (kind === 'outcome' || kind === 'client_error') {
        setOutcome(kind === 'outcome' ? payload : { status: 'error', detail: payload.detail })
        setRunning(false)
      }
    })
  }

  function updateArtifact(index, value) {
    setWiArtifacts((prev) => prev.map((a, i) => (i === index ? value : a)))
  }
  function addArtifact() {
    setWiArtifacts((prev) => [...prev, ''])
  }
  function removeArtifact(index) {
    setWiArtifacts((prev) => (prev.length <= 1 ? [''] : prev.filter((_, i) => i !== index)))
  }
  // Order is meaning in this list: it is what a person works down, so the one they must do first
  // belongs at the top. The reviewer could add, edit and strike an entry but not move one, which
  // left reordering as "delete it and retype it somewhere else".
  function moveArtifact(index, delta) {
    setWiArtifacts((prev) => {
      const to = index + delta
      if (to < 0 || to >= prev.length) return prev
      const next = [...prev]
      ;[next[index], next[to]] = [next[to], next[index]]
      return next
    })
  }

  async function addToDo() {
    const summary = wiSummary.trim()
    if (summary.length < 20 || summary.length > 600) {
      setWiError(`Rationale must be 20–600 characters (currently ${summary.length}).`)
      return
    }
    // Checked here, against the same rule the tool applies, so a violation is reported next to
    // the field that caused it rather than as a raw 422 quoting an entry the reviewer has to go
    // and find. `cleanArtifact` already keeps typed text legal; this catches a paste.
    const artifacts = wiArtifacts.map((a) => a.trim()).filter(Boolean)
    const bad = artifacts.findIndex((a) => a.length > ARTIFACT_MAX || a.includes('; ') || /[\r\n]/.test(a))
    if (bad !== -1) {
      setWiError(
        `Artifact ${bad + 1} is ${artifacts[bad].length} characters and must be at most ` +
          `${ARTIFACT_MAX}, on one line, with no semicolon followed by a space. Shorten it, or ` +
          `split it into two artifacts.`,
      )
      return
    }
    if (artifacts.length > 8) {
      setWiError(`At most 8 required artifacts; there are ${artifacts.length}. Remove ${artifacts.length - 8}.`)
      return
    }
    setWiBusy(true)
    setWiError(null)
    setWiResult(null)
    try {
      // Step 1: a harmless dry-run preview. No episode/verdict/action/summary match is
      // checked on this path, and nothing is written — its only job here is to hand back
      // the verdict id this episode's *current* verdict actually has, which the tool
      // derives server-side and which no read endpoint exposes (see api.js).
      const draft = await api.agent.createWorkItem({
        episode_id: episodeId,
        from_verdict_id: 0,
        recommended_action: wiAction,
        summary,
        required_artifacts: artifacts,
        dry_run: true,
      })
      // Step 2: the real commit, now carrying the verdict id the tool just derived, and
      // the original proposal (if any) so the server can log what the human changed.
      const committed = await api.agent.createWorkItem({
        episode_id: episodeId,
        from_verdict_id: draft.from_verdict_id,
        recommended_action: wiAction,
        summary,
        required_artifacts: artifacts,
        dry_run: false,
        proposed: outcome?.proposal
          ? {
              recommended_action: outcome.proposal.action,
              summary: outcome.proposal.reasoning,
              required_artifacts: (outcome.proposal.required_artifacts ?? []).map(cleanArtifact),
            }
          : null,
        run_id: outcome?.run_id ?? null,
      })
      setWiResult(committed)
    } catch (exc) {
      setWiError(String(exc.message ?? exc))
    } finally {
      setWiBusy(false)
    }
  }

  const showDraftForm = outcome && outcome.proposal && outcome.status !== 'error' && outcome.status !== 'episode_not_found'

  return (
    <section className="panel">
      <div className="panel-head-row">
        <h2>2. Decide next steps</h2>
        <button type="button" data-testid="decide-btn" onClick={run} disabled={running || !canRun} title={!canRun ? 'Run Explain first' : undefined}>
          {running ? 'Running…' : outcome ? 'Run again' : 'Decide next steps'}
        </button>
      </div>
      <p className="hint">
        The Workflow Coordinator proposes an action, an independent Evaluator grades it against a
        16-point checklist, and the loop repeats until the score clears the bar, the proposer asks
        to stop, or a budget runs out. Every step below is the harness's own audit trail, streamed
        live — nothing here is a summary written after the fact.
      </p>

      {unavailable ? (
        <div className="agent-unavailable" data-testid="decide-unavailable">
          {unavailable}
        </div>
      ) : null}

      {running ? (
        <p className="spinner" data-testid="decide-progress">
          {progress ?? 'working'}…
        </p>
      ) : null}

      {outcome ? <OutcomeBanner outcome={outcome} /> : null}

      {showDraftForm ? (
        <div className="wi-form" data-testid="work-item-form">
          <h3 className="dossier-section" style={{ marginTop: 18 }}>
            Proposed work item — edit before committing
          </h3>
          <label className="wi-field">
            <span>Action</span>
            <select data-testid="wi-action" value={wiAction} onChange={(e) => setWiAction(e.target.value)}>
              {ACTIONS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </label>

          <label className="wi-field">
            <span>Rationale ({wiSummary.trim().length}/600, min 20)</span>
            <textarea
              data-testid="wi-summary"
              rows={4}
              value={wiSummary}
              onChange={(e) => setWiSummary(e.target.value)}
            />
          </label>

          <div className="wi-field">
            <span>Required artifacts</span>
            {wiArtifacts.map((artifact, i) => (
              <div key={i} className="wi-artifact-row">
                <span className="wi-artifact-n" aria-hidden="true">{i + 1}</span>
                <input
                  data-testid={`wi-artifact-${i}`}
                  type="text"
                  value={artifact}
                  maxLength={ARTIFACT_MAX}
                  onChange={(e) => updateArtifact(i, e.target.value)}
                  placeholder="e.g. corrected NDC on a resubmitted claim"
                />
                <span
                  className={`wi-artifact-count${artifact.length >= ARTIFACT_MAX ? ' at-limit' : ''}`}
                  title={`${ARTIFACT_MAX}-character limit`}
                >
                  {artifact.length}/{ARTIFACT_MAX}
                </span>
                <button
                  type="button"
                  data-testid={`wi-artifact-up-${i}`}
                  onClick={() => moveArtifact(i, -1)}
                  disabled={i === 0}
                  title="Move up"
                  aria-label={`Move artifact ${i + 1} up`}
                >
                  ↑
                </button>
                <button
                  type="button"
                  data-testid={`wi-artifact-down-${i}`}
                  onClick={() => moveArtifact(i, 1)}
                  disabled={i === wiArtifacts.length - 1}
                  title="Move down"
                  aria-label={`Move artifact ${i + 1} down`}
                >
                  ↓
                </button>
                <button type="button" onClick={() => removeArtifact(i)} title="Remove this artifact" aria-label={`Remove artifact ${i + 1}`}>
                  ✕
                </button>
              </div>
            ))}
            <button type="button" data-testid="wi-add-artifact" onClick={addArtifact} disabled={wiArtifacts.length >= 8}>
              + add artifact
            </button>
          </div>

          {wiError ? <div className="error">{wiError}</div> : null}
          {wiResult ? (
            <div className="agent-unavailable" style={{ borderColor: 'var(--status-good)' }} data-testid="wi-result">
              {wiResult.created ? 'Work item created' : 'Already existed'}: {wiResult.work_item_id}. {wiResult.message}
            </div>
          ) : null}

          <button type="button" className="primary" data-testid="add-to-do" onClick={addToDo} disabled={wiBusy}>
            {wiBusy ? 'Adding…' : 'Add to-do'}
          </button>
        </div>
      ) : null}

      {episodeWorkItems.length > 0 ? (
        <>
          <h3 className="dossier-section" style={{ marginTop: 18 }}>
            Existing to-dos for this episode
          </h3>
          <div className="table-wrap" data-testid="episode-work-items">
            <table>
              <thead>
                <tr>
                  <th>Work item</th>
                  <th>Action</th>
                  <th>Created</th>
                  <th>By</th>
                </tr>
              </thead>
              <tbody>
                {episodeWorkItems.map((item) => (
                  <tr key={item.work_item_id}>
                    <td className="mono">{item.work_item_id}</td>
                    <td><span className="verdict-code">{item.recommended_action}</span></td>
                    <td className="mono">{item.created_at?.slice(0, 10)}</td>
                    <td>{item.created_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  )
}

// ═══ the page ═══════════════════════════════════════════════════════════════════════════════
// `Cited` -- the `[[raw:79]]`-token renderer shared with the dashboard's Portfolio Analyst
// panel -- now lives in ../components/Cited.jsx; see that file's own comment for why.

export default function Analyse({ episodeId }) {
  const [dossier, setDossier] = useState(null)
  const [dossierBusy, setDossierBusy] = useState(true)
  const [dossierError, setDossierError] = useState(null)
  const [explainRan, setExplainRan] = useState(false)

  useEffect(() => {
    let live = true
    setDossierBusy(true)
    api
      .dossier(episodeId, null)
      .then((payload) => {
        if (live) setDossier(payload)
      })
      .catch((exc) => {
        if (live) setDossierError(String(exc.message ?? exc))
      })
      .finally(() => {
        if (live) setDossierBusy(false)
      })
    return () => {
      live = false
    }
  }, [episodeId])

  return (
    <div className="app">
      {/*
        Titled after the claim, not after the technology that is about to look at it. "Agent
        analysis — E-000013" names the machinery; a reader opening this page wants to know which
        claim they are looking at and what it is worth. The dossier supplies the drug and the
        payer once it lands, so the id carries the header until then rather than the page
        re-titling itself halfway through a read.
      */}
      <header className="masthead">
        <div>
          <h1>
            {dossier ? (
              <>
                {dossier.identity.drug}
                <span className="masthead-sub-id">{episodeId}</span>
              </>
            ) : (
              episodeId
            )}
          </h1>
          <div className="sub">
            {dossier ? (
              <>
                {dossier.identity.payer} · dispensed {dossier.identity.date_of_service}
                {' · '}
              </>
            ) : null}
            Every figure here was decided by the engine. The agent explains it and recommends what
            a human should do next; it never computes a number.
          </div>
        </div>
        <div className="masthead-tools">
          <a className="back-link" href="/">
            ← Back to the dashboard
          </a>
        </div>
      </header>

      {dossierError ? <div className="error">{dossierError}</div> : null}

      <div className="layout-grid">
        <div className="layout-left">
          <section className="panel">
            <h2>Episode dossier</h2>
            <p className="hint">
              The evidence the recommendation on the right has to answer to — the same object the
              agent layer receives in a single call, so nothing said about it can be checked
              anywhere else.
            </p>
            <EpisodeDossier dossier={dossier} busy={dossierBusy} />
          </section>
        </div>

        <div className="layout-right">
          {/* "run in order": Decide stays disabled until Explain has been clicked at least
              once, matching the intended workflow — understand the episode before
              recommending what to do about it — without blocking a second Explain / Decide
              cycle afterwards (`explainRan` only ever flips false -> true). */}
          <ExplainPanel episodeId={episodeId} onRun={() => setExplainRan(true)} />
          <DecidePanel episodeId={episodeId} canRun={explainRan} />
        </div>
      </div>
    </div>
  )
}
