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
import os
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path

ESPORTS = Path(__file__).resolve().parent.parent.parent   # folder holding engineer/, sessions/, archive/
SESSIONS = ESPORTS / "sessions"
ARCHIVE = ESPORTS / "archive"
# Where copies go. On the server: the PC's drive shared at /mnt/fbi-esports. On the PC the
# project already lives on that drive, so there is nothing to copy to (None).
DRIVE = None if os.name == "nt" else Path(os.environ.get("F1_ESPORTS_DRIVE", "/mnt/fbi-esports"))
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
    if drive is not None and not Path(drive).is_dir():
        raise SystemExit(f"The external drive is not available at {drive}.")
    date = min(p.name[:8] for p in paths)
    Path(archive).mkdir(parents=True, exist_ok=True)
    out = Path(archive) / f"sessions_{date}_{name}.tar.gz"
    if out.exists():
        raise SystemExit(f"{out} already exists; choose another --name. Nothing was changed.")

    log(f"Compressing {len(paths)} folder(s), {sum(folder_size(p) for p in paths) / 1e9:.2f} GB, into {out} ...")
    with tarfile.open(out, "w:gz", compresslevel=6) as tar:   # 6: much faster than 9, similar size
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
    if drive is None:
        log("The project already lives on the external drive, so no extra copy is needed. "
            "Nothing was deleted.")
        return {"archive": str(out), "drive_archive": None, "sha256": digest, "files": len(got),
                "drive_folders": []}

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


def copy_file_checked(src, dest_dir, log=print):
    """Copy one file into dest_dir and check it by sha256. Never overwrites."""
    src = Path(src)
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    dest = Path(dest_dir) / src.name
    if dest.exists():
        if sha256(dest) == sha256(src):
            log(f"{dest} already there and identical (sha256); skipped.")
            return str(dest)
        raise SystemExit(f"{dest} already exists and differs; not overwriting.")
    log(f"Copying {src} -> {dest} ...")
    shutil.copyfile(src, dest)
    if sha256(dest) != sha256(src):
        raise SystemExit(f"Copy check FAILED for {dest}.")
    log(f"{dest.name}: copy matches (sha256).")
    return str(dest)


def copy_existing(archives=(), folders=(), capture=False, sessions=SESSIONS, archive=ARCHIVE,
                  drive=DRIVE, capture_dir=ESPORTS / "capture", log=print):
    """Copy already-made archives, session folders and/or the capture code to the drive,
    checking each copy. Never deletes, never overwrites a different file."""
    if not Path(drive).is_dir():
        raise SystemExit(f"The external drive is not available at {drive}.")
    done = []
    for name in archives:
        done.append(copy_file_checked(Path(archive) / name, Path(drive) / "archive", log))
    for f in folders:
        src = Path(sessions) / f
        if not src.is_dir():
            raise SystemExit(f"Not a session folder: {src}")
        if recently_written(src):
            raise SystemExit(f"{src.name} was written in the last {ACTIVE_WITHIN_S} s; stop capture first.")
        for file in sorted(p for p in src.rglob("*") if p.is_file()):
            done.append(copy_file_checked(file, Path(drive) / "sessions" / f / file.parent.relative_to(src), log))
    if capture:
        for file in sorted(Path(capture_dir).glob("*.py")):
            done.append(copy_file_checked(file, Path(drive) / "capture", log))
    log(f"Copied and checked {len(done)} file(s). Nothing was deleted.")
    return done


def _under(base, rel):
    p = (Path(base) / rel).resolve()
    if Path(base).resolve() not in p.parents and p != Path(base).resolve():
        raise SystemExit(f"{rel} is outside {base}; refusing.")
    return p


def copy_paths(rels, base=ESPORTS, drive=DRIVE, log=print):
    """Copy files or folders given relative to the project folder to the same relative place
    on the drive, checking each file by sha256. Never deletes, never overwrites a different file."""
    if drive is None or not Path(drive).is_dir():
        raise SystemExit(f"The external drive is not available at {drive}.")
    done = []
    for rel in rels:
        src = _under(base, rel)
        files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
        for f in files:
            dest_dir = Path(drive) / f.parent.relative_to(Path(base).resolve())
            done.append(copy_file_checked(f, dest_dir, log))
    log(f"Copied and checked {len(done)} file(s). Nothing was deleted.")
    return done


