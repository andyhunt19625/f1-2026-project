"""Save step: compress session folders, check the archive reads back, copy it (and
optionally the folders) to the external drive and check the copies match. NEVER deletes.

  python3 -m race_engineer.archive list
  python3 -m race_engineer.archive save --name madrid_practice FOLDER [FOLDER ...] [--copy-folders]

FOLDER is a folder name inside ~/esports/sessions (for example
20261112T162447Z_4eff2f9a2a46768d). The archive is written to ~/esports/archive as
sessions_<YYYYMMDD>_<name>.tar.gz, matching sessions_20261029_jeddah_race.tar.gz, and
copied to <drive>/archive/. With --copy-folders the folders are also copied to
<drive>/sessions/. Removing anything from the server is a separate, explicit step.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path

ESPORTS = Path.home() / "esports"
SESSIONS = ESPORTS / "sessions"
ARCHIVE = ESPORTS / "archive"
DRIVE = Path("/mnt/fbi-esports")
ACTIVE_WITHIN_S = 20
NAME_RE = re.compile(r"^[a-z0-9_]+$")


def sha256(path, chunk=4 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


def folder_size(p):
    return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file())


def recently_written(p, now=None):
    now = now or time.time()
    return any(now - f.stat().st_mtime < ACTIVE_WITHIN_S for f in Path(p).rglob("*") if f.is_file())


def meta(p):
    try:
        return json.loads((Path(p) / "session_meta.json").read_text())
    except (OSError, ValueError):
        return {}


def cmd_list(sessions=SESSIONS):
    rows = []
    for p in sorted(Path(sessions).iterdir()):
        if p.is_dir():
            m = meta(p)
            rows.append(f"{p.name} | trackId {m.get('trackId')} | sessionType {m.get('sessionType')} | "
                        f"{folder_size(p) / 1e6:.1f} MB")
    print("\n".join(rows) or "(no session folders)")
    return 0


def save(folders, name, sessions=SESSIONS, archive=ARCHIVE, drive=DRIVE, copy_folders=False, log=print):
    if not NAME_RE.match(name):
        raise SystemExit("--name must be lower-case letters, digits and underscores, e.g. madrid_practice")
    paths = [Path(sessions) / f for f in folders]
    for p in paths:
        if not p.is_dir():
            raise SystemExit(f"Not a session folder: {p}")
        if recently_written(p):
            raise SystemExit(f"{p.name} was written in the last {ACTIVE_WITHIN_S} s; it may still be "
                             f"recording. Stop capture first.")
    if not Path(drive).is_dir():
        raise SystemExit(f"The external drive is not available at {drive}.")
    date = min(p.name[:8] for p in paths)
    Path(archive).mkdir(parents=True, exist_ok=True)
    out = Path(archive) / f"sessions_{date}_{name}.tar.gz"
    if out.exists():
        raise SystemExit(f"{out} already exists; choose another --name. Nothing was changed.")

    log(f"Compressing {len(paths)} folder(s), {sum(folder_size(p) for p in paths) / 1e9:.2f} GB, into {out} ...")
    with tarfile.open(out, "w:gz") as tar:
        for p in paths:
            tar.add(p, arcname=p.name)
    expected = sorted(f"{p.name}/{f.relative_to(p)}" for p in paths for f in p.rglob("*") if f.is_file())
    with tarfile.open(out, "r:gz") as tar:          # reads the whole archive back
        got = sorted(m.name for m in tar.getmembers() if m.isfile())
    if got != expected:
        raise SystemExit(f"Archive check FAILED: {len(got)} files read back, {len(expected)} expected. "
                         f"Kept {out} for inspection; nothing else was changed.")
    digest = sha256(out)
    log(f"Archive reads back: {len(got)} files, {out.stat().st_size / 1e9:.2f} GB, sha256 {digest[:16]}...")

    dest_dir = Path(drive) / "archive"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / out.name
    if dest.exists():
        raise SystemExit(f"{dest} already exists on the drive; not overwriting.")
    log(f"Copying archive to {dest} ...")
    shutil.copyfile(out, dest)
    if sha256(dest) != digest:
        raise SystemExit(f"Copy check FAILED: {dest} does not match {out}. The server copy is kept.")
    log("Archive copy on the drive matches (sha256).")

    copied = []
    if copy_folders:
        sdir = Path(drive) / "sessions"
        sdir.mkdir(exist_ok=True)
        for p in paths:
            target = sdir / p.name
            if target.exists():
                raise SystemExit(f"{target} already exists on the drive; not overwriting.")
            log(f"Copying folder {p.name} ...")
            shutil.copytree(p, target)
            for f in p.rglob("*"):
                if f.is_file():
                    t = target / f.relative_to(p)
                    if not t.is_file() or t.stat().st_size != f.stat().st_size:
                        raise SystemExit(f"Folder copy check FAILED at {t}.")
            copied.append(str(target))
        log(f"Folder copies on the drive match by file list and size: {len(copied)}.")
    log("Nothing was deleted. Removing the folders from the server is a separate step.")
    return {"archive": str(out), "drive_archive": str(dest), "sha256": digest, "files": len(got),
            "drive_folders": copied}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("save")
    s.add_argument("--name", required=True)
    s.add_argument("--copy-folders", action="store_true")
    s.add_argument("folders", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        return cmd_list()
    save(a.folders, a.name, copy_folders=a.copy_folders)
    return 0


if __name__ == "__main__":
    sys.exit(main())
