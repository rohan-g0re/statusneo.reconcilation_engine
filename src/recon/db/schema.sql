-- ═══════════════════════════════════════════════════════════════════════════
-- recon -- persistence schema
--
-- Executed whole, once, against a fresh database.  There are no ALTER migrations:
-- the database is a derived artefact, fully reconstructible by replaying immutable
-- feeds, so schema evolution during the prototype is drop-and-rebuild.  (A real
-- deployment needs versioned, reversible migrations; that gap is named in the
-- README.)
--
-- Three things in here are architecture rather than storage:
--
--   1. The pharmacy-XOR-medical rule is a CHECK on `episode`.  Duplicate billing is
--      therefore *unrepresentable*, not merely discouraged by a code review.
--   2. Source-record immutability is a set of BEFORE UPDATE / BEFORE DELETE triggers
--      that RAISE(ABORT).  "Nothing is ever written back to a source record" is the
--      load-bearing claim behind replay, `reopened_from` and the whole audit trail;
--      a trigger turns it from a promise into a property.
--   3. Every derived row carries the `received_at` of the record that caused it, and
--      every crosswalk or parked lookup is expected to carry a cursor predicate
--      against it.  Without that, replay leaks future knowledge -- a key established
--      on 18 March would resolve a lookup performed at cursor 10 March.
--
-- Type discipline: money is INTEGER cents (never REAL -- a float 47218.4 in an
-- amount column produces a residue indistinguishable from a real variance);
-- timestamps are fixed-width TEXT `YYYY-MM-DDTHH:MM:SSZ` so lexicographic order is
-- chronological order and the cursor filter is a plain string comparison; dates are
-- TEXT `YYYY-MM-DD`; booleans are INTEGER 0/1.
--
-- STRICT throughout (SQLite >= 3.37).  Without it SQLite's type affinity would
-- happily store `47218.4` in an INTEGER column.
--
-- There is no age column, no SLA column and no due-by column anywhere, by design
-- (Decision 19).  Aging is `cursor - date_of_service`, computed in SQL at read time.
-- A test regexes every column name in every table and fails on any that look like
-- one.
-- ═══════════════════════════════════════════════════════════════════════════

-- ═══ META ══════════════════════════════════════════════════════════════════
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;
-- rows: schema_version · master_seed · profile · reference_fingerprint
--       window_start · window_end · adapter_version · engine_version

-- ═══ INGEST BATCH ══════════════════════════════════════════════════════════
CREATE TABLE ingest_batch (
  batch_id      INTEGER PRIMARY KEY,
  source_file   TEXT    NOT NULL,
  source_system TEXT    NOT NULL,
  file_sha256   TEXT    NOT NULL UNIQUE,   -- re-loading the same file is a no-op
  record_count  INTEGER NOT NULL,
  loaded_at     TEXT    NOT NULL           -- operational wall-clock; NOT a domain date
) STRICT;

-- ═══ RAW -- immutable, exactly as received ═════════════════════════════════
CREATE TABLE raw_record (
  raw_id           INTEGER PRIMARY KEY,
  batch_id         INTEGER NOT NULL REFERENCES ingest_batch(batch_id),
  source_system    TEXT    NOT NULL CHECK (source_system IN (
                     'PBM_ADJUDICATION','PBM_REMITTANCE','TPA_PORTAL','MANUFACTURER_REBATE',
                     'CLEARINGHOUSE_837','MEDICAL_REMITTANCE','BANK')),
  source_record_id TEXT,                   -- record_id as given; NULL for bank CSV rows
  source_line_no   INTEGER NOT NULL,       -- 1-based position in the file
  payload          TEXT    NOT NULL,       -- verbatim JSONL line, or CSV row re-encoded as JSON
  payload_sha256   TEXT    NOT NULL,
  received_at      TEXT    NOT NULL,       -- THE single system-added field (Decision 16),
                                           -- lifted out of the payload for indexing only
  UNIQUE (batch_id, source_line_no)
) STRICT;
-- Deliberately NO unique constraint on source_record_id.  Defect D-1 is literally
-- "the same record_id emitted twice in the file", so the raw layer has to be able to
-- store it twice.  Adding UNIQUE(source_record_id) here "for safety" would silently
-- delete that test case.
CREATE INDEX ix_raw_received_at ON raw_record(received_at);
CREATE INDEX ix_raw_record_id   ON raw_record(source_system, source_record_id);
CREATE INDEX ix_raw_payload_sha ON raw_record(payload_sha256);   -- D-1 detection is a query

