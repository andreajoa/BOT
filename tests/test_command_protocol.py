import unittest
from datetime import datetime, timedelta, timezone

from approval import TradeApproval
from command_protocol import Action, Side, TradeCommand


class CommandProtocolTests(unittest.TestCase):
    def _times(self):
        now = datetime.now(timezone.utc)
        return (
            now,
            (now - timedelta(seconds=2)).isoformat(),
            (now + timedelta(seconds=60)).isoformat(),
        )

    def test_valid_open_command(self):
        now, issued, expires = self._times()
        command = TradeCommand.from_dict(
            {
                "command_id": "cmd-1",
                "action": "OPEN_POSITION",
                "issued_at": issued,
                "expires_at": expires,
                "symbol": "testusdt",
                "side": "LONG",
                "strategy": "dynamic-test",
                "regime": "TREND",
                "confidence": 0.75,
                "margin_usdt": 0.50,
                "leverage": 10,
                "stop_loss": 0.48,
                "take_profits": [{"price": 0.55, "close_pct": 100}],
                "trailing": {"enabled": False},
            }
        )

        command.validate(now=now)
        self.assertEqual(command.action, Action.OPEN_POSITION)
        self.assertEqual(command.side, Side.LONG)
        self.assertEqual(command.symbol, "TESTUSDT")

    def test_expired_command_is_rejected(self):
        now = datetime.now(timezone.utc)
        command = TradeCommand.from_dict(
            {
                "command_id": "cmd-expired",
                "action": "WAIT",
                "issued_at": (now - timedelta(minutes=2)).isoformat(),
                "expires_at": (now - timedelta(minutes=1)).isoformat(),
            }
        )
        with self.assertRaises(ValueError):
            command.validate(now=now)

    def test_open_command_requires_stop_and_take_profit(self):
        now, issued, expires = self._times()
        command = TradeCommand.from_dict(
            {
                "command_id": "cmd-no-protection",
                "action": "OPEN_POSITION",
                "issued_at": issued,
                "expires_at": expires,
                "symbol": "TESTUSDT",
                "side": "SHORT",
                "margin_usdt": 0.50,
                "leverage": 10,
            }
        )
        with self.assertRaises(ValueError):
            command.validate(now=now)

    def test_approval_must_match_time_window(self):
        now = datetime.now(timezone.utc)
        approval = TradeApproval(
            command_id="cmd-1",
            approved=True,
            approved_at=(now - timedelta(seconds=1)).isoformat(),
            expires_at=(now + timedelta(seconds=30)).isoformat(),
        )
        self.assertTrue(approval.is_valid(now=now))

    def test_expired_approval_is_invalid(self):
        now = datetime.now(timezone.utc)
        approval = TradeApproval(
            command_id="cmd-1",
            approved=True,
            approved_at=(now - timedelta(minutes=2)).isoformat(),
            expires_at=(now - timedelta(minutes=1)).isoformat(),
        )
        self.assertFalse(approval.is_valid(now=now))


if __name__ == "__main__":
    unittest.main()
