import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

// ═══ the Connectivity view ═══════════════════════════════════════════════════════════════════
//
// Requirement F3's connector-readiness report, on screen. Everything rendered here is read from
// `/api/connectivity` verbatim; this file computes no cell of its own, for the same reason the
// report itself derives every cell by inspecting an object — a number typed into a component is a
// claim about a connector, and it becomes false the first afternoon somebody deletes one.
//
// **The honesty requirement is the point of this screen.** Every connector in this build talks to
// a local mock. Not one of them has exchanged a byte with Verity, Craneware or Beacon, and a page
// that read as though one had would be the single most misleading artefact in the repository. So
// the sentence that says so is the first thing on the page, at the top, in its own banner, with
// the sources that reach a vendor enumerated beside it — an empty list rather than a claim that
// the list is empty. The server computes that sentence (`live_vendor_connection_statement`); this
// file cannot phrase it more generously than the report does, because it does not phrase it.
//
// No cursor. Every other call in `api.js` takes one; this one does not, because connector
// readiness is a property of the build rather than of the moment being replayed. Adding a cursor
// would imply the report moves with the timeline, and it does not.

// Doc 2's ladder, in Doc 2's order. The four rungs are `Stage`'s four members — there is no
// "in progress", because a rung is either reached or it is not and a report that could say
// "mostly" would always say "mostly".
//
// The *counts* come from the payload. The descriptions are the only prose on this screen that is
// not server-derived, and they are UI copy for a fixed vocabulary rather than a per-source cell:
// they say what each rung means, not which sources reached it. Each is the condensed form of
// `recon.connectors.readiness.Stage`'s own docstring, and `docs/connectivity_layer_-
// requirements.md` §0 is the source both follow.
const LADDER = [
  {
    key: 'DECLARED',
    label: 'Declared',
    meaning:
      'The registry holds a row and nothing more: no transport moves its bytes, or nothing resolves its credential, or no mapping says what the bytes mean.',
  },
  {
    key: 'CONNECTOR_READY',
    label: 'Connector-ready',
    ceiling: true,
    meaning:
      'Doc 2 steps 1-5, against a local mock. Built to the vendor’s published contract, exercised against a mock that reproduces it, wrapped in the transport, auth, idempotency and checkpointing machinery a real connection would use, and switchable to a live endpoint by changing configuration only. This is the ceiling for this build.',
  },
  {
    key: 'WORKING_CONNECTION',
    label: 'Working connection',
    meaning:
      'Doc 2’s own bar, and it begins with the word authorized. It needs a credential the vendor issues. No source here reaches it, and each one’s blocked list says so in evidence ids.',
  },
  {
    key: 'PRODUCTION_READY',
    label: 'Production-ready',
    meaning:
      'Doc 2 step 6: retry and replay, observability, DQ queue surfacing, secrets rotation, runbooks, alerting, lineage, backfill, cutover. Explicitly out of scope by decision, so nothing here can compute it and nothing here pretends to.',
  },
]

// The three provenance tiers, strongest evidence first — `readiness._TIERS`' own order.
// Each carries a colour AND its own count in the label beside the bar: the bar is a shape, the
// numbers are the fact, and colour never carries the meaning on its own (the same rule the
// disposition tiles follow).
const TIERS = [
  { key: 'spec', label: 'SPEC', color: 'var(--status-good)', hint: 'a vendor document states this field' },
  { key: 'standard', label: 'STANDARD', color: 'var(--border-strong)', hint: 'an external standard (X12, NCPDP, ACH) states it; the vendor does not' },
  { key: 'invented', label: 'INVENTED', color: 'var(--status-serious)', hint: 'we made it up because no source states it — plausible, and not evidence' },
]

function count(sources, stage) {
  return sources.filter((row) => row.stage === stage).length
}

function StageChip({ stage }) {
  const rung = LADDER.find((entry) => entry.key === stage)
  return (
    <span className={`chip conn-stage conn-stage-${stage}`} title={rung ? rung.meaning : undefined}>
      {rung ? rung.label : stage}
    </span>
  )
}