CREATE TRIGGER trg_raw_no_update BEFORE UPDATE ON raw_record
  BEGIN SELECT RAISE(ABORT, 'raw_record is immutable'); END;
CREATE TRIGGER trg_raw_no_delete BEFORE DELETE ON raw_record
  BEGIN SELECT RAISE(ABORT, 'raw_record is immutable'); END;

-- ═══ QUARANTINE -- D-5 malformed: cannot be normalized, lineage intact ═════
CREATE TABLE quarantined_record (
  quarantine_id INTEGER PRIMARY KEY,
  raw_id        INTEGER NOT NULL UNIQUE REFERENCES raw_record(raw_id),
  received_at   TEXT    NOT NULL,
  reason_code   TEXT    NOT NULL,   -- validated against domain.enums.QuarantineReason in Python
  detail        TEXT
) STRICT;
CREATE INDEX ix_quarantine_received ON quarantined_record(received_at);

-- ═══ NORMALIZED -- canonical shape, lineage back to raw ════════════════════
CREATE TABLE normalized_record (
  norm_id          INTEGER PRIMARY KEY,
  raw_id           INTEGER NOT NULL REFERENCES raw_record(raw_id),         -- LINEAGE
  parent_norm_id   INTEGER REFERENCES normalized_record(norm_id),          -- 835 claim lines,
                                                                           -- rebate dispense lines
  -- record_kind IS a closed canonical vocabulary (anything that does not map is
  -- quarantined, never coerced), so unlike reason_code it DOES carry a CHECK.  The
  -- literal list must stay in lockstep with domain.enums.RecordKind; a test parses
  -- this file and asserts they match.
  record_kind      TEXT NOT NULL CHECK (record_kind IN (
                     'PHARMACY_CLAIM','PHARMACY_REVERSAL',
                     'REMITTANCE','REMITTANCE_CLAIM_LINE','PROVIDER_LEVEL_ADJUSTMENT',
                     'MEDICAL_SUBMISSION','MEDICAL_SUBMISSION_LINE','MEDICAL_ACKNOWLEDGMENT',
                     'TPA_QUALIFICATION','TPA_REBATE_REQUEST','TPA_MANUFACTURER_DECISION',
                     'TPA_REVERSAL','REBATE_BATCH','REBATE_DISPENSE_LINE','BANK_TRANSACTION')),
  source_system    TEXT NOT NULL CHECK (source_system IN (
                     'PBM_ADJUDICATION','PBM_REMITTANCE','TPA_PORTAL','MANUFACTURER_REBATE',
                     'CLEARINGHOUSE_837','MEDICAL_REMITTANCE','BANK')),
  adapter_version  TEXT NOT NULL,
  received_at      TEXT NOT NULL,
  -- Idempotency is the SOURCE record_id, never a natural key.  A-17 (duplicate
  -- payment) and B-16 (duplicate 835) are *genuine* duplicate business events with
  -- different record ids; collapsing on a natural key would merge them and delete two
  -- of the state space's track states.  Bank CSV rows carry no record id, so they use
  -- ach_trace_number, which the feed spec states is always present -- the network
  -- cannot move money without one.  Both forms still mean "this delivery arrived
  -- twice", which is D-1.
  idempotency_key  TEXT NOT NULL,

  -- canonical crosswalk-facing columns, nullable per kind
  pharmacy_npi         TEXT,
  provider_npi         TEXT,   -- 837 billing provider; the medical 340B join (Decision 37)
  -- VERBATIM, exactly as the feed spelled it (Decision A23).  There is deliberately no
  -- normalized twin of this column.  The generators inject an identifier-drift defect
  -- ('07845102' in one feed against '7845102' in another) and that drift is MEANT to
  -- miss: the miss IS the D-6 crosswalk-failure exception.  A normalized column here
  -- would resolve the record, delete the exception, and leave the engine covering up
  -- the defect it exists to surface.  The claim is not missing -- the mapping failed.
  rx_number            TEXT,
  fill_number          TEXT,
  ndc11                TEXT,
  date_of_service      TEXT,   -- YYYY-MM-DD
  clm01                TEXT,
  clp07                TEXT,
  trn02                TEXT,
  ach_trace_number     TEXT,
  allocation_code      TEXT,
  authorization_number TEXT,
  payer_id             TEXT,   -- resolved reference id, NULL if unresolvable
  amount_cents         INTEGER,
  quantity_milli       INTEGER,
  status_code          TEXT,
  canonical            TEXT NOT NULL       -- kind-specific canonical JSON
) STRICT;

