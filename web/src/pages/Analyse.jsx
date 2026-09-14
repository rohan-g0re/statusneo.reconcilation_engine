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

const VERDICT_COLOR = {
  SUPPORTED: 'var(--status-good)',
  CONTRADICTED: 'var(--status-critical)',
  NOT_ADDRESSED: 'var(--status-warning)',
}

const GATE_COLOR = {
  complete: 'var(--status-good)',
  insufficient_data: 'var(--accent)',
  stalled: 'var(--status-warning)',
  capped: 'var(--status-warning)',
  continue: 'var(--text-muted)',
}

function truncate(text, n) {
  if (!text) return ''
  return text.length > n ? `${text.slice(0, n)}…` : text
}

// ═══ the decide loop's event log — one row per SSE frame, in arrival order ══════════════════

function LogRow({ kind, payload }) {
  switch (kind) {
    case 'run_started':
      return (
        <div className="agent-log-row muted">
          run started — role {payload.role}, cursor {String(payload.cursor).slice(0, 10)}
        </div>
      )
    case 'iteration_started':
      return <div className="agent-log-divider">Iteration {payload.iteration + 1}</div>
    case 'llm_request':
      return (
        <div className="agent-log-row muted">
          → {payload.agent} calling {payload.model}
        </div>
      )
    case 'llm_response':
      return (
        <div className="agent-log-row muted">
          ← {payload.agent} responded
          {payload.usage?.total_tokens ? ` (${payload.usage.total_tokens} tokens)` : ''}
          {payload.finish_reason ? ` · ${payload.finish_reason}` : ''}
        </div>
      )
    case 'llm_error':
      return (
        <div className="agent-log-row bad">
          llm_error: {payload.agent} / {payload.model} — status {payload.status}, {payload.attempts} attempt(s)
        </div>
      )
    case 'tool_call':
      return (
        <div className="agent-log-row mono">
          tool_call {payload.name}({truncate(JSON.stringify(payload.arguments ?? {}), 140)})
        </div>
      )
    case 'tool_result':
      return (
        <div className={`agent-log-row mono ${payload.status === 'error' ? 'bad' : 'muted'}`}>
          ↳ {payload.status}
          {payload.error_type ? ` (${payload.error_type})` : ''} · {payload.bytes ?? 0} B · {payload.elapsed_ms ?? 0} ms
        </div>
      )
    case 'proposal':
      return (
        <div className="agent-log-card">
          <div className="agent-log-card-head">
            Proposal <span className="verdict-code">{payload.action}</span>
            {payload.blocked ? <span className="chip flag">blocked: {payload.blocked_reason}</span> : null}
          </div>
          <div className="agent-log-card-body">{truncate(payload.reasoning, 320)}</div>
          <div className="agent-log-card-foot">
            {payload.evidence?.length ?? 0} evidence span(s) · {payload.required_artifacts?.length ?? 0} artifact(s) required
          </div>
        </div>
      )
    case 'evaluation':
      return (
        <div className="agent-log-card">
          <div className="agent-log-card-head">Evaluation ({payload.model})</div>
          <div className="agent-log-criteria">
            {(payload.per_criterion ?? []).map((f) => (
              <span
                key={f.criterion_id}
                className="chip"
                style={{ borderColor: VERDICT_COLOR[f.verdict], color: 'var(--text-primary)' }}
                title={f.reasoning}
              >
                {f.criterion_id.split('_')[0]} {f.verdict}
              </span>
            ))}
          </div>
        </div>
      )
    case 'score': {
      // Show the criteria that FAILED, not just the number.
      //
      // The four vetoes are deterministic -- Python, no model call -- so they never
      // appeared in the `evaluation` event, and a reviewer watching the stream saw
      // three green SUPPORTED chips sitting directly above "score: 0.000" with
      // nothing to explain the contradiction. It reads as a broken scorer rather than
      // as a veto doing its job, which is the opposite of what a watchable loop is for.
      const failed = (payload.findings ?? []).filter((f) => f.verdict !== 'SUPPORTED')
      return (
        <div className="agent-log-row">
          <div>
            score: <strong>{Number(payload.value).toFixed(3)}</strong>
            {failed.length ? <span className="muted"> — {failed.length} criterion/criteria not supported</span> : null}
          </div>
          {failed.map((f) => (
            <div key={f.criterion_id} className="agent-log-criteria" style={{ marginTop: 4 }}>
              <span
                className="chip"
                style={{ borderColor: VERDICT_COLOR[f.verdict], color: 'var(--text-primary)' }}
                title={f.reasoning}
              >
                {f.criterion_id.split('_')[0]} {f.verdict}
              </span>
              <span className="muted" style={{ fontSize: 11 }}>{truncate(f.reasoning, 150)}</span>
            </div>
          ))}
        </div>
      )
    }
    case 'critique':
      // The text actually sent back to the proposer for the next round. Without it the
      // stream shows two independent-looking proposals and leaves the reviewer to
      // infer what changed the model's mind -- when the feed-forward IS the loop's
      // argument for existing.
      return (
        <div className="agent-log-card">
          <div className="agent-log-card-head">Fed back to the proposer</div>
          <div className="agent-log-card-body">{truncate(payload.text, 600)}</div>
        </div>
      )
    case 'gate':
      return (
        <div className="agent-log-row" style={{ color: GATE_COLOR[payload.decision] ?? 'inherit' }}>
          gate: <strong>{payload.decision}</strong>
          {payload.score !== undefined ? ` (score ${Number(payload.score).toFixed(3)})` : ''}
          {payload.unanswerable?.length ? ` — unanswerable: ${payload.unanswerable.join(', ')}` : ''}
          {payload.budget_note ? ` — ${payload.budget_note}` : ''}
          {payload.proposer_blocked ? ' — the proposer asked to stop' : ''}
        </div>
      )
    case 'iteration_finished':
      return <hr className="agent-log-rule" />
    case 'injection_attempt_recorded':
      return (
        <div className="agent-log-row bad">
          ⚠ injection attempt recorded: {payload.name}({truncate(JSON.stringify(payload.arguments ?? {}), 100)})
        </div>
      )
    case 'forced_tool_name_coerced':
      return (
        <div className="agent-log-row muted">
          note: {payload.model} answered under {payload.returned_name}, coerced to {payload.coerced_to}
        </div>
      )
    case 'work_item_written':
      return (
        <div className="agent-log-row">
          work item {payload.work_item_id} written for verdict {payload.from_verdict_id}
        </div>
      )
    case 'run_finished':
      return (
        <div className="agent-log-row muted">
          run finished — outcome {payload.outcome}
          {payload.wall_ms !== undefined ? ` · ${payload.wall_ms} ms` : ''}
        </div>
      )
    case 'client_error':
      return <div className="agent-log-row bad">stream error: {payload.detail}</div>
    case 'outcome':
      return null // rendered as the banner below the log, not as a log line
    default:
      return (
        <div className="agent-log-row muted">
          {kind}: {truncate(JSON.stringify(payload), 200)}
        </div>
      )
  }
}

