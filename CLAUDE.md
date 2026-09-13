Global Instructions

  Work Parallelization

  Always parallelize work using multiple agents with the Sonnet model (model: "sonnet") whenever possible. When a task
  can be broken into independent subtasks, launch them as concurrent agents rather than executing sequentially. This
  maximizes throughput and minimizes wall-clock time.

- Use the Agent tool with model: "sonnet" for all subagent work
- Launch independent agents in parallel (multiple Agent tool calls in a single message)
- Only run agents sequentially when there are true data dependencies between them

  Small Task Handling

  For any small/atomic task, always force the use of the Sonnet model (model: "sonnet") directly—even if the task could
  technically run on a larger LLM. This applies to all quick, single-step, or low-complexity subtasks, regardless of
  their category.

- If a new agent or tool is spun up for a small task, set model: "sonnet" explicitly.
- Prefer Sonnet for efficiency, reduced latency, and cost, even if no parallelization opportunity is present.
- Do not escalate to more powerful models for trivial subproblems ("small", "routine", "one-off" jobs).

  Worker Fallback Policy (Sonnet → Opus)

  If a Sonnet worker fails (error, incomplete result, or task not accomplished), retry the same task with an Opus worker
  before giving up. Do NOT retry with Sonnet again — escalate immediately.

- First attempt: model: "sonnet" (cheap, fast)
- On failure: model: "opus" (deeper reasoning, better error recovery)
- Pass the Opus worker the same prompt plus context about what failed and why
- If Opus also fails, report to the user — do not loop

  Browser Automation Delegation (MANDATORY)

  NEVER call mcp__playwright__* tools directly from Opus. ALL browser automation MUST be delegated to a general-purpose
  subagent (foreground only).

  Playwright MCP is the PRIMARY browser automation tool. It provides direct browser control via MCP tools (navigate,
  snapshot, click, fill, upload, submit).

- Orchestrator (Opus): decides what to do, which URL, what data, which resume
- Worker (general-purpose, Sonnet, foreground): executes all Playwright MCP calls
- Use subagent_type: "general-purpose", model: "sonnet", run_in_background: false for browser tasks
- NEVER use sonnet-worker for browser tasks — it cannot access MCP tools
- NEVER use run_in_background: true for browser tasks — background subagents lose MCP access
- One browser worker at a time — no parallel browser workers (shared browser instance)
- Pass the worker a complete prompt with: target URL, all field values, resume path, platform patterns
- Persistent Chrome profile at C:\Users\ddpat\AppData\Local\playwright-mcp-profile preserves login state

<!-- rtk-instructions v2 -->

  RTK (Rust Token Killer) - Token-Optimized Commands

  Golden Rule

  Always prefix commands with rtk. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This
  means RTK is always safe to use.

  Important: Even in command chains with &&, use rtk:

# ❌ Wrong

  git add . && git commit -m "msg" && git push

