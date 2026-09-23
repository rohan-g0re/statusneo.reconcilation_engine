import React, { useState } from 'react'
import { api, formatMoney } from '../api.js'
import * as labels from '../labels.js'

// The episode's event timeline, read as a story.
//
// Extracted out of EpisodeDossier so a second screen (the agent-recommendation view at
// /analyse/{episode_id}) can render the same timeline beside something else, without
// reimplementing the source drilldown that goes with it.
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

// REMITTANCE_CLAIM_LINE -> "Payer's payment decision". A lookup, not a transformation.
//
// This replaces lowercasing the tag and capitalising the first letter, which worked until it met
// an acronym: TPA_QUALIFICATION rendered as "Tpa qualification" on every episode carrying a
// rebate, and TPA_MANUFACTURER_DECISION as "Tpa manufacturer decision". Casing rules cannot know
// that TPA is an initialism and REMITTANCE is a word, so the map states it. It also buys better
// names than the enum has — an event called MEDICAL_ACKNOWLEDGMENT is, to a reader, "the
// clearinghouse accepted the claim".
//
// The raw tag is still on the element's `title`, so the exact identifier is never lost.
function humanizeTag(tag) {
  return labels.recordKind(tag).label
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

// Exported: EpisodeDossier's own economics block (received/expected reimbursement and rebate)
// renders the same money values outside this timeline, and must format them identically rather
// than growing a second copy of this formatting.
export function Money({ cents, muted }) {
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
//
// `onlyKeys`, when given, restricts rendering to that subset — this is how the simple view reuses
// this exact renderer (same value handling: money, nested tables, chips, yes/no) over fewer facts,
// rather than growing a second, parallel facts renderer that could drift from this one.
function FactsList({ facts, onlyKeys }) {
  if (!facts || typeof facts !== 'object') return null
  let entries = Object.entries(facts).filter(([, value]) => value !== null && value !== undefined)
  if (onlyKeys) {
    const keep = new Set(onlyKeys)
    entries = entries.filter(([key]) => keep.has(key))
  }
  if (entries.length === 0) return null
  return (
    <dl className="dossier-facts">
      {entries.map(([key, value]) => (
        <FactRow key={key} factKey={key} value={value} />
      ))}
    </dl>
  )
}

// A hash is shown short because the whole 64-character string is never what anyone reads at a
// glance — the truncated form is what a human scans, the full value lives in `title` for the one
// time someone needs to copy it out to compare byte for byte.
function shortHash(hash) {
  if (!hash) return null
  return hash.length > 16 ? `${hash.slice(0, 16)}…` : hash
}

// The panel revealed under a timeline event once its source line is clicked. `entry` is this raw
// record's slot in the timeline's own fetch cache — undefined while nothing has happened yet, which
// this component never sees because the caller only renders it once a fetch has been kicked off.
function RecordPanel({ entry }) {
  if (!entry || entry.status === 'loading') {
    return (
      <div className="source-panel" data-testid="source-panel">
        <p className="spinner">loading the source record…</p>
      </div>
    )
  }
  if (entry.status === 'error') {
    return (
      <div className="source-panel" data-testid="source-panel">
        <p className="error" style={{ margin: 0 }}>Could not load the source record: {entry.error}</p>
      </div>
    )
  }

  const record = entry.data
  let payload = record.payload
  try {
    payload = JSON.stringify(JSON.parse(record.payload), null, 2)
  } catch {
    // Not valid JSON, or not a string at all — show whatever the API sent rather than crash on it.
    payload = record.payload
  }

  return (
    <div className="source-panel" data-testid="source-panel">
      <div className="source-panel-meta mono">
        {record.source_file}:{record.source_line_no} · {record.source_record_id} ·{' '}
        {record.source_system}
      </div>
      <pre>{String(payload)}</pre>
      <div className="source-panel-hashes">
        <span title={record.payload_sha256}>payload {shortHash(record.payload_sha256)}</span>
        <span title={record.file_sha256}>file {shortHash(record.file_sha256)}</span>
      </div>
    </div>
  )
}

// The timeline itself. `timeline` is the ordered event array straight off the dossier payload;
// `view` is 'simple' | 'detailed' and is owned by the caller, because the Simple/Detailed toggle
// buttons live outside this component (in the dossier's sticky header) — Timeline only reacts to
// the value, it never changes it.
//
// The source-record drilldown — `openRecords`, `recordCache`, and the lazy `api.record` fetch in
// `toggleSource` — lives entirely inside this component rather than being lifted to the caller.
// That is deliberate: a second screen that renders this same timeline (the agent-recommendation
// view at /analyse/{episode_id}) gets a working expand/collapse for free, with nothing to wire up
// and no risk of two callers drifting into different caching behaviour. Keying the cache by
// raw_id rather than by event index is what lets it survive the `timeline` prop being replaced
// wholesale — a cursor move, a different episode selected — without invalidating a panel that is
// already open.
export default function Timeline({ timeline, view }) {
  const [openRecords, setOpenRecords] = useState(() => new Set())
  const [recordCache, setRecordCache] = useState({})

  function toggleSource(rawId) {
    setOpenRecords((prev) => {
      const next = new Set(prev)
      if (next.has(rawId)) next.delete(rawId)
      else next.add(rawId)
      return next
    })
    if (!recordCache[rawId]) {
      setRecordCache((prev) => ({ ...prev, [rawId]: { status: 'loading' } }))
      api
        .record(rawId)
        .then((data) => setRecordCache((prev) => ({ ...prev, [rawId]: { status: 'ready', data } })))
        .catch((exc) =>
          setRecordCache((prev) => ({
            ...prev,
            [rawId]: { status: 'error', error: String(exc.message ?? exc) },
          })),
        )
    }
  }

  return (
    <ol className="dossier-timeline" data-testid="dossier-timeline" data-view={view}>
      {timeline.map((event, index) => {
        const dot = TAG_DOT[event.tag] ?? 'var(--border-strong)'
        const rawId = event.source?.raw_id
        const sourceOpen = rawId !== undefined && rawId !== null && openRecords.has(rawId)
        return (
          <li key={index} style={{ '--dot': dot }} data-testid={`event-${event.tag}`}>
            {/* Date only. The raw tag used to sit here too, but the gutter is fixed-width and
                long tags (MEDICAL_ACKNOWLEDGMENT, PROVIDER_LEVEL_ADJUSTMENT) overflowed into the
                facts column once the dossier moved into the narrower right-hand panel. It was
                redundant anyway — the headline is the same tag, humanised — so the exact value
                lives on the headline's title and in the event's data-testid. */}
            <div className="dossier-when">
              <time dateTime={event.at}>{event.at.slice(0, 10)}</time>
            </div>
            <div className="dossier-body">
              <div className="dossier-headline" title={event.tag}>
                {humanizeTag(event.tag)}
                {event.late ? (
                  <span className="chip" style={{ marginLeft: 6 }} title="Arrival lagged the event">
                    late-arriving
                  </span>
                ) : null}
              </div>
              {/* Simple: only the facts this event's own `essential` list names — the outcome and
                  the money, not the identifiers that got it there. Detailed: everything, via the
                  same generic renderer, just over the full key set. */}
              <FactsList facts={event.facts} onlyKeys={view === 'simple' ? event.essential : null} />
              {view === 'detailed' ? (
                <>
                  <div className="dossier-provenance">
                    {event.occurred_on && event.occurred_on !== event.at.slice(0, 10) ? (
                      <span title="The event happened on this date; we learned of it later">
                        happened {event.occurred_on}, learned {event.at.slice(0, 10)}
                      </span>
                    ) : null}
                    {event.source?.file ? (
                      rawId !== undefined && rawId !== null ? (
                        <button
                          type="button"
                          className="source-link"
                          data-testid={`source-link-${index}`}
                          aria-expanded={sourceOpen}
                          onClick={() => toggleSource(rawId)}
                        >
                          {event.source.file}:{event.source.line}
                          {event.source.record_id ? ` · ${event.source.record_id}` : ''}
                        </button>
                      ) : (
                        <span className="mono">
                          {event.source.file}:{event.source.line}
                          {event.source.record_id ? ` · ${event.source.record_id}` : ''}
                        </span>
                      )
                    ) : null}
                    {event.source?.ach_trace_number ? (
                      <span className="mono">ACH {event.source.ach_trace_number}</span>
                    ) : null}
                  </div>
                  {sourceOpen ? <RecordPanel entry={recordCache[rawId]} /> : null}
                </>
              ) : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
