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
//
// Timeline events carry no prose. Each one is a TAG (what kind of thing happened) and FACTS (that
// record's own fields, carried verbatim, with absent ones omitted). This view renders both
// generically — it never special-cases a tag's field list, because the whole point of `facts` is
// that its key set states what the record has, and a new field should render without a code change
// here.

const TAG_DOT = {
  PHARMACY_CLAIM: 'var(--accent)',
  PHARMACY_REVERSAL: 'var(--status-serious)',
  REMITTANCE: 'var(--status-warning)',
  REMITTANCE_CLAIM_LINE: 'var(--status-warning)',
  PROVIDER_LEVEL_ADJUSTMENT: 'var(--status-warning)',
  MEDICAL_SUBMISSION: 'var(--accent)',
  MEDICAL_ACKNOWLEDGMENT: 'var(--accent)',
  TPA_QUALIFICATION: 'var(--track-rebate)',
  TPA_REBATE_REQUEST: 'var(--track-rebate)',
  TPA_MANUFACTURER_DECISION: 'var(--track-rebate)',
  TPA_REVERSAL: 'var(--status-serious)',
  REBATE_BATCH: 'var(--track-rebate)',
  REBATE_DISPENSE_LINE: 'var(--track-rebate)',
  BANK_TRANSACTION: 'var(--status-good)',
  CASH: 'var(--status-good)',
  VERDICT: 'var(--status-critical)',
}

const DISPOSITION_COLOR = {
  CLOSED: 'var(--status-good)',
  PENDING: 'var(--status-warning)',
  EXCEPTION: 'var(--status-critical)',
}

// REMITTANCE_CLAIM_LINE -> "Remittance claim line". Prominent and skimmable; the raw tag is still
// shown verbatim alongside it (see the timeline render below) so nothing is lost for anyone who
// wants the exact identifier.
function humanizeTag(tag) {
  const lower = String(tag).toLowerCase().replace(/_/g, ' ')
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

// clp02_claim_status_code -> "CLP02 claim status code". EDI segment identifiers (a short letter
// run followed by digits, e.g. clp02, svc01, stc12) are kept upper-case because that is how they
// are written everywhere else in this domain; every other word is left as a normal word, with only
// the first one capitalised.
function humanizeKey(key) {
  return String(key)
    .split('_')
    .map((word, index) => {
      if (/^[a-z]{2,4}\d{1,2}$/i.test(word)) return word.toUpperCase()
      if (index === 0) return word.charAt(0).toUpperCase() + word.slice(1)
      return word
    })
    .join(' ')
}

// A `_cents` suffix is a formatting instruction, not part of the label — "amount_cents" is just
// "Amount" once it is rendered as money.
function labelForKey(key) {
  if (key.endsWith('_cents')) {
    const base = key.slice(0, -'_cents'.length)
    return base ? humanizeKey(base) : 'Amount'
  }
  return humanizeKey(key)
}

function isArrayOfObjects(value) {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => item !== null && typeof item === 'object' && !Array.isArray(item))
  )
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

// A single scalar fact value. Never prints `undefined`, `null`, or `[object Object]` — anything
// that is still an object or array at this point (a shape no tag currently produces, but facts are
// carried verbatim from records this view does not control) falls back to JSON rather than the
// default string coercion.
function FactScalar({ value, cents }) {
  if (value === null || value === undefined) return <span className="mono">—</span>
  if (typeof value === 'boolean') return <span className="mono">{value ? 'yes' : 'no'}</span>
  if (cents && typeof value === 'number') return <Money cents={value} />
  if (typeof value === 'object') return <span className="mono">{JSON.stringify(value)}</span>
  return <span className="mono">{String(value)}</span>
}

// One fact's value, rendered by shape rather than by name — this is what lets a tag's field list
// vary freely without a matching change here.
function FactValue({ factKey, value }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="mono">—</span>

    if (isArrayOfObjects(value)) {
      // adjustments, service_lines, and anything else shaped like a small line-item table.
      const columns = Array.from(new Set(value.flatMap((row) => Object.keys(row))))
      return (
        <div className="table-wrap fact-nested">
          <table>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column}>{labelForKey(column)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {value.map((row, index) => (
                <tr key={index}>
                  {columns.map((column) => (
                    <td key={column}>
                      <FactScalar value={row[column]} cents={column.endsWith('_cents')} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    }

    // reason_codes, reject_codes, cross_track_flags, and any other array of primitives.
    return (
      <span>
        {value.map((item, index) => (
          <span className="chip" key={index}>
            {item !== null && typeof item === 'object' ? JSON.stringify(item) : String(item)}
          </span>
        ))}
      </span>
    )
  }

  if (value !== null && typeof value === 'object') {
    // A single nested record — e.g. the PBM path's singular `service_line`, where the medical path
    // carries a plural `service_lines` list instead.
    const entries = Object.entries(value).filter(([, v]) => v !== null && v !== undefined)
    if (entries.length === 0) return <span className="mono">—</span>
    return (
      <div className="fact-object">
        {entries.map(([childKey, childValue]) => (
          <div className="fact-object-row" key={childKey}>
            <span className="fact-object-key mono">{labelForKey(childKey)}</span>
            <FactScalar value={childValue} cents={childKey.endsWith('_cents')} />
          </div>
        ))}
      </div>
    )
  }

  return <FactScalar value={value} cents={factKey.endsWith('_cents')} />
}

function FactRow({ factKey, value }) {
  const wide = isArrayOfObjects(value) || (value !== null && typeof value === 'object' && !Array.isArray(value))
  return (
    <div className={wide ? 'dossier-fact wide' : 'dossier-fact'}>
      <dt>{labelForKey(factKey)}</dt>
      <dd>
        <FactValue factKey={factKey} value={value} />
      </dd>
    </div>
  )
}

// `facts` renders generically: whatever keys a record's projection kept, in the order the API sent
// them. Absent fields are already dropped server-side, so every key here is one worth showing.
function FactsList({ facts }) {
  if (!facts || typeof facts !== 'object') return null
  const entries = Object.entries(facts).filter(([, value]) => value !== null && value !== undefined)
  if (entries.length === 0) return null
  return (
    <dl className="dossier-facts">
      {entries.map(([key, value]) => (
        <FactRow key={key} factKey={key} value={value} />
      ))}
    </dl>
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
          const dot = TAG_DOT[event.tag] ?? 'var(--border-strong)'
          return (
            <li key={index} style={{ '--dot': dot }} data-testid={`event-${event.tag}`}>
              <div className="dossier-when">
                <time dateTime={event.at}>{event.at.slice(0, 10)}</time>
                <span className="dossier-kind" title={event.tag}>
                  {event.tag}
                </span>
              </div>
              <div className="dossier-body">
                <div className="dossier-headline">
                  {humanizeTag(event.tag)}
                  {event.late ? (
                    <span className="chip" style={{ marginLeft: 6 }} title="Arrival lagged the event">
                      late-arriving
                    </span>
                  ) : null}
                </div>
                <FactsList facts={event.facts} />
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
