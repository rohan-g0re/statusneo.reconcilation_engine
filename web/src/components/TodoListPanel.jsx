import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

// ═══ the cross-episode to-do list ═══════════════════════════════════════════════════════════
// Every to-do (`work_item`) is written from exactly one place: the "Add to-do" button on an
// episode's own /analyse/{id} screen, committed only once a reviewer accepts a drafted
// recommendation -- the human gate docs/agent_layer_design.md describes. This panel is the read
// side of that write: one flat list across every episode, for "what has been committed,
// anywhere, so far" -- the dashboard's own question, the same reason the Portfolio Analyst panel
// in App.jsx answers "what is the state of the book" rather than one episode's state.
//
// Read-only by schema, not merely by convention: `work_item` carries a trigger that aborts
// UPDATE and DELETE outright (docs/decision_ledger.md), so there is no "done" state to reach for
// and nothing on this screen could mutate a row even if it tried. That is why there is no
// checkbox, no delete affordance, and no optimistic local state here -- there is nothing to be
// optimistic about.
//
// App.jsx opens /analyse/{id} in its own tab via `window.open`, so a to-do committed there never
// reaches this tab through any state this app already holds -- no shared store, no socket,
// nothing. Re-reading when this tab regains focus is the cheapest honest fix: the moment a
// reviewer alt-tabs back here is exactly the moment a new row might exist. No polling -- nothing
// else in this app polls, and a fixed interval would be either too slow to feel live or too fast
// to be worth the requests.
export default function TodoListPanel({ cursor }) {
  const [items, setItems] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [unavailable, setUnavailable] = useState(null)
  const [expandedId, setExpandedId] = useState(null)

  // Monotonic request id, same reasoning as App.jsx:216 -- `focus` and `visibilitychange` can
  // both fire while a request is already in flight, and without a ticket the slower of two
  // overlapping responses can land last and repaint the table with stale data over fresh.
  const requestId = useRef(0)

  const load = useCallback(async () => {
    const ticket = ++requestId.current
    setBusy(true)
    try {
      const payload = await api.agent.workItems(200)
      if (ticket !== requestId.current) return
      setItems(payload.work_items)
      setError(null)
      setUnavailable(null)
    } catch (exc) {
      if (ticket !== requestId.current) return
      if (exc.status === 503) setUnavailable(String(exc.message ?? exc))
      else setError(String(exc.message ?? exc))
      // Same house convention as App.jsx:236-242: a populated table under a heading that has
      // already failed to refresh presents stale rows as a real answer, and an empty table plus
      // the error is the honest one.
      setItems(null)
    } finally {
      if (ticket === requestId.current) setBusy(false)
    }
  }, [])

  useEffect(() => {
    load()
    const onFocus = () => load()
    const onVisible = () => {
      if (document.visibilityState === 'visible') load()
    }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // `load` is a `useCallback` with its own empty dep array, so its identity never changes --
    // an empty array here mounts this effect exactly once, matching that stability rather than
    // re-running it on a reference that was never going to move.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Both sides are the same fixed 20-char `YYYY-MM-DDTHH:MM:SSZ` shape, so a plain lexicographic
  // string compare orders the same way a parsed-timestamp compare would -- no Date parsing needed
  // to know whether a to-do was recorded at or before the cursor on screen.
  const visible = items && cursor ? items.filter((i) => i.at_cursor <= cursor) : []
  const hiddenCount = items ? items.length - visible.length : 0

  // `items` and `cursor` are two unsynchronized requests -- this component's own `load()` on
  // mount, and `App.jsx`'s `api.meta()` resolving the cursor -- so there is a real window where
  // `items` has landed and `cursor` has not. Until the cursor arrives, "hidden by the cursor" and
  // "nothing at or before the cursor" are not yet true statements about anything; they are just
  // what the derivations above compute from a cursor of `null`. Gating the cursor-derived UI on
  // this one flag keeps that timing gap from being rendered as a claim about the data.
  const loading = !error && !unavailable && (items === null || cursor === null)

  function toggleArtifacts(id) {
    setExpandedId((prev) => (prev === id ? null : id))
  }

  return (
    <section className="panel" data-testid="todo-panel">
      <div className="panel-head-row">
        <h2>To-do list — all episodes</h2>
        <button type="button" data-testid="todo-refresh" onClick={load} disabled={busy}>
          {busy ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
      <p className="hint">
        Read-only: work_item is append-only by schema trigger, so there is no "done" state and
        nothing on this screen mutates anything. Every row here was committed on its own episode's
        analysis screen, behind the human gate -- this panel only reads what already happened. It
        re-reads itself when this tab regains focus, or on Refresh.
      </p>

      {unavailable ? (
        <div className="agent-unavailable" data-testid="todo-unavailable">
          {unavailable}
        </div>
      ) : null}
      {error ? (
        <div className="error" data-testid="todo-error">
          {error}
        </div>
      ) : null}

      {loading ? (
        <p className="spinner">loading…</p>
      ) : null}

      {!loading && hiddenCount > 0 ? (
        <div className="todo-hidden" data-testid="todo-hidden-note">
          {hiddenCount} to-do{hiddenCount === 1 ? '' : 's'} recorded after this cursor — move the
          cursor forward to see them.
        </div>
      ) : null}

      {!loading && items && items.length === 0 ? (
        <p className="empty" data-testid="todo-empty">
          No to-dos yet. Open an episode with "Inspect using AI" and commit a work item on its
          analysis screen.
        </p>
      ) : null}
      {!loading && items && items.length > 0 && visible.length === 0 ? (
        <p className="empty" data-testid="todo-empty">
          None at or before this cursor.
        </p>
      ) : null}

      {visible.length > 0 ? (
        <div className="table-wrap" data-testid="todo-table">
          <table>
            <thead>
              <tr>
                <th>Work item</th>
                <th>Episode</th>
                <th>Action</th>
                <th>Rationale</th>
                <th>Created</th>
                <th>By</th>
                <th>Artifacts</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => {
                const n = item.required_artifacts?.length ?? 0
                return (
                  <React.Fragment key={item.work_item_id}>
                    <tr data-testid={`todo-row-${item.work_item_id}`}>
                      <td className="mono">{item.work_item_id}</td>
                      <td>
                        <a
                          data-testid={`todo-episode-link-${item.work_item_id}`}
                          href={`/analyse/${encodeURIComponent(item.episode_id)}`}
                          target="_blank"
                          rel="noopener"
                        >
                          {item.episode_id}
                        </a>
                      </td>
                      <td>
                        <span className="verdict-code">{item.recommended_action}</span>
                      </td>
                      <td className="todo-rationale">{item.summary}</td>
                      <td className="mono">{item.created_at?.slice(0, 10)}</td>
                      <td>{item.created_by}</td>
                      <td>
                        {n > 0 ? (
                          <button
                            type="button"
                            data-testid={`todo-expand-${item.work_item_id}`}
                            aria-expanded={expandedId === item.work_item_id}
                            onClick={() => toggleArtifacts(item.work_item_id)}
                          >
                            {expandedId === item.work_item_id ? '▾' : '▸'} {n} artifact{n === 1 ? '' : 's'}
                          </button>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                    {expandedId === item.work_item_id ? (
                      <tr>
                        <td
                          colSpan={7}
                          className="todo-artifacts"
                          data-testid={`todo-artifacts-${item.work_item_id}`}
                        >
                          {item.required_artifacts.map((artifact, i) => (
                            <span className="chip" key={i}>
                              {artifact}
                            </span>
                          ))}
                        </td>
                      </tr>
                    ) : null}
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}
