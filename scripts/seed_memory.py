"""Seed Cognee with policy, vendor registry, payment history and incidents; render the graph."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import memory  # noqa: E402


async def main():
    await memory.seed()
    print("Seeded. Test recall:")
    print(await memory.recall("Has anyone tried to change Acme Cloud's bank account?"))
    print("Graph:", await memory.render_graph())


if __name__ == "__main__":
    asyncio.run(main())
