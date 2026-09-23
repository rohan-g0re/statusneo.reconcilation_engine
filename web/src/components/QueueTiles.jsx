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
// One label, and it is the bucket's own name. The tile used to carry two -- an invented action
// word in the heading and the engine's enum beside it in muted mono -- which made an operator
// read the same fact twice and left them to work out that the two were the same thing. See
// `labels.js` for why the invented half was the half that had to go: it claimed more than the
// bucket guarantees.
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
        const stats = byDisposition[tile.key] ?? { episodes: 0, variance_cents: 0 }
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
            </span>
            {/*
              The dollars are the headline and the claim count is the subtitle. It used to be the
              other way round -- a 28px episode count above a 12px money line -- so the eye landed
              on "40" when the reason anyone opened the screen is $517,240.34.

              Subtitle, though, not footnote. The count sat at 12px in muted mono, level with the
              blurb, which put "how many claims is that?" -- the second question anyone asks, and
              the one that turns a number into a workload -- below the line where the eye stops.
              It is a figure of its own now: the number reads at display weight, the unit stays
              small beside it.
            */}
            <div className="tile-money" data-testid={`tile-money-${tile.key}`}>
              {formatMoney(stats.variance_cents)}
            </div>
            <div className="tile-claims">
              <span className="n" data-testid={`tile-count-${tile.key}`}>{stats.episodes}</span>
              <span className="unit">{stats.episodes === 1 ? 'claim' : 'claims'}</span>
            </div>
            {/* Three lines per tile, and the third is a sentence rather than a second statistic.
                A red "N previously settled, now reopened" used to sit here, on the argument that
                money which had already been recognised and came undone is the loudest fact on the
                screen. It is -- but a tile is not where it gets read. Three of them stacked put a
                fourth figure on a card that already has two, in a colour that says "error" next to
                a count that is only context, and the eye stopped there instead of on the money.
                The fact keeps its real homes: the queue's default sort is reopened-first, every
                reopened row is marked, and the episode panel says which bucket it came back from. */}
            <div className="tile-blurb">{detail}</div>
          </button>
        )
      })}
    </div>
  )
}
