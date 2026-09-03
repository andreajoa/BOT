# -*- coding: utf-8 -*-
"""Primary entrypoint for the Adaptive Binance Futures Executor."""

import os

from dotenv import load_dotenv

load_dotenv()

BRAIN_MODE = os.getenv("BRAIN_MODE", "external_chatgpt").strip().lower()

if BRAIN_MODE == "external_chatgpt":
    from external_production_runtime import main
elif BRAIN_MODE == "openai_api":
    from production_runtime import main
else:
    raise RuntimeError(
        f"BRAIN_MODE invalido: {BRAIN_MODE!r}. Use external_chatgpt ou openai_api."
    )


if __name__ == "__main__":
    main()
