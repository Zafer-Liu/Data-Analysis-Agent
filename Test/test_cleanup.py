#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for infrastructure/cleanup.py — TTL-based runtime pruning.

Uses tempdir + manual mtime backdating so we don't have to wait for real time.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from infrastructure.cleanup import _sweep_one, run_cleanup


class TestSweepOne(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="baa_cleanup_test_"))

    def tearDown(self):
        # Walk and remove anything left behind
        if self.tmp.exists():
            for p in sorted(self.tmp.rglob("*"), key=lambda p: -len(p.parts)):
                try:
                    p.unlink() if p.is_file() else p.rmdir()
                except OSError:
                    pass
            try:
                self.tmp.rmdir()
            except OSError:
                pass

    def _touch(self, relpath: str, age_days: float, content: bytes = b"x"):
        path = self.tmp / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        mtime = time.time() - (age_days * 86400)
        os.utime(path, (mtime, mtime))
        return path

    def test_old_files_removed(self):
        old = self._touch("old.xlsx",  age_days=40)
        new = self._touch("fresh.xlsx", age_days=5)
        n, freed = _sweep_one(self.tmp, max_age_days=30, label="test")
        self.assertEqual(n, 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertGreater(freed, 0)

    def test_missing_dir_is_noop(self):
        # Sweeping a non-existent directory must not raise
        n, freed = _sweep_one(self.tmp / "ghost", max_age_days=30, label="ghost")
        self.assertEqual((n, freed), (0, 0))

    def test_nested_subdir(self):
        old = self._touch("session1/data.csv", age_days=100)
        new = self._touch("session1/keep.csv", age_days=1)
        n, _ = _sweep_one(self.tmp, max_age_days=90, label="nested")
        self.assertEqual(n, 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_empty_dirs_pruned(self):
        # A subdir whose only file got cleaned should itself be removed
        old = self._touch("orphan/file.txt", age_days=200)
        n, _ = _sweep_one(self.tmp, max_age_days=30, label="orphan-test")
        self.assertEqual(n, 1)
        self.assertFalse((self.tmp / "orphan").exists(),
                         "empty parent directory should be pruned")

    def test_nonempty_dirs_kept(self):
        # A subdir that still has fresh files must NOT be removed
        self._touch("alive/a.txt", age_days=5)
        self._touch("alive/b.txt", age_days=200)
        _sweep_one(self.tmp, max_age_days=30, label="alive-test")
        self.assertTrue((self.tmp / "alive").exists())
        self.assertTrue((self.tmp / "alive/a.txt").exists())
        self.assertFalse((self.tmp / "alive/b.txt").exists())

    def test_size_reporting(self):
        self._touch("big.bin", age_days=50, content=b"y" * 1024)
        n, freed = _sweep_one(self.tmp, max_age_days=30, label="size")
        self.assertEqual(n, 1)
        self.assertGreaterEqual(freed, 1024)


class TestRunCleanup(unittest.TestCase):

    def test_run_cleanup_iterates_rules(self):
        tmp = Path(tempfile.mkdtemp(prefix="baa_cleanup_runs_"))
        try:
            (tmp / "uploads").mkdir()
            (tmp / "outputs/charts").mkdir(parents=True)
            old1 = tmp / "uploads" / "old1.xlsx"; old1.write_text("x")
            old2 = tmp / "outputs/charts" / "old2.html"; old2.write_text("y")
            past = time.time() - (200 * 86400)
            for p in (old1, old2):
                os.utime(p, (past, past))
            run_cleanup(tmp, rules=(
                ("uploads",        30, "uploads"),
                ("outputs/charts", 90, "charts"),
            ))
            self.assertFalse(old1.exists())
            self.assertFalse(old2.exists())
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
