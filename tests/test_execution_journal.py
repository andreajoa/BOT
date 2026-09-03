# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from command_protocol import Action, Side, TradeCommand
from execution.journal import ExecutionJournal


class ExecutionJournalTests(unittest.TestCase):
    def test_journal_filters_by_command_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "journal.jsonl")
            journal = ExecutionJournal(path)
            journal.validation("cmd-1", True, "OK", symbol="SUIUSDT")
            journal.validation("cmd-2", False, "expired")
            rows = journal.read("cmd-1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["command_id"], "cmd-1")
            self.assertTrue(rows[0]["payload"]["accepted"])

    def test_command_received_serializes_dataclass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "journal.jsonl")
            journal = ExecutionJournal(path)
            command = TradeCommand(
                command_id="cmd-3",
                action=Action.WAIT,
                issued_at="2026-09-03T00:00:00+00:00",
                expires_at="2026-09-03T00:05:00+00:00",
                side=None,
            )
            journal.command_received(command)
            row = journal.read("cmd-3")[0]
            self.assertEqual(row["event"], "COMMAND_RECEIVED")
            self.assertEqual(row["payload"]["command"]["command_id"], "cmd-3")


if __name__ == "__main__":
    unittest.main()