CREATE UNIQUE INDEX ux_norm_idempotency ON normalized_record(record_kind, idempotency_key);

CREATE INDEX ix_norm_kind_received ON normalized_record(record_kind, received_at);
CREATE INDEX ix_norm_parent        ON normalized_record(parent_norm_id);
-- ═══ A note on what is NOT indexed here ════════════════════════════════════════════════
-- normalized_record deliberately carries almost no identifier indexes.  Resolving an identifier
-- is the crosswalk's job, and crosswalk_key is the table indexed for it; duplicating those
-- indexes here would cost write throughput on a write-once table for reads that never happen.
-- An earlier version carried nine such indexes and an audit found every one of them unused.

-- The amount+date rescue path: when the CCD+ addenda are lost (~20% of deposits) the only
-- remaining handle is the pair (amount, effective date).
--
-- NOT a partial index, and that is deliberate.  A partial index on
-- `WHERE record_kind IN ('REMITTANCE','REBATE_BATCH')` looks tighter, but the call site binds
-- record_kind as a *parameter*, and SQLite cannot prove a bound placeholder satisfies a partial
-- index's literal predicate.  It silently declines the index and walks every remittance instead --
-- a scan disguised as a seek, on ~20% of all deposits.  A plain composite index is usable
-- whatever form the parameters take.
CREATE INDEX ix_norm_amount_match  ON normalized_record(record_kind, amount_cents, date_of_service);

-- The backward half of the same rescue: find keyless parked deposits by amount.  Partial here is
-- safe because `trn02 IS NULL` is a literal predicate in the query, not a parameter.
CREATE INDEX ix_norm_keyless_amount ON normalized_record(amount_cents, date_of_service)
  WHERE trn02 IS NULL;

CREATE TRIGGER trg_norm_no_update BEFORE UPDATE ON normalized_record
  BEGIN SELECT RAISE(ABORT, 'normalized_record is immutable; re-normalize by rebuild'); END;
CREATE TRIGGER trg_norm_no_delete BEFORE DELETE ON normalized_record
  BEGIN SELECT RAISE(ABORT, 'normalized_record is immutable; re-normalize by rebuild'); END;

-- ═══ EPISODE -- claim-level; the XOR is a CHECK, not a convention ══════════
CREATE TABLE episode (
  episode_id               TEXT PRIMARY KEY,                    -- 'EP-000001'
  reimbursement_track      TEXT    NOT NULL CHECK (reimbursement_track IN ('PHARMACY','MEDICAL')),
  anchor_norm_id           INTEGER NOT NULL REFERENCES normalized_record(norm_id),
  pharmacy_npi             TEXT,
  ndc11                    TEXT    NOT NULL,
  date_of_service          TEXT    NOT NULL,
  quantity_milli           INTEGER NOT NULL,
  prescriber_npi           TEXT,
  -- pharmacy identity (NULL on medical)
  rx_number                TEXT,
  fill_number              TEXT,
  pbm_id                   TEXT,
  cardholder_id            TEXT,
  -- medical identity (NULL on pharmacy)
  clm01                    TEXT,
  medical_payer_id         TEXT,
  billing_provider_npi     TEXT,
  -- 340B
  is_340b_flagged          INTEGER NOT NULL DEFAULT 0 CHECK (is_340b_flagged IN (0,1)),
  covered_entity_id        TEXT,
  created_from_received_at TEXT    NOT NULL,
  -- Exactly one reimbursement track.  Never both, never neither.  This is what makes
  -- duplicate billing of a single dispense unrepresentable rather than flagged
  -- downstream.
  CHECK (
    (reimbursement_track = 'PHARMACY'
       AND rx_number IS NOT NULL AND pharmacy_npi IS NOT NULL AND clm01 IS NULL)
    OR
    (reimbursement_track = 'MEDICAL'
       AND clm01 IS NOT NULL AND billing_provider_npi IS NOT NULL AND rx_number IS NULL)
  )
) STRICT;

