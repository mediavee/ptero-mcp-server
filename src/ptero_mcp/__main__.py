"""Entry point: ``python -m ptero_mcp`` or the ``ptero-mcp`` console script."""

from __future__ import annotations

import asyncio

from ptero_mcp.server import run


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