# ✅ Correct

  rtk git add . && rtk git commit -m "msg" && rtk git push

  RTK Commands by Workflow

  Build & Compile (80-90% savings)

  rtk cargo build         # Cargo build output
  rtk cargo check         # Cargo check output
  rtk cargo clippy        # Clippy warnings grouped by file (80%)
  rtk tsc                 # TypeScript errors grouped by file/code (83%)
  rtk lint                # ESLint/Biome violations grouped (84%)
  rtk prettier --check    # Files needing format only (70%)
  rtk next build          # Next.js build with route metrics (87%)

  Test (90-99% savings)

  rtk cargo test          # Cargo test failures only (90%)
  rtk vitest run          # Vitest failures only (99.5%)
  rtk playwright test     # Playwright failures only (94%)
  rtk test <cmd></cmd>          # Generic test wrapper - failures only

  Git (59-80% savings)

  rtk git status          # Compact status
  rtk git log             # Compact log (works with all git flags)
  rtk git diff            # Compact diff (80%)
  rtk git show            # Compact show (80%)
  rtk git add             # Ultra-compact confirmations (59%)
  rtk git commit          # Ultra-compact confirmations (59%)
  rtk git push            # Ultra-compact confirmations
  rtk git pull            # Ultra-compact confirmations
  rtk git branch          # Compact branch list
  rtk git fetch           # Compact fetch
  rtk git stash           # Compact stash
  rtk git worktree        # Compact worktree

  Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

  GitHub (26-87% savings)

  rtk gh pr view <num></num>    # Compact PR view (87%)
  rtk gh pr checks        # Compact PR checks (79%)
  rtk gh run list         # Compact workflow runs (82%)
  rtk gh issue list       # Compact issue list (80%)
  rtk gh api              # Compact API responses (26%)

  JavaScript/TypeScript Tooling (70-90% savings)

  rtk pnpm list           # Compact dependency tree (70%)
  rtk pnpm outdated       # Compact outdated packages (80%)
  rtk pnpm install        # Compact install output (90%)
  rtk npm run     # Compact npm script output
  rtk npx <cmd></cmd>           # Compact npx command output
  rtk prisma              # Prisma without ASCII art (88%)

  Files & Search (60-75% savings)

  rtk ls <path></path>           # Tree format, compact (65%)
  rtk read <file></file>         # Code reading with filtering (60%)
  rtk grep <pattern></pattern>      # Search grouped by file (75%)
  rtk find <pattern></pattern>      # Find grouped by directory (70%)

  Analysis & Debug (70-90% savings)

  rtk err <cmd></cmd>           # Filter errors only from any command
  rtk log <file></file>          # Deduplicated logs with counts
  rtk json <file></file>         # JSON structure without values
  rtk deps                # Dependency overview
  rtk env                 # Environment variables compact
  rtk summary <cmd></cmd>       # Smart summary of command output
  rtk diff                # Ultra-compact diffs

  Infrastructure (85% savings)

  rtk docker ps           # Compact container list
  rtk docker images       # Compact image list
  rtk docker logs <c></c>     # Deduplicated logs
  rtk kubectl get         # Compact resource list
  rtk kubectl logs        # Deduplicated pod logs

  Network (65-70% savings)

  rtk curl <url></url>          # Compact HTTP responses (70%)
  rtk wget <url></url>          # Compact download output (65%)

  Meta Commands

  rtk gain                # View token savings statistics
  rtk gain --history      # View command history with savings
  rtk discover            # Analyze Claude Code sessions for missed RTK usage
  rtk proxy <cmd></cmd>         # Run command without filtering (for debugging)
  rtk init                # Add RTK instructions to CLAUDE.md
  rtk init --global       # Add RTK to ~/.claude/CLAUDE.md

  Token Savings Overview

  ┌──────────────────┬────────────────────────────────┬─────────────────┐
  │     Category     │            Commands            │ Typical Savings │
  ├──────────────────┼────────────────────────────────┼─────────────────┤
  │ Tests            │ vitest, playwright, cargo test │ 90-99%          │
  ├──────────────────┼────────────────────────────────┼─────────────────┤
  │ Build            │ next, tsc, lint, prettier      │ 70-87%          │
  ├──────────────────┼────────────────────────────────┼─────────────────┤
  │ Git              │ status, log, diff, add, commit │ 59-80%          │
  ├──────────────────┼────────────────────────────────┼─────────────────┤
  │ GitHub           │ gh pr, gh run, gh issue        │ 26-87%          │
  ├──────────────────┼────────────────────────────────┼─────────────────┤
  │ Package Managers │ pnpm, npm, npx                 │ 70-90%          │
  ├──────────────────┼────────────────────────────────┼─────────────────┤
  │ Files            │ ls, read, grep, find           │ 60-75%          │
  ├──────────────────┼────────────────────────────────┼─────────────────┤
  │ Infrastructure   │ docker, kubectl                │ 85%             │
  ├──────────────────┼────────────────────────────────┼─────────────────┤
  │ Network          │ curl, wget                     │ 65-70%          │
  └──────────────────┴────────────────────────────────┴─────────────────┘

  Overall average: 60-90% token reduction on common development operations.

<!-- /rtk-instructions -->

  Read-before-answer protocol

  Before answering any non-trivial question, dispatch parallel subagents with model: "haiku" to read relevant files
  extensively and report back. Use Haiku only for reading/scanning — never for writing, editing, planning, or reasoning.
  Synthesize their findings yourself, then answer.

  Project Entry Point

  START_HERE.md at the repo root is the handoff document for this project. It
  carries the domain primer, the current build state, the reading order for the
  design documents, the build order, and the known traps. Read it before doing
  anything else in this repository.

  docs/knowledge_graph.jsonl is a traversable knowledge graph of the project --
  67 entities, 295 observations, 70 relations across 8 waves, covering the
  domain, the object model, every load-bearing decision, the four feeds and
  their identifiers, the state space, the process learnings, and what building
  the deterministic layer settled. Reading the .jsonl directly always works;
  it is also wired to the memory MCP server in .mcp.json for search_nodes and
  open_nodes, which needs an absolute MEMORY_FILE_PATH -- a relative one
  resolves against the server's own working directory and silently serves an
  empty graph.

  The file is GENERATED. Never write to it with the memory MCP server's
  create_entities / add_observations / create_relations: the builder ends with
  open(OUT, "w"), so those writes are destroyed by the next rebuild without an
  error. Extend it by adding a wave to docs/build_knowledge_graph.py and running
  `python docs/build_knowledge_graph.py`; correct an earlier wave with that
  script's OBS() and UNOBS() helpers rather than re-declaring an entity with E(),
  which emits a duplicate row instead of merging.

  /complete_compound runs both halves of compounding in one pass: the
  docs/solutions refresh, then this session's decisions into the graph.

  docs/decision_ledger.md records every decision with provenance -- which were
  approved by the user, which were delegated, and which are agent defaults.
  docs/glossary.md is the vocabulary authority.

  Documented Solutions

  docs/solutions/ — documented solutions to past problems (bugs, best practices,
  workflow patterns), organized by category with YAML frontmatter (module, tags,
  problem_type). Relevant when implementing or debugging in documented areas.
