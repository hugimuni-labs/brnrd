"""Fixture-fed repro for the WAKE tab's owner/authority chips + inspect-in-place.

Run directly (``python -m brr.operator_console.repro.wake_tab_repro``) against
a throwaway ``tmp_path``-style directory — never against a real ``.brr/``. It
builds one run with a realistic ``wake-manifest.json`` (three authorities, a
mounted block, a synthesized block, an absent block, and a file-backed block
whose source file carries a fake credential), drives the real
``OperatorConsole`` app with Textual's own test harness, expands a few
Collapsibles the way an operator would, and writes an SVG screenshot plus a
plain-text transcript of what each pane rendered.

This is the TUI analogue of the frontend's Playwright repro pattern
(``src/frontend/repro/drive-observed-model.mjs``): a fixture, the real
component, a screenshot — adapted because the surface this task actually
touches is a local Textual console, not a browser page. See
``.tmp/reports/the-wake-tab-that-opens.md`` for why.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from brr import presence
from brr.operator_console.tui import (
    _wake_block_detail,
    _wake_block_title,
    _wake_topology_table,
)

_WAKE_MANIFEST = {
    "schema_version": "1",
    "run_id": "run-repro",
    "blocks": [
        {
            "name": "boot-kernel",
            "label": "Boot kernel (action-first score)",
            "owner": "daemon-live",
            "authority": "runtime",
            "present": True,
            "sources": [{"synthesized": True}],
            "bytes_kept": 512,
            "bytes_cut": None,
            "budget_bytes": None,
            "trim_kind": None,
            "freshness": None,
        },
        {
            "name": "identity-core",
            "label": "Resident Identity Core",
            "owner": "product",
            "authority": "identity",
            "present": True,
            "sources": [{"path": "__IDENTITY_PATH__", "store": "product-prompt"}],
            "bytes_kept": None,  # filled in below once the file is written
            "bytes_cut": None,
            "budget_bytes": None,
            "trim_kind": None,
            "freshness": "2026-07-12",
        },
        {
            "name": "portal-verb-grammar",
            "label": "Portal verb grammar",
            "owner": "product",
            "authority": "contract",
            "present": True,
            "sources": [{"path": "__PORTALS_PATH__", "store": "product-prompt"}],
            "bytes_kept": 41,
            "bytes_cut": None,
            "budget_bytes": None,
            "trim_kind": None,
            "freshness": None,
        },
        {
            "name": "dominion-digest",
            "label": "Dominion digest",
            "owner": "resident",
            "authority": "memory",
            "present": False,
            "sources": [{"path": "/home/.brr/dominion/playbook.md", "store": "dominion"}],
            "bytes_kept": None,
            "bytes_cut": None,
            "budget_bytes": None,
            "trim_kind": None,
            "freshness": None,
        },
    ],
}


def _write_fixture(repo: Path) -> None:
    brr = repo / ".brr"
    run_dir = brr / "runs" / "run-repro"
    run_dir.mkdir(parents=True)

    identity_path = run_dir / "identity-core-source.md"
    identity_path.write_text(
        "You are the resident.\n"
        "export GITHUB_TOKEN=ghp_1234567890abcdefEXTRA\n",
        encoding="utf-8",
    )
    manifest = json.loads(json.dumps(_WAKE_MANIFEST))
    manifest["blocks"][1]["sources"][0]["path"] = str(identity_path)
    manifest["blocks"][1]["bytes_kept"] = identity_path.stat().st_size
    manifest["blocks"][2]["sources"][0]["path"] = str(run_dir / "portals-source.md")

    (run_dir / "portals-source.md").write_text(
        "gate: <name>  respawn: true  spawn: true\n", encoding="utf-8"
    )

    (run_dir / "run.md").write_text(
        "---\nevent_id: evt-repro\nstatus: running\n---\n", encoding="utf-8"
    )
    prompt_text = "the assembled wake prompt body — kept for the RAW PROMPT.MD section"
    (run_dir / "prompt.md").write_text(prompt_text, encoding="utf-8")
    (run_dir / "boundaries.jsonl").write_text("", encoding="utf-8")
    (run_dir / "wake-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "prompt-mounted.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-repro",
                "blocks": {"portal-verb-grammar": "gate: <name>  (curated slice, exact bytes)"},
            }
        ),
        encoding="utf-8",
    )

    presence.register(
        brr,
        kind="daemon",
        label="repro",
        run_id="run-repro",
        repo_label="hugimuni-labs/brnrd",
        stream="cli:repro",
        pid=os.getpid(),
        runner_name="claude",
        runner_shell="claude",
        runner_core="sonnet",
    )


async def _drive(repo: Path, out_dir: Path) -> None:
    from brr.operator_console.tui import build_console_app
    from textual.widgets import Collapsible

    OperatorConsole = build_console_app()
    app = OperatorConsole(repo)
    async with app.run_test(size=(120, 40)) as pilot:
        if app._poll_timer is not None:
            app._poll_timer.pause()
        app._poll()
        await pilot.pause(0.1)

        app._tabs().active = "wake"
        await pilot.pause(0.05)

        run = app.snapshot.selected if app.snapshot else None
        # Render from the run's *actual* loaded manifest (bytes_kept/path
        # filled in by `_write_fixture`), not the module-level template —
        # the two differ once the fixture stamps real file sizes in.
        manifest_blocks = run.wake_manifest if run is not None else _WAKE_MANIFEST["blocks"]
        transcript_lines = [
            "=== TOPOLOGY TABLE ===",
            _wake_topology_table(manifest_blocks),
            "",
        ]

        wake_nodes = [n for n in app.query(Collapsible) if hasattr(n, "wake_block_name")]
        for node in wake_nodes:
            if node.wake_block_name != "__raw__":
                node.collapsed = False
        await pilot.pause(0.05)

        for node in wake_nodes:
            transcript_lines.append(f"--- title: {node.title} ---")
        transcript_lines.append("")
        if run is not None:
            for block in run.wake_manifest:
                transcript_lines.append(f"=== DETAIL: {block.get('name')} ===")
                transcript_lines.append(
                    _wake_block_detail(
                        block, repo_root=app.repo_root, mounted_blocks=run.mounted_blocks
                    )
                )
                transcript_lines.append("")

        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = out_dir / "wake-tab-console.svg"
        app.save_screenshot(str(screenshot_path))
        (out_dir / "wake-tab-console.txt").write_text(
            "\n".join(transcript_lines), encoding="utf-8"
        )
        print(f"screenshot: {screenshot_path}")
        print(f"transcript: {out_dir / 'wake-tab-console.txt'}")
        assert "GITHUB_TOKEN=<redacted>" in "\n".join(transcript_lines) or (
            "ghp_1234567890abcdefEXTRA" not in "\n".join(transcript_lines)
        ), "secret leaked into the inspectable block text"
        print("redaction check: pass — no raw secret in the rendered transcript")


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp()) / "out"
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _write_fixture(repo)
        asyncio.run(_drive(repo, out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
