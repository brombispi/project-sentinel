import json
import sys
import tempfile
import unittest
import hashlib
import io
import builtins
from contextlib import redirect_stdout
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

import os
import shutil

from modules.archive import (
    ACQUISITION_SOURCE_FILENAME,
    AcquisitionSourceError,
    CHUNK_SIZE,
    FingerprintEvidenceError,
    SHA256_FILENAME,
    _compute_sha256_digest,
    create_recovery_folder,
    read_acquisition_source,
    read_fingerprint_evidence,
    summarize_recovered_artifacts,
)
from i18n import set_language

VALID_ACQUISITION_SOURCE = {
    "serial": "S4EWNF0M803123A",
    "model": "Samsung SSD 860",
    "size_bytes": 500107862016,
    "logical_sector_size": 512,
    "physical_sector_size": 512,
    "path": "/dev/sdb",
    "timestamp": "2026-07-16T10:00:00",
}


def _write_acquisition_source(case_dir, payload):
    evidence_dir = case_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / ACQUISITION_SOURCE_FILENAME
    evidence_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return evidence_path


class ReadAcquisitionSourceTests(unittest.TestCase):
    def test_read_acquisition_source_returns_valid_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_acquisition_source(case_dir, VALID_ACQUISITION_SOURCE)

            result = read_acquisition_source(case_dir)

            self.assertEqual(result, VALID_ACQUISITION_SOURCE)

    def test_read_acquisition_source_returns_none_when_file_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)

            result = read_acquisition_source(case_dir)

            self.assertIsNone(result)

    def test_read_acquisition_source_raises_on_malformed_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            evidence_path = _write_acquisition_source(case_dir, VALID_ACQUISITION_SOURCE)
            evidence_path.write_text("{not valid json", encoding="utf-8")

            with self.assertRaises(AcquisitionSourceError) as context:
                read_acquisition_source(case_dir)

            self.assertIn("acquisition_source.json is malformed", str(context.exception))

    def test_read_acquisition_source_raises_when_payload_is_not_object(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            evidence_path = _write_acquisition_source(case_dir, VALID_ACQUISITION_SOURCE)
            evidence_path.write_text("[1, 2, 3]", encoding="utf-8")

            with self.assertRaises(AcquisitionSourceError) as context:
                read_acquisition_source(case_dir)

            self.assertIn(
                "acquisition_source.json must contain a JSON object",
                str(context.exception),
            )


VALID_FINGERPRINT_EVIDENCE = {
    "algorithm": "SHA-256",
    "digest": "abc123def456",
    "image_filename": "source.img",
    "image_size_bytes": 500107862016,
    "timestamp": "2026-07-16 10:00:00",
}


def _write_fingerprint_evidence(case_dir, payload):
    evidence_dir = case_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / SHA256_FILENAME
    evidence_path.write_text(
        "algorithm={algorithm}\n"
        "digest={digest}\n"
        "image={image_filename}\n"
        "size_bytes={image_size_bytes}\n"
        "timestamp={timestamp}\n".format(**payload),
        encoding="utf-8",
    )
    return evidence_path


class ReadFingerprintEvidenceTests(unittest.TestCase):
    def test_read_fingerprint_evidence_returns_valid_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_fingerprint_evidence(case_dir, VALID_FINGERPRINT_EVIDENCE)

            result = read_fingerprint_evidence(case_dir)

            self.assertEqual(result, VALID_FINGERPRINT_EVIDENCE)

    def test_read_fingerprint_evidence_returns_none_when_file_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)

            result = read_fingerprint_evidence(case_dir)

            self.assertIsNone(result)

    def test_read_fingerprint_evidence_raises_on_malformed_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            evidence_path = _write_fingerprint_evidence(
                case_dir,
                VALID_FINGERPRINT_EVIDENCE,
            )
            evidence_path.write_text("not valid evidence", encoding="utf-8")

            with self.assertRaises(FingerprintEvidenceError) as context:
                read_fingerprint_evidence(case_dir)

            self.assertIn("source.sha256 is malformed", str(context.exception))


EMPTY_RECOVERED_SUMMARY = {
    "recovered_file_count": 0,
    "recovered_directory_count": 0,
    "recovered_size_bytes": 0,
    "recup_directories": [],
    "recovery_present": False,
}


