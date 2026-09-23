import React from 'react'
import * as labels from '../labels.js'

// What kinds of problem are in the book, rather than which pair of codes each claim holds.
//
// This was the last panel on the operations screen and it did not belong there. It carries no
// money at all -- only episode counts -- and on the demo profile twenty-three of its twenty-five
// rows say "1", so a reader scrolling past learned nothing twice. It is a reference: the place
// you come to when a code on another screen needs explaining.
//
// It consumes `overview`, which the operations screen already fetches on every cursor move. No
// new endpoint, no new request, no route.

// Families, in the order an operator cares about them. The mapping is by reason code rather than
// by verdict, because `overview.reason_codes` is the only grouping the API returns that has no
// row limit -- `verdict_pairs` is capped at 25, so its counts do not sum to the book and a
// "families" view built on it would quietly under-report.
const FAMILIES = [
  {
    key: 'short',
    title: 'Not fully settled',
    blurb: 'Money that was supposed to arrive and did not, or arrived and was never confirmed.',
    codes: [
      'UNDERPAID',
      'ADJUSTMENT_RESIDUAL',
      'NO_CASH',
      'AWAITING_CASH',
      'CASH_MISMATCH',
      'SETTLEMENT_MISSING',
    ],
  },
  {
    key: 'refused',
    title: 'Refused',
    blurb: 'Someone said no. The question is whether to appeal or write it off.',
    codes: [
      'DENIED',
      'REJECTED_AT_POS',
      'CLEARINGHOUSE_REJECTED',
      'APPEAL_LOST',
      'APPEAL_PENDING',
      'APPEAL_WON_NO_CASH',
      'TOTAL_LOSS',
    ],
  },
  {
    key: 'rebate',
    title: 'Rebate outstanding',
    blurb: 'A second receivable, from a counterparty most claim systems do not model.',
    codes: [
      'REBATE_AWAITING_QUALIFICATION',
      'REBATE_AWAITING_SUBMISSION',
      'REBATE_AWAITING_MANUFACTURER',
      'REBATE_AWAITING_PAYMENT',
      'REBATE_NO_CASH',
      'REBATE_UNDERPAID',
      'REBATE_REJECTED',
      'REBATE_NOT_QUALIFIED',
      'REBATE_RE_REQUEST_AVAILABLE',
    ],
  },
  {
    key: 'reversed',
    title: 'Taken back, or paid twice',
    blurb: 'Money that moved the wrong way, or moved twice.',
    codes: [
      'OVERPAID',
      'DUPLICATE_PAYMENT',
      'DUPLICATE_REMITTANCE',
      'DUPLICATE_REBATE',
      'REVERSAL_CASH_NOT_RETURNED',
      'RECOUPMENT_UNTRACEABLE',
      'REBATE_CLAWED_BACK',
    ],
  },
  {
    key: 'compliance',
    title: 'Compliance exposure',
    blurb: 'Visible only by looking at both tracks at once. The highest-value rows in the book.',
    codes: [
      'DENIED_WITH_REBATE_PAID',
      'REBATE_ON_UNDISPENSED_CLAIM',
      'QUALIFICATION_UNDERMINED_BY_RECOUPMENT',
      'CORRELATED_CASH_GAP',
    ],
  },
  {
    key: 'waiting',
    title: 'Waiting, no defect',
    blurb: 'Nothing is wrong. Somebody outside owes us a response.',
    codes: ['AWAITING_REMITTANCE'],
  },
  {
    key: 'undecidable',
    title: 'We could not tell',
    blurb: 'The engine knows it cannot decide, and says so rather than reporting a false zero.',
    codes: ['INSUFFICIENT_DATA'],
  },
  {
    key: 'data',
    title: 'Data faults',
    blurb: 'A problem with what arrived, not with the claim.',
    codes: [
      'CROSSWALK_MISS',
      'ORPHAN_DEPOSIT',
      'ORPHAN_REBATE',
      'ALLOCATION_RESIDUAL',
      'DUPLICATE_DELIVERY',
      'MALFORMED_RECORD',
    ],
  },
]

