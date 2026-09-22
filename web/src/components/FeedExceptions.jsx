import React from 'react'
import { formatMoney } from '../api.js'
import * as labels from '../labels.js'

// Feed-level exceptions are properties of *ingestion*, not of any claim — which is why they are not
// multiplied into the episode state space and why they get their own panel.
//
// Every one of these is a query, not a stored flag. Nothing was ever moved into an "orphan" table;
// the question is asked of the records at a cursor, which is why it can be asked at any cursor.
const SECTIONS = [
  {
    key: 'orphan_deposits',
    code: 'D-3',
    blurb:
      'Cash received with no linkable claim, rebate or remittance. Not a windfall — cash held without provenance is audit exposure, and it may be a crosswalk failure rather than genuinely unattributable money.',
    columns: [
      ['amount_cents', 'Amount', true],
      ['ach_trace_number', 'ACH trace', false],
      ['received_at', 'Received', false],
    ],
  },
  {
    key: 'allocation_residuals',
    code: 'D-7',
    blurb:
      'A single deposit covered N claims and the allocated amounts did not sum to it. The deposit is not wrong; the allocation is incomplete, and the residual is tracked rather than absorbed.',
    columns: [
      ['allocated_cents', 'Amount', true],
      ['bank_norm_id', 'Bank row', false],
      ['caused_by_received_at', 'Caused by', false],
    ],
  },
  {
    key: 'parked',
    code: 'D-6',
    blurb:
      'A document whose keys resolved to nothing, held rather than discarded, and re-checked on every later arrival. A crosswalk miss is not a missing claim — the mapping failed, which is a different fix with a different owner.',
    columns: [
      ['record_kind', 'Record', false],
      ['park_reason', 'Why', false],
      ['received_at', 'Received', false],
    ],
  },
  {
    key: 'orphan_rebates',
    code: 'D-4',
    blurb:
      'Rebate cash or a rebate event that cannot be tied to any qualified dispense in any other feed. Not a windfall — cash held without provenance is audit exposure.',
    columns: [
      ['record_kind', 'Record', false],
      ['park_reason', 'Why', false],
      ['received_at', 'Received', false],
    ],
  },
  {
    key: 'duplicate_deliveries',
    code: 'D-1',
    blurb:
      'The same payload delivered twice — an SFTP rerun, or a retry after an ambiguous timeout. Collapsed on the source record id, never summed: summing would double-count real money.',
    columns: [
      ['source_system', 'Source', false],
      ['deliveries', 'Deliveries', true],
      ['payload_sha256', 'Payload digest', false],
    ],
  },
]

// Dates render as dates. An ISO timestamp is exact and unreadable; every other surface in this app
// shows the day, so this one does too.
function formatCell(key, value) {
  if (key.endsWith('_cents')) return formatMoney(value)
  if (value === null || value === undefined) return '—'
  if (key.endsWith('_at') || key.endsWith('_date')) return String(value).slice(0, 10)
  // The two enums in these tables read as sentences rather than as SCREAMING_SNAKE. Park
  // reasons especially: they are four genuinely different problems with four different owners,
  // and "NO_KEY_MATCH" next to "AMBIGUOUS_KEY_MATCH" hides that distinction rather than making
  // it. Anything else is still truncated, but with an ellipsis so the cut is visible — a
  // payload digest chopped silently at 48 characters reads as a complete value.
  if (key === 'park_reason') return labels.parkReason(value).label
  if (key === 'record_kind') return labels.recordKind(value).label
  const text = String(value)
  return text.length > 48 ? `${text.slice(0, 48)}…` : text
}

export default function FeedExceptions({ data }) {
  if (!data) return <p className="spinner">loading…</p>
  return (
    <div data-testid="feed-exceptions">
      {SECTIONS.map((section) => {
        const rows = data[section.key] ?? []
        return (
          /*
            Open when there is something to see, closed when there is not. The rule this
            replaces was `rows.length > 0 && rows.length < 12`, which hid the section with the
            *most* rows — a panel that looked busy while concealing its largest item.
          */
          <details key={section.key} className="evidence" open={rows.length > 0}>
            <summary data-testid={`feed-section-${section.code}`}>
              {/*
                The sentence leads and the code follows. `D-3` says nothing to a reader, and
                nothing anywhere on the screen said what the letter D meant — it is a fourth
                namespace, not a fourth track, which is the confusion it reliably caused.
              */}
              <strong style={{ fontWeight: 600 }}>{labels.dataException(section.code).label}</strong>
              <span className="chip chip-code">{section.code}</span>
              <span style={{ color: 'var(--text-muted)' }}>
                {rows.length} {rows.length === 1 ? 'item' : 'items'}
              </span>
            </summary>
            <div style={{ padding: '10px 12px' }}>
              <p className="hint" style={{ marginTop: 0 }}>{section.blurb}</p>
              {rows.length === 0 ? (
                <p className="empty" style={{ padding: 0 }}>None at this cursor.</p>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        {section.columns.map(([key, label, numeric]) => (
                          <th key={key} className={numeric ? 'num' : undefined}>{label}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.slice(0, 40).map((row, index) => (
                        <tr key={index}>
                          {section.columns.map(([key, , numeric]) => (
                            <td key={key} className={numeric ? 'num' : 'mono'}>
                              {formatCell(key, row[key])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {rows.length > 40 ? (
                    <p className="empty" style={{ padding: '8px 10px' }}>
                      showing 40 of {rows.length}
                    </p>
                  ) : null}
                </div>
              )}
            </div>
          </details>
        )
      })}
    </div>
  )
}
