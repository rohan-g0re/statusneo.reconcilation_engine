"""Read a built database and write a standalone HTML dashboard of what is in it.

This is a *development* tool for looking at the data, not part of the product. The dashboard
the product ships is `web/`; this is for answering "what does this dataset actually contain"
without writing the same six SELECTs by hand every time.

Three rules it follows, all of them borrowed from the system it is looking at:

  * It opens the database read-only, over a `file:...?mode=ro` URI. A tool that inspects an
    append-only store must not be the thing that writes to it, and the immutability triggers
    would abort anyway -- but relying on a trigger to catch your own bug is not a design.
  * Every figure comes from a SELECT and nothing is computed twice. Where the engine already
    stores a number in cents, it is divided by 100 for display and not recomputed from parts.
  * The output is one self-contained file. `include_plotlyjs=True` inlines the library, so the
    HTML opens with no network and keeps working in a room with bad wifi -- which is the only
    kind of room a demo is ever given in.

Usage:
    python scripts/visualize_db.py                      # demo profile -> data/dumps/
    python scripts/visualize_db.py --profile full
    python scripts/visualize_db.py --db path/to.sqlite --out anywhere.html
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ModuleNotFoundError:  # pragma: no cover - the message is the whole point
    sys.exit(
        "plotly is not installed. It is an optional extra so the base install stays\n"
        "dependency-free:\n\n    uv pip install -e '.[viz]'\n"
    )

REPO = Path(__file__).resolve().parents[1]

# The dashboard's palette. Status colours are the stylesheet's own, so a chart and the screen
# it describes never disagree about what red means.
CRITICAL, WARNING, GOOD, ACCENT, REBATE = "#d03b3b", "#fab219", "#0ca30c", "#5e6ad2", "#4a3aa7"
DISPOSITION_COLOUR = {"EXCEPTION": CRITICAL, "PENDING": WARNING, "CLOSED": GOOD}
INK, MUTED, GRID = "#16161c", "#66677a", "#e4e5ea"


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"no database at {path}. Build one first: POST /api/regenerate?profile=demo")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    return conn.execute(sql).fetchall()


# --- the queries -----------------------------------------------------------
# One per panel. Each reads the LATEST verdict per episode, which is what every queue in the
# product means by "the answer": a verdict row is never updated, so "current" is the newest
# row at or below a cursor, not a flag on the table.

LATEST = """
    WITH latest AS (
      SELECT v.*, ROW_NUMBER() OVER (
               PARTITION BY episode_id ORDER BY cursor_at DESC, verdict_id DESC) AS rn
      FROM verdict v
    )
    SELECT * FROM latest WHERE rn = 1
"""


def money_by_disposition(conn):
    return rows(conn, f"""
        WITH cur AS ({LATEST})
        SELECT episode_disposition AS disposition,
               COUNT(*) AS episodes,
               SUM(ABS(reimbursement_variance_cents) + ABS(rebate_variance_cents)) AS variance_cents,
               SUM(expected_reimbursement_cents + expected_rebate_cents) AS expected_cents,
               SUM(received_reimbursement_cents + received_rebate_cents) AS received_cents
        FROM cur GROUP BY 1 ORDER BY variance_cents DESC
    """)


def top_reasons(conn):
    return rows(conn, f"""
        WITH cur AS ({LATEST})
        SELECT r.reason_code, COUNT(DISTINCT c.episode_id) AS episodes
        FROM cur c JOIN verdict_reason r ON r.verdict_id = c.verdict_id
        GROUP BY 1 ORDER BY episodes DESC LIMIT 15
    """)


def money_by_track(conn):
    return rows(conn, f"""
        WITH cur AS ({LATEST})
        SELECT e.reimbursement_track AS track,
               SUM(ABS(c.reimbursement_variance_cents)) AS reimbursement_cents,
               SUM(ABS(c.rebate_variance_cents)) AS rebate_cents
        FROM cur c JOIN episode e ON e.episode_id = c.episode_id
        GROUP BY 1 ORDER BY 1
    """)


def verdict_mix(conn):
    return rows(conn, f"""
        WITH cur AS ({LATEST})
        SELECT reimbursement_verdict_code AS code, COUNT(*) AS episodes
        FROM cur GROUP BY 1 ORDER BY episodes DESC LIMIT 12
    """)


def arrivals_by_month(conn):
    """When records reached us, by feed. The shape of the ingest, not of the money."""
    return rows(conn, """
        SELECT substr(received_at, 1, 7) AS month, source_system, COUNT(*) AS records
        FROM normalized_record GROUP BY 1, 2 ORDER BY 1, 2
    """)


def park_reasons(conn):
    return rows(conn, """
        SELECT park_reason, COUNT(*) AS records
        FROM parked_record GROUP BY 1 ORDER BY records DESC
    """)


def disposition_over_time(conn):
    """How the book moved as evidence arrived. One point per cursor the engine evaluated."""
    return rows(conn, """
        WITH per_cursor AS (
          SELECT cursor_at, episode_id, episode_disposition,
                 ROW_NUMBER() OVER (PARTITION BY cursor_at, episode_id
                                    ORDER BY verdict_id DESC) AS rn
          FROM verdict
        )
        SELECT cursor_at, episode_disposition AS disposition, COUNT(*) AS episodes
        FROM per_cursor WHERE rn = 1 GROUP BY 1, 2 ORDER BY 1, 2
    """)


def table_sizes(conn):
    names = [r[0] for r in rows(conn, """
        SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)]
    sizes = [(n, conn.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0]) for n in names]
    return [s for s in sizes if s[1] > 0]


