# -*- coding: utf-8 -*-
"""Production wiring for AdaptiveRuntime with crash-safe execution semantics."""

from __future__ import annotations

import asyncio
import signal

import adaptive_runtime
from execution.resilient_command_executor import ResilientCommandExecutor


class ProductionRuntime(adaptive_runtime.AdaptiveRuntime):
    async def initialize(self) -> None:
        # AdaptiveRuntime resolves CommandExecutor from its module global at
        # initialization time. Replace only that execution component; all market,
        # brain, risk and approval layers remain unchanged.
        adaptive_runtime.CommandExecutor = ResilientCommandExecutor
        await super().initialize()

    async def shutdown(self) -> None:
        if self.executor and hasattr(self.executor, "stop_recovery"):
            self.executor.stop_recovery()
        await super().shutdown()


async def _main() -> None:
    runtime = ProductionRuntime()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, runtime.request_stop)
        except NotImplementedError:
            pass
    await runtime.run()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
