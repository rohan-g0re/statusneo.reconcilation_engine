import React, { useState } from 'react'
import Timeline, { Money } from './Timeline.jsx'

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
                {current.episode_disposition}
              </div>
              <div>
                <span className="verdict-code">{current.reimbursement_verdict}</span>{' '}
                <span className="verdict-code">{current.rebate_verdict}</span>
              </div>
              {current.reopened_from ? (
                <div className="chip reopened" style={{ marginTop: 6 }}>
                  reopened from {current.reopened_from}
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

        {current && (current.reason_codes.length > 0 || current.cross_track_flags.length > 0) ? (
          <p className="dossier-reasons">
            {current.reason_codes.map((code) => (
              <span className="chip" key={code}>{code}</span>
            ))}
            {current.cross_track_flags.map((flag) => (
              <span className="chip flag" key={flag}>{flag}</span>
            ))}
            {current.cross_track_flags.length > 0 ? (
              <span className="dossier-note">
                cross-track — a story no single-track view can tell
              </span>
            ) : null}
          </p>
        ) : null}

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
                    <td>{row.record_kind}</td>
                    <td><span className="chip">{row.park_reason}</span></td>
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
                    <td><span className="chip">{row.key_type}</span></td>
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
