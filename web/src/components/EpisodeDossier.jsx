import React, { useState } from 'react'
import Timeline, { Money } from './Timeline.jsx'
import * as labels from '../labels.js'

/** One track's verdict, named. The code stays as a quotable suffix. */
function TrackVerdict({ kind, code }) {
  const { label, detail } = labels.verdict(code)
  return (
    <div className="dossier-track" title={detail || code}>
      <span className="dossier-track-kind">{kind}</span>
      <span className="dossier-track-label">{label}</span>
      <span className="verdict-code">{code}</span>
    </div>
  )
}

// The episode, read as a story.
//
// This is the view the whole design points at. An episode is the unit of reconciliation, and the
// question an operator actually asks is "what happened to this claim?" — which runs in one
// direction, through time, mixing things that happened in the world with things we concluded about
// them. Six separate tables cannot be read that way; one ordered narrative can.
//
// It is also exactly the payload the agent layer receives. Whatever a person can see here, the agent
// gets in one call — and nothing in it was invented by a model: every line is composed in Python
// from fields the records carry.
//
// The event-by-event timeline itself — its generic fact rendering and its source-record drilldown
// — lives in Timeline.jsx, so a second screen can render the same timeline without reimplementing
// it. This component owns the surrounding dossier: identity, verdict, economics, the Simple/Detailed
// toggle, the unresolved-records table, and the raw evidence underneath.

const DISPOSITION_COLOR = {
  CLOSED: 'var(--status-good)',
  PENDING: 'var(--status-warning)',
  EXCEPTION: 'var(--status-critical)',
}

