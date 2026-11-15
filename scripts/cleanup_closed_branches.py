#!/usr/bin/env python3
"""Remove unchanged branches abandoned by their newest PR; dry-run by default.

Run from a checkout with GitHub CLI authentication and git push credentials:
  python scripts/cleanup_closed_branches.py --repo OWNER/REPO
  python scripts/cleanup_closed_branches.py --repo OWNER/REPO --apply

REST ref deletion has no expected-SHA precondition. Use a git deletion with an
explicit force-with-lease instead, after refreshing PRs and checking both live
refs. This closes the concurrent branch-push race, but GitHub offers no atomic
transaction spanning PR state and git refs: a PR can reopen after the final
read. A branch changed away and back to the same SHA is also indistinguishable.
No retry on a failed deletion; another run must evaluate everything again.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import re
import subprocess
import sys
from urllib.parse import urlencode


AGE = timedelta(days=30)


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def api(path, *, pages=False):
    args = ['gh', 'api', path]
    if pages:
        args += ['--paginate', '--slurp']
    data = json.loads(run(*args))
    return [item for page in data for item in page] if pages else data


def candidate(prs, repo, branch, default_branch, now):
    """Newest means creation order, never last updated/closed order."""
    if branch in {'main', default_branch}:
        return None
    matching = [p for p in prs if p['head']['ref'] == branch
                and (p['head'].get('repo') or {}).get('full_name', '').lower() == repo.lower()]
    if not matching or any(p['state'] == 'open' for p in matching):
        return None
    newest = max(matching, key=lambda p: (p['created_at'], p['number']))
    if newest['state'] != 'closed' or newest['merged_at'] or not newest['closed_at']:
        return None
    closed = datetime.fromisoformat(newest['closed_at'].replace('Z', '+00:00'))
    if closed >= now - AGE:
        return None
    if not re.fullmatch(r'[0-9a-f]{40}', newest['head']['sha']):
        return None
    return newest


class GitHub:
    def __init__(self, repo):
        self.repo = repo
        self.remote = f'https://github.com/{repo}.git'

    def default_branch(self):
        return api(f'repos/{self.repo}')['default_branch']

    def branches(self):
        return [b['name'] for b in api(f'repos/{self.repo}/branches?per_page=100', pages=True)]

    def prs(self, branch):
        query = urlencode({'state': 'all', 'head': f'{self.repo.split("/")[0]}:{branch}',
                           'sort': 'created', 'direction': 'desc', 'per_page': 100})
        return api(f'repos/{self.repo}/pulls?{query}', pages=True)

    def retained(self, branch, pr):
        refs = [f'refs/heads/{branch}', f'refs/pull/{pr["number"]}/head']
        lines = run('git', 'ls-remote', '--refs', self.remote, *refs).splitlines()
        found = dict(line.split('\t', 1)[::-1] for line in lines)
        return all(found.get(ref) == pr['head']['sha'] for ref in refs)

    def delete(self, branch, sha):
        ref = f'refs/heads/{branch}'
        # The remote is explicit: never delete from an unrelated origin checkout.
        # Explicit expected SHA is essential; remote-tracking refs are not a lease.
        run('git', 'push', '--porcelain', f'--force-with-lease={ref}:{sha}',
            self.remote, f':{ref}')


def cleanup(github, *, apply=False, clock=lambda: datetime.now(timezone.utc)):
    default = github.default_branch()
    for branch in github.branches():
        pr = candidate(github.prs(branch), github.repo, branch, default, clock())
        if not pr or not github.retained(branch, pr):
            continue
        label = f'branch={branch!r} PR=#{pr["number"]} sha={pr["head"]["sha"]}'
        if not apply:
            print(f'DRY-RUN would delete {label}')
            continue
        # Repeat every eligibility check, including default-branch protection,
        # after discovery. Ref reads follow this and immediately precede the push.
        fresh = candidate(github.prs(branch), github.repo, branch,
                          github.default_branch(), clock())
        if not fresh or (fresh['number'], fresh['head']['sha']) != (pr['number'], pr['head']['sha']):
            print(f'SKIP changed eligibility {label}')
            continue
        if not github.retained(branch, fresh):
            print(f'SKIP changed/missing refs {label}')
            continue
        print(f'DELETING {label}', flush=True)
        github.delete(branch, fresh['head']['sha'])
        print(f'DELETED {label}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='GitHub OWNER/REPO')
    parser.add_argument('--apply', action='store_true', help='Delete eligible branches (default: dry-run)')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo):
        parser.error('--repo must be OWNER/REPO')
    try:
        cleanup(GitHub(args.repo), apply=args.apply)
    except subprocess.CalledProcessError as error:
        print(error.stderr or str(error), file=sys.stderr)
        raise SystemExit(error.returncode) from error


if __name__ == '__main__':
    main()
