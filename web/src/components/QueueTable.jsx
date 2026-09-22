import React from 'react'
import { formatMoney } from '../api.js'
import * as labels from '../labels.js'

// The four the server's whitelist accepts, worded for a reader rather than named after the
// column they sort on. The values are the API's and cannot change: anything outside
// QUEUE_ORDERINGS comes back a 422.
const ORDERINGS = [
  ['reopened_first', 'Reopened first'],
  ['age_desc', 'Oldest first'],
  ['variance_desc', 'Biggest money first'],
  ['episode_id', 'Claim number'],
]

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** `2025-08-20` reads as `20 Aug 2025`. Parsed by hand rather than through Date, which would
 *  shift the day backwards for anyone west of UTC — a date-of-service is a calendar fact, not
 *  a moment, and it has no timezone to be converted from. */
function formatDate(iso) {
  if (typeof iso !== 'string' || iso.length < 10) return iso ?? '—'
  const [year, month, day] = iso.slice(0, 10).split('-')
  const name = MONTHS[Number(month) - 1]
  return name ? `${Number(day)} ${name} ${year}` : iso
}

/** One track's verdict: what it means, with the code kept as a quotable suffix. */
function Verdict({ kind, code }) {
  const { label, detail } = labels.verdict(code)
  return (
    <span className="queue-verdict" title={detail || code}>
      <span className="queue-verdict-kind">{kind}</span>
      <span className="queue-verdict-label">{label}</span>
      <span className="verdict-code">{code}</span>
    </span>
  )
}

// Prioritisation is a deterministic sort over verdicts, never a judgement — which is exactly why the
// orderings are a closed whitelist on the server. A free-form ORDER BY from the client would be an
// injection point, and "rank by importance" would be an opinion the engine has no business holding.
export default function QueueTable({ rows, orderBy, onOrderBy, selected, onSelect, busy }) {
  return (
    <>
      {/* Class, not inline style. These three values were hardcoded as 10 / 10 / 12.5 px, and a
          browser measurement caught the 12.5 as the one text size on the screen that sits off
          the type scale. A scale that anything may opt out of inline is a suggestion. */}
      <div className="queue-controls">
        <label htmlFor="order-by" className="queue-controls-label">
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
          <table data-testid="queue-table" className="queue-table">
            {/*
              Explicit widths, because auto layout starves the one column that matters. Every
              other cell is `nowrap` and so claims its natural width first, leaving the verdict
              sentences to wrap at four or five characters a line. Fixed layout makes the
              allocation a decision rather than a side effect of content length.
            */}
            <colgroup>
              <col style={{ width: '17%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '13%' }} />
              <col style={{ width: '9%' }} />
              <col style={{ width: '36%' }} />
              <col style={{ width: '15%' }} />
            </colgroup>
            <thead>
              <tr>
                <th>Claim</th>
                <th>Billed to</th>
                <th>Dispensed</th>
                <th className="num">Age</th>
                {/*
                  One column where there were three. The two it merges were bare codes — two
                  four-character tokens side by side with nothing saying which was insurance and
                  which was the rebate, and the only place that mapping appeared was a column
                  header inside a collapsed accordion on another panel.

                  The third was `Why`, a bag of up to five raw reason chips. Once the verdicts
                  read as sentences the two columns said the same thing twice: A-17 renders as
                  "Paid twice" and its reason code DUPLICATE_PAYMENT renders as "Paid twice".
                  The reason codes are not lost — they are on the episode panel, in full, where
                  there is room for them.
                */}
                <th>What’s wrong</th>
                <th className="num">Outstanding</th>
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
                  <td className="mono">
                    {row.episode_id}
                    {/*
                      Reopened is a property of the row, not one more thing that is wrong with
                      the claim, so it marks the claim rather than joining a list of reasons.
                      It is also the strongest signal in the default sort — money that was
                      already recognised and has come undone outranks money never collected.
                    */}
                    {row.reopened_from ? (
                      <span
                        className="queue-reopened"
                        title={`Was ${labels.disposition(row.reopened_from).label.toLowerCase()} and came back`}
                      >
                        reopened
                      </span>
                    ) : null}
                  </td>
                  <td>{row.track === 'PHARMACY' ? 'Pharmacy' : 'Medical'}</td>
                  <td className="mono">{formatDate(row.date_of_service)}</td>
                  <td className="num">{row.age_days} days</td>
                  <td className="queue-verdicts">
                    <Verdict kind="Insurance" code={row.reimbursement_verdict} />
                    {/*
                      C-00 is "this claim has no 340B rebate". Rendering it as a line of its own
                      puts an absence next to a problem and makes two thirds of the column read as
                      though something were wrong with it.
                    */}
                    {labels.isAbsentRebate(row.rebate_verdict) ? null : (
                      <Verdict kind="340B rebate" code={row.rebate_verdict} />
                    )}
                  </td>
                  <td className="num" title="Negative means an overpayment — a refund liability">
                    {formatMoney(row.total_variance_cents)}
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
