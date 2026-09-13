import React from 'react'
import { formatMoney } from '../api.js'

const ORDERINGS = [
  ['reopened_first', 'Reopened first, then oldest'],
  ['age_desc', 'Oldest first'],
  ['variance_desc', 'Largest variance first'],
  ['episode_id', 'Episode id'],
]

// Prioritisation is a deterministic sort over verdicts, never a judgement — which is exactly why the
// orderings are a closed whitelist on the server. A free-form ORDER BY from the client would be an
// injection point, and "rank by importance" would be an opinion the engine has no business holding.
export default function QueueTable({ rows, orderBy, onOrderBy, selected, onSelect, busy }) {
  return (
    <>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10 }}>
        <label htmlFor="order-by" style={{ color: 'var(--text-secondary)', fontSize: 12.5 }}>
          Rank by
        </label>
        <select
          id="order-by"
          data-testid="order-by"
          value={orderBy}
          onChange={(event) => onOrderBy(event.target.value)}
        >
          {ORDERINGS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <span className="spinner" data-testid="queue-count">
          {busy ? 'loading…' : `${rows.length} episode${rows.length === 1 ? '' : 's'}`}
        </span>
      </div>

      {rows.length === 0 && !busy ? (
        <p className="empty" data-testid="queue-empty">
          No episodes in this queue at this cursor. An empty queue is an answer, not a missing one.
        </p>
      ) : (
        <div className="table-wrap queue-scroll">
          <table data-testid="queue-table">
            <thead>
              <tr>
                <th>Episode</th>
                <th>Track</th>
                <th>Service date</th>
                <th className="num">Age</th>
                <th>Reimb.</th>
                <th>Rebate</th>
                <th className="num">Variance</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.episode_id}
                  // A clickable row that cannot be reached by keyboard is unusable for anyone not
                  // using a mouse. The role and handler are what make it an actual control.
                  role="button"
                  tabIndex={0}
                  aria-current={selected === row.episode_id ? 'true' : undefined}
                  aria-selected={selected === row.episode_id}
                  data-testid={`queue-row-${row.episode_id}`}
                  onClick={() => onSelect(row.episode_id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onSelect(row.episode_id)
                    }
                  }}
                >
                  <td className="mono">{row.episode_id}</td>
                  <td>{row.track === 'PHARMACY' ? 'Pharmacy' : 'Medical'}</td>
                  <td className="mono">{row.date_of_service}</td>
                  <td className="num">{row.age_days}d</td>
                  <td>
                    <span className="verdict-code">{row.reimbursement_verdict}</span>
                  </td>
                  <td>
                    <span className="verdict-code">{row.rebate_verdict}</span>
                  </td>
                  <td className="num" title="Negative means an overpayment — a refund liability">
                    {formatMoney(row.total_variance_cents)}
                  </td>
                  <td>
                    {row.reopened_from ? (
                      <span className="chip reopened">reopened from {row.reopened_from}</span>
                    ) : null}
                    {row.reason_codes.map((code) => (
                      <span className="chip" key={code}>
                        {code}
                      </span>
                    ))}
                    {!row.reopened_from && row.reason_codes.length === 0 ? (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