def copy_as(pairs, base=ESPORTS, drive=DRIVE, log=print):
    """Copy single files to a NEW name on the drive ("logs/capture.log=logs/capture.server.log"),
    so a different file already on the drive is kept and nothing is overwritten. Checked by sha256."""
    if drive is None or not Path(drive).is_dir():
        raise SystemExit(f"The external drive is not available at {drive}.")
    done = []
    for pair in pairs:
        rel, _, new = pair.partition("=")
        src = _under(base, rel)
        if not src.is_file() or not new:
            raise SystemExit(f"Use SRC=DEST with SRC a file in the project folder: {pair}")
        dest = _under(drive, new)
        if dest.exists():
            if sha256(dest) == sha256(src):
                log(f"{dest} already there and identical; skipped.")
                done.append(str(dest))
                continue
            raise SystemExit(f"{dest} already exists and differs; not overwriting.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        if sha256(dest) != sha256(src):
            raise SystemExit(f"Copy check FAILED for {dest}.")
        log(f"{src} -> {dest}: copy matches (sha256).")
        done.append(str(dest))
    return done


def verify(rels, base=ESPORTS, drive=DRIVE, log=print, ignore=()):
    """Before deleting from the server: every file under each folder must exist on the drive at
    the same relative path with the same size. Files matching an --ignore pattern (relative path,
    shell-style) are listed but not required. Returns True only if everything else matches."""
    import fnmatch
    if drive is None or not Path(drive).is_dir():
        raise SystemExit(f"The external drive is not available at {drive}.")
    ok, n, missing = True, 0, []
    for rel in rels:
        src = _under(base, rel)
        files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
        for f in files:
            relpath = f.relative_to(Path(base).resolve()).as_posix()
            if any(fnmatch.fnmatch(relpath, pat) for pat in ignore):
                log(f"ignored as agreed: {relpath}")
                continue
            n += 1
            d = Path(drive) / f.relative_to(Path(base).resolve())
            if not d.is_file() or d.stat().st_size != f.stat().st_size:
                ok = False
                missing.append(str(f))
    for m in missing[:20]:
        log(f"NOT on the drive (or different size): {m}")
    log(f"Verified {n - len(missing)} of {n} file(s) on the drive." + ("" if ok else " NOT SAFE TO DELETE."))
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("save")
    s.add_argument("--name", required=True)
    s.add_argument("--copy-folders", action="store_true")
    s.add_argument("folders", nargs="+")
    c = sub.add_parser("copy", help="copy existing archives / folders / capture code to the drive")
    c.add_argument("--archive", action="append", default=[], help="archive file name in ~/esports/archive")
    c.add_argument("--capture", action="store_true", help="also copy ~/esports/capture/*.py")
    c.add_argument("--path", action="append", default=[],
                   help="file or folder relative to the project folder (e.g. logs)")
    c.add_argument("--as", dest="as_pairs", action="append", default=[],
                   help="copy one file to a new name on the drive: SRC=DEST (relative paths)")
    c.add_argument("folders", nargs="*")
    v = sub.add_parser("verify", help="check every file under these project paths is on the drive")
    v.add_argument("--ignore", action="append", default=[], help="relative path pattern not required")
    v.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        return cmd_list()
    if a.cmd == "verify":
        return 0 if verify(a.paths, ignore=a.ignore) else 1
    if a.cmd == "copy":
        if a.archive or a.folders or a.capture:
            copy_existing(a.archive, a.folders, a.capture)
        if a.path:
            copy_paths(a.path)
        if a.as_pairs:
            copy_as(a.as_pairs)
        return 0
    save(a.folders, a.name, copy_folders=a.copy_folders)
    return 0


if __name__ == "__main__":
    sys.exit(main())