export default function VerdictLibrary({ overview }) {
  if (!overview) return <p className="spinner">loading…</p>

  const counts = new Map(overview.reason_codes.map((row) => [row.reason_code, row.episodes]))
  // Anything the engine reported that no family claims. Rendered rather than dropped: a family
  // list that silently swallows a code is how this screen would start lying as the engine grows.
  const claimed = new Set(FAMILIES.flatMap((family) => family.codes))
  const unclaimed = overview.reason_codes.filter((row) => !claimed.has(row.reason_code))

  return (
    <div className="layout-grid">
      <div className="layout-left">
        <section className="panel">
          <h2>What kinds of problem are in the book</h2>
          <p className="hint">
            Grouped by what an operator does about them. A claim can appear in more than one
            group.
          </p>
          <div className="family-list">
            {FAMILIES.map((family) => {
              const rows = family.codes
                .map((code) => ({ code, episodes: counts.get(code) ?? 0 }))
                .filter((row) => row.episodes > 0)
                .sort((a, b) => b.episodes - a.episodes)
              const total = rows.reduce((sum, row) => sum + row.episodes, 0)
              return (
                <section className="family" key={family.key} data-testid={`family-${family.key}`}>
                  <div className="family-head">
                    <h3>{family.title}</h3>
                    <span className="family-count">{total}</span>
                  </div>
                  <p className="family-blurb">{family.blurb}</p>
                  {rows.length === 0 ? (
                    <p className="family-empty">None in the book at this cursor.</p>
                  ) : (
                    <ul className="family-reasons">
                      {rows.map((row) => (
                        <li key={row.code}>
                          <span className="family-reason-label">{labels.reason(row.code).label}</span>
                          <span className="family-reason-count">{row.episodes}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              )
            })}
            {unclaimed.length > 0 ? (
              <section className="family" data-testid="family-unclaimed">
                <div className="family-head">
                  <h3>Not yet grouped</h3>
                  <span className="family-count">
                    {unclaimed.reduce((sum, row) => sum + row.episodes, 0)}
                  </span>
                </div>
                <p className="family-blurb">
                  Reported by the engine and not claimed by any group above.
                </p>
                <ul className="family-reasons">
                  {unclaimed.map((row) => (
                    <li key={row.reason_code}>
                      <span className="family-reason-label">
                        {labels.reason(row.reason_code).label}
                      </span>
                      <span className="family-reason-count">{row.episodes}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        </section>

        <section className="panel">
          <h2>Every combination, most frequent first</h2>
          <p className="hint">
            Top 25 pairs. The counts do not sum to the book total.
          </p>
          <div className="table-wrap queue-scroll">
            <table data-testid="verdict-distribution">
              <thead>
                <tr>
                  <th>Insurance</th>
                  <th>340B rebate</th>
                  <th className="num">Claims</th>
                </tr>
              </thead>
              <tbody>
                {overview.verdict_pairs.map((pair) => (
                  <tr key={`${pair.reimbursement}|${pair.rebate}`}>
                    <td>
                      {labels.verdict(pair.reimbursement).label}{' '}
                      <span className="verdict-code">{pair.reimbursement}</span>
                    </td>
                    <td>
                      {labels.verdict(pair.rebate).label}{' '}
                      <span className="verdict-code">{pair.rebate}</span>
                    </td>
                    <td className="num">{pair.episodes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <div className="layout-right">
        {/*
          The legend belongs on the reference screen, and "where does D come from" is the single
          most common question this vocabulary provokes -- a reader who has learned that A, B and
          C are tracks will reasonably assume there is a track D.
        */}
        <section className="panel">
          <h2>How the codes work</h2>
          <p className="hint">{labels.TRACK_NOTE}</p>
          <dl className="namespace-list">
            {labels.NAMESPACES.map((namespace) => (
              <div className="namespace" key={namespace.key}>
                <dt>
                  <span className="verdict-code">{namespace.key}</span>
                  {namespace.title}
                </dt>
                <dd>{namespace.blurb}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
    </div>
  )
}
