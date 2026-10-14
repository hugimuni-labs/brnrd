"""Edit a private copy; only a config accepted by the loader replaces the file."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import ConfigError, config_path, load_config, seed_config


def edit_config(home: Path) -> int:
    path = config_path(home)
    seed_config(home)
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    try:
        command = shlex.split(editor)
    except ValueError as exc:
        print(f"config edit: invalid editor command: {exc}", file=sys.stderr)
        return 1
    if not command:
        print("config edit: empty editor command", file=sys.stderr)
        return 1
    # Use the same loader and layout as the live home. Keeping the copy on
    # this filesystem also makes the final replace atomic.
    with tempfile.TemporaryDirectory(prefix=".config-edit-", dir=path.parent) as directory:
        draft_home = Path(directory)
        draft = config_path(draft_home)
        draft.parent.mkdir()
        shutil.copy2(path, draft)
        error_block = ""
        while True:
            before = draft.read_text(encoding="utf-8")
            try:
                result = subprocess.run([*command, str(draft)], check=False)
                edited = draft.read_text(encoding="utf-8")
            except OSError as exc:
                print(f"config edit: {exc}", file=sys.stderr)
                return 1
            if result.returncode:
                print(f"config edit: editor exited {result.returncode}; kept {path}", file=sys.stderr)
                return 1
            if error_block and edited == before:
                print(f"config edit: abandoned; kept {path}", file=sys.stderr)
                return 1
            # Remove only the diagnostic we inserted, never the user's
            # comments. It is not part of the config being validated.
            if error_block and edited.startswith(error_block):
                edited = edited[len(error_block):]
                draft.write_text(edited, encoding="utf-8")
            try:
                load_config(draft_home)
            except ConfigError as exc:
                error = str(exc).replace(str(draft), str(path))
                print(error, file=sys.stderr)
                error_block = "# Config error (fix below, or quit unchanged to abandon):\n"
                error_block += "".join(f"# {line}\n" for line in error.splitlines()) + "\n"
                draft.write_text(error_block + edited, encoding="utf-8")
                continue
            os.replace(draft, path)
            print(f"config: {path}")
            return 0
