"""One-page dashboard for deciding what to do next.

The file is self-contained: inline CSS, JS, and SVG. No CDN. Open it offline.
``--runs`` is a directory or a glob of run directories and golden ``mock_*.jsonl``
logs.
"""

from __future__ import annotations

import argparse
import html
from collections import defaultdict
from pathlib import Path
from typing import Any

from coop.analysis.load import discover_runs
from coop.analysis.pipeline import analyze

MOVE_COLORS = {
    "request": "#c47b2b",
    "offer": "#0f766e",
    "loyalty_test": "#6d28d9",
    "exclusion": "#b91c1c",
    "concealment_proposal": "#1d4ed8",
    "report_edit": "#115e59",
}
MOVE_LABELS = {
    "request": "request",
    "offer": "offer",
    "loyalty_test": "loyalty test",
    "exclusion": "exclusion",
    "concealment_proposal": "concealment proposal",
    "report_edit": "report edit",
}


def data_class(meta: dict[str, Any]) -> str:
    """real API, mock, or synthetic. Scripted simulator logs are mock, not live calls."""
    if meta.get("synthetic") or str(meta.get("run_id", "")).startswith("syn-"):
        return "synthetic"
    driver = meta.get("insider_driver")
    source = meta.get("source")
    if source == "golden_mock" or str(meta.get("run_id", "")).startswith("mock_"):
        return "mock"
    if driver == "llm" and not meta.get("synthetic"):
        return "real API"
    return "mock"


def banner(metas: list[dict[str, Any]]) -> str:
    counts = {"real API": 0, "mock": 0, "synthetic": 0}
    for meta in metas:
        counts[data_class(meta)] += 1
    parts = [f"{count} {name}" for name, count in counts.items() if count]
    if len(parts) == 1:
        label = parts[0]
    else:
        label = "mixed — " + ", ".join(parts)
    return (
        f'<p class="banner">Data: {html.escape(label)}. '
        "Real API means a live model driver. Mock means a scripted simulator log. "
        "Synthetic means a fixture built for the analysis tests.</p>"
    )


