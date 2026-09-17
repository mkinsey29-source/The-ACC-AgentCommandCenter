"""Bounded review copies of Git-visible files, verified again before acceptance."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess


def digest(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def inventory(project, git=True):
    project = Path(project)
    if git:
        result = subprocess.run(['git', '-C', str(project), 'ls-files', '-z', '--cached',
                                 '--others', '--exclude-standard'], capture_output=True, timeout=20)
        if result.returncode:
            raise ValueError('Managed review requires a Git worktree.')
        paths = sorted(set(result.stdout.decode('utf-8').split('\0')) - {''})
    else:
        paths = sorted(p.relative_to(project).as_posix() for p in project.rglob('*') if not p.is_dir() or p.is_symlink())
    if len(paths) > 10000:
        raise ValueError('Review exceeds 10,000 files; narrow the managed project.')
    entries, size = {}, 0
    for name in paths:
        path = project / name
        if path.is_symlink() or not path.resolve().is_relative_to(project.resolve()):
            raise ValueError('Review snapshots do not support symbolic links: ' + name)
        if not path.exists():
            continue  # tracked deletion
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError('Review snapshots require regular files (no submodules): ' + name)
        size += path.stat().st_size
        if size > 100 * 1024 * 1024:
            raise ValueError('Review exceeds 100 MiB; narrow the managed project.')
        entries[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'executable': bool(mode & stat.S_IXUSR)}
    return entries


def freeze(project, destination):
    destination = Path(destination)
    entries = inventory(project)
    files = destination / 'files'
    files.mkdir(parents=True)
    for name in entries:
        target = files / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(project) / name, target)
    if inventory(files, git=False) != entries or inventory(project) != entries:
        raise ValueError('Files changed while capturing review snapshot. Retry after edits stop.')
    snap = {'id': 'sha256:' + digest(entries), 'path': str(files), 'entries': entries}
    (destination / 'manifest.json').write_text(json.dumps(snap, indent=2), encoding='utf-8')
    diff = subprocess.run(['git', '-C', str(project), 'diff', '--binary', 'HEAD'],
                          capture_output=True, timeout=20)
    (destination / 'changes.patch').write_bytes(diff.stdout)
    return snap


def verify(snapshot, project):
    if inventory(snapshot['path'], git=False) != snapshot['entries']:
        raise ValueError('Review snapshot changed; acceptance blocked.')
    if inventory(project) != snapshot['entries']:
        raise ValueError('Project changed after snapshot; run implementation and review again.')
