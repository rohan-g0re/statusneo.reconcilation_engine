import React from 'react'
import { formatMoney } from '../api.js'

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

const KIND_STYLE = {
  CLAIM: { dot: 'var(--accent)', label: 'Claim' },
  ACKNOWLEDGMENT: { dot: 'var(--accent)', label: 'Clearinghouse' },
  REMITTANCE: { dot: 'var(--status-warning)', label: 'Remittance' },
  CASH: { dot: 'var(--status-good)', label: 'Cash' },
  REBATE: { dot: 'var(--track-rebate)', label: '340B' },
  CLAWBACK: { dot: 'var(--status-serious)', label: 'Clawback' },
  REVERSAL: { dot: 'var(--status-serious)', label: 'Reversal' },
  VERDICT: { dot: 'var(--status-critical)', label: 'Verdict' },
}

const DISPOSITION_COLOR = {
  CLOSED: 'var(--status-good)',
  PENDING: 'var(--status-warning)',
  EXCEPTION: 'var(--status-critical)',
}

function Money({ cents, muted }) {
  if (cents === null || cents === undefined) return null
  const negative = cents < 0
  return (
    <span
      className="dossier-amount"
      style={{ color: muted ? 'var(--text-muted)' : negative ? 'var(--status-critical)' : undefined }}
    >
      {formatMoney(cents)}
    </span>
  )
}

export default function EpisodeDossier({ dossier, busy, onClose }) {
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

      <ol className="dossier-timeline" data-testid="dossier-timeline">
        {timeline.map((event, index) => {
          const style = KIND_STYLE[event.kind] ?? { dot: 'var(--border-strong)', label: event.kind }
          return (
            <li key={index} style={{ '--dot': style.dot }} data-testid={`event-${event.kind}`}>
              <div className="dossier-when">
                <time dateTime={event.at}>{event.at.slice(0, 10)}</time>
                <span className="dossier-kind" style={{ color: style.dot }}>
                  {style.label}
                </span>
              </div>
              <div className="dossier-body">
                <div className="dossier-headline">
                  {event.headline}
                  {event.amount_cents !== null && event.amount_cents !== undefined ? (
                    <>
                      {' '}
                      <Money cents={event.amount_cents} />
                    </>
                  ) : null}
                  {event.late ? (
                    <span className="chip" style={{ marginLeft: 6 }} title="Arrival lagged the event">
                      late-arriving
                    </span>
                  ) : null}
                </div>
                <div className="dossier-detail">{event.detail}</div>
                <div className="dossier-provenance">
                  {event.occurred_on && event.occurred_on !== event.at.slice(0, 10) ? (
                    <span title="The event happened on this date; we learned of it later">
                      happened {event.occurred_on}, learned {event.at.slice(0, 10)}
                    </span>
                  ) : null}
                  {event.source?.file ? (
                    <span className="mono">
                      {event.source.file}:{event.source.line}
                      {event.source.record_id ? ` · ${event.source.record_id}` : ''}
                    </span>
                  ) : null}
                  {event.source?.ach_trace_number ? (
                    <span className="mono">ACH {event.source.ach_trace_number}</span>
                  ) : null}
                </div>
              </div>
            </li>
          )
        })}
      </ol>

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