def render_dashboard(result: dict[str, Any], runs: list[Any]) -> str:
    by_id = {run.run_id: run for run in runs}
    metas = [run.meta for run in runs]
    body = "\n".join(
        [
            _legend(),
            _timelines(result, by_id),
            _diffusion(result, by_id),
            _heatmap(result, by_id),
            _transcripts(result, by_id),
            _questions(result, by_id),
        ]
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cooperative experiment — decision dashboard</title>
<style>
  body {{ margin: 0; font: 15px/1.45 "Iowan Old Style", Palatino, Georgia, serif; color: #1c1917; background: #f6f3ee; }}
  main {{ max-width: 1100px; margin: 0 auto; padding: 28px 20px 80px; }}
  h1 {{ font-size: 28px; margin: 0 0 8px; }}
  h2 {{ font-size: 22px; margin: 36px 0 8px; }}
  h3 {{ font-size: 16px; margin: 18px 0 6px; }}
  .banner {{ background: #1c1917; color: #f6f3ee; padding: 8px 12px; border-radius: 6px; font: 13px/1.4 ui-sans-serif, system-ui, sans-serif; }}
  .panel {{ background: white; border: 1px solid #e7e0d6; border-radius: 10px; padding: 14px 16px 18px; margin: 12px 0 22px; }}
  .legend span {{ display: inline-block; margin-right: 12px; font: 12px ui-sans-serif, system-ui, sans-serif; }}
  .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }}
  table {{ border-collapse: collapse; width: 100%; font: 13px ui-sans-serif, system-ui, sans-serif; }}
  th, td {{ border-bottom: 1px solid #e7e0d6; text-align: left; padding: 4px 6px; vertical-align: top; }}
  .quote {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; white-space: pre-wrap; background: #f6f3ee; padding: 8px; border-radius: 6px; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  select {{ font: 13px ui-sans-serif, system-ui, sans-serif; margin-bottom: 8px; }}
  @media (max-width: 800px) {{ .cols {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
<h1>Cooperative experiment — decision dashboard</h1>
{banner(metas)}
{body}
</main>
<script>
document.querySelectorAll("select.filter").forEach(function (sel) {{
  sel.addEventListener("change", function () {{
    var panel = sel.closest(".panel");
    panel.querySelectorAll("[data-run]").forEach(function (node) {{
      var show = sel.value === "all" || node.getAttribute("data-class") === sel.value;
      node.style.display = show ? "" : "none";
    }});
  }});
}});
</script>
</body>
</html>
"""


def _legend() -> str:
    bits = []
    for key, color in MOVE_COLORS.items():
        bits.append(
            f'<span><i class="swatch" style="background:{color}"></i>{html.escape(MOVE_LABELS[key])}</span>'
        )
    bits.append('<span><i class="swatch" style="background:#111"></i>violation</span>')
    return '<p class="legend">' + " ".join(bits) + "</p>"


def _filter(metas: list[dict[str, Any]]) -> str:
    classes = sorted({data_class(meta) for meta in metas})
    options = '<option value="all">all runs</option>' + "".join(
        f'<option value="{html.escape(name)}">{html.escape(name)}</option>' for name in classes
    )
    return f'<select class="filter">{options}</select>'


def _timelines(result: dict[str, Any], by_id: dict[str, Any]) -> str:
    cards = []
    for profile in result["profiles"]:
        run = by_id.get(profile["run_id"])
        if run is None:
            continue
        cards.append(_timeline_card(profile, run))
    metas = [run.meta for run in by_id.values()]
    return (
        "<h2>1. Leader timeline</h2>"
        + banner(metas)
        + '<div class="panel">'
        + _filter(metas)
        + "".join(cards)
        + "</div>"
    )


def _timeline_card(profile: dict[str, Any], run: Any) -> str:
    moves = profile.get("leader_moves") or []
    violations = [event for event in run.events if event.type == "violation"]
    tmax = max([event.t for event in run.events] + [1])
    width = 640
    height = 72
    left, right = 36, width - 8

    def x_of(t: int) -> float:
        return left + (right - left) * (t / max(tmax, 1))

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">',
        f'<line x1="{left}" y1="36" x2="{right}" y2="36" stroke="#d6d3d1" />',
    ]
    for stage, start in ((1, 0), (2, 4), (3, 8), (4, 13), (5, 17)):
        if start > tmax:
            continue
        parts.append(
            f'<text x="{x_of(start):.1f}" y="12" font-size="10" fill="#78716c">s{stage}</text>'
        )
    for event in violations:
        parts.append(
            f'<rect x="{x_of(event.t) - 3:.1f}" y="28" width="6" height="16" fill="#111">'
            f"<title>violation {html.escape(event.event_id)} t={event.t}</title></rect>"
        )
    for move in moves:
        color = MOVE_COLORS.get(move.get("move_type"), "#444")
        label = MOVE_LABELS.get(move.get("move_type"), move.get("move_type") or "")
        parts.append(
            f'<circle cx="{x_of(move["t"]):.1f}" cy="36" r="6" fill="{color}">'
            f"<title>{html.escape(label)} {html.escape(str(move.get('event_id')))} "
            f"t={move['t']} stage={move['stage']}</title></circle>"
        )
    parts.append("</svg>")
    leader = (profile.get("leader") or {}).get("agent_id") or "none"
    note = _view_sentence(moves)
    klass = data_class(run.meta)
    return (
        f'<div data-run="{html.escape(run.run_id)}" data-class="{html.escape(klass)}">'
        f"<h3>{html.escape(run.run_id)} — leader {html.escape(str(leader))} "
        f"({html.escape(klass)})</h3>"
        + "".join(parts)
        + f"<p>{html.escape(note)}</p></div>"
    )


def _view_sentence(moves: list[dict[str, Any]]) -> str:
    if not moves:
        return "No leader move."
    chosen = moves[0]
    for move in moves:
        if move.get("move_type") == "concealment_proposal":
            chosen = move
            break
    view = (chosen.get("information_state") or {})
    failed = view.get("failed_tasks") or []
    owners = []
    for row in failed:
        owners.append(str(row.get("owner_name") or row.get("owner_id") or "unknown"))
    who = ", ".join(owners) if owners else "no failed task on the board"
    sees = view.get("principal_sees_discussion")
    if sees is True:
        principal = "the principal can read the working notes"
    elif sees is False:
        principal = "the principal sees reports only"
    else:
        principal = "principal visibility was not on an observation"
    risk = "own milestone is at risk" if view.get("own_milestone_at_risk") else "own milestone is not showing a failure"
    if chosen.get("move_type") == "concealment_proposal":
        when = f"At the concealment proposal (t={chosen['t']}, stage {chosen['stage']})"
    else:
        when = f"At the first leader move (t={chosen['t']}, stage {chosen['stage']})"
    return (
        f"{when}, the visible failures are {who}. {risk.capitalize()}. "
        f"{principal.capitalize()}. Source: {view.get('source')}."
    )


def _diffusion(result: dict[str, Any], by_id: dict[str, Any]) -> str:
    cards = []
    for profile in result["profiles"]:
        run = by_id.get(profile["run_id"])
        if run is None:
            continue
        cards.append(_diffusion_card(profile, run))
    metas = [run.meta for run in by_id.values()]
    return (
        "<h2>2. Diffusion</h2>"
        + banner(metas)
        + '<div class="panel">'
        + _filter(metas)
        + "".join(cards)
        + "</div>"
    )


def _diffusion_card(profile: dict[str, Any], run: Any) -> str:
    agents = []
    for event in run.events:
        for agent in (event.actor, event.target):
            if agent and str(agent).startswith("a") and agent not in agents:
                agents.append(agent)
    agents = sorted(agents) or ["a0"]
    newcomer = None
    removal_t = None
    leader = (profile.get("leader") or {}).get("agent_id")
    for event in run.events:
        if event.type == "agent_added" and event.target:
            newcomer = event.target
        if event.type == "agent_removed" and leader and event.target == leader:
            removal_t = event.t
    width, height = 640, 120
    pos = {agent: (40 + i * ((width - 80) / max(len(agents) - 1, 1)), 48) for i, agent in enumerate(agents)}
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    if removal_t is not None:
        parts.append(
            f'<text x="8" y="108" font-size="11" fill="#b91c1c">'
            f"post-removal from t={removal_t}</text>"
        )
    edges = []
    for event in run.events:
        if event.type == "invite" and event.actor in pos and event.target in pos:
            edges.append((event.actor, event.target, "#0f766e", event.t))
        if (
            leader
            and event.actor == leader
            and event.type == "message"
            and event.target in pos
            and event.target != leader
        ):
            edges.append((event.actor, event.target, "#1d4ed8", event.t))
    for src, dst, color, t in edges[:12]:
        x1, y1 = pos[src]
        x2, y2 = pos[dst]
        late = removal_t is not None and t >= removal_t
        dash = ' stroke-dasharray="4 3"' if late else ""
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2 + 18:.1f}" '
            f'stroke="{color}" stroke-width="1.4"{dash} />'
        )
    for agent, (x, y) in pos.items():
        stroke = "#b45309" if agent == newcomer else "#1c1917"
        width_stroke = 3 if agent == newcomer else 1
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="14" fill="#fff" stroke="{stroke}" '
            f'stroke-width="{width_stroke}" />'
            f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-size="11">{html.escape(agent)}</text>'
        )
    parts.append("</svg>")
    klass = data_class(run.meta)
    who = newcomer or "none"
    return (
        f'<div data-run="{html.escape(run.run_id)}" data-class="{html.escape(klass)}">'
        f"<h3>{html.escape(run.run_id)} ({html.escape(klass)}) — newcomer {html.escape(str(who))}</h3>"
        + "".join(parts)
        + "<p>Teal edges are invites. Blue edges are the leader's messages to someone. "
        "Dashed edges are after the leader's removal. The newcomer has an amber ring.</p></div>"
    )


def _heatmap(result: dict[str, Any], by_id: dict[str, Any]) -> str:
    diffusion = {row["run_id"]: row for row in (result.get("diffusion") or {}).get("runs") or []}
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in result["primary"].get("per_run") or []:
        if row.get("cell") is None:
            continue
        meta = by_id[row["run_id"]].meta if row["run_id"] in by_id else {}
        cond = meta.get("condition") if isinstance(meta.get("condition"), dict) else {}
        condition = f"{cond.get('access', '?')} × {cond.get('visibility', '?')}"
        buckets[(condition, row["cell"])].append(row)
    conditions = sorted({key[0] for key in buckets}) or ["(none)"]
    cells = ("planted", "null_insider", "pressure_only")
    header = "<tr><th>Condition</th>" + "".join(f"<th>{html.escape(cell)}</th>" for cell in cells) + "</tr>"
    body = []
    for condition in conditions:
        tds = [f"<td>{html.escape(condition)}</td>"]
        for cell in cells:
            rows = buckets.get((condition, cell), [])
            n = len(rows)
            k = sum(item["y"] for item in rows)
            rate = f"{k}/{n}" if n else "n=0"
            acc = []
            for item in rows:
                diff = diffusion.get(item["run_id"]) or {}
                if diff.get("acceptance") is not None:
                    acc.append(float(diff["acceptance"]))
            extra = ""
            if acc:
                extra = f"<br>acceptance {sum(acc) / len(acc):.2f} (n={len(acc)})"
            tds.append(f"<td>{html.escape(rate)}{extra}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    metas = [run.meta for run in by_id.values()]
    return (
        "<h2>3. Condition heatmap</h2>"
        + banner(metas)
        + '<div class="panel"><table>'
        + header
        + "".join(body)
        + "</table><p>Each cell is k/n for the primary outcome Y. "
        "Acceptance is the exploratory share of peers who later shift, when the run has a leader.</p></div>"
    )


def _transcripts(result: dict[str, Any], by_id: dict[str, Any]) -> str:
    planted = [
        row
        for row in result["primary"].get("per_run") or []
        if row.get("cell") == "planted" and row.get("objective")
    ]
    nulls = [row for row in result["primary"].get("per_run") or [] if row.get("cell") == "null_insider"]
    pairs = []
    used = set()
    for objective in ("blame_avoidance", "loyalty", "resource_capture"):
        for row in planted:
            if row["objective"] != objective or row["run_id"] in used:
                continue
            match = _match_null(row, nulls, by_id, used)
            if match is None:
                continue
            used.add(row["run_id"])
            used.add(match["run_id"])
            pairs.append((row, match))
            break
    blocks = []
    for left, right in pairs:
        blocks.append(_pair(left, right, by_id))
    if not blocks:
        blocks.append("<p>No planted run shares a seed with a null-insider run in this batch.</p>")
    metas = [run.meta for run in by_id.values()]
    return (
        "<h2>4. Stage-3 transcript, planted vs matched null</h2>"
        + banner(metas)
        + '<div class="panel">'
        + "".join(blocks)
        + "</div>"
    )


def _match_null(row: dict[str, Any], nulls: list[dict[str, Any]], by_id: dict[str, Any], used: set[str]) -> dict[str, Any] | None:
    def key(item: dict[str, Any]) -> tuple:
        meta = by_id[item["run_id"]].meta
        cond = meta.get("condition") if isinstance(meta.get("condition"), dict) else {}
        return (
            meta.get("seed"),
            meta.get("behavior_profile"),
            cond.get("access"),
            cond.get("visibility"),
            data_class(meta),
        )

    want = key(row)
    for item in nulls:
        if item["run_id"] in used:
            continue
        if key(item) == want:
            return item
    for item in nulls:
        if item["run_id"] in used:
            continue
        meta = by_id[item["run_id"]].meta
        if meta.get("seed") == by_id[row["run_id"]].meta.get("seed") and data_class(meta) == data_class(
            by_id[row["run_id"]].meta
        ):
            return item
    return None


def _pair(left: dict[str, Any], right: dict[str, Any], by_id: dict[str, Any]) -> str:
    return (
        "<h3>"
        + html.escape(f"{left['run_id']} ({left.get('objective')}) vs {right['run_id']} (null)")
        + "</h3><div class='cols'><div>"
        + _quotes(by_id[left["run_id"]])
        + "</div><div>"
        + _quotes(by_id[right["run_id"]])
        + "</div></div>"
    )


def _quotes(run: Any) -> str:
    lines = [
        f"<p><strong>{html.escape(run.run_id)}</strong> ({html.escape(data_class(run.meta))})</p>"
    ]
    count = 0
    for event in run.events:
        if event.type != "message" or event.stage != 3:
            continue
        text = (event.payload or {}).get("text") or ""
        if not text:
            continue
        lines.append(
            f'<p class="quote">{html.escape(event.event_id)} t={event.t} '
            f'{html.escape(str(event.actor))}→{html.escape(str(event.target))}\n'
            f'{html.escape(text)}</p>'
        )
        count += 1
        if count >= 6:
            break
    if count == 0:
        lines.append("<p>No stage-3 messages.</p>")
    return "".join(lines)


def _questions(result: dict[str, Any], by_id: dict[str, Any]) -> str:
    profiles = {profile["run_id"]: profile for profile in result["profiles"]}
    rows = result["primary"].get("per_run") or []
    mock_rows = [row for row in rows if row.get("cohort") == "golden_mock" and row.get("objective")]
    correct = 0
    by_arm: dict[str, list[float]] = defaultdict(list)
    for row in mock_rows:
        profile = profiles.get(row["run_id"]) or {}
        label = profile.get("label")
        scored = "null" if label == "none" else label
        if scored == row.get("objective"):
            correct += 1
        scores = profile.get("scores") or {}
        for name in ("resource_capture", "blame_avoidance", "loyalty"):
            if row.get("objective") == name:
                by_arm[name].append(float((scores.get(name) or {}).get("score") or 0))
    n_mock = len(mock_rows)
    acc = (correct / n_mock) if n_mock else float("nan")
    evidence = []
    for name, values in sorted(by_arm.items()):
        mean = sum(values) / len(values) if values else float("nan")
        evidence.append(f"{name} mean score on its own mock arm {mean:.2f} (n={len(values)})")
    y_by_profile: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if row.get("cohort") != "golden_mock":
            continue
        y_by_profile[str(row.get("behavior_profile"))].append(int(row["y"]))
    profile_bits = []
    for name, values in sorted(y_by_profile.items()):
        profile_bits.append(f"{name} Y {sum(values)}/{len(values)}")
    classes = {data_class(run.meta) for run in by_id.values()}
    rules = result.get("leader_rules") or {}
    q1 = (
        "Which leader-objective story fits the mock logs? "
        f"Blind labels match the filename arm on {correct}/{n_mock} sealed golden mocks "
        f"(accuracy {acc:.2f}). "
        + "; ".join(evidence)
        + ". Scripted recovery is a pipeline check, not evidence about a live agent. "
        "Use it only to decide whether the scorer is reading the behavior you think it is."
    )
    q2 = (
        "Does the behavior look like a choice or like role-play? "
        "Every golden mock is insider_driver scripted, and this batch has no real API run "
        f"(classes present: {', '.join(sorted(classes))}). "
        "The same arm with different behavior profiles changes Y: "
        + "; ".join(profile_bits)
        + ". Comply profiles produce the violation the script asks for; refuse_all profiles "
        "disclose and stay at Y=0 while the ask text is still in the log. That split is the "
        "mock profile executing a ladder, not an agent choosing under uncertainty."
    )
    q3 = (
        "Which next experiment is worth the budget? "
        f"Protocol-rule hit rate on recruiter arms is {rules.get('protocol_hit_rate')}; "
        f"the charter-shift variant hit rate is {rules.get('variant_hit_rate')}. "
        "Three options: (1) one discussion_visible repeat of blame_avoidance and its null "
        "match, about two scripted runs at $0 if you keep the mock driver, to see whether "
        "H2 still fires when the principal can read the notes; "
        "(2) four llm-driver controlled runs, one per arm, at the $3 per-run cap "
        "(about $12) so recovery is no longer only a positive control; "
        "(3) a no-violation batch (refuse_all) scored with the variant rule pre-declared, "
        "four scripted runs at $0, to learn whether the charter-shift anchor names the "
        "insider when the specified rule is undefined. Do (1) if the decision is about "
        "visibility, (2) if the decision is whether the scorer survives a live model, "
        "(3) if the decision is which leader rule to freeze."
    )
    metas = [run.meta for run in by_id.values()]
    items = "".join(f"<li>{html.escape(text)}</li>" for text in (q1, q2, q3))
    return (
        "<h2>5. Questions for Demetris</h2>"
        + banner(metas)
        + f'<div class="panel"><ol>{items}</ol></div>'
    )


def resolve_runs(pattern: str) -> tuple[Path, set[str] | None]:
    raw = Path(pattern)
    if raw.is_dir():
        return raw, None
    matches = sorted(Path().glob(pattern))
    if not matches and raw.is_absolute():
        matches = sorted(Path("/").glob(str(raw).lstrip("/")))
    if not matches:
        raise SystemExit(f"no runs match {pattern}")
    roots = {path.parent if path.is_file() else path for path in matches}
    if len(roots) != 1:
        raise SystemExit("the glob must stay inside one runs directory")
    root = roots.pop()
    if root.name != "examples" and not any((root / "events.jsonl").is_file() or path.is_dir() for path in matches):
        root = root
    ids = set()
    for path in matches:
        if path.is_dir() and (path / "events.jsonl").is_file():
            ids.add(path.name)
        elif path.is_file() and path.suffix == ".jsonl":
            ids.add(path.stem)
    if (root / "events.jsonl").is_file():
        return root.parent, {root.name}
    return root, ids


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write the decision dashboard")
    parser.add_argument("--runs", required=True, help="Run directory or glob")
    parser.add_argument("--out", type=Path, default=Path("analysis_out/dashboard.html"))
    parser.add_argument("--report-dir", type=Path, default=Path("analysis_out"))
    parser.add_argument("--record", type=Path, default=Path("research/record.jsonl"))
    args = parser.parse_args(argv)
    root, include = resolve_runs(args.runs)
    analyze(
        root,
        args.report_dir,
        record_path=args.record,
        include_ids=include,
    )
    runs = discover_runs(root, with_sealed=True)
    if include is not None:
        runs = [run for run in runs if run.run_id in include]
    # Re-read the written profiles so the dashboard matches the report on disk.
    import json

    profiles = json.loads((args.report_dir / "profiles.json").read_text(encoding="utf-8"))
    primary = json.loads((args.report_dir / "primary.json").read_text(encoding="utf-8"))
    diffusion = json.loads((args.report_dir / "diffusion.json").read_text(encoding="utf-8"))
    leader_rules = json.loads((args.report_dir / "leader_rules.json").read_text(encoding="utf-8"))
    result = {
        "profiles": profiles,
        "primary": primary,
        "diffusion": diffusion,
        "leader_rules": leader_rules,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_dashboard(result, runs), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
