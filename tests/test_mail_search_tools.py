"""Tests for structured email search and bulk update helpers."""

import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools import search as search_tools


def _record_line(
    message_id,
    subject,
    internet_message_id="<abc@example.com>",
    sender="sender@example.com",
    mailbox="INBOX",
    account="Work",
    is_read=False,
    received_date="2026-03-07T10:00:00",
    content_preview="",
):
    return "|||".join(
        [
            str(message_id),
            internet_message_id,
            subject,
            sender,
            mailbox,
            account,
            "true" if is_read else "false",
            received_date,
            content_preview,
        ]
    )


class SearchToolTests(unittest.TestCase):
    def test_search_emails_pagination_consistency(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "\n".join(
                [
                    _record_line(
                        100,
                        "Ticket 100",
                        received_date="2026-03-07T12:00:00",
                    ),
                    _record_line(
                        101,
                        "Ticket 101",
                        received_date="2026-03-07T11:00:00",
                    ),
                    _record_line(
                        102,
                        "Ticket 102",
                        received_date="2026-03-07T10:00:00",
                    ),
                ]
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.search_emails(
                    account="Work",
                    output_format="json",
                    offset=1,
                    limit=2,
                    max_results=None,
                )
            )

        self.assertEqual(response["offset"], 1)
        self.assertEqual(response["returned"], 2)
        self.assertTrue(response["has_more"])
        self.assertEqual(response["next_offset"], 3)
        self.assertEqual(
            response["items"][0]["mail_link"],
            "message://%3Cabc@example.com%3E",
        )
        self.assertIn("set offsetRemaining to 1", captured["script"])
        self.assertIn("set collectLimit to 3", captured["script"])

    def test_search_emails_unread_only_filter(self):
        """Test that read_status='unread' adds the correct whose clause."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return _record_line(201, "Unread Ticket", is_read=False)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="Ticket",
                    read_status="unread",
                    output_format="json",
                    limit=1,
                )
            )

        self.assertEqual(len(response["items"]), 1)
        self.assertFalse(response["items"][0]["is_read"])
        self.assertIn("read status is false", captured["script"])

    def test_search_emails_builds_real_date_filters(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return _record_line(
                301,
                "Dated Ticket",
                received_date="2026-03-05T09:00:00",
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="Ticket",
                    date_from="2026-03-01",
                    date_to="2026-03-07",
                    output_format="json",
                    limit=1,
                    max_results=None,
                )
            )

        self.assertEqual(response["items"][0]["message_id"], "301")
        self.assertIn("set year of fromDate to 2026", captured["script"])
        self.assertIn("set month of fromDate to March", captured["script"])
        self.assertIn("date received >= fromDate", captured["script"])
        self.assertIn("date received <= toDate", captured["script"])

    def test_large_mailbox_search_uses_prefiltered_selection(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.search_emails(
                    account="Work",
                    subject_keywords=["INC-1", "INC-2"],
                    include_content=False,
                    output_format="json",
                    limit=50,
                    max_results=None,
                )
            )

        self.assertEqual(response["items"], [])
        self.assertIn(
            "set matchingMessages to every message of currentMailbox whose",
            captured["script"],
        )
        self.assertNotIn(
            "set mailboxMessages to every message of currentMailbox", captured["script"]
        )

    def test_search_emails_returns_mail_link_from_internet_message_id(self):
        def fake_run(script, timeout=120):
            return _record_line(
                401,
                "Linked Ticket",
                internet_message_id="<QwcH6OP9REaEX0pi8aR6-g@geopod-ismtpd-60>",
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="Linked",
                    output_format="json",
                    limit=1,
                    max_results=None,
                )
            )

        self.assertEqual(
            response["items"][0]["internet_message_id"],
            "<QwcH6OP9REaEX0pi8aR6-g@geopod-ismtpd-60>",
        )
        self.assertEqual(
            response["items"][0]["mail_link"],
            "message://%3CQwcH6OP9REaEX0pi8aR6-g@geopod-ismtpd-60%3E",
        )

    def test_search_emails_mail_link_normalizes_missing_angle_brackets(self):
        """AppleScript sometimes returns the Message-ID without angle brackets;
        the mail_link should still include them (percent-encoded)."""

        def fake_run(script, timeout=120):
            return _record_line(
                402,
                "Unbracketed Ticket",
                internet_message_id="abc@example.com",
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="Unbracketed",
                    output_format="json",
                    limit=1,
                    max_results=None,
                )
            )

        self.assertEqual(
            response["items"][0]["internet_message_id"],
            "abc@example.com",
        )
        self.assertEqual(
            response["items"][0]["mail_link"],
            "message://%3Cabc@example.com%3E",
        )

    def test_search_emails_account_none_iterates_all_accounts(self):
        """When account is None, the script should iterate all accounts."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.search_emails(
                account=None,
                subject_keyword="Test",
                output_format="json",
                limit=5,
            )

        self.assertIn("set searchAccounts to every account", captured["script"])

    def test_search_emails_body_text_uses_lowercase_handler(self):
        """When body_text is provided, the script should include LOWERCASE_HANDLER."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.search_emails(
                account="Work",
                body_text="invoice",
                output_format="json",
                limit=5,
            )

        self.assertIn("on lowercase(str)", captured["script"])
        self.assertIn('lowerContent contains "invoice"', captured["script"])


class ManageToolTests(unittest.TestCase):
    def test_update_email_status_with_message_ids_uses_exact_id_condition(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "updated"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.update_email_status(
                account="Work",
                mailbox="INBOX",
                message_ids=["101", "202"],
                action="mark_read",
            )

        self.assertEqual(result, "updated")
        self.assertIn("id is 101", captured["script"])
        self.assertIn("id is 202", captured["script"])
        self.assertIn("set read status of targetMessages to true", captured["script"])

    def test_move_email_id_path_emits_atomic_residual_verify(self):
        # Live (non-dry_run) id-based move must include a residual count
        # check immediately after the move so silent no-ops fail loudly
        # instead of getting reported as success.
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "moved"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                from_mailbox="INBOX",
                message_ids=["101"],
            )

        script = captured["script"]
        self.assertEqual(result, "moved")
        # Captures id before move so post-move stale-reference doesn't bite.
        self.assertIn("set msgId to id of aMessage", script)
        # Move is NOT wrapped in an inner try (errors must propagate to
        # the outer handler).
        move_idx = script.index("move aMessage to destMailbox")
        # The 200 chars before the move should not contain a `try` —
        # this guards against a future regression that re-introduces
        # the error-swallowing inner try/end try.
        self.assertNotIn("try\n", script[max(0, move_idx - 200):move_idx])
        # Post-move residual count check on the source mailbox by id.
        self.assertIn("messages of sourceMailbox whose id is msgId", script)
        # Promote all-failure to an AppleScript error.
        self.assertIn(
            "all targeted message(s) still in source after move", script
        )

    def test_move_email_dry_run_skips_verify_and_move(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "preview"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                from_mailbox="INBOX",
                message_ids=["101"],
                dry_run=True,
            )

        script = captured["script"]
        self.assertNotIn("move aMessage to destMailbox", script)
        self.assertNotIn("whose id is msgId", script)


class EvictArchivedFromInboxTests(unittest.TestCase):
    def test_evict_emits_message_id_match_move_and_residual_verify(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "EVICT FROM LOCAL INBOX -> Archive\nREQUESTED: 1, EVICTED: 1, ALREADY GONE: 0"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.evict_archived_from_inbox(
                account="Gunpowder",
                internet_message_ids=["<abc@mail.gmail.com>"],
            )

        script = captured["script"]
        self.assertIn("EVICTED: 1", result)
        # Matches by RFC Message-ID (bare value; brackets stripped on input).
        self.assertIn('whose message id is targetMid', script)
        # Cross-version bracketed fallback.
        self.assertIn('whose message id is targetMidBracketed', script)
        self.assertIn('set targetMidBracketed to "<" & targetMid & ">"', script)
        # Bare id reaches the AppleScript list literal (no angle brackets).
        self.assertIn('"abc@mail.gmail.com"', script)
        # Moves to Archive and residual-verifies it left INBOX.
        self.assertIn("move aMessage to destMailbox", script)
        self.assertIn("messages of sourceMailbox whose id is msgId", script)
        # LOCAL reconcile only: never deletes, never trashes.
        self.assertNotIn("delete aMessage", script)
        self.assertNotIn("Trash", script)

    def test_evict_rejects_empty_ids(self):
        # No run_applescript call should happen for an empty/blank id list.
        with patch(
            "apple_mail_mcp.tools.manage.run_applescript",
            side_effect=AssertionError("should not run AppleScript"),
        ):
            result = manage_tools.evict_archived_from_inbox(
                account="Gunpowder",
                internet_message_ids=["", "  ", "<>"],
            )
        self.assertTrue(result.startswith("Error:"))


class ListMailboxesJsonTests(unittest.TestCase):
    def test_json_output_returns_account_grouped_payload(self):
        # AppleScript output is pipe-delimited; the json path parses it
        # and emits a {"accounts": [...]} structure.
        raw = (
            "Work|||INBOX|||120|||3\n"
            "Work|||All Mail|||5400|||0\n"
            "Work|||Projects|||10|||0\n"
            "Work|||Projects/Q4|||4|||1\n"
            "Personal|||INBOX|||50|||2\n"
        )

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            from apple_mail_mcp.tools import inbox as inbox_tools

            result = inbox_tools.list_mailboxes(output_format="json")

        payload = json.loads(result)
        self.assertIn("accounts", payload)
        accounts = {a["account"]: a["mailboxes"] for a in payload["accounts"]}
        self.assertIn("Work", accounts)
        self.assertIn("Personal", accounts)
        work_paths = {m["path"] for m in accounts["Work"]}
        self.assertEqual(
            work_paths, {"INBOX", "All Mail", "Projects", "Projects/Q4"}
        )
        # Nested path retains its slash form; name is the leaf.
        q4 = next(m for m in accounts["Work"] if m["path"] == "Projects/Q4")
        self.assertEqual(q4["name"], "Q4")
        self.assertEqual(q4["total"], 4)
        self.assertEqual(q4["unread"], 1)

    def test_json_output_rejects_invalid_format(self):
        from apple_mail_mcp.tools import inbox as inbox_tools

        result = inbox_tools.list_mailboxes(output_format="xml")
        self.assertTrue(result.startswith("Error:"))


if __name__ == "__main__":
    unittest.main()