export default function EpisodeDossier({ dossier, busy, onClose }) {
  // Simple is the default: an operator opening an episode wants "what happened", not a provenance
  // audit. Detailed is one click away, not a second surface, because it is the same timeline with
  // more of each event's own facts shown — never a different data source. The toggle buttons live
  // here (in the sticky header); Timeline only reads the resulting `view` value.
  const [view, setView] = useState('simple')

  if (busy && !dossier) return <p className="spinner">loading the episode…</p>
  if (!dossier) {
    return (
      <p className="empty" data-testid="dossier-empty">
        Pick an episode from a queue above to see everything that happened to it — when the claim was
        filed, what the payer said, when the money moved, how the 340B rebate went, and every point at
        which the verdict changed.
      </p>
    )
  }

  const { identity, current, economics, timeline, counts, unresolved } = dossier

  return (
    <div data-testid="dossier">
      <div className="dossier-sticky">
        <div className="dossier-head">
          <div>
            <div className="dossier-id">{dossier.episode_id}</div>
            <div className="dossier-drug">
              {identity.drug} · {identity.quantity} units ·{' '}
              {identity.track === 'PHARMACY' ? 'pharmacy benefit' : 'medical benefit'} ·{' '}
              {identity.payer}
            </div>
            <div className="dossier-keys">
              dispensed {identity.date_of_service}
              {identity.rx_number ? ` · Rx ${identity.rx_number}/${identity.fill_number}` : null}
              {identity.clm01 ? ` · CLM01 ${identity.clm01}` : null}
              {identity.is_340b_flagged ? ' · flagged 340B at the point of sale' : null}
            </div>
          </div>
          {current ? (
            <div className="dossier-verdict" style={{ '--d': DISPOSITION_COLOR[current.episode_disposition] }}>
              <div className="dossier-disposition" data-testid="dossier-disposition">
                {labels.disposition(current.episode_disposition).label}
                <span className="dossier-enum">{current.episode_disposition}</span>
              </div>
              {/*
                Two labelled rows, not two adjacent pills. The pair this replaces was two
                four-character codes side by side with nothing saying which was which — and the
                only place that mapping appeared was a column header inside the collapsed
                evidence accordion at the bottom of this same panel.
              */}
              <TrackVerdict kind="Insurance" code={current.reimbursement_verdict} />
              <TrackVerdict kind="340B rebate" code={current.rebate_verdict} />
              {current.reopened_from ? (
                <div className="chip reopened" style={{ marginTop: 6 }}>
                  was {labels.disposition(current.reopened_from).label.toLowerCase()}, reopened
                </div>
              ) : null}
            </div>
          ) : (
            <div className="dossier-verdict">
              <div className="dossier-disposition">no verdict yet</div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                at this cursor the claim exists and has not been evaluated
              </div>
            </div>
          )}
        </div>

        {current && current.reason_codes.length > 0 ? (
          <p className="dossier-reasons">
            {current.reason_codes.map((code) => (
              <span className="chip" key={code} title={code}>
                {labels.reason(code).label}
              </span>
            ))}
          </p>
        ) : null}

        {/*
          Cross-track flags get a band of their own rather than a chip in the row above. They are
          compliance findings, not diagnostics: X-1 means the insurer refused to pay and the
          manufacturer paid the rebate anyway, which leaves the hospital holding money it may owe
          back on a claim that paid nothing. Rendered as a third grey chip after two reason codes,
          the single most valuable finding in the product read as the least.
        */}
        {current?.cross_track_flags.map((code) => {
          const { label, detail } = labels.flag(code)
          return (
            <div className="dossier-flag" key={code} data-testid={`flag-${code}`}>
              <span className="dossier-flag-head">
                <span aria-hidden="true">▲</span> {label}
                <span className="verdict-code">{code}</span>
              </span>
              <span className="dossier-flag-detail">{detail}</span>
            </div>
          )
        })}

        <div className="view-toggle" role="group" aria-label="Timeline detail level">
          <button
            type="button"
            data-testid="view-simple"
            aria-pressed={view === 'simple'}
            onClick={() => setView('simple')}
          >
            Simple
          </button>
          <button
            type="button"
            data-testid="view-detailed"
            aria-pressed={view === 'detailed'}
            onClick={() => setView('detailed')}
          >
            Detailed
          </button>
        </div>
      </div>

      {economics ? (
        <div className="dossier-money" data-testid="dossier-economics">
          <div>
            <span className="k">Reimbursement</span>
            <span className="v">
              <Money cents={economics.received_reimbursement_cents} /> of{' '}
              <Money cents={economics.expected_reimbursement_cents} muted /> expected
              {economics.reimbursement_variance_cents !== 0 ? (
                <>
                  {' · '}
                  <Money cents={economics.reimbursement_variance_cents} /> outstanding
                </>
              ) : null}
            </span>
          </div>
          <div>
            <span className="k">340B rebate</span>
            <span className="v">
              {economics.expected_rebate_cents === 0 && economics.received_rebate_cents === 0 ? (
                <span style={{ color: 'var(--text-muted)' }}>no rebate expected</span>
              ) : (
                <>
                  <Money cents={economics.received_rebate_cents} /> of{' '}
                  <Money cents={economics.expected_rebate_cents} muted /> expected
                  {economics.rebate_variance_cents !== 0 ? (
                    <>
                      {' · '}
                      <Money cents={economics.rebate_variance_cents} /> outstanding
                    </>
                  ) : null}
                </>
              )}
            </span>
          </div>
        </div>
      ) : null}

      <h3 className="dossier-section">
        What happened
        <span className="dossier-note">
          {counts.records} records · {counts.cash_allocations} cash movements ·{' '}
          {counts.verdict_changes} verdict change{counts.verdict_changes === 1 ? '' : 's'} across{' '}
          {counts.verdicts_written} evaluations
        </span>
      </h3>

      <Timeline timeline={timeline} view={view} />

      {unresolved && unresolved.length > 0 ? (
        <>
          <h3 className="dossier-section">
            Records that tried to reach this claim and could not
            <span className="dossier-note">
              a crosswalk miss leaves nothing attached, which is exactly why it needs saying here
            </span>
          </h3>
          <div className="table-wrap" data-testid="dossier-unresolved">
            <table>
              <thead>
                <tr>
                  <th>Record</th>
                  <th>Why it parked</th>
                  <th>Key it carried</th>
                  <th>Arrived</th>
                </tr>
              </thead>
              <tbody>
                {unresolved.map((row, index) => (
                  <tr key={index}>
                    <td>{labels.recordKind(row.record_kind).label}</td>
                    <td><span className="chip" title={row.park_reason}>{labels.parkReason(row.park_reason).label}</span></td>
                    <td className="mono">{row.key_value}</td>
                    <td className="mono">{row.received_at.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <details className="evidence" style={{ marginTop: 16 }}>
        <summary>
          The raw material — every verdict written, and every key that resolved here
        </summary>
        <div style={{ padding: '10px 12px' }}>
          <div className="table-wrap" style={{ marginBottom: 12 }}>
            <table data-testid="dossier-verdict-log">
              <thead>
                <tr>
                  <th>Evaluated at</th>
                  <th>Disposition</th>
                  <th>Reimb.</th>
                  <th>Rebate</th>
                  <th>Reasons</th>
                </tr>
              </thead>
              <tbody>
                {dossier.verdict_log.map((row, index) => (
                  <tr key={index}>
                    <td className="mono">{row.cursor_at.slice(0, 10)}</td>
                    <td>
                      <span style={{ color: DISPOSITION_COLOR[row.episode_disposition] }} aria-hidden="true">●</span>{' '}
                      {row.episode_disposition}
                    </td>
                    <td><span className="verdict-code">{row.reimbursement_verdict}</span></td>
                    <td><span className="verdict-code">{row.rebate_verdict}</span></td>
                    <td>{row.reason_codes.join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Key type</th>
                  <th>Value</th>
                  <th>First seen</th>
                </tr>
              </thead>
              <tbody>
                {dossier.crosswalk_keys.map((row, index) => (
                  <tr key={index}>
                    <td><span className="chip" title={row.key_type}>{labels.keyType(row.key_type).label}</span></td>
                    <td className="mono">{row.key_value}</td>
                    <td className="mono">{row.first_seen_at.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </details>
    </div>
  )
}
