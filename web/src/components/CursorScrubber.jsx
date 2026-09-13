import React from 'react'
import { cursorFromDate, dateFromCursor } from '../api.js'

// The cursor control. Moving it re-asks every question at a different point in time.
//
// There is no "replay" button because there is no replay mode: the engine processes
// `records where received_at <= cursor`, so forward and backward are the same code path. Dragging
// left does not undo anything — it asks what we believed then.
export default function CursorScrubber({ bounds, cursor, onChange, busy }) {
  if (!bounds) return null

  const start = new Date(`${bounds.window_start}T00:00:00Z`)
  const end = new Date(`${bounds.window_end}T00:00:00Z`)
  const totalDays = Math.round((end - start) / 86400000)
  const current = new Date(`${dateFromCursor(cursor)}T00:00:00Z`)
  const offset = Math.max(0, Math.min(totalDays, Math.round((current - start) / 86400000)))

  const dateAt = (days) => new Date(start.getTime() + days * 86400000).toISOString().slice(0, 10)

  return (
    <div className="scrubber">
      <div className="row">
        <label htmlFor="cursor-range" style={{ fontWeight: 650 }}>Cursor</label>
        <output className="readout" data-testid="cursor-readout">{dateFromCursor(cursor)}</output>
        <input
          id="cursor-range"
          data-testid="cursor-range"
          type="range"
          min={0}
          max={totalDays}
          value={offset}
          aria-label="Replay cursor"
          aria-valuetext={dateFromCursor(cursor)}
          onChange={(event) => onChange(cursorFromDate(dateAt(Number(event.target.value))))}
        />
        <button
          data-testid="cursor-end"
          onClick={() => onChange(bounds.max)}
          disabled={busy || cursor === bounds.max}
          title="Jump to the end of the window, where every record has arrived"
        >
          Latest
        </button>
      </div>
      <div className="ends">
        <span>{bounds.window_start}</span>
        <span>{bounds.window_end}</span>
      </div>
    </div>
  )
}
