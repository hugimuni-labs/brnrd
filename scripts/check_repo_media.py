"""Refuse new media and growing legacy media; see docs/repository-media.md."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

BINARY_LIMIT = 16 * 1024
ALLOWLIST = '.github/media-allowlist.json'
MEDIA = frozenset('''.png .jpg .jpeg .gif .webp .avif .heic .heif .bmp .tif .tiff
.ico .icns .svg .psd .ai .eps .pdf .mp4 .mov .webm .mkv .avi .m4v .mpeg .mpg
.ogv .wav .mp3 .ogg .flac .aac .m4a .woff .woff2 .ttf .otf .eot'''.split())


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE)


def tree(repo: Path, ref: str) -> dict[str, tuple[str, int]]:
    entries = {}
    for entry in git(repo, 'ls-tree', '-rlz', ref).split(b'\0'):
        if not entry:
            continue
        info, path = entry.split(b'\t', 1)
        _mode, kind, oid, size = info.split()
        if kind == b'blob':
            entries[os.fsdecode(path)] = (oid.decode(), int(size))
    return entries


def binary(data: bytes) -> bool:
    if b'\0' in data:
        return True
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return True
    return False


def violations(repo: Path, base: str, head: str) -> list[str]:
    before, after = tree(repo, base), tree(repo, head)
    allowed = {}
    if ALLOWLIST in after:
        allowed = json.loads(git(repo, 'cat-file', 'blob', after[ALLOWLIST][0]))
    if not isinstance(allowed, dict):
        raise ValueError('media allow list must be an object')
    for path, rule in allowed.items():
        if (not isinstance(rule, dict) or type(rule.get('max_bytes')) is not int
                or rule['max_bytes'] <= 0 or not isinstance(rule.get('reason'), str)
                or not rule['reason'].strip()):
            raise ValueError(f'invalid allow list entry: {path!r} (need positive max_bytes and reason)')
    bad = []
    for path, (oid, size) in after.items():
        old_oid, old_size = before.get(path, ('', -1))
        if oid == old_oid or size <= old_size:
            continue
        media = Path(path).suffix.lower() in MEDIA
        if not media and size > BINARY_LIMIT:
            media = binary(git(repo, 'cat-file', 'blob', oid))
        if media and size > allowed.get(path, {}).get('max_bytes', -1):
            bad.append(f'{path!r}: {size} bytes (base: {max(old_size, 0)})')
    return bad


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default=os.environ.get('MEDIA_BASE', 'origin/main'))
    parser.add_argument('--head', default=os.environ.get('MEDIA_HEAD', 'HEAD'))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    try:
        base = git(repo, 'merge-base', args.base, args.head).decode().strip()
        bad = violations(repo, base, args.head)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f'Media check could not compare revisions: {exc}', file=sys.stderr)
        return 1
    if bad:
        print('Media belongs in https://github.com/hugimuni-labs/animations.\n'
              'New media and growing legacy media are refused:\n' + '\n'.join(bad)
              + '\nRare code assets need an exact-path entry with a byte cap and reason in '
              + ALLOWLIST + '; see docs/repository-media.md.', file=sys.stderr)
        return 1
    print('Media check passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
