# What I would change

The screens work. They are just built for the person who wrote them, not for the person buying.

Nothing here adds a feature. It is all moving, shrinking, renaming and splitting.

---

## The one problem behind all of it

Open the app today and the first things you see are: a title, a paragraph about how the date slider works, and a paragraph about how the AI panel works.

**You have to scroll to see a dollar figure.**

The money is the reason anyone opened the page. It is below the fold, and when you get there the biggest number on screen is a claim count, not an amount.

---

## 1. Move things

**Money to the top.** The three buckets go directly under the title. They are the page. Everything else is support.

**Date slider into the top bar.** It does not need its own box or its own heading. It is one control. Put it next to the title.

**AI panel below the list.** It sits second right now, above the money. That makes it look like a demo toy. It answers a question you ask *after* you have seen the numbers, so put it after them.

**Rebuild buttons out of the header.** Three buttons in the best spot on the page, and one of them wipes the database. They are test tools. Move them to a corner or behind a menu.

---

## 2. Shrink things

**Cut every explanation paragraph to one line.** Each panel opens with three to five lines explaining how the system was built. It is good writing. It is written for an engineer reviewing architecture. Replace each with one short line saying what the panel answers.

**Flip the number sizes.** The claim count is large and the dollars are small. Swap them. Dollars big, count as a small line underneath.

**Fewer columns.** The claim list has eight columns. The connectivity table has twelve. Show four or five, put the rest behind a "show detail" toggle. The data stays; it just stops shouting.

**Two code chips become one line with words.** Every claim shows two short codes side by side with nothing saying what they mean. Show the plain sentence instead, with the code small and grey next to it.

**Cap the long tables.** One table on the page has no height limit and can run for thousands of pixels. Every other table is capped. Cap it too.

---

## 3. Rename things

Right now the screen shows database words. Every one has a plain version.

| On screen today | Say this |
|---|---|
| Exception / Pending / Closed | Needs work / Waiting / Settled |
| Replay cursor | Showing the book as of… |
| Variance | Outstanding |
| Reimb. | Insurance |
| 315d | 315 days |
| 2026-07-01 | 1 July 2026 |
| Feed-level exceptions | Money and documents we could not match |
| Portfolio Analyst | Ask about the book |
| B-10 | Denied by the payer `B-10` |
| D-3 | Cash we received but cannot attribute `D-3` |

**Keep the codes.** Just make them small and grey, sitting after the words. An operator still needs to quote them, and the AI layer depends on them.

**Bring the hover text onto the page.** The best sentences in the product are hidden in tooltips. "A defect exists. Work it." explains a whole bucket, and you only see it if you hover with a mouse. Print it.

---

## 4. Split things

**The verdict table gets its own page.** It is a long list of code pairs with counts, and on the real data most of those counts are "1". It teaches nothing on the main screen and it makes the page enormous. Move it to its own tab and turn it into a reference — problems grouped into families, with the code dictionary next to them.

This is the only change that touches wiring. It is small: the tab switcher is a single value, so it means one more value, one more button, one more branch. No new page route, no new data call.

**Engineering detail moves behind a toggle.** Claim identifiers, file names, hashes, transport and schema details. Keep all of it — an operator needs it on demand. Just stop showing it by default.

---

## 5. Two small visual things

**Make the compliance flag look different.** A claim where the insurer refused to pay but the manufacturer paid the rebate anyway is a compliance problem. It currently renders as a tiny grey tag identical to every other tag. It should be the loudest thing on the row.

**Pick one style per kind of thing.** Right now a code can appear as a bordered pill, a grey chip, plain monospace, or a bare uppercase word — and the same pill style is used for both a diagnosis and a decision. One look per kind of thing.

---

## What stays exactly as it is

- The light colour scheme.
- The fonts, spacing and corner radius. Already measured, already right.
- No shadows. That was a deliberate choice, not an oversight.
- The three-bucket idea. It is correct and it is the product.
- The connectivity page's honesty — it says plainly what is *not* connected. Do not soften it.
- The date slider behaviour.
- The AI panel's two-step lock: you cannot get a recommendation before reading what happened.

---

## Order I would do it in

1. Reorder the landing page. Biggest gain, lowest risk.
2. Put words next to the codes. Second biggest gain.
3. Cut the explanation paragraphs.
4. Flip the number sizes.
5. Split off the verdict table.
6. Everything else.

Steps 1 to 4 are text and CSS. Nobody touches the backend.