function FidelityBar({ fidelity, compact }) {
  if (!fidelity.applies) {
    // "No provenance table covers this source" is the correct answer for the six generated
    // feeds — they are this repository's own synthetic data and were never a mock of anybody.
    // The table cell says it in three words; the detail panel says it in the report's own.
    return <span className="conn-muted">{compact ? 'no vendor mock' : fidelity.summary}</span>
  }
  const total = fidelity.rows || 1
  return (
    <div className="fidelity">
      <span className="fidelity-bar" aria-hidden="true">
        {TIERS.map((tier) =>
          fidelity[tier.key] > 0 ? (
            <span
              key={tier.key}
              className="fidelity-seg"
              style={{ width: `${(fidelity[tier.key] / total) * 100}%`, background: tier.color }}
            />
          ) : null,
        )}
      </span>
      <span className="fidelity-counts">
        {TIERS.map((tier, index) => (
          <React.Fragment key={tier.key}>
            {index > 0 ? ' · ' : ''}
            <span title={tier.hint}>
              {fidelity[tier.key]} {tier.label}
            </span>
          </React.Fragment>
        ))}
      </span>
    </div>
  )
}

function authCell(auth) {
  if (!auth.required) return 'not required'
  return `${auth.kind ?? 'shape unknown'} · ${auth.configured ? 'configured' : 'no credential'}`
}

function totalsCell(totals) {
  if (totals.checked === null || totals.checked === undefined) {
    return totals.storage_declared ? 'not checked' : 'no storage'
  }
  return `${totals.checked} checked · ${totals.unreconciled} unreconciled`
}

// ═══ one source, in full ═════════════════════════════════════════════════════════════════════