def _write_recovered_artifacts(case_dir):
    recup_dir = case_dir / "recovered" / "recup.1"
    recup_dir.mkdir(parents=True, exist_ok=True)
    (recup_dir / "file_a.bin").write_bytes(b"abc")
    (recup_dir / "nested" / "file_b.bin").parent.mkdir(parents=True, exist_ok=True)
    (recup_dir / "nested" / "file_b.bin").write_bytes(b"12345")


def _write_testdisk_artifacts(case_dir):
    testdisk_dir = case_dir / "recovered" / "testdisk"
    testdisk_dir.mkdir(parents=True, exist_ok=True)
    (testdisk_dir / "recovered_1.dat").write_bytes(b"wxyz")
    nested = testdisk_dir / "partition_1"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "recovered_2.dat").write_bytes(b"mn")


class CreateRecoveryFolderTests(unittest.TestCase):
    def test_create_recovery_folder_creates_working_directory(self):
        session_id = "REC-TEST-CREATE-WORKING"
        recovery_path = Path(create_recovery_folder(session_id))
        try:
            self.assertTrue((recovery_path / "working").is_dir())
            # The pre-existing structural directories are preserved.
            for name in (
                "images",
                "recovered",
                "exports",
                "notes",
                "reports",
                "evidence",
            ):
                self.assertTrue((recovery_path / name).is_dir())
        finally:
            shutil.rmtree(recovery_path, ignore_errors=True)


class SummarizeRecoveredArtifactsTests(unittest.TestCase):
    def test_summarize_recovered_artifacts_returns_populated_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_recovered_artifacts(case_dir)

            result = summarize_recovered_artifacts(case_dir)

            self.assertEqual(result["recovered_directory_count"], 1)
            self.assertEqual(result["recovered_file_count"], 2)
            self.assertEqual(result["recovered_size_bytes"], 8)
            self.assertEqual(result["recup_directories"], ["recovered/recup.1"])
            self.assertTrue(result["recovery_present"])

    def test_summarize_recovered_artifacts_returns_empty_summary_for_empty_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            (case_dir / "recovered").mkdir()

            result = summarize_recovered_artifacts(case_dir)

            self.assertEqual(result, EMPTY_RECOVERED_SUMMARY)

    def test_summarize_recovered_artifacts_returns_empty_summary_when_directory_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)

            result = summarize_recovered_artifacts(case_dir)

            self.assertEqual(result, EMPTY_RECOVERED_SUMMARY)


