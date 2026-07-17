import json
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from modules.archive import (
    ACQUISITION_SOURCE_FILENAME,
    AcquisitionSourceError,
    FingerprintEvidenceError,
    SHA256_FILENAME,
    read_acquisition_source,
    read_fingerprint_evidence,
)

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


if __name__ == "__main__":
    unittest.main()
