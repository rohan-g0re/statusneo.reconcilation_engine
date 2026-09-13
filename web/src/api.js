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
    throw new Error(`${response.status}: ${detail}`)
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
  feedExceptions: (cursor) => get('/feed-exceptions', { cursor }),
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
