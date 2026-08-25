from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from model_evolution.evidence import (
    extract_codex_sessions,
    redact_secrets,
    validate_evidence_bundle,
)
from model_evolution.config import initialize_project, load_project
from model_evolution.records import base_record, validate_record


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_session(
    path: Path,
    *,
    cwd: Path,
    session_id: str,
    commit: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "timestamp": "2026-06-22T01:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": "2026-06-22T01:00:00Z",
                "cwd": str(cwd),
                "cli_version": "test",
                "git": {"commit_hash": commit, "branch": "main"},
            },
        },
        {
            "timestamp": "2026-06-22T01:01:00Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "I expect the metric loss to improve in runs/metric-a.",
            },
        },
        {
            "timestamp": "2026-06-22T01:02:00Z",
            "type": "event_msg",
            "payload": {
                "type": "agent_reasoning",
                "text": "This hidden reasoning must never be extracted.",
            },
        },
        {
            "timestamp": "2026-06-22T01:03:00Z",
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": "The checkpoint passed evaluation.",
            },
        },
        {
            "timestamp": "2026-06-22T01:04:00Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-1",
                "arguments": '{"cmd":"make train"}',
            },
        },
        {
            "timestamp": "2026-06-22T01:05:00Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"access_token":"secret-value","loss":0.5}',
            },
        },
        {
            "timestamp": "2026-06-22T01:06:00Z",
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "summary": ["Also forbidden."],
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class EvidenceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repository"
        self.root.mkdir()
        _git(self.root, "init")
        _git(self.root, "config", "user.email", "test@example.com")
        _git(self.root, "config", "user.name", "Evidence Test")
        (self.root / "README.md").write_text("# test\n", encoding="utf-8")
        _git(self.root, "add", "README.md")
        _git(self.root, "commit", "-m", "initial")
        self.commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        initialize_project(
            self.root,
            project_id="evidence-test",
            artifact_store=(self.root / "artifacts").as_uri(),
            adapter="test",
        )
        self.project = load_project(self.root)
        self.sessions = Path(self.temporary.name) / "sessions"
        _write_session(
            self.sessions / "2026" / "06" / "session.jsonl",
            cwd=self.root,
            session_id="session-matching",
            commit=self.commit,
        )
        other = Path(self.temporary.name) / "other"
        other.mkdir()
        _write_session(
            self.sessions / "2026" / "06" / "other.jsonl",
            cwd=other,
            session_id="session-other",
            commit=self.commit,
        )
        run = self.root / "runs" / "metric-a"
        run.mkdir(parents=True)
        (run / "best.pt").write_bytes(b"checkpoint")
        (run / "metrics.jsonl").write_text('{"loss": 0.5}\n', encoding="utf-8")
        dataset = self.root / "data" / "generated" / "fixture"
        dataset.mkdir(parents=True)
        (dataset / "dataset.json").write_text(
            '{"dataset_version":"fixture-v1"}\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_extracts_visible_evidence_and_validates_identities(self) -> None:
        result = extract_codex_sessions(
            self.project,
            sessions_root=self.sessions,
            active_window_seconds=0,
        )
        self.assertEqual(result["sessions"], 1)
        self.assertEqual(result["artifacts"], 3)
        bundle = Path(result["path"])
        evidence = (bundle / "evidence.jsonl").read_text(encoding="utf-8")
        self.assertIn("metric loss", evidence)
        self.assertIn("[REDACTED]", evidence)
        self.assertNotIn("secret-value", evidence)
        self.assertNotIn("hidden reasoning", evidence)
        validation = validate_evidence_bundle(self.project, bundle=bundle)
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["sessions"], 1)
        self.assertEqual(validation["artifacts"], 3)

    def test_bundle_digest_detects_modified_evidence(self) -> None:
        result = extract_codex_sessions(
            self.project,
            sessions_root=self.sessions,
            active_window_seconds=0,
        )
        bundle = Path(result["path"])
        with (bundle / "evidence.jsonl").open("a", encoding="utf-8") as destination:
            destination.write("{}\n")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            validate_evidence_bundle(self.project, bundle=bundle)

    def test_source_validation_accepts_append_but_rejects_prefix_change(self) -> None:
        result = extract_codex_sessions(
            self.project,
            sessions_root=self.sessions,
            active_window_seconds=0,
        )
        source = self.sessions / "2026" / "06" / "session.jsonl"
        with source.open("a", encoding="utf-8") as destination:
            destination.write("\n")
        self.assertTrue(
            validate_evidence_bundle(self.project, bundle=result["path"])["valid"]
        )
        payload = source.read_bytes()
        source.write_bytes(b"X" + payload[1:])
        with self.assertRaisesRegex(ValueError, "captured source session prefix changed"):
            validate_evidence_bundle(self.project, bundle=result["path"])


class EvidenceSchemaTests(unittest.TestCase):
    def test_redactor_handles_private_keys_bearers_and_tokens(self) -> None:
        text = (
            "-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY----- "
            "Bearer abc.def.ghi sk-abcdefghijklmnop"
        )
        redacted, count = redact_secrets(text)
        self.assertNotIn("private\n", redacted)
        self.assertNotIn("abc.def.ghi", redacted)
        self.assertNotIn("sk-abcdefghijklmnop", redacted)
        self.assertEqual(count, 3)

    def test_record_provenance_is_field_typed(self) -> None:
        record = base_record(
            "study",
            "study-1",
            status="draft",
        )
        body = (
            "# Evidence study\n\n"
            "## Claim\n\nClaim.\n\n"
            "## Basis\n\nBasis.\n\n"
            "## Expected evidence\n\nEvidence.\n\n"
            "## Falsification\n\nFalsification."
        )
        record["provenance"] = [
            {
                "kind": "codex_session",
                "locator": "session-1#ev-1",
                "sha256": "a" * 64,
                "claim_type": "inferred",
                "confidence": "medium",
            }
        ]
        validate_record(record, body=body)
        record["provenance"][0]["claim_type"] = "certain"
        with self.assertRaisesRegex(ValueError, "claim_type"):
            validate_record(record, body=body)

if __name__ == "__main__":
    unittest.main()
