import React from 'react'
import { formatMoney } from '../api.js'

const DOT = { CLOSED: 'var(--status-good)', PENDING: 'var(--status-warning)', EXCEPTION: 'var(--status-critical)' }

function Field({ label, value }) {
  return (
    <div className="field">
      <div className="k">{label}</div>
      <div className="v">{value ?? '—'}</div>
    </div>
  )
}

// One episode, traced. This is the walkthrough view: the debrief asks for one claim followed end to
// end, and that is two directions at once —
//
//   * lineage runs DOWNWARD: every number points back at the raw source row that produced it;
//   * the audit trail runs ACROSS TIME: every verdict the episode has held, in order.
//
// Both are shown, because they answer different questions. Lineage answers "where did this number
// come from"; the audit trail answers "what did we believe, and when did we stop believing it".
export default function EpisodePanel({ detail, trace, busy }) {
  if (busy && !detail) return <p className="spinner">loading episode…</p>
  if (!detail) {
    return (
      <p className="empty" data-testid="episode-empty">
        Select an episode to trace it end to end.
      </p>
    )
  }

  const verdict = detail.verdict
  return (
    <div data-testid="episode-detail">
      <div className="detail-grid">
        <Field label="Episode" value={detail.episode_id} />
        <Field label="Track" value={detail.track === 'PHARMACY' ? 'Pharmacy benefit' : 'Medical benefit'} />
        <Field label="Service date" value={detail.date_of_service} />
        <Field label="Age at cursor" value={`${detail.age_days} days`} />
        <Field label="NDC" value={detail.ndc11} />
        <Field label="Payer" value={detail.payer_id} />
        {detail.track === 'PHARMACY' ? (
          <>
            <Field label="Pharmacy NPI" value={detail.pharmacy_npi} />
            <Field label="Rx / fill" value={`${detail.rx_number} / ${detail.fill_number}`} />
          </>
        ) : (
          <>
            <Field label="Billing provider" value={detail.billing_provider_npi} />
            <Field label="CLM01" value={detail.clm01} />
          </>
        )}
        <Field label="340B flagged" value={detail.is_340b_flagged ? 'yes' : 'no'} />
      </div>

      {!verdict ? (
        <p className="empty">
          This episode exists at this cursor but has no verdict yet — a real state, not an error.
        </p>
      ) : (
        <>
          <h3 style={{ fontSize: 13, margin: '0 0 8px', color: 'var(--text-secondary)' }}>Verdict</h3>
          <div className="detail-grid">
            <Field
              label="Disposition"
              value={
                <>
                  <span style={{ color: DOT[verdict.episode_disposition] }} aria-hidden="true">●</span>{' '}
                  {verdict.episode_disposition}
                </>
              }
            />
            <Field label="Reimbursement" value={verdict.reimbursement_verdict} />
            <Field label="Rebate" value={verdict.rebate_verdict} />
            <Field label="Evaluated at" value={verdict.cursor_at} />
          </div>

          {verdict.reopened_from ? (
            <p className="banner" data-testid="reopened-banner">
              <span className="chip reopened">reopened from {verdict.reopened_from}</span>
              Previously settled at {verdict.previously_closed_at}. Money already recognised is now at
              risk of being un-recognised, which outranks money that was never collected.
            </p>
          ) : null}

          <div className="table-wrap" style={{ margin: '12px 0 16px' }}>
            <table className="money-table">
              <thead>
                <tr>
                  <th>Track</th>
                  <th className="num">Expected</th>
                  <th className="num">Received</th>
                  <th className="num">Variance</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Reimbursement</td>
                  <td className="num">{formatMoney(verdict.expected_reimbursement_cents)}</td>
                  <td className="num">{formatMoney(verdict.received_reimbursement_cents)}</td>
                  <td className={`num ${verdict.reimbursement_variance_cents ? 'variance-bad' : 'variance-ok'}`}>
                    {formatMoney(verdict.reimbursement_variance_cents)}
                  </td>
                </tr>
                <tr>
                  <td>340B rebate</td>
                  <td className="num">{formatMoney(verdict.expected_rebate_cents)}</td>
                  <td className="num">{formatMoney(verdict.received_rebate_cents)}</td>
                  <td className={`num ${verdict.rebate_variance_cents ? 'variance-bad' : 'variance-ok'}`}>
                    {formatMoney(verdict.rebate_variance_cents)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {verdict.reason_codes.length > 0 ? (
            <p style={{ margin: '0 0 10px' }} data-testid="reason-codes">
              {verdict.reason_codes.map((code) => (
                <span className="chip" key={code}>{code}</span>
              ))}
            </p>
          ) : null}
          {verdict.cross_track_flags.length > 0 ? (
            <p style={{ margin: '0 0 14px' }} data-testid="cross-track-flags">
              {verdict.cross_track_flags.map((flag) => (
                <span className="chip flag" key={flag}>{flag}</span>
              ))}
              <span style={{ color: 'var(--text-muted)', fontSize: 12.5, marginLeft: 6 }}>
                cross-track — invisible to any single-track view
              </span>
            </p>
          ) : null}

          <h3 style={{ fontSize: 13, margin: '0 0 8px', color: 'var(--text-secondary)' }}>
            Lineage — every number traced to its source record
          </h3>
          <div data-testid="evidence-list">
            {(detail.evidence ?? []).map((row, index) => (
              <details className="evidence" key={index}>
                <summary>
                  <span className="chip">{row.role}</span>
                  <span className="mono" style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
                    {row.source_file}:{row.source_line_no}
                  </span>
                  <span style={{ color: 'var(--text-muted)' }}>{row.source_record_id}</span>
                  <span style={{ color: 'var(--text-muted)' }}>arrived {row.received_at}</span>
                </summary>
                <pre>{row.payload}</pre>
              </details>
            ))}
          </div>

          {(detail.cash_allocations ?? []).length > 0 ? (
            <>
              <h3 style={{ fontSize: 13, margin: '16px 0 8px', color: 'var(--text-secondary)' }}>
                Cash allocated to this episode
              </h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th className="num">Amount</th>
                      <th>Matched on</th>
                      <th>Caused by a record received</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.cash_allocations.map((row, index) => (
                      <tr key={index}>
                        <td className="num">{formatMoney(row.allocated_cents)}</td>
                        <td><span className="chip">{row.basis}</span></td>
                        <td className="mono">{row.caused_by_received_at}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </>
      )}

      {trace && trace.verdict_history.length > 0 ? (
        <>
          <h3 style={{ fontSize: 13, margin: '18px 0 8px', color: 'var(--text-secondary)' }}>
            Audit trail — what we believed, and when we stopped
          </h3>
          <ul className="timeline" data-testid="verdict-history">
            {trace.verdict_history.map((entry, index) => (
              <li key={index} style={{ '--dot': DOT[entry.episode_disposition] }}>
                <div className="when">{entry.cursor_at}</div>
                <div>
                  {entry.episode_disposition} ·{' '}
                  <span className="verdict-code">{entry.reimbursement_verdict}</span>{' '}
                  <span className="verdict-code">{entry.rebate_verdict}</span>
                  {entry.reopened_from ? (
                    <span className="chip reopened" style={{ marginLeft: 6 }}>
                      reopened from {entry.reopened_from}
                    </span>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {trace ? (
        <>
          <h3 style={{ fontSize: 13, margin: '18px 0 8px', color: 'var(--text-secondary)' }}>
            Crosswalk — the keys that resolved to this episode
          </h3>
          <div className="table-wrap">
            <table data-testid="crosswalk-table">
              <thead>
                <tr>
                  <th>Key type</th>
                  <th>Key value</th>
                  <th>First seen</th>
                </tr>
              </thead>
              <tbody>
                {trace.crosswalk_keys.map((row, index) => (
                  <tr key={index}>
                    <td><span className="chip">{row.key_type}</span></td>
                    <td className="mono">{row.key_value}</td>
                    <td className="mono">{row.first_seen_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </div>
  )
}