CREATE UNIQUE INDEX ux_episode_pharmacy
  ON episode(pharmacy_npi, rx_number, fill_number, date_of_service)
  WHERE reimbursement_track = 'PHARMACY';
CREATE UNIQUE INDEX ux_episode_medical ON episode(clm01) WHERE reimbursement_track = 'MEDICAL';
CREATE INDEX ix_episode_dos     ON episode(date_of_service);   -- aging sort at read time
CREATE INDEX ix_episode_created ON episode(created_from_received_at);
CREATE INDEX ix_episode_anchor  ON episode(anchor_norm_id);

CREATE TRIGGER trg_episode_no_update BEFORE UPDATE ON episode
  BEGIN SELECT RAISE(ABORT, 'episode identity is immutable'); END;
CREATE TRIGGER trg_episode_no_delete BEFORE DELETE ON episode
  BEGIN SELECT RAISE(ABORT, 'episode identity is immutable'); END;

-- ═══ CROSSWALK / RESOLVED KEYS ═════════════════════════════════════════════
CREATE TABLE crosswalk_key (
  crosswalk_id          INTEGER PRIMARY KEY,
  -- The EIGHT key types (Decision A24).  Mirrored by domain.enums.KeyType; a test
  -- parses this file and asserts the two lists match exactly.
  --
  -- The ACH trace number is NOT among them: it is banking plumbing with zero business
  -- content and resolves to nothing on its own, so it stays a column on the bank record
  -- serving D-1 duplicate detection.  There is no CLP01_PARSED either -- a pharmacy 835
  -- carries payee_npi and the service line's date_of_service, so parsing CLP01 apart
  -- yields the whole NCPDP_CLAIM key rather than a weaker two-field variant.
  key_type              TEXT NOT NULL CHECK (key_type IN (
                          'NCPDP_CLAIM','MEDICAL_CLM01','PAYER_ICN','TRN02',
                          'ALLOCATION_CODE','NATURAL_340B_PHARMACY','NATURAL_340B_MEDICAL',
                          'PBM_AUTH')),
  key_value             TEXT NOT NULL,     -- canonical normalized string form
  episode_id            TEXT REFERENCES episode(episode_id),
  remittance_norm_id    INTEGER REFERENCES normalized_record(norm_id),  -- bank hop 1
  resolved_from_norm_id INTEGER NOT NULL REFERENCES normalized_record(norm_id),
  first_seen_at         TEXT NOT NULL,     -- received_at of the establishing record
  CHECK (episode_id IS NOT NULL OR remittance_norm_id IS NOT NULL)
) STRICT;

CREATE UNIQUE INDEX ux_crosswalk ON crosswalk_key(key_type, key_value, resolved_from_norm_id);
-- HOT QUERY 1 -- covering index: includes the cursor column and both payload columns,
-- so key resolution on an inbound document is one index probe with no table fetch.
CREATE INDEX ix_crosswalk_lookup
  ON crosswalk_key(key_type, key_value, first_seen_at, episode_id, remittance_norm_id,
                   resolved_from_norm_id);
CREATE INDEX ix_crosswalk_episode ON crosswalk_key(episode_id);
-- HOT QUERY 3 -- "which episode did this record resolve to?", asked once per remittance claim
-- line, PLB entry and rebate dispense line during every allocation.  Without it the allocator
-- scans the whole crosswalk per child, which is the difference between an allocation that is a
-- handful of seeks and one that is quadratic in the size of the crosswalk.
CREATE INDEX ix_crosswalk_resolved_from ON crosswalk_key(resolved_from_norm_id, episode_id);

