"""Activate a separately prepared checkout interpreter, retaining the old prefix.

Invoked by the public installer through the candidate's exact Python with -I.
Board/source checks happen before the shim or the installed service is changed.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import entry, service
from .spabuild import SpaBuild

ORIGINS = {
    'https://github.com/hugimuni-labs/brnrd',
    'https://github.com/hugimuni-labs/brnrd.git',
    'git@github.com:hugimuni-labs/brnrd.git',
}


def git(source: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()


def managed_source(source: Path) -> bool:
    """A dedicated installer prefix importing the upstream checkout."""
    try:
        return (entry.prefix().parent.resolve() == (entry.root() / 'envs').resolve()
                and (source / '.git').exists() and git(source, 'remote', 'get-url', 'origin') in ORIGINS)
    except (OSError, subprocess.SubprocessError):
        return False


def prepare(source: Path) -> str:
    import brr
    if Path(brr.__file__).resolve() != source / 'src' / 'brr' / '__init__.py':
        raise RuntimeError('candidate interpreter does not import the requested checkout')
    if not managed_source(source):
        raise RuntimeError('candidate must use an installer-owned environment and an upstream checkout')
    head = git(source, 'rev-parse', 'HEAD')
    spa = SpaBuild(source / 'src' / 'frontend', print)
    wanted = spa.wanted()
    if not wanted or not spa.upstream:
        raise RuntimeError('checkout has no upstream main ancestor; not activated')
    if spa.built() != wanted:
        spa._build(wanted)
    if not (spa.build / '200.html').is_file():
        print('board: unavailable; installation continues (retry command printed above)')
    if git(source, 'rev-parse', 'HEAD') != head:
        raise RuntimeError('checkout HEAD moved during preparation; rerun the installer')
    print(f'source: {source}\nHEAD: {head}\nboard: {spa.build} (tree {spa.built()})')
    return head


def snapshot(path: Path) -> tuple[bytes, int] | None:
    return (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None


def restore(path: Path, saved: tuple[bytes, int] | None) -> None:
    if saved is None:
        path.unlink(missing_ok=True)
    else:
        from .home import atomic_write
        atomic_write(path, saved[0])
        path.chmod(saved[1])


def activate(source: Path) -> None:
    head = prepare(source)
    system = service._platform(None)
    unit = (service.linux.unit_path(service.UNIT) if system == 'Linux'
            else service.macos.plist_path(home=Path.home(), label=service.LABEL))
    # Discover the old home before switching current. An unreadable unit is a
    # refusal, never permission to silently strand its service on the old code.
    installed = service._installed_home(None, system) if unit.exists() else None
    if unit.exists() and installed is None:
        raise RuntimeError(f'cannot read the installed home from {unit}; repair it before migration')
    link = entry.current()
    if link.exists() and not link.is_symlink():
        raise RuntimeError(f'{link} is not a symlink; not activated')
    previous = os.readlink(link) if link.is_symlink() else None
    paths = [entry.shim(), entry.root() / 'launcher.py', entry.root() / 'launcher-source', unit]
    saved = {path: snapshot(path) for path in paths}
    try:
        if installed is not None:
            code = service.main(['install', '--home', str(installed)])
            if code:
                raise RuntimeError(f'candidate service install failed ({code})')
            if service.main(['status']):
                raise RuntimeError('candidate service status failed')
        else:
            entry.write_shim()
            entry.point(entry.prefix())
        if link.resolve() != entry.prefix().resolve() or not entry.sound():
            raise RuntimeError('current/shim readback does not match candidate interpreter')
        if git(source, 'rev-parse', 'HEAD') != head:
            raise RuntimeError('source HEAD changed during activation')
        # Run the public shim, rather than just inspecting its contents. -I is
        # used for the identity probe so PYTHONPATH cannot fake the readback.
        probe = subprocess.check_output(
            [str(link / 'bin' / 'python'), '-I', '-c',
             'import brr,json,sys; print(json.dumps([sys.prefix,brr.__file__]))'], text=True)
        prefix, package = json.loads(probe)
        if Path(prefix).resolve() != entry.prefix().resolve() or Path(package).resolve() != source / 'src/brr/__init__.py':
            raise RuntimeError('current interpreter/source readback mismatch')
        subprocess.run([str(entry.shim()), '--version'], check=True, env={
            key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}})
    except (Exception, SystemExit):
        for path, data in saved.items():
            restore(path, data)
        if previous is None:
            link.unlink(missing_ok=True)
        else:
            entry.point(Path(previous) if Path(previous).is_absolute() else link.parent / previous)
        if installed is not None:
            # The old unit and current link are restored before its restart.
            # No old interpreter prefix is ever removed, even on success.
            try:
                if system == 'Linux':
                    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
                if service.main(['restart']):
                    raise RuntimeError('restored service did not restart')
            except (Exception, SystemExit) as exc:
                print(f'rollback restart failed: {exc}; run {entry.shim()} loom restart', file=sys.stderr)
        raise
    before = (Path(previous) if Path(previous).is_absolute() else link.parent / previous) if previous else None
    print(f'current: {link} -> {entry.prefix()}\nprevious prefix retained: {before or "none"}')
    for path in prune([entry.prefix(), before]):
        print(f'removed older installer environment: {path}')
    print(f'service: moved from {before or "no interpreter"} to {entry.prefix()}' if installed is not None
          else 'service: none installed; brnrd setup installs it')


def prune(keep: list[Path | None]) -> list[Path]:
    """Remove installer-made environments other than current and the one before it.

    Only ``envs/install-*`` directories the installer made are candidates; a
    prefix outside ``envs/`` (a uv tool, a checkout's .venv) is never touched.
    """
    envs = entry.root() / 'envs'
    kept = {path.resolve() for path in keep if path is not None}
    removed = []
    for path in sorted(envs.glob('install-*')) if envs.is_dir() else []:
        if path.is_symlink() or not path.is_dir() or path.resolve() in kept:
            continue
        try:
            shutil.rmtree(path)
            removed.append(path)
        except OSError as exc:
            print(f'could not remove older installer environment {path}: {exc}', file=sys.stderr)
    return removed


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit('usage: python -I -m brr.loom.runtime.install_checkout CHECKOUT')
    try:
        entry.root().mkdir(parents=True, exist_ok=True)
        with (entry.root() / 'install.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            activate(Path(args[0]).resolve())
    except (Exception, SystemExit) as exc:
        print(f'checkout install: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