function Detail({ row }) {
  if (!row) return <p className="empty">Select a source to see its evidence.</p>
  return (
    <div data-testid="connectivity-detail">
      <div className="dossier-head">
        <div>
          <div className="dossier-id">{row.source_id}</div>
          <div className="dossier-drug">
            {row.vendor}
            {row.enabled ? null : <span className="chip conn-off">switched off</span>}
          </div>
          <div className="dossier-keys">
            reaches {row.reaches} — <code>{row.endpoint}</code>
          </div>
        </div>
        <div>
          <StageChip stage={row.stage} />
        </div>
      </div>

      <div className="conn-section">Transport &amp; auth</div>
      <dl className="conn-facts">
        <div className="conn-fact">
          <dt>Transport ({row.transport.kind})</dt>
          <dd>
            {row.transport.implementations.length
              ? row.transport.implementations.map((name) => (
                  <code key={name} className="conn-code">
                    {name}
                  </code>
                ))
              : 'no implementation registered'}
            <div className="conn-sub">
              {row.transport.bound ? `bound to ${row.transport.bound}` : 'not bound'}
            </div>
          </dd>
        </div>
        <div className="conn-fact">
          <dt>Auth</dt>
          <dd>
            {row.auth.required
              ? `${row.auth.kind ?? 'shape undetermined'} via ${row.auth.resolver ?? 'no resolver'}`
              : 'not required'}
            {row.auth.detail ? <div className="conn-sub">{row.auth.detail}</div> : null}
            {/* Variable NAMES. The report exposes what an operator has to set and never what it
                is set to; a test asserts no credential value reaches this payload at all. */}
            {row.auth.env_vars.length ? (
              <div className="conn-sub">
                set (names only, never values):{' '}
                {row.auth.env_vars.map((name) => (
                  <code key={name} className="conn-code">
                    {name}
                  </code>
                ))}
              </div>
            ) : null}
          </dd>
        </div>
        <div className="conn-fact">
          <dt>Schema contract</dt>
          <dd>
            {row.schema.registered
              ? `registered at ${row.schema.current_version} — ${row.schema.field_count} fields, ${row.schema.required_field_count} required`
              : 'no contract registered; this source validates vacuously'}
            <div className="conn-sub">
              {row.schema.version_matches_mapping === null
                ? `mapping version ${row.schema.mapping_version}`
                : row.schema.version_matches_mapping
                  ? 'matches the row’s mapping version'
                  : `disagrees with the row’s mapping version ${row.schema.mapping_version}`}
            </div>
          </dd>
        </div>
        <div className="conn-fact">
          <dt>Mapping</dt>
          <dd>
            {row.mapping.implemented
              ? `${row.mapping.module} ${row.mapping.per_source ? '(declares this source id)' : '(covers this vendor)'}`
              : 'none'}
          </dd>
        </div>
      </dl>

      <div className="conn-section">Mock fidelity</div>
      <FidelityBar fidelity={row.fidelity} />
      <div className="conn-sub">
        {row.mock_modules.length
          ? `stood in for by ${row.mock_modules.join(', ')}`
          : 'no mock module'}
        {row.fidelity.table ? ` — tallied from ${row.fidelity.table}` : ''}
      </div>
      {row.fidelity.evidence_ids.length ? (
        <div className="conn-sub">
          cited:{' '}
          {row.fidelity.evidence_ids.map((id) => (
            <span className="chip" key={id}>
              {id}
            </span>
          ))}
        </div>
      ) : null}

      <div className="conn-section">Golden claims &amp; control totals</div>
      <dl className="conn-facts">
        <div className="conn-fact">
          <dt>Golden-claim trace (F1)</dt>
          <dd>
            {row.golden.traced
              ? `traced by ${row.golden.traced_by.join(', ')}${row.golden.test_module ? ` in ${row.golden.test_module}` : ''}`
              : 'no test names this source'}
            {row.golden.dataset_rows !== null && row.golden.dataset_rows !== undefined ? (
              <div className="conn-sub">this dataset produced {row.golden.dataset_rows} rows</div>
            ) : null}
          </dd>
        </div>
        <div className="conn-fact">
          <dt>Control totals (F2)</dt>
          <dd>
            {row.control_totals.storage_declared
              ? 'storage declared in schema.sql'
              : 'no storage declared'}
            <div className="conn-sub">
              {row.control_totals.checked === null || row.control_totals.checked === undefined
                ? 'not checked in this database — which is not the same as clean'
                : `${row.control_totals.checked} checked, ${row.control_totals.reconciled} reconciled, ${row.control_totals.unreconciled} unreconciled`}
            </div>
          </dd>
        </div>
      </dl>

      {/* Ours to close, theirs to unblock. Kept apart because collapsing them loses the only
          question an operator actually has: who acts next. */}
      <div className="conn-section">Gaps — ours to close</div>
      {row.gaps.length ? (
        <ul className="conn-list" data-testid="connectivity-gaps">
          {row.gaps.map((gap) => (
            <li key={gap}>{gap}</li>
          ))}
        </ul>
      ) : (
        <p className="empty">None.</p>
      )}

      <div className="conn-section">Vendor-blocked — theirs to unblock</div>
      {row.blocked.length ? (
        <div className="table-wrap">
          <table data-testid="connectivity-blocked">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Evidence</th>
                <th>Retrieved</th>
                <th>What is blocked</th>
              </tr>
            </thead>
            <tbody>
              {row.blocked.map((item, index) => (
                <tr key={`${item.evidence_id ?? 'access'}-${index}`}>
                  <td className="mono">{item.kind}</td>
                  <td className="mono">
                    {item.evidence_id ? (
                      item.url ? (
                        <a href={item.url} target="_blank" rel="noreferrer">
                          {item.evidence_id}
                        </a>
                      ) : (
                        item.evidence_id
                      )
                    ) : (
                      'access gate'
                    )}
                  </td>
                  <td className="mono">{item.retrieved ?? '—'}</td>
                  <td className="conn-wrap">
                    {item.summary}
                    {item.detail ? <div className="conn-sub">{item.detail}</div> : null}
                    {item.env_vars.length ? (
                      <div className="conn-sub">
                        clears by setting{' '}
                        {item.env_vars.map((name) => (
                          <code key={name} className="conn-code">
                            {name}
                          </code>
                        ))}
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty">Nothing.</p>
      )}
    </div>
  )
}

// ═══ the screen ══════════════════════════════════════════════════════════════════════════════

export default function Connectivity() {
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    let live = true
    api
      .connectivity()
      .then((payload) => {
        if (!live) return
        setReport(payload)
        setSelected(payload.sources[0]?.source_id ?? null)
      })
      .catch((exc) => {
        if (live) setError(String(exc.message ?? exc))
      })
    return () => {
      live = false
    }
  }, [])

  if (error) {
    return (
      <div className="error" data-testid="connectivity-error">
        {error}
      </div>
    )
  }
  if (!report) return <p className="spinner">loading the connector readiness report…</p>

  const sources = report.sources
  const row = sources.find((entry) => entry.source_id === selected) ?? null
  const live = report.live_vendor_connections

  return (
    <div data-testid="connectivity">
      {/*
        The banner, not a footnote. `live_vendor_connections` is enumerated rather than
        summarised: an empty list is a fact a reader can check, where "none" is a claim they
        have to take. If it were ever non-empty this banner would name the sources and read
        `--status-critical`, because at that point the interesting question is which.
      */}
      <div
        className={`conn-banner${live.length ? ' conn-banner-live' : ''}`}
        data-testid="connectivity-banner"
      >
        <div className="conn-banner-head">
          <span className="conn-banner-icon" aria-hidden="true">
            {live.length ? '▲' : '◉'}
          </span>
          <span className="conn-banner-statement" data-testid="connectivity-statement">
            {report.live_vendor_connection_statement}
          </span>
        </div>
        <div className="conn-banner-body">
          Live vendor connections: <strong>{live.length ? live.join(', ') : 'none'}</strong>. Every
          note below is generated from code and config at request time — the registry rows, the
          transport classes, the schema contracts, the hand-written provenance tables and the
          evidence index, read as they are now. No cell on this screen is hand-maintained.
          {report.notes.map((note) => (
            <div className="conn-note" key={note}>
              {note}
            </div>
          ))}
        </div>
      </div>

      {/*
        Connector-ready, working connection and production-ready are three different claims and
        the whole page turns on not letting them read as synonyms. Each rung carries its own
        count, so "14 sources are connector-ready and none has a working connection" is a shape
        on the screen rather than a sentence to be trusted.
      */}
      <section className="panel">
        <h2>Where each source actually is</h2>
        <p className="hint">
          Doc 2’s ladder, with the number of sources standing on each rung. A rung is reached or it
          is not; there is no “in progress”, because a report that could say “mostly” would always
          say “mostly”.
        </p>
        <ol className="conn-ladder" data-testid="connectivity-ladder">
          {LADDER.map((rung) => {
            const reached = count(sources, rung.key)
            return (
              <li
                key={rung.key}
                className={`conn-rung${reached ? ' conn-rung-reached' : ''}`}
                data-testid={`rung-${rung.key}`}
              >
                <div className="conn-rung-count" data-testid={`rung-count-${rung.key}`}>
                  {reached}
                </div>
                <div className="conn-rung-body">
                  <div className="conn-rung-label">
                    {rung.label}
                    {rung.ceiling ? (
                      <span className="chip conn-ceiling">the ceiling for this build</span>
                    ) : null}
                    {reached === 0 ? <span className="conn-rung-none">no source reaches this</span> : null}
                  </div>
                  <div className="conn-rung-meaning">{rung.meaning}</div>
                </div>
              </li>
            )
          })}
        </ol>
      </section>

      <div className="layout-grid">
        <div className="layout-left">
          <section className="panel">
            <h2>Gate evidence table</h2>
            <p className="hint">
              One row per registered source — Doc 2’s Week 3 gate evidence table, derived. Select a
              row for that source’s full document: its transport, its credential shape, every
              provenance tier behind its mock, and the vendor evidence for everything it is blocked
              on.
            </p>
            <div className="table-wrap">
              <table data-testid="connectivity-table">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Vendor</th>
                    <th>Stage</th>
                    <th>Reaches</th>
                    <th>Transport</th>
                    <th>Auth</th>
                    <th>Schema</th>
                    <th>Mock fidelity</th>
                    <th>Golden</th>
                    <th>Control totals</th>
                    <th className="num">Gaps</th>
                    <th className="num">Blocked</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map((entry) => (
                    <tr
                      key={entry.source_id}
                      aria-selected={entry.source_id === selected}
                      data-testid={`connectivity-row-${entry.source_id}`}
                      onClick={() => setSelected(entry.source_id)}
                    >
                      <td className="mono">{entry.source_id}</td>
                      <td className="mono">{entry.vendor}</td>
                      <td>
                        <StageChip stage={entry.stage} />
                      </td>
                      <td>{entry.reaches}</td>
                      <td className="mono">
                        {entry.transport.kind}
                        {entry.transport.implemented ? '' : ' — none'}
                      </td>
                      <td>{authCell(entry.auth)}</td>
                      <td className="mono">
                        {entry.schema.registered ? entry.schema.current_version : 'not registered'}
                      </td>
                      <td>
                        <FidelityBar fidelity={entry.fidelity} compact />
                      </td>
                      <td>
                        {entry.golden.traced
                          ? `${entry.golden.traced_by.length} trace${entry.golden.traced_by.length === 1 ? '' : 's'}`
                          : 'not traced'}
                      </td>
                      <td>{totalsCell(entry.control_totals)}</td>
                      <td className="num">{entry.gaps.length}</td>
                      <td className="num">{entry.blocked.length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel">
            <h2>Repository-level evidence</h2>
            <p className="hint">
              An archetype is a property of a claim rather than of a feed, and a claim crosses four
              feeds on its way to a verdict — so the golden-claim traces are counted here and not
              attributed to whichever source happened to be asserted first.
            </p>
            <div className="detail-grid">
              <div className="field">
                <div className="k">Evidence index</div>
                <div className="v" data-testid="connectivity-evidence-entries">
                  {report.evidence_entries} entries
                </div>
              </div>
              <div className="field">
                <div className="k">Exists and could not be retrieved</div>
                <div className="v">{report.unavailable_evidence.length}</div>
              </div>
              <div className="field">
                <div className="k">Golden-claim test module</div>
                <div className="v">{report.golden_test_module ?? 'none'}</div>
              </div>
              <div className="field">
                <div className="k">Generated at</div>
                <div className="v">{report.generated_at}</div>
              </div>
            </div>
            <div className="table-wrap">
              <table data-testid="connectivity-archetypes">
                <thead>
                  <tr>
                    <th>Archetype</th>
                    <th>Traced by</th>
                  </tr>
                </thead>
                <tbody>
                  {report.archetype_traces.map((entry) => (
                    <tr key={entry.archetype}>
                      <td className="mono">{entry.archetype}</td>
                      <td className="conn-wrap">
                        {entry.traced_by.length ? entry.traced_by.join(', ') : 'not traced'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {report.unavailable_evidence.length ? (
              <p className="conn-sub" style={{ marginTop: 'var(--space-4)' }}>
                unretrievable sources:{' '}
                {report.unavailable_evidence.map((id) => (
                  <span className="chip" key={id}>
                    {id}
                  </span>
                ))}
              </p>
            ) : null}
          </section>
        </div>

        <div className="layout-right">
          <section className="panel" data-testid="connectivity-detail-panel">
            <h2>The source, in full</h2>
            <Detail row={row} />
          </section>
        </div>
      </div>
    </div>
  )
}