-- ═══ VERDICT -- append-only, keyed (episode, cursor) ═══════════════════════
CREATE TABLE verdict (
  verdict_id                   INTEGER PRIMARY KEY,
  episode_id                   TEXT NOT NULL REFERENCES episode(episode_id),
  cursor_at                    TEXT NOT NULL,   -- the (episode, cursor) pair
  computed_at                  TEXT NOT NULL,   -- operational wall-clock only
  engine_version               TEXT NOT NULL,
  reference_fingerprint        TEXT NOT NULL,   -- detects price-table drift

  episode_disposition          TEXT NOT NULL CHECK (episode_disposition       IN ('CLOSED','PENDING','EXCEPTION')),
  reimbursement_disposition    TEXT NOT NULL CHECK (reimbursement_disposition IN ('CLOSED','PENDING','EXCEPTION')),
  rebate_disposition           TEXT          CHECK (rebate_disposition        IN ('CLOSED','PENDING','EXCEPTION')),
                                                   -- NULL iff rebate_verdict_code = 'C-00'

  -- 31 reimbursement codes (A-01..A-17 minus the retired A-03; B-01..B-16 minus the
  -- retired B-03) and 12 rebate codes (C-00..C-14 minus C-04, C-06, C-12).  The
  -- ranges are NOT contiguous, so there is no CHECK here -- the authority is
  -- domain.verdicts, which is itself tested against decision_tree/pairs.json.
  reimbursement_verdict_code   TEXT NOT NULL,
  rebate_verdict_code          TEXT NOT NULL,   -- 'C-00' = track absent, not "track finished"

  expected_reimbursement_cents INTEGER NOT NULL DEFAULT 0,
  received_reimbursement_cents INTEGER NOT NULL DEFAULT 0,
  reimbursement_variance_cents INTEGER NOT NULL DEFAULT 0,
  expected_rebate_cents        INTEGER NOT NULL DEFAULT 0,
  received_rebate_cents        INTEGER NOT NULL DEFAULT 0,
  rebate_variance_cents        INTEGER NOT NULL DEFAULT 0,

  -- Reopened is a flag, never a fourth disposition.
  reopened_from                TEXT CHECK (reopened_from IN ('CLOSED','PENDING')),
  previously_closed_at         TEXT,
  reopened_on                  TEXT,

  UNIQUE (episode_id, cursor_at)
) STRICT;
-- No age column.  No sla_*.  No due_by.  Aging is cursor - date_of_service, computed
-- at read time.

-- HOT QUERY 2 -- latest verdict per episode at a cursor.
CREATE INDEX ix_verdict_latest   ON verdict(episode_id, cursor_at DESC, verdict_id DESC);
CREATE INDEX ix_verdict_cursor   ON verdict(cursor_at);
CREATE INDEX ix_verdict_queue    ON verdict(episode_disposition, cursor_at DESC);
CREATE INDEX ix_verdict_reopened ON verdict(reopened_from) WHERE reopened_from IS NOT NULL;

CREATE TRIGGER trg_verdict_no_update BEFORE UPDATE ON verdict
  BEGIN SELECT RAISE(ABORT, 'verdict log is append-only'); END;
CREATE TRIGGER trg_verdict_no_delete BEFORE DELETE ON verdict
  BEGIN SELECT RAISE(ABORT, 'verdict log is append-only'); END;

-- ═══ VERDICT CHILDREN -- reasons are a LIST, and the list is ordered ═══════
CREATE TABLE verdict_reason (
  verdict_id  INTEGER NOT NULL REFERENCES verdict(verdict_id),
  ordinal     INTEGER NOT NULL,
  reason_code TEXT    NOT NULL,   -- validated against domain.enums.ReasonCode in Python.
                                  -- Deliberately not a CHECK: Group 3 adds reason codes
                                  -- as it implements rules, and a CHECK would make every
                                  -- addition a schema migration.
  track       TEXT    NOT NULL CHECK (track IN ('REIMBURSEMENT','REBATE','CROSS_TRACK')),
  PRIMARY KEY (verdict_id, ordinal)
) STRICT;
-- "Show me every EXCEPTION carrying CASH_MISMATCH" is an indexed lookup, not a JSON scan.
CREATE INDEX ix_verdict_reason_code ON verdict_reason(reason_code);

