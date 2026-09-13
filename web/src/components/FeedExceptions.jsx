import React from 'react'
import { formatMoney } from '../api.js'

// Feed-level exceptions are properties of *ingestion*, not of any claim — which is why they are not
// multiplied into the episode state space and why they get their own panel.
//
// Every one of these is a query, not a stored flag. Nothing was ever moved into an "orphan" table;
// the question is asked of the records at a cursor, which is why it can be asked at any cursor.
const SECTIONS = [
  {
    key: 'orphan_deposits',
    code: 'D-3',
    title: 'Orphan deposits',
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
    title: 'Allocation residuals',
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
    title: 'Parked records',
    blurb:
      'A document whose keys resolved to nothing, held rather than discarded, and re-checked on every later arrival. A crosswalk miss is not a missing claim — the mapping failed, which is a different fix with a different owner.',
    columns: [
      ['record_kind', 'Record', false],
      ['park_reason', 'Why', false],
      ['received_at', 'Received', false],
    ],
  },
  {
    key: 'duplicate_deliveries',
    code: 'D-1',
    title: 'Duplicate deliveries',
    blurb:
      'The same payload delivered twice — an SFTP rerun, or a retry after an ambiguous timeout. Collapsed on the source record id, never summed: summing would double-count real money.',
    columns: [
      ['source_system', 'Source', false],
      ['deliveries', 'Deliveries', true],
      ['payload_sha256', 'Payload digest', false],
    ],
  },
]

export default function FeedExceptions({ data }) {
  if (!data) return <p className="spinner">loading…</p>
  return (
    <div data-testid="feed-exceptions">
      {SECTIONS.map((section) => {
        const rows = data[section.key] ?? []
        return (
          <details key={section.key} className="evidence" open={rows.length > 0 && rows.length < 12}>
            <summary data-testid={`feed-section-${section.code}`}>
              <span className="chip">{section.code}</span>
              <strong style={{ fontWeight: 600 }}>{section.title}</strong>
              <span style={{ color: 'var(--text-muted)' }}>
                {rows.length} {rows.length === 1 ? 'row' : 'rows'}
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
                              {key.endsWith('_cents') ? formatMoney(row[key]) : String(row[key] ?? '—').slice(0, 48)}
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