class SummarizeRecoveredArtifactsDisjointRootTests(unittest.TestCase):
    """
    recovered/recup.* (PhotoRec) and recovered/testdisk/ (TestDisk) are disjoint
    recovery roots. Each is counted independently and summed, with no recup.*
    artifact ever double-counted (TestDiskIntegration.md §8, Decision A).
    """

    # PhotoRec-only counting is covered by
    # SummarizeRecoveredArtifactsTests.test_summarize_recovered_artifacts_returns_populated_summary;
    # it is intentionally not duplicated here.

    def test_testdisk_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_testdisk_artifacts(case_dir)

            result = summarize_recovered_artifacts(case_dir)

            self.assertEqual(result["recovered_directory_count"], 1)
            self.assertEqual(result["recovered_file_count"], 2)
            self.assertEqual(result["recovered_size_bytes"], 6)
            self.assertEqual(result["recup_directories"], ["recovered/testdisk"])
            self.assertTrue(result["recovery_present"])

    def test_photorec_and_testdisk_together_sum_without_overlap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_recovered_artifacts(case_dir)
            _write_testdisk_artifacts(case_dir)

            result = summarize_recovered_artifacts(case_dir)

            # Roots are disjoint: totals are the exact sum of each root.
            self.assertEqual(result["recovered_directory_count"], 2)
            self.assertEqual(result["recovered_file_count"], 4)
            self.assertEqual(result["recovered_size_bytes"], 14)
            self.assertEqual(
                result["recup_directories"],
                ["recovered/recup.1", "recovered/testdisk"],
            )
            self.assertTrue(result["recovery_present"])

    def test_empty_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            (case_dir / "recovered").mkdir()

            result = summarize_recovered_artifacts(case_dir)

            self.assertEqual(result, EMPTY_RECOVERED_SUMMARY)

    def test_recup_prefixed_name_inside_testdisk_is_not_double_counted(self):
        # A directory named like a PhotoRec batch (recup.*) living *inside* the
        # TestDisk root must be counted once, as part of the single testdisk
        # root, and never promoted to a second recup.* root. This fails for any
        # naive implementation that scans all of recovered/ for recup.*.
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_recovered_artifacts(case_dir)
            _write_testdisk_artifacts(case_dir)
            decoy = case_dir / "recovered" / "testdisk" / "recup.9"
            decoy.mkdir(parents=True, exist_ok=True)
            (decoy / "decoy.dat").write_bytes(b"z")

            result = summarize_recovered_artifacts(case_dir)

            # recup.* roots at the top level: only recovered/recup.1.
            # testdisk root: 1. Total directory roots = 2 (the decoy is not a
            # root, only a file inside the testdisk tree).
            self.assertEqual(result["recovered_directory_count"], 2)
            # PhotoRec 2 files + TestDisk 2 files + 1 decoy file inside testdisk.
            self.assertEqual(result["recovered_file_count"], 5)
            self.assertEqual(result["recovered_size_bytes"], 15)
            self.assertEqual(
                result["recup_directories"],
                ["recovered/recup.1", "recovered/testdisk"],
            )

    def test_testdisk_path_as_plain_file_is_treated_as_absent(self):
        # recovered/testdisk existing as a regular file (not a directory) must
        # be ignored, exactly as an absent testdisk root: no counts, no
        # location, no recovery_present.
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            (case_dir / "recovered").mkdir()
            (case_dir / "recovered" / "testdisk").write_bytes(b"not a dir")

            result = summarize_recovered_artifacts(case_dir)

            self.assertEqual(result, EMPTY_RECOVERED_SUMMARY)

    def test_testdisk_empty_nested_directory_adds_no_files_and_one_root(self):
        # A nested subdirectory (empty here) inside the single testdisk root
        # contributes zero files and does NOT inflate recovered_directory_count:
        # the count is of recovery roots, not of every directory in the tree.
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            (case_dir / "recovered" / "testdisk" / "empty_sub").mkdir(parents=True)

            result = summarize_recovered_artifacts(case_dir)

            self.assertEqual(result["recovered_file_count"], 0)
            self.assertEqual(result["recovered_size_bytes"], 0)
            # Root count is 1 (the testdisk root only), unaffected by the nested
            # empty subdirectory.
            self.assertEqual(result["recovered_directory_count"], 1)
            self.assertEqual(result["recup_directories"], ["recovered/testdisk"])
            # Current semantics: a present root marks recovery_present, even with
            # zero files (parallels an empty recup.* directory). Locked as-is.
            self.assertTrue(result["recovery_present"])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks not supported")
    def test_testdisk_directory_symlink_is_not_traversed(self):
        # os.walk runs with followlinks=False, so a symlinked *directory* inside
        # the testdisk root is not descended into; its contents are never
        # counted. This guards against symlink cycles and counting data outside
        # the root.
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            testdisk_dir = case_dir / "recovered" / "testdisk"
            testdisk_dir.mkdir(parents=True)
            (testdisk_dir / "keep.dat").write_bytes(b"abc")

            external_dir = case_dir / "external"
            external_dir.mkdir()
            (external_dir / "outside_a.bin").write_bytes(b"1234")
            (external_dir / "outside_b.bin").write_bytes(b"56789")

            try:
                os.symlink(external_dir, testdisk_dir / "link_dir")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not permitted")

            result = summarize_recovered_artifacts(case_dir)

            # Only the real file counts; the symlinked directory is not traversed.
            self.assertEqual(result["recovered_file_count"], 1)
            self.assertEqual(result["recovered_size_bytes"], 3)
            self.assertEqual(result["recovered_directory_count"], 1)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks not supported")
    def test_testdisk_file_symlink_is_counted_and_sized_by_target(self):
        # LOCKED CURRENT BEHAVIOR: a symlink to a regular file is counted as a
        # file and sized by its target (Path.is_file()/stat() follow the link).
        # This matches the existing PhotoRec counting path and is intentionally
        # preserved; changing it would alter counting semantics for both roots.
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            testdisk_dir = case_dir / "recovered" / "testdisk"
            testdisk_dir.mkdir(parents=True)
            (testdisk_dir / "keep.dat").write_bytes(b"abc")

            target_file = case_dir / "external_target.bin"
            target_file.write_bytes(b"wxyz")

            try:
                os.symlink(target_file, testdisk_dir / "link.dat")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not permitted")

            result = summarize_recovered_artifacts(case_dir)

            # keep.dat (3) + link.dat resolving to the 4-byte target = 2 files,
            # 7 bytes.
            self.assertEqual(result["recovered_file_count"], 2)
            self.assertEqual(result["recovered_size_bytes"], 7)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "mkfifo not supported")
    def test_testdisk_fifo_special_file_is_skipped(self):
        # A FIFO (named pipe) is not a regular file, so Path.is_file() is False
        # and it is skipped: neither counted nor sized.
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            testdisk_dir = case_dir / "recovered" / "testdisk"
            testdisk_dir.mkdir(parents=True)
            (testdisk_dir / "keep.dat").write_bytes(b"abc")

            try:
                os.mkfifo(testdisk_dir / "pipe")
            except (OSError, NotImplementedError):
                self.skipTest("mkfifo not permitted")

            result = summarize_recovered_artifacts(case_dir)

            # Only the regular file is counted; the FIFO is skipped.
            self.assertEqual(result["recovered_file_count"], 1)
            self.assertEqual(result["recovered_size_bytes"], 3)
            self.assertEqual(result["recovered_directory_count"], 1)