CREATE TRIGGER trg_verdict_reason_no_update BEFORE UPDATE ON verdict_reason
  BEGIN SELECT RAISE(ABORT, 'verdict log is append-only'); END;
CREATE TRIGGER trg_verdict_reason_no_delete BEFORE DELETE ON verdict_reason
  BEGIN SELECT RAISE(ABORT, 'verdict log is append-only'); END;

CREATE TABLE verdict_cross_track_flag (
  verdict_id INTEGER NOT NULL REFERENCES verdict(verdict_id),
  flag_code  TEXT    NOT NULL CHECK (flag_code IN ('X-1','X-2','X-3','X-4','X-5','X-6','X-7')),
  PRIMARY KEY (verdict_id, flag_code)
) STRICT;

CREATE TRIGGER trg_verdict_flag_no_update BEFORE UPDATE ON verdict_cross_track_flag
  BEGIN SELECT RAISE(ABORT, 'verdict log is append-only'); END;
CREATE TRIGGER trg_verdict_flag_no_delete BEFORE DELETE ON verdict_cross_track_flag
  BEGIN SELECT RAISE(ABORT, 'verdict log is append-only'); END;

-- Evidence: lineage runs DOWNWARD.  raw_id is carried alongside norm_id so that
-- "trace this number back to the synthetic source record" is one join rather than two.
CREATE TABLE verdict_evidence (
  verdict_id INTEGER NOT NULL REFERENCES verdict(verdict_id),
  ordinal    INTEGER NOT NULL,
  norm_id    INTEGER NOT NULL REFERENCES normalized_record(norm_id),
  raw_id     INTEGER NOT NULL REFERENCES raw_record(raw_id),
  role       TEXT    NOT NULL,   -- validated against domain.enums.EvidenceRole in Python
  PRIMARY KEY (verdict_id, ordinal)
) STRICT;
CREATE INDEX ix_evidence_raw  ON verdict_evidence(raw_id);
CREATE INDEX ix_evidence_norm ON verdict_evidence(norm_id);

CREATE TRIGGER trg_verdict_evidence_no_update BEFORE UPDATE ON verdict_evidence
  BEGIN SELECT RAISE(ABORT, 'verdict log is append-only'); END;
CREATE TRIGGER trg_verdict_evidence_no_delete BEFORE DELETE ON verdict_evidence
  BEGIN SELECT RAISE(ABORT, 'verdict log is append-only'); END;

-- ═══ PARKED -- unresolvable on arrival; the backward re-check must be indexed ═
CREATE TABLE parked_record (
  parked_id   INTEGER PRIMARY KEY,
  norm_id     INTEGER NOT NULL UNIQUE REFERENCES normalized_record(norm_id),
  raw_id      INTEGER NOT NULL REFERENCES raw_record(raw_id),
  record_kind TEXT    NOT NULL,
  received_at TEXT    NOT NULL,
  park_reason TEXT    NOT NULL CHECK (park_reason IN (
                'NO_KEY_MATCH','AMBIGUOUS_KEY_MATCH','NO_KEYS_PRESENT'))
) STRICT;
-- Orphan-rebate reporting asks for parked rows by kind, so the kind leads.
CREATE INDEX ix_parked_kind_received ON parked_record(record_kind, received_at);

CREATE TABLE parked_record_key (
  parked_id INTEGER NOT NULL REFERENCES parked_record(parked_id),
  key_type  TEXT    NOT NULL CHECK (key_type IN (
              'NCPDP_CLAIM','MEDICAL_CLM01','PAYER_ICN','TRN02',
              'ALLOCATION_CODE','NATURAL_340B_PHARMACY','NATURAL_340B_MEDICAL',
              'PBM_AUTH')),
  key_value TEXT    NOT NULL,
  PRIMARY KEY (parked_id, key_type, key_value)
) STRICT;
-- Without this index the backward re-check is a full scan of the parked pool on EVERY
-- arrival: the primary key leads with parked_id, which is the wrong way round for a
-- lookup by key.
CREATE INDEX ix_parked_key_lookup ON parked_record_key(key_type, key_value);

