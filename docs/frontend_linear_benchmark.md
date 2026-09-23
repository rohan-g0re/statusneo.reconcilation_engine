# The Linear benchmark, measured

The brief was *"make the frontend like the Linear website — better fonts and spacing — light
mode stays."* This document is the measurement that turned that into numbers, and the record
of where we match, where we were wrong, and where we diverge on purpose.

**Everything in the "linear.app" column was measured in a browser against the live site**, not
recalled. Three passes: the whole document (3,113 visible elements), the dense "AI and
automations" section that renders Linear's own dashboard mockup (298 elements — the closest
analogue to an operator dashboard), and the top nav on its own.

## The comparison

| Metric | linear.app, measured | Ours | Verdict |
|---|---|---|---|
| UI font | Inter Variable (472 uses) | Inter Variable, local npm | **match** |
| Font for identifiers | Berkeley Mono (185 uses) — issue ids, timestamps | `ui-monospace` on episode ids, dates, money | **match in kind** |
| Weights | 400 body, 510 headings, 590 dense labels | 400 body, 640 h1, 550 buttons | close; both use the variable font's off-standard weights |
| Dense-UI type scale | 13px (30), 12px (12), 11px (2) | 11 / 12 / 13 / 14 | **match** |
| Display tracking | **−0.022em**, and only at ≥32px (64/72/48/32px all measured exactly) | was −0.022em at **19px** | **we were wrong — fixed** |
| UI-text tracking | **−0.01em** at 13–15px | was −0.006em | **we were light — fixed** |
| Gaps | 8px (90), 6px (64), 4px (64) dominant | 2/4/6/8/12/16/24/32/48 | **match** — both include 4/6/8 |
| Spacing grid | *not* a rigid 4/8/12 grid; 6, 10, 11, 14 appear freely | ours is a strict scale | divergence, deliberate — see below |
| Card radius, dense section | **12px** dominant | was 10px | **fixed** |
| Pills | 999px | 999px | **match** |
| Shadow : border | 42 : 98 page-wide; 12 : 14 in the dense mockup | 0 : 251 | divergence, deliberate — see below |
| Background | `rgb(8, 9, 10)` — near-black | `rgb(247, 248, 250)` | divergence, **required** |

## What the measurement corrected

Three things, all of which "it looks about right" would never have surfaced:

1. **Display tracking applied at UI size.** Our 19px masthead carried −0.022em. Linear applies
   that value to display type only, with a clean threshold near 32px, and uses −0.01em for
   text at UI sizes. Ours is now −0.014em at 19px, with −0.022em reserved for the 28px
   display figure — the only genuinely display-sized text in this app.
2. **Body tracking too light.** −0.006em against their measured −0.01em. Now −0.01em.
3. **Panel radius 2px tight.** 10px against the 12px their own dashboard mockup uses.

## Where we diverge on purpose

**Colour.** Linear's site is dark: background `rgb(8,9,10)`, text `rgb(247,248,248)`. The brief
says light mode stays, so none of their colour decisions transfer directly. What does transfer
is the *relationship*: near-black rather than pure black, near-white rather than pure white. We
use `#f7f8fa` and `#16161c`, the same refusal to use `#fff`/`#000` inverted for a light UI.

**Shadows.** An earlier version of this build's commentary claimed Linear's look is
"borders-not-shadows". **The measurement says otherwise** — they run 42 shadowed elements to 98
bordered page-wide, and 12 to 14 in the dense section, using shadow for floating cards and
borders for internal dividers. We use zero shadows and 251 borders.

That stays, and the reason is the colour divergence rather than stubbornness: a shadow reads as
depth against a near-black page, and on a `#f7f8fa` page at this information density it reads
as smudge. Borders do the separating work in light mode. This is a considered difference from
the benchmark, which is worth more than an unconsidered match.

**Spacing grid.** Linear's real spacing is looser than a token scale — 6, 7, 10, 11 and 14px
all appear, and padding is frequently asymmetric (`14px 14px 14px 20px`). That is what a design
team tuning individual components produces. We keep a strict scale, because on this build the
scale is enforceable and a one-off inline value is exactly the defect a browser measurement
caught in `QueueTable.jsx` — `fontSize: 12.5`, the single off-scale size on the screen.

## Method

The loop was: measure the benchmark → measure ours → diff → change → re-measure. Both sides are
computed styles read out of a live browser, so neither column is an impression. The gaps above
were found by the diff, not by looking at screenshots and forming an opinion.
