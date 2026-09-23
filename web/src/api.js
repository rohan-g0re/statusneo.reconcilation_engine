// One place that talks to the API, so no component builds a URL by hand.
//
// Every call takes a cursor. That is the architecture surfacing in the client: there is no
// "replay mode" to enter, because asking what we believed on a given day is the same request
// with a different parameter.

async function get(path, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== null && value !== undefined),
  )
  const suffix = query.toString() ? `?${query}` : ''
  const response = await fetch(`/api${path}${suffix}`)
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = (await response.json()).detail ?? detail
    } catch {
      /* a non-JSON error body is still worth surfacing as the status text */
    }
    // Attach `.status` the same way `post` does below, so a caller can tell a 503 (the agent
    // layer is unavailable, a normal state) from a real error without parsing the message text.
    const err = new Error(`${response.status}: ${detail}`)
    err.status = response.status
    throw err
  }
  return response.json()
}

// POST with a JSON body (or none) and a JSON response — the shape every /api/agent/* write
// and action endpoint shares. Kept separate from `get` rather than folding a `method` option
// in: the query-string handling and the body handling do not overlap enough to make one
// function simpler than two.
async function post(path, body) {
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = (await response.json()).detail ?? detail
    } catch {
      /* a non-JSON error body is still worth surfacing as the status text */
    }
    const err = new Error(`${response.status}: ${detail}`)
    err.status = response.status
    throw err
  }
  return response.json()
}