-- Decision A24 asks for "a partial index on live rows", and this is where that
-- requirement meets what SQLite can actually express, so the deviation is stated rather
-- than glossed.  A parked row is *live* until a resolution row exists for it, and
-- liveness therefore lives in a different table -- which a partial-index WHERE clause
-- cannot reference.  The alternative is a mutable `resolved_at` column on
-- parked_record, which buys the literal partial index at the cost of making the parked
-- pool update-in-place; that trade is declined, because append-only is what makes
-- replay and the audit trail work at all (Decision A8).
--
-- What the hot path does instead, and why it is still a point seek rather than a scan:
-- ix_parked_key_lookup seeks straight to the handful of rows carrying the inbound
-- document's key, then the anti-join against parked_record_resolution is one primary-key
-- probe per candidate.  Resolved rows accumulate forever but are never *scanned* --
-- they are only ever probed by id, for keys that already matched.

-- Unparking appends a resolution row; it never deletes the park row.
CREATE TABLE parked_record_resolution (
  parked_id                      INTEGER PRIMARY KEY REFERENCES parked_record(parked_id),
  resolved_by_norm_id            INTEGER NOT NULL REFERENCES normalized_record(norm_id),
  resolved_by_received_at        TEXT    NOT NULL,
  resolved_to_episode_id         TEXT    REFERENCES episode(episode_id),
  resolved_to_remittance_norm_id INTEGER REFERENCES normalized_record(norm_id)
) STRICT;
CREATE INDEX ix_parked_res_received ON parked_record_resolution(resolved_by_received_at);

-- ═══ CASH ALLOCATION -- a bank line resolves in two hops ═══════════════════
-- Beyond the six required tables.  A deposit carries no claim identifier at all: its
-- trn02 resolves to a *remittance*, and the remittance holds the claim list.  One
-- bank line can therefore touch dozens of episodes through one remittance.
CREATE TABLE cash_allocation (
  allocation_id         INTEGER PRIMARY KEY,
  bank_norm_id          INTEGER NOT NULL REFERENCES normalized_record(norm_id),
  remittance_norm_id    INTEGER REFERENCES normalized_record(norm_id),
  episode_id            TEXT    REFERENCES episode(episode_id),
  allocated_cents       INTEGER NOT NULL,
  basis                 TEXT    NOT NULL CHECK (basis IN (
                          'TRN02','ACH_TRACE','ALLOCATION_CODE','AMOUNT_DATE','RESIDUAL')),
  caused_by_received_at TEXT    NOT NULL
) STRICT;
CREATE INDEX ix_alloc_bank    ON cash_allocation(bank_norm_id);
CREATE INDEX ix_alloc_episode ON cash_allocation(episode_id);
CREATE INDEX ix_alloc_remit   ON cash_allocation(remittance_norm_id);
-- D-7 residual reporting: cash attributed no more precisely than the account it landed in.
CREATE INDEX ix_alloc_residual ON cash_allocation(basis, caused_by_received_at);

-- ═══ WORK ITEM -- the agent's only write target ════════════════════════════
-- Beyond the six required tables.  The agent flags a human; it never moves money and
-- never writes a ledger entry.  Append-only: there is no close and no update.
CREATE TABLE work_item (
  work_item_id       INTEGER PRIMARY KEY,
  episode_id         TEXT    NOT NULL REFERENCES episode(episode_id),
  created_at         TEXT    NOT NULL,
  created_by         TEXT    NOT NULL,       -- agent role name
  at_cursor          TEXT    NOT NULL,
  from_verdict_id    INTEGER NOT NULL REFERENCES verdict(verdict_id),
  summary            TEXT    NOT NULL,
  recommended_action TEXT    NOT NULL
) STRICT;
CREATE INDEX ix_work_item_episode ON work_item(episode_id);

PRAGMA user_version = 2;
