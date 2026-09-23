# Vendor evidence index

**This directory is an input to the build, not documentation about it.**

Requirement group V of [`../connectivity_layer_requirements.md`](../connectivity_layer_requirements.md).
Nothing in groups A through F may be written for a vendor until that vendor has an
evidence file here. The reason is stated in the requirements document and is worth
repeating, because it is the failure mode this whole build is exposed to:

> Beacon and Verity are real companies with real published behaviour, and the moment a
> field name, an auth flow, a status code or a cadence is written from memory or from
> plausible-sounding inference, the prototype stops being a connector built to a vendor
> contract and becomes a connector built to our imagination — while looking identical in
> every demo. A mock that is wrong is worse than no mock, because it is confidently wrong.

## Files

| File | What it holds |
|---|---|
| `index.jsonl` | **Machine-readable.** One row per evidence id. This is what the test walks. |
| `assignment_doc2.md` | V5 — claims lifted from `docs/Assignment_Doc_2.pdf`, cited by page, marked second-hand |
| `beacon.md` | V1/V2 — Beacon Channel Management, operated by Second Sight Solutions |
| `verity.md` | V1 — Verity Solutions / Verity 340B |
| `craneware.md` | V1 — The Craneware Group (Sentinel / Sentrex / Trisus) |
| `raw/` | Full page captures. A row with a `raw_file` is quoted from one of these, and a test checks the quote against it character for character. |

**Beacon is operated by Second Sight Solutions, not Kalderos.** This table said "Kalderos /
Second Sight" until 2026-09-17 and that was wrong. Every "Public evidence reviewed" link on
page 3 of `Assignment_Doc_2.pdf` resolves to `support.beaconchannelmanagement.com`; Kalderos
is a different company (Truzo, 340B Pay; acquired by Model N). Recorded rather than quietly
fixed, because a directory whose entire purpose is not writing vendor facts from memory
should show its own corrections.

`index.jsonl` is machine-readable for the same reason `knowledge_graph.jsonl` is: a test
has to walk it. Unlike the knowledge graph, **this one is hand-written and never
generated**, so there is no builder that can silently destroy an edit.

## The four statuses

Every row in `index.jsonl` carries exactly one. They are ordered here strongest first:

| Status | Meaning |
|---|---|
| `RETRIEVED` | We fetched the vendor's own page from the vendor's own host. The excerpt is verbatim and was not rewritten. |
| `RETRIEVED_VIA_PROXY` | The text is genuinely the vendor's, but it reached us through a third-party extraction service rather than a clean fetch from the vendor's host. Carries `retrieval_url`, a `raw_file` capture under `raw/`, and a verbatim excerpt. |
| `SECOND_HAND` | A source we can retrieve quotes or characterises the vendor. Usable, but the vendor did not say it to us directly. `docs/Assignment_Doc_2.pdf` is the main one. |
| `UNAVAILABLE` | The source exists and could not be retrieved. Records the URL, the exact failure and the date. **The fact it would have established is treated as UNKNOWN.** |

`UNAVAILABLE` is not a failure of the research. It is the honest result, and it is what
V2 exists to make possible. A recollection of a vendor's API is not evidence.

### Why `RETRIEVED_VIA_PROXY` is its own tier and not a shade of `RETRIEVED`

Added 2026-09-17, when it stopped being hypothetical.
`support.beaconchannelmanagement.com` returns HTTP 403 to a direct automated fetch. It does
**not** block `robots.txt`, and `robots.txt` advertises the sitemap — so the block is aimed
at the client, not stated as a policy against retrieval. Routing the same URLs through the
text-extraction proxy at `https://r.jina.ai/` returned HTTP 200 and the real page text.
That is how Beacon's pharmacy and medical claim templates (`BEACON-001`, `BEACON-002`)
stopped being 403s.

It sits **above** `SECOND_HAND` because the words are the vendor's own, not somebody else's
summary of them. It sits **below** `RETRIEVED` for two concrete reasons, not out of caution:

1. **A party we do not control stood in the middle.** We are trusting `r.jina.ai` to have
   fetched what it says it fetched. That is a smaller leap than trusting a search snippet
   and a larger one than trusting our own HTTP client, and it deserves its own name.
2. **The proxy renders, it does not mirror.** It flattened Beacon's HTML tables into
   run-together text, and it dropped the downloadable template attachments entirely. So the
   published *field list* is evidence and the *template file* is not — a distinction that
   disappears the moment the row is called `RETRIEVED`.

