import React from 'react'
import { formatMoney } from '../api.js'

// Three dispositions, because "what do I do with this?" has three answers.
//
// A stat tile rather than a chart: three counts are read, not compared along a continuum, and a bar
// chart of three bars would add ink without adding meaning.
//
// Each tile carries an icon AND a label as well as its colour. These are *status* colours — reserved
// for state, never reused as a series — and two of them sit below 3:1 contrast on a light surface by
// design, so the icon-plus-label pairing is what keeps the meaning legible. Colour alone never
// carries it.
const TILES = [
  {
    key: 'EXCEPTION',
    label: 'Exception',
    icon: '▲',
    color: 'var(--status-critical)',
    blurb: 'A defect exists. Work it.',
  },
  {
    key: 'PENDING',
    label: 'Pending',
    icon: '◷',
    color: 'var(--status-warning)',
    blurb: 'Waiting on an external party. No defect. Ranked by age.',
  },
  {
    key: 'CLOSED',
    label: 'Closed',
    icon: '✓',
    color: 'var(--status-good)',
    blurb: 'Nothing to do.',
  },
]

export default function QueueTiles({ overview, selected, onSelect }) {
  const byDisposition = overview?.by_disposition ?? {}
  return (
    <div className="tiles" role="group" aria-label="Episodes by disposition">
      {TILES.map((tile) => {
        const stats = byDisposition[tile.key] ?? { episodes: 0, variance_cents: 0, reopened: 0 }
        return (
          <button
            key={tile.key}
            className="tile"
            style={{ '--tile-color': tile.color }}
            aria-pressed={selected === tile.key}
            data-testid={`tile-${tile.key}`}
            onClick={() => onSelect(tile.key)}
            title={tile.blurb}
          >
            <span className="label">
              <span className="icon" aria-hidden="true">{tile.icon}</span>
              {tile.label}
            </span>
            <div className="count" data-testid={`tile-count-${tile.key}`}>{stats.episodes}</div>
            <div className="money">
              {formatMoney(stats.variance_cents)} variance
              {stats.reopened > 0 ? (
                <>
                  {' · '}
                  <span className="reopened">{stats.reopened} reopened</span>
                </>
              ) : null}
            </div>
          </button>
        )
      })}
    </div>
  )
}
