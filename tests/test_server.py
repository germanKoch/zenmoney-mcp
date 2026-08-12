import unittest
from unittest.mock import patch

from mcp import Client

import zenmoney_mcp.server as server


class FakeZenMoneyClient:
    def __init__(self, token: str) -> None:
        self.accounts: dict = {}
        self.transactions: dict = {}
        self.tags: dict = {}
        self.instruments: dict = {}
        self.budgets: dict = {}

    async def sync(self, force: bool = False) -> None:
        pass

    async def close(self) -> None:
        pass


async def fake_get_token() -> str:
    return "test-token"


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_schemas_do_not_expose_context(self) -> None:
        tools = await server.mcp.list_tools()

        self.assertEqual(len(tools), 8)
        for tool in tools:
            self.assertNotIn("ctx", tool.input_schema.get("properties", {}))

    async def test_legacy_and_v2_protocols(self) -> None:
        with (
            patch.object(server, "ZenMoneyClient", FakeZenMoneyClient),
            patch.object(server, "get_token", fake_get_token),
        ):
            for mode in ("legacy", "2026-07-28"):
                with self.subTest(mode=mode):
                    async with Client(server.mcp, mode=mode) as client:
                        tools = await client.list_tools()
                        result = await client.call_tool("get_accounts")

                        self.assertEqual(len(tools.tools), 8)
                        self.assertFalse(result.is_error)
                        self.assertEqual(result.content[0].text, "No accounts found.")


if __name__ == "__main__":
    unittest.main()
