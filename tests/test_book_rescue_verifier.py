"""A1.4 isolated restore proof tests, not Ollama/Vision/DOCWORK reruns."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]/"scripts"

def load(filename,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/filename)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

backup=load("backup-book-job.py","book_backup")
external=load("archive-book-external.py","book_external")
verify=load("verify-book-rescue.py","book_verify")

from test_book_external_archive import fixture


class VerifySnapshot(unittest.TestCase):
    def test_sqlite_and_explicit_external_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=root/"original.sqlite3"
            fixture(db)
            original=root/"rescue.zip"
            backup.create_archive(db,original,"j123")
            info=verify.check_archive(original)
            self.assertTrue(info["verified"])
            self.assertEqual(info["saved_chapters"],1)
            self.assertEqual(info["external_files_verified"],0)
            self.assertFalse(info["publication_snapshot_complete"])
            image=root/"chapter.svg"
            image.write_text("<svg/>",encoding="utf-8")
            combined=root/"with-files.zip"
            external.create_extended_archive(original,combined,["art="+str(image)])
            info=verify.check_archive(combined)
            self.assertEqual(info["external_files_verified"],1)
            self.assertEqual(info["generated_words"],1200)
            self.assertTrue(db.exists())

    def test_detects_tampered_job_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=root/"source.sqlite3"
            fixture(db)
            original=root/"rescue.zip"
            backup.create_archive(db,original,"j123")
            corrupt=root/"modified.zip"
            with zipfile.ZipFile(original) as src,zipfile.ZipFile(corrupt,"w") as dst:
                for name in src.namelist():
                    raw=src.read(name)
                    if name=="report/job-rescue-report.json":
                        report=json.loads(raw)
                        report["chapters"][0]["text_sha256"]="0"*64
                        raw=json.dumps(report).encode()
                    dst.writestr(name,raw)
            with self.assertRaises(ValueError):
                verify.check_archive(corrupt)

    def test_rejects_duplicate_members_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            for names in [
                ["database/achewiw-consistent.sqlite3"]*2,
                ["../private/file.txt"],
                ["external/../escape.txt"],
            ]:
                path=Path(tmp)/("bad"+str(len(names))+".zip")
                with zipfile.ZipFile(path,"w") as z:
                    for idx,name in enumerate(names):
                        z.writestr(name,str(idx))
                with zipfile.ZipFile(path) as z:
                    with self.assertRaises(ValueError):
                        verify.safe_names(z)

if __name__=="__main__":
    unittest.main()