export const api = {
  meta: () => get('/meta'),
  overview: (cursor) => get('/overview', { cursor }),
  queue: (disposition, cursor, orderBy, limit = 200) =>
    get(`/queue/${disposition}`, { cursor, order_by: orderBy, limit }),
  episode: (episodeId, cursor) => get(`/episode/${episodeId}`, { cursor }),
  trace: (episodeId) => get(`/episode/${episodeId}/trace`),
  // One call, one episode, its entire history — the same payload the agent layer receives.
  dossier: (episodeId, cursor) => get(`/episode/${episodeId}/dossier`, { cursor }),
  // The verbatim source line behind a record event — what the operator sees when they click a
  // timeline event's `file:line` in the detailed view.
  record: (rawId) => get(`/record/${rawId}`),
  feedExceptions: (cursor) => get('/feed-exceptions', { cursor }),
  // Connector readiness (requirement F3). The only call in this file that takes no cursor, and
  // deliberately: readiness is a property of the build — which transports exist, which contracts
  // are registered, which credential nobody has issued — not of the moment being replayed.
  // Derived on the server from code and config on every request; there is no generated file
  // behind it, so a connector deleted five minutes ago is gone from the next response.
  connectivity: () => get('/connectivity'),
  // `seed` omitted rebuilds the published dataset byte for byte; a seed gives a genuinely
  // different one. The seed is chosen HERE, at the edge — nothing inside the generator reads a
  // clock, because a generator that invented its own seed could never be replayed.
  regenerate: async (profile, seed) => {
    const query = new URLSearchParams({ profile })
    if (seed !== undefined && seed !== null) query.set('seed', String(seed))
    const response = await fetch(`/api/regenerate?${query}`, { method: 'POST' })
    if (!response.ok) throw new Error(`regenerate failed: ${response.status}`)
    return response.json()
  },

  // A fresh seed for "give me different data". Derived from the clock on the client, which is the
  // one place a wall-clock reading is harmless.
  freshSeed: () => (Date.now() % 100_000_000) + 1,

  // The agent layer's HTTP surface (docs/agent_layer_design.md S8.6). Every call here can 503
  // with "the agent layer is unavailable" — no API key configured, or the optional `agent`
  // extra not installed — which is a normal, expected state for this app (see README), not a
  // bug: the deterministic layer works fully without it. Callers surface that message rather
  // than treating it as a crash.
  agent: {
    // A single request/response — the Investigator reads the episode and writes a narrative
    // report. No SSE: unlike `decide`, there is no propose/evaluate loop to watch step by step.
    explain: (episodeId, cursor) =>
      post(`/agent/explain/${episodeId}${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),

    // The propose -> evaluate -> score -> gate loop, streamed as Server-Sent Events. Not
    // `EventSource`: that API can only ever issue a GET, and this endpoint is a POST (it starts
    // a real, possibly expensive run rather than just subscribing to one). `onEvent(kind,
    // payload)` fires once per SSE frame, in arrival order, including the terminal `outcome`
    // frame this module's own SSE writer appends after the journal's own `run_finished`.
    // Returns an abort function; the caller does not have to drain the stream to cancel it.
    decide(episodeId, cursor, onEvent) {
      const controller = new AbortController()
      const url = `/api/agent/decide/${episodeId}${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`
      ;(async () => {
        let response
        try {
          response = await fetch(url, { method: 'POST', signal: controller.signal })
        } catch (exc) {
          if (controller.signal.aborted) return
          onEvent('client_error', { detail: String(exc.message ?? exc) })
          return
        }
        if (!response.ok) {
          let detail = response.statusText
          try {
            detail = (await response.json()).detail ?? detail
          } catch {
            /* non-JSON error body: the status text is still worth showing */
          }
          onEvent('client_error', { detail: `${response.status}: ${detail}` })
          return
        }
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        try {
          // Frames are separated by a blank line (`\n\n`); within a frame, an `event: <kind>`
          // line names it and a `data: <json>` line carries the payload — exactly the shape
          // `recon.agents.api._sse` writes, and the only shape this parser needs to understand.
          for (;;) {
            const { value, done } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            let boundary
            while ((boundary = buffer.indexOf('\n\n')) !== -1) {
              const frame = buffer.slice(0, boundary)
              buffer = buffer.slice(boundary + 2)
              let kind = 'message'
              let data = null
              for (const line of frame.split('\n')) {
                if (line.startsWith('event:')) kind = line.slice('event:'.length).trim()
                else if (line.startsWith('data:')) data = line.slice('data:'.length).trim()
              }
              if (data !== null) {
                try {
                  onEvent(kind, JSON.parse(data))
                } catch {
                  onEvent('client_error', { detail: `could not parse an SSE frame of kind ${kind}` })
                }
              }
            }
          }
        } catch (exc) {
          if (!controller.signal.aborted) onEvent('client_error', { detail: String(exc.message ?? exc) })
        }
      })()
      return () => controller.abort()
    },

    // The only write path: a human-edited draft, committed only once a reviewer accepts it.
    // `dry_run: true` is a harmless preview — no episode/verdict/action/summary match is
    // checked and nothing is written — and it is the only way a browser client learns the
    // verdict id a commit must cite: that id is derived server-side from the current cursor's
    // verdict and is not exposed by any read endpoint (`/api/episode*` all omit it by design,
    // as an internal database key rather than a domain field).
    createWorkItem: (body) => post('/agent/work-item', body),

    workItems: (limit) => get('/agent/work-items', { limit }),
    workItemsForEpisode: (episodeId) => get(`/agent/work-items/${episodeId}`),
    run: (runId) => get(`/agent/runs/${runId}`),
  },
}

// Money is integer cents end to end, and it stays integer until the moment it is displayed.
export function formatMoney(cents) {
  if (cents === null || cents === undefined) return '—'
  const negative = cents < 0
  const whole = Math.trunc(Math.abs(cents) / 100)
  const fraction = String(Math.abs(cents) % 100).padStart(2, '0')
  const grouped = whole.toLocaleString('en-US')
  return `${negative ? '−' : ''}$${grouped}.${fraction}`
}

export function cursorFromDate(isoDate) {
  return `${isoDate}T23:59:59Z`
}

export function dateFromCursor(cursor) {
  return cursor ? cursor.slice(0, 10) : ''
}