# --- the figure ------------------------------------------------------------

def build(conn, profile: str) -> go.Figure:
    fig = make_subplots(
        rows=4, cols=2,
        subplot_titles=(
            "Money at risk, by bucket", "Claims by bucket",
            "Most common reasons", "Money at risk, by billing road",
            "How the book moved as evidence arrived", "Insurance outcomes",
            "Records received, by feed", "Rows per table",
        ),
        specs=[[{"type": "bar"}, {"type": "bar"}],
               [{"type": "bar"}, {"type": "bar"}],
               [{"type": "scatter"}, {"type": "bar"}],
               [{"type": "bar"}, {"type": "bar"}]],
        vertical_spacing=0.09, horizontal_spacing=0.12,
    )

    # 1 + 2 — the three buckets, money first then count. Same order as the product's tiles.
    buckets = money_by_disposition(conn)
    labels = [b["disposition"] for b in buckets]
    colours = [DISPOSITION_COLOUR.get(b["disposition"], ACCENT) for b in buckets]
    fig.add_trace(go.Bar(
        x=labels, y=[b["variance_cents"] / 100 for b in buckets], marker_color=colours,
        text=[f"${b['variance_cents']/100:,.0f}" for b in buckets], textposition="outside",
        hovertemplate="%{x}<br>$%{y:,.2f} at risk<extra></extra>", showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=labels, y=[b["episodes"] for b in buckets], marker_color=colours,
        text=[b["episodes"] for b in buckets], textposition="outside",
        hovertemplate="%{x}<br>%{y} claims<extra></extra>", showlegend=False,
    ), row=1, col=2)

    # 3 — reason codes, horizontal because the labels are long and a rotated label is unreadable.
    reasons = list(reversed(top_reasons(conn)))
    fig.add_trace(go.Bar(
        x=[r["episodes"] for r in reasons],
        y=[r["reason_code"].replace("_", " ").lower() for r in reasons],
        orientation="h", marker_color=ACCENT,
        hovertemplate="%{y}<br>%{x} claims<extra></extra>", showlegend=False,
    ), row=2, col=1)

    # 4 — the two tracks, stacked, because the point is that one claim carries both.
    tracks = money_by_track(conn)
    fig.add_trace(go.Bar(
        x=[t["track"] for t in tracks], y=[(t["reimbursement_cents"] or 0) / 100 for t in tracks],
        name="Insurance", marker_color=ACCENT,
        hovertemplate="%{x} insurance<br>$%{y:,.2f}<extra></extra>",
    ), row=2, col=2)
    fig.add_trace(go.Bar(
        x=[t["track"] for t in tracks], y=[(t["rebate_cents"] or 0) / 100 for t in tracks],
        name="340B rebate", marker_color=REBATE,
        hovertemplate="%{x} rebate<br>$%{y:,.2f}<extra></extra>",
    ), row=2, col=2)

    # 5 — the replay, which is the one chart that could not exist without the cursor.
    series: dict[str, dict[str, int]] = {}
    for row in disposition_over_time(conn):
        series.setdefault(row["disposition"], {})[row["cursor_at"][:10]] = row["episodes"]
    for disposition, points in sorted(series.items()):
        fig.add_trace(go.Scatter(
            x=list(points), y=list(points.values()), mode="lines+markers", name=disposition,
            line=dict(color=DISPOSITION_COLOUR.get(disposition, ACCENT), width=2),
            hovertemplate="%{x}<br>%{y} claims " + disposition + "<extra></extra>",
        ), row=3, col=1)

    # 6 — verdict codes as the engine stores them. Codes, not labels: this is the raw view.
    mix = verdict_mix(conn)
    fig.add_trace(go.Bar(
        x=[m["code"] for m in mix], y=[m["episodes"] for m in mix], marker_color=ACCENT,
        hovertemplate="%{x}<br>%{y} claims<extra></extra>", showlegend=False,
    ), row=3, col=2)

    # 7 — arrivals, stacked by feed, which is what "four feeds on four timetables" looks like.
    by_feed: dict[str, dict[str, int]] = {}
    months: list[str] = []
    for row in arrivals_by_month(conn):
        by_feed.setdefault(row["source_system"], {})[row["month"]] = row["records"]
        if row["month"] not in months:
            months.append(row["month"])
    for feed, points in sorted(by_feed.items()):
        fig.add_trace(go.Bar(
            x=months, y=[points.get(m, 0) for m in months], name=feed,
            hovertemplate="%{x} " + feed + "<br>%{y} records<extra></extra>",
        ), row=4, col=1)

    # 8 — table sizes, so the shape of the store is visible at a glance.
    sizes = list(reversed(table_sizes(conn)))
    fig.add_trace(go.Bar(
        x=[s[1] for s in sizes], y=[s[0] for s in sizes], orientation="h", marker_color=MUTED,
        hovertemplate="%{y}<br>%{x:,} rows<extra></extra>", showlegend=False,
    ), row=4, col=2)

    episodes = conn.execute("SELECT count(*) FROM episode").fetchone()[0]
    at_risk = sum(b["variance_cents"] for b in buckets) / 100
    fig.update_layout(
        title=dict(
            text=f"<b>{profile} profile</b> — {episodes:,} claims, "
                 f"${at_risk:,.2f} at risk<br>"
                 f"<span style='font-size:12px;color:{MUTED}'>"
                 f"Every figure read from the database. Nothing here recomputes a verdict."
                 f"</span>",
            x=0.5, xanchor="center",
        ),
        height=1750, barmode="stack", bargap=0.25,
        template="plotly_white", font=dict(family="Inter, system-ui, sans-serif", color=INK),
        paper_bgcolor="#f7f8fa", plot_bgcolor="#ffffff",
        legend=dict(orientation="h", yanchor="bottom", y=-0.045, xanchor="center", x=0.5),
        margin=dict(t=120, b=90),
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    # The money is on the value axis, which is y for the vertical bars and x for the
    # horizontal ones. Putting the prefix on the wrong axis of a bar chart labels the
    # categories instead of the numbers, and the first panel read "$EXCEPTION".
    fig.update_yaxes(tickprefix="$", row=1, col=1)
    fig.update_yaxes(tickprefix="$", row=2, col=2)
    fig.update_xaxes(tickangle=-40, row=3, col=2)
    for annotation in fig.layout.annotations:
        annotation.font.size = 13
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="demo", choices=("demo", "full"))
    parser.add_argument("--db", type=Path, help="override the database path")
    parser.add_argument("--out", type=Path, help="override the output path")
    args = parser.parse_args()

    db_path = args.db or REPO / "data" / f"recon_{args.profile}.sqlite"
    out_path = args.out or REPO / "data" / "dumps" / f"recon_{args.profile}_dashboard.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        figure = build(conn, args.profile)
    finally:
        conn.close()

    # include_plotlyjs=True inlines the library. The file gets to ~4 MB and in exchange it
    # opens on a laptop with no network, which is the trade every demo wants.
    figure.write_html(str(out_path), include_plotlyjs=True, full_html=True)
    print(f"{out_path}  {out_path.stat().st_size / 1_048_576:.1f} MB")


if __name__ == "__main__":
    main()