class _FailOnSecondRead:
    def __init__(self, real_file):
        self._f = real_file
        self._reads = 0

    def read(self, n=-1):
        self._reads += 1
        if self._reads > 1:
            raise OSError(5, "Input/output error")
        return self._f.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._f.close()


class ComputeSha256DigestTests(unittest.TestCase):
    def setUp(self):
        set_language("en", persist=False)

    def _run_digest(self, file_path, image_size):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            digest = _compute_sha256_digest(file_path, image_size)
        return digest, stdout.getvalue()

    def test_non_empty_success_progress_output_and_digest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "source.img"
            data = b"x" * (CHUNK_SIZE + 123)
            image_path.write_bytes(data)

            digest, output = self._run_digest(image_path, len(data))

            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertIn("\rFingerprinting:", output)
            self.assertTrue(output.endswith("\n"))
            self.assertEqual(output.count("\n"), 1)
            self.assertIn("\rFingerprinting: 100%", output)

    def test_zero_byte_success_progress_output_and_digest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "source.img"
            image_path.write_bytes(b"")

            digest, output = self._run_digest(image_path, 0)

            self.assertEqual(digest, hashlib.sha256(b"").hexdigest())
            self.assertEqual(output, "\rFingerprinting: 100%\n")

    def test_failure_after_progress_prints_newline_and_reraises_oserror(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "source.img"
            data = b"x" * (CHUNK_SIZE + 100)
            image_path.write_bytes(data)
            real_open = builtins.open

            def selective_open(path, mode="r", *args, **kwargs):
                if str(path) == str(image_path) and "b" in mode:
                    return _FailOnSecondRead(
                        real_open(path, mode, *args, **kwargs)
                    )
                return real_open(path, mode, *args, **kwargs)

            builtins.open = selective_open
            stdout = io.StringIO()
            try:
                with redirect_stdout(stdout):
                    with self.assertRaises(OSError) as context:
                        _compute_sha256_digest(image_path, len(data))
            finally:
                builtins.open = real_open

            output = stdout.getvalue()
            self.assertEqual(context.exception.errno, 5)
            self.assertIn("\rFingerprinting:", output)
            self.assertTrue(output.endswith("\n"))
            self.assertEqual(output.count("\n"), 1)

    def test_failure_before_progress_produces_no_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "missing.img"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                with self.assertRaises(OSError):
                    _compute_sha256_digest(missing_path, 100)

            self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
