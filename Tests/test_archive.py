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
    read_acquisition_source,
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


if __name__ == "__main__":
    unittest.main()
