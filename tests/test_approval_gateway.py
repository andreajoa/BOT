# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest

from command_protocol import Action, Side, TakeProfitTarget, TradeCommand
from control.approval_gateway import ApprovalGateway


def _command(command_id="cmd-gateway"):
    now = datetime.now(timezone.utc)
    return TradeCommand(
        command_id=command_id,
        action=Action.OPEN_POSITION,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=90)).isoformat(),
        symbol="SUIUSDT",
        side=Side.LONG,
        margin_usdt=0.5,
        leverage=10,
        stop_loss=0.78,
        take_profits=[TakeProfitTarget(0.84, 100)],
    )


class ApprovalGatewayTests(unittest.TestCase):
    def _gateway(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return ApprovalGateway(
            pending_path=os.path.join(tmp.name, "pending.json"),
            approval_path=os.path.join(tmp.name, "approval.json"),
            history_path=os.path.join(tmp.name, "history.jsonl"),
        )

    def test_publish_and_consume_exact_approval(self):
        gateway = self._gateway()
        command = _command()
        gateway.publish(command, {"accepted": True})
        loaded, wrapper = gateway.pending()
        self.assertEqual(loaded.command_id, command.command_id)
        self.assertTrue(wrapper["preflight"]["accepted"])
        gateway.approve(command.command_id)
        approval = gateway.consume_approval(command.command_id)
        self.assertIsNotNone(approval)
        self.assertEqual(approval.command_id, command.command_id)
        self.assertIsNone(gateway.consume_approval(command.command_id))

    def test_wrong_command_id_cannot_be_approved(self):
        gateway = self._gateway()
        gateway.publish(_command())
        with self.assertRaises(ValueError):
            gateway.approve("different-command")

    def test_new_publish_deletes_old_approval(self):
        gateway = self._gateway()
        first = _command("first")
        gateway.publish(first)
        gateway.approve("first")
        second = _command("second")
        gateway.publish(second)
        self.assertIsNone(gateway.consume_approval("first"))


if __name__ == "__main__":
    unittest.main()
