import os
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from race_engineer import archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sessions, self.arch, self.drive = root / "sessions", root / "archive", root / "drive"
        for d in (self.sessions, self.drive):
            d.mkdir()
        self.folder = self.sessions / "20261112T162447Z_4eff2f9a2a46768d"
        self.folder.mkdir()
        (self.folder / "raw.bin").write_bytes(os.urandom(5000))
        (self.folder / "parsed.jsonl").write_text('{"a": 1}\n' * 100)
        (self.folder / "session_meta.json").write_text('{"trackId": 42, "sessionType": 4}')
        old = time.time() - 600
        for f in self.folder.iterdir():
            os.utime(f, (old, old))

    def tearDown(self):
        self.tmp.cleanup()

    def run_save(self, **kw):
        return archive.save([self.folder.name], kw.pop("name", "madrid_practice"), self.sessions,
                            self.arch, self.drive, log=lambda *_: None, **kw)

    def test_save_archives_checks_and_copies_without_deleting(self):
        res = self.run_save(copy_folders=True)
        out = self.arch / "sessions_20261112_madrid_practice.tar.gz"
        self.assertEqual(res["archive"], str(out))
        self.assertTrue(out.is_file())
        self.assertEqual(res["files"], 3)
        with tarfile.open(out) as t:
            self.assertIn(f"{self.folder.name}/raw.bin", t.getnames())
        drive_copy = self.drive / "archive" / out.name
        self.assertEqual(archive.sha256(drive_copy), res["sha256"])
        self.assertTrue((self.drive / "sessions" / self.folder.name / "raw.bin").is_file())
        self.assertTrue(self.folder.is_dir())            # nothing deleted
        self.assertTrue((self.folder / "raw.bin").is_file())

    def test_refuses_to_overwrite(self):
        self.run_save()
        with self.assertRaises(SystemExit):
            self.run_save()

    def test_refuses_folder_still_recording(self):
        (self.folder / "raw.bin").write_bytes(b"new")     # just written
        with self.assertRaises(SystemExit) as cm:
            self.run_save()
        self.assertIn("may still be recording", str(cm.exception))
        self.assertFalse(self.arch.exists() and any(self.arch.iterdir()))

    def test_refuses_when_drive_missing(self):
        with self.assertRaises(SystemExit) as cm:
            archive.save([self.folder.name], "x", self.sessions, self.arch, Path(self.tmp.name) / "nodrive",
                         log=lambda *_: None)
        self.assertIn("not available", str(cm.exception))

    def test_name_must_be_simple(self):
        with self.assertRaises(SystemExit):
            self.run_save(name="Madrid Practice!")


if __name__ == "__main__":
    unittest.main()
