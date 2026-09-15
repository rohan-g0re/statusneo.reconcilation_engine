import React from 'react'

// The model is required to put a `[[raw:79]]`-style token after every factual clause,
// because that token is what makes a claim checkable -- the harness verifies each one
// against a real source row. But a token is machinery, not prose, and a reader who
// wanted a sentence should not be handed a parser. So the tokens stay in the model's
// output and come out here: each becomes a small numbered marker, numbered in order of
// first use, titled with what it points at. Same evidence, none of the noise.
//
// Shared by the agent analysis screen (pages/Analyse.jsx, one episode) and the
// dashboard's Portfolio Analyst panel (App.jsx, the whole book) -- both render prose a
// model wrote, citing the same four token kinds, and a second copy of this parser would
// silently drift from this one the first time either screen's citation handling changed.
// Every citation kind ANY role emits. The Analyst's `overview:` and `queue:` were
// missing, so its tokens matched nothing and the whole `[[queue:E-000006]]` string
// passed through into the prose as literal brackets -- on every run, in a component
// whose entire job is to make that not happen. A shared renderer has to know about
// every producer, and this one silently knew about two of three.
const CITATION_RE = /\[\[(raw|event|calc|verdict|overview|queue):([A-Za-z0-9_.\-]+)\]\]/g

export default function Cited({ text }) {
  if (!text) return null
  const seen = new Map()
  const out = []
  let last = 0
  let m
  CITATION_RE.lastIndex = 0
  while ((m = CITATION_RE.exec(text)) !== null) {
    const [token, kind, ref] = m
    if (m.index > last) out.push(text.slice(last, m.index))
    const key = `${kind}:${ref}`
    if (!seen.has(key)) seen.set(key, seen.size + 1)
    const n = seen.get(key)
    const label =
      kind === 'raw' ? `source record ${ref}`
      : kind === 'event' ? `timeline event ${ref}`
      : kind === 'calc' ? `computed field ${ref}`
      : kind === 'overview' ? `portfolio overview field ${ref}`
      : kind === 'queue' ? `queue row ${ref}`
      : `verdict ${ref}`
    out.push(
      <sup key={`${m.index}-${key}`} className="agent-cite" title={label}>
        {n}
      </sup>,
    )
    last = m.index + token.length
  }
  out.push(text.slice(last))
  return <div style={{ fontSize: 13, lineHeight: 1.55 }}>{out}</div>
}