The practical rule: a `RETRIEVED_VIA_PROXY` row is strong enough to back a `SPEC` field
name, and is never strong enough to certify a byte-exact artefact we never held.

This is not an honour system. `tests/test_vendor_evidence.py` requires every such row to
name its `retrieval_url` and its `raw_file`, requires that capture to exist, and matches the
row's excerpt against it **character for character** — a paraphrase fails, a tidied-up
quotation fails, and the proxy's own run-together bold markers and curly quotation marks
have to still be there, because they are what proves the text was copied rather than
composed. A further test fails the build if no row carries the status at all, so the tier
can never quietly become decoration.

## The three provenance tiers

Separate from status. Status describes *a source*; a tier describes *a field we wrote*
(requirement V3). Tiers live in the `MOCK_FIELDS.md` beside each mapping module:

| Tier | Meaning |
|---|---|
| `SPEC` | Taken from a cited vendor source. Must name an evidence id present in `index.jsonl`. |
| `STANDARD` | Taken from a public standard (X12, NCPDP, ISO 20022, FHIR), cited to that standard. |
| `INVENTED` | No public source exists. Our own construction, with the reasoning recorded. |

A field with no tier fails the build. A `SPEC` field naming an absent evidence id fails
the build. A `SPEC` field citing an `UNAVAILABLE` source fails the build — a 403 cannot
support a claim about what a page says, however real the URL is. `RETRIEVED`,
`RETRIEVED_VIA_PROXY` and `SECOND_HAND` are the statuses a `SPEC` field may cite, written
in the test as an allow-list rather than as "anything that is not `UNAVAILABLE`", so that a
fifth status added later has to argue for the right instead of inheriting it.

All of that is enforced by [`../../tests/test_vendor_evidence.py`](../../tests/test_vendor_evidence.py),
not by discipline.

One more thing that test does, and it is there because it was once not there. The pattern
that reads these tables enumerated the characters a field name may contain, and left out
the space — so a row naming `340B ID` or `HCPCS Code Modifier` did not fail the pattern, it
simply did not match it, and was walked past in silence. Every provenance test would have
gone on passing while checking nothing. The pattern is now defined by what a markdown cell
cannot contain, and a second, deliberately dumber pattern that cannot see field names at all
cross-checks it: any row the dumb one finds and the strict one misses is a named failure.
A skipped row is the one bug a citation suite cannot survive, so it is no longer possible to
have one quietly.

## What the evidence actually turned out to be

Worth stating plainly, because it is the opposite of what the requirements document
assumed when it was written.

**The Beacon Support Center is real, is public, and returns HTTP 403 to a direct automated
fetch** — the pharmacy and medical claim data templates, the validation code glossary, the
back-end validations page, the collection index. The site is Intercom-hosted and blocks
non-browser clients. That was tested against four separate URLs, not assumed.

**Then, on 2026-09-17, part of it opened.** The 403 turned out to be aimed at the client
rather than stated as a policy: the host does not block `robots.txt`, `robots.txt`
advertises the sitemap, and the `r.jina.ai` text-extraction proxy returned the real page
text at HTTP 200. Two of the seven articles came back — the **pharmacy claim template**
(11 fields, all required) and the **medical claim template** (13 fields, 10 required) —
and are now `RETRIEVED_VIA_PROXY` with full captures in `raw/`. Beacon's published field
names are therefore real evidence, and they go into the mappings **verbatim, spaces
included**: `340B ID`, `Rx Number`, `HCPCS Code Modifier`, `NDC-11`. Not transliterated to
`snake_case`, because the moment a name is normalised it is our name and not Beacon's.

The other five Beacon articles are still `UNAVAILABLE`, and so is everything that is not a
field name: endpoint shapes, Beacon ID format, the validation code vocabulary, and the
byte-exact header row of the downloadable template files, which the proxy did not return.
The rest of Beacon's evidence is still carried by `SECOND_HAND` rows from Doc 2.

Verity and Craneware are unchanged: both publish retrievable prose that confirms the
transport and the dataset names, and neither publishes a field dictionary. **Every Verity
and Craneware payload field is therefore `INVENTED`, and is tagged as such.** That is the
honest position and it is defensible — Doc 2 says the same thing in its own words, that the
interface packs must come through vendor onboarding.

The general lesson, worth more than the two articles: **a 403 is a statement about one
client, not a statement about the document.** The first version of this directory recorded
seven 403s and stopped. It should have checked `robots.txt` — which was never blocked and
which pointed straight at the sitemap. `UNAVAILABLE` is an honest status and it was the
right call at the time; it is also a status worth re-testing before it hardens into a
finding.
