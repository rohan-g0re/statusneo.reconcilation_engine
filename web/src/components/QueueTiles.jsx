import React from 'react'
import { formatMoney } from '../api.js'
import * as labels from '../labels.js'

// Three dispositions, because "what do I do with this?" has three answers.
//
// A stat tile rather than a chart: three counts are read, not compared along a continuum, and a bar
// chart of three bars would add ink without adding meaning.
//
// Each tile carries an icon AND a label as well as its colour. These are *status* colours — reserved
// for state, never reused as a series — and two of them sit below 3:1 contrast on a light surface by
// design, so the icon-plus-label pairing is what keeps the meaning legible. Colour alone never
// carries it.
//
// The label names the action, not the state. `Exception`, `Pending` and `Closed` are the
// engine's enum members and they describe what a row *is*; an operator is deciding what to
// *do*, and the whole reason there are exactly three buckets is that the question has exactly
// three answers. The enum still shows, as a muted suffix, because it is what the API returns
// and what anyone reading the design note will be looking for.
//
// The one-line meaning moves out of the `title` attribute and onto the tile. It was the most
// valuable copy on the screen and it was invisible without a mouse.
const TILES = [
  { key: 'EXCEPTION', icon: '▲', color: 'var(--status-critical)' },
  { key: 'PENDING', icon: '◷', color: 'var(--status-warning)' },
  { key: 'CLOSED', icon: '✓', color: 'var(--status-good)' },
]

export default function QueueTiles({ overview, selected, onSelect }) {
  const byDisposition = overview?.by_disposition ?? {}
  return (
    <div className="tiles" role="group" aria-label="Episodes by disposition">
      {TILES.map((tile) => {
        const stats = byDisposition[tile.key] ?? { episodes: 0, variance_cents: 0, reopened: 0 }
        const { label, detail } = labels.disposition(tile.key)
        return (
          <button
            key={tile.key}
            className="tile"
            style={{ '--tile-color': tile.color }}
            aria-pressed={selected === tile.key}
            data-testid={`tile-${tile.key}`}
            onClick={() => onSelect(tile.key)}
          >
            <span className="label">
              <span className="icon" aria-hidden="true">{tile.icon}</span>
              {label}
              <span className="tile-enum">{tile.key}</span>
            </span>
            {/*
              The dollars are the headline and the claim count is the subtitle. It used to be the
              other way round -- a 28px episode count above a 12px money line -- so the eye landed
              on "40" when the reason anyone opened the screen is $517,240.34.
            */}
            <div className="count" data-testid={`tile-money-${tile.key}`}>
              {formatMoney(stats.variance_cents)}
            </div>
            <div className="money">
              <span data-testid={`tile-count-${tile.key}`}>{stats.episodes}</span>
              {stats.episodes === 1 ? ' claim' : ' claims'}
            </div>
            {/* Reopened gets its own line. 31 of 40 exceptions are money that was already
                recognised and has come undone, which is the loudest signal on the page and read
                as a six-word fragment at the end of a money line. */}
            {stats.reopened > 0 ? (
              <div className="tile-reopened">
                {stats.reopened} previously settled, now reopened
              </div>
            ) : null}
            <div className="tile-blurb">{detail}</div>
          </button>
        )
      })}
    </div>
  )
}