// ═══ the terminal outcome banner ═════════════════════════════════════════════════════════════

function OutcomeBanner({ outcome }) {
  const style = OUTCOME_STYLE[outcome.status] ?? { color: 'var(--border-strong)', label: outcome.status, icon: '?' }
  return (
    <div className="outcome-banner" style={{ '--oc': style.color }} data-testid="decide-outcome" data-status={outcome.status}>
      <div className="outcome-banner-head">
        <span className="outcome-banner-icon" aria-hidden="true">
          {style.icon}
        </span>
        <span className="outcome-banner-label">{style.label}</span>
        {outcome.iterations_run !== undefined ? (
          <span className="dossier-note">
            {outcome.iterations_run} iteration{outcome.iterations_run === 1 ? '' : 's'}
            {outcome.final_score !== undefined && outcome.final_score !== null
              ? ` · final score ${Number(outcome.final_score).toFixed(3)}`
              : ''}
          </span>
        ) : null}
      </div>
      {outcome.score_trajectory?.length ? (
        <div className="outcome-trajectory mono">
          {outcome.score_trajectory.map((v) => Number(v).toFixed(2)).join(' → ')}
          {outcome.self_bias_suspected ? (
            <span className="chip flag" style={{ marginLeft: 8 }}>
              possible self-bias
            </span>
          ) : null}
        </div>
      ) : null}
      {outcome.detail ? <p className="dossier-note" style={{ margin: '6px 0 0' }}>{outcome.detail}</p> : null}
      {outcome.missing_criteria?.length ? (
        <p className="dossier-note" style={{ margin: '6px 0 0' }}>
          not addressed: {outcome.missing_criteria.join(', ')}
          {outcome.missing_narrative ? ` — ${outcome.missing_narrative}` : ''}
        </p>
      ) : null}
      {outcome.proposer_blocked ? (
        <p className="dossier-note" style={{ margin: '6px 0 0' }}>the proposer asked to stop: {outcome.blocked_reason}</p>
      ) : null}
      {outcome.budget_note ? <p className="dossier-note" style={{ margin: '6px 0 0' }}>{outcome.budget_note}</p> : null}

      {outcome.findings ? (
        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: 'pointer', fontSize: 12.5, color: 'var(--text-secondary)' }}>
            Full checklist ({Object.keys(outcome.findings).length} criteria graded)
          </summary>
          <div className="agent-log-criteria" style={{ marginTop: 8 }}>
            {Object.entries(outcome.findings).map(([criterionId, finding]) => (
              <span
                key={criterionId}
                className="chip"
                style={{ borderColor: VERDICT_COLOR[finding.verdict], color: 'var(--text-primary)' }}
                title={finding.reasoning}
              >
                {criterionId} — {finding.verdict}
              </span>
            ))}
          </div>
        </details>
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
  const [events, setEvents] = useState([])
  const [running, setRunning] = useState(false)
  const [outcome, setOutcome] = useState(null)
  const [unavailable, setUnavailable] = useState(null)
  const abortRef = useRef(null)
  const logRef = useRef(null)

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
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [events])

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
      setWiArtifacts(outcome.proposal.required_artifacts?.length ? [...outcome.proposal.required_artifacts] : [''])
      setWiSummary((outcome.proposal.reasoning || '').slice(0, 600))
      setWiResult(null)
      setWiError(null)
    }
  }, [outcome])

  function run() {
    if (abortRef.current) abortRef.current()
    setEvents([])
    setOutcome(null)
    setUnavailable(null)
    setRunning(true)
    abortRef.current = api.agent.decide(episodeId, null, (kind, payload) => {
      if (kind === 'client_error' && /503/.test(payload.detail ?? '')) {
        setUnavailable(payload.detail)
        setRunning(false)
        return
      }
      setEvents((prev) => [...prev, { key: prev.length, kind, payload }])
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

  async function addToDo() {
    const summary = wiSummary.trim()
    if (summary.length < 20 || summary.length > 600) {
      setWiError(`Rationale must be 20–600 characters (currently ${summary.length}).`)
      return
    }
    const artifacts = wiArtifacts.map((a) => a.trim()).filter(Boolean)
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
              required_artifacts: outcome.proposal.required_artifacts,
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

      {events.length > 0 ? (
        <div className="agent-log" ref={logRef} data-testid="decide-log">
          {events
            .filter((e) => e.kind !== 'outcome')
            .map((e) => (
              <LogRow key={e.key} kind={e.kind} payload={e.payload} />
            ))}
        </div>
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
                <input
                  data-testid={`wi-artifact-${i}`}
                  type="text"
                  value={artifact}
                  onChange={(e) => updateArtifact(i, e.target.value)}
                  placeholder="e.g. corrected NDC on a resubmitted claim"
                />
                <button type="button" onClick={() => removeArtifact(i)} title="Remove this artifact">
                  ✕
                </button>
              </div>
            ))}
            <button type="button" onClick={addArtifact}>
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
      <header className="masthead">
        <div>
          <h1>Agent analysis — {episodeId}</h1>
          <div className="sub">
            The agent layer never computes a number: every figure below was already decided by
            the deterministic engine. The agent only explains it and recommends what a human
            should do next. <a href="/">← Back to the dashboard</a>
          </div>
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
