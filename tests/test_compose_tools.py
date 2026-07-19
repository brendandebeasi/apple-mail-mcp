"""Tests for compose and rich draft helpers."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools


class ComposeToolTests(unittest.TestCase):
    def test_create_rich_email_draft_writes_multipart_eml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "weekly-update.eml"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="sender@example.com",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run") as mock_run,
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Weekly Update",
                    to="team@example.com",
                    text_body="Plain fallback",
                    html_body="<html><body><h1>Weekly Update</h1></body></html>",
                    output_path=str(output_path),
                    open_in_mail=True,
                )

            payload = output_path.read_text()
            self.assertIn("multipart/alternative", payload)
            self.assertIn("<h1>Weekly Update</h1>", payload)
            self.assertIn("Subject: Weekly Update", payload)
            self.assertIn("Opened in Mail: yes", result)
            mock_run.assert_called_once_with(
                ["open", "-a", "Mail", str(output_path)], check=True
            )

    def test_create_rich_email_draft_allows_partial_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "partial.eml"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="sender@example.com",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            payload = output_path.read_text()
            self.assertIn("Draft outline", payload)
            self.assertIn("Missing details: subject, to, body", result)
            self.assertIn("Opened in Mail: no", result)

    def test_create_rich_email_draft_can_save_to_drafts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "saved.eml"
            run_results = ["sender@example.com", "saved"]

            def fake_run_applescript(script, timeout=120):
                return run_results.pop(0)

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run_applescript,
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Saved Draft",
                    output_path=str(output_path),
                    open_in_mail=True,
                    save_as_draft=True,
                )

            self.assertIn("Saved in Drafts: yes", result)


class B64AttachmentTests(unittest.TestCase):
    """Tests for the inline base64 attachment path used by Sane on prod."""

    def test_stage_b64_attachments_decodes_and_writes(self):
        import base64
        import json
        import os
        import shutil

        payload = b"hello attachment bytes"
        spec = json.dumps(
            [
                {
                    "filename": "Q3.pdf",
                    "content_base64": base64.b64encode(payload).decode(),
                }
            ]
        )
        paths, tmpdir, error = compose_tools._stage_b64_attachments(spec)
        try:
            self.assertIsNone(error)
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].endswith("Q3.pdf"))
            with open(paths[0], "rb") as fh:
                self.assertEqual(fh.read(), payload)
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

    def test_stage_b64_attachments_strips_path_traversal(self):
        import base64
        import json
        import os
        import shutil

        spec = json.dumps(
            [
                {
                    "filename": "../../etc/passwd",
                    "content_base64": base64.b64encode(b"x").decode(),
                }
            ]
        )
        paths, tmpdir, error = compose_tools._stage_b64_attachments(spec)
        try:
            self.assertIsNone(error)
            # basename() collapses the traversal — the file lands inside tmpdir.
            self.assertEqual(os.path.dirname(paths[0]), tmpdir)
            self.assertTrue(paths[0].endswith("passwd"))
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

    def test_stage_b64_attachments_rejects_bad_json(self):
        paths, tmpdir, error = compose_tools._stage_b64_attachments("not json{")
        self.assertIsNone(tmpdir)
        self.assertIsNotNone(error)
        self.assertIn("valid JSON", error)

    def test_stage_b64_attachments_rejects_bad_base64(self):
        import json

        spec = json.dumps([{"filename": "x.bin", "content_base64": "!!!notb64!!!"}])
        paths, tmpdir, error = compose_tools._stage_b64_attachments(spec)
        self.assertIsNone(tmpdir)
        self.assertIsNotNone(error)
        self.assertIn("base64 decode failed", error)

    def test_reply_to_email_by_id_forwards_b64_attachment(self):
        import base64
        import json
        import os
        import re

        payload = b"%PDF-1.4 fake report bytes"
        spec = json.dumps(
            [
                {
                    "filename": "report.pdf",
                    "content_base64": base64.b64encode(payload).decode(),
                }
            ]
        )

        captured = {}

        def fake_run(cmd, input=None, capture_output=None, timeout=None):
            script = input.decode("utf-8") if isinstance(input, bytes) else input
            captured["script"] = script
            # Confirm the decoded temp file exists at send time and matches.
            m = re.search(r'POSIX file "([^"]+report\.pdf)"', script)
            captured["path"] = m.group(1) if m else None
            if captured["path"]:
                captured["existed"] = os.path.exists(captured["path"])
                if captured["existed"]:
                    with open(captured["path"], "rb") as fh:
                        captured["bytes"] = fh.read()

            class R:
                returncode = 0
                stdout = b"SENDING REPLY (BY ID)\n\nReply sent successfully!\n"
                stderr = b""

            return R()

        with patch("apple_mail_mcp.tools.compose.subprocess.run", side_effect=fake_run):
            result = compose_tools.reply_to_email_by_id(
                message_id="<abc@example.com>",
                reply_body="Here is the report.",
                account="Work",
                attachments_b64=spec,
                mode="send",
            )

        # The AppleScript attached the decoded file by path...
        self.assertIsNotNone(captured.get("path"))
        self.assertTrue(captured.get("existed"))
        self.assertEqual(captured.get("bytes"), payload)
        self.assertIn("make new attachment", captured["script"])
        self.assertIn("Reply sent successfully!", result)
        # ...and the per-call temp dir was cleaned up afterward.
        self.assertFalse(os.path.exists(captured["path"]))


if __name__ == "__main__":
    unittest.main()
