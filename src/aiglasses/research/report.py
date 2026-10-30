import csv
from datetime import datetime, timezone
from pathlib import Path

from aiglasses.research.capture import FROM_GLASSES, TO_GLASSES, Analysis, reactions


def write_reports(
    analysis: Analysis, segments: list[int], labels: list[str], out_dir: Path
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    first = analysis.rows[0].ts if analysis.rows else 0.0
    csv_path, md_path = out_dir / "analysis.csv", out_dir / "analysis.md"
    header = ["segment", "action", "utc", "t_plus_s", "direction", "channel", "op", "target"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([*header, "hex", "decoded", "frames"])
        for row, seg in zip(analysis.rows, segments, strict=True):
            utc = datetime.fromtimestamp(row.ts, timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            action = labels[seg] if labels else ""
            writer.writerow(
                [
                    seg,
                    action,
                    utc,
                    f"{row.ts - first:.3f}",
                    row.direction,
                    row.channel,
                    row.op,
                    row.target,
                    row.hex,
                    row.decoded,
                    row.count,
                ]
            )
    lines = [
        "# Capture analysis",
        "",
        "| Seg | Action | +s | Dir | Op | Target | Hex / decoded |",
        "|---|---|---|---|---|---|---|",
    ]
    for row, seg in zip(analysis.rows, segments, strict=True):
        arrow = "->" if row.direction == TO_GLASSES else "<-"
        action = labels[seg] if labels else ""
        lines.append(
            f"| {seg} | {action} | {row.ts - first:.2f} | {arrow} | {row.op} | {row.target} "
            f"| {row.decoded or row.hex} |"
        )
    lines += [
        "",
        f"Connections: {analysis.connections}",
        f"L2CAP by PSM: {analysis.psm_counts}",
        "",
    ]
    lines += ["## Writes after each glasses event", ""]
    for event, writes in reactions(analysis.rows):
        reply = ", ".join(f"{w.target}={w.hex}" for w in writes) or "(no writes)"
        lines.append(f"- {event.decoded} ({FROM_GLASSES}): {reply}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path
