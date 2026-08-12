import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from . import __version__
from .zen_client import ZenMoneyClient, ZenMoneyError, get_token

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    client: ZenMoneyClient


@asynccontextmanager
async def lifespan(server: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
    token = await get_token()
    client = ZenMoneyClient(token)
    logger.info("Initial sync...")
    await client.sync(force=True)
    logger.info(
        "Synced: %d accounts, %d transactions, %d categories",
        len(client.accounts),
        len(client.transactions),
        len(client.tags),
    )
    try:
        yield AppContext(client=client)
    finally:
        await client.close()


mcp = MCPServer[AppContext]("ZenMoney", version=__version__, lifespan=lifespan)


def _get_client(ctx: Context[AppContext]) -> ZenMoneyClient:
    return ctx.request_context.lifespan_context.client


def _currency_symbol(client: ZenMoneyClient, instrument_id: int | None) -> str:
    if instrument_id and instrument_id in client.instruments:
        return client.instruments[instrument_id].symbol or client.instruments[instrument_id].shortTitle
    return ""


# ──────────── Tools ────────────


@mcp.tool()
async def get_accounts(ctx: Context[AppContext]) -> str:
    """Get all active accounts with balances."""
    client = _get_client(ctx)
    await client.sync()
    lines = []
    for acc in sorted(client.accounts.values(), key=lambda a: a.title):
        if acc.archive:
            continue
        cur = _currency_symbol(client, acc.instrument)
        lines.append(f"- {acc.title}: {acc.balance} {cur}  (id: {acc.id})")
    return "\n".join(lines) or "No accounts found."


@mcp.tool()
async def get_transactions(
    ctx: Context[AppContext],
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: str | None = None,
    tag_id: str | None = None,
    limit: int = 50,
) -> str:
    """Get transactions with optional filters.

    Args:
        date_from: Start date (YYYY-MM-DD)
        date_to: End date (YYYY-MM-DD)
        account_id: Filter by account ID
        tag_id: Filter by category/tag ID
        limit: Max number of transactions (default 50)
    """
    client = _get_client(ctx)
    await client.sync()
    txns = list(client.transactions.values())

    if date_from:
        txns = [t for t in txns if t.date >= date_from]
    if date_to:
        txns = [t for t in txns if t.date <= date_to]
    if account_id:
        txns = [t for t in txns if account_id in (t.incomeAccount, t.outcomeAccount)]
    if tag_id:
        txns = [t for t in txns if t.tag and tag_id in t.tag]

    txns.sort(key=lambda t: t.date, reverse=True)
    txns = txns[:limit]

    lines = []
    for t in txns:
        tags = ""
        if t.tag:
            tag_names = [client.tags[tid].title for tid in t.tag if tid in client.tags]
            tags = ", ".join(tag_names)
        payee = t.payee or ""
        comment = t.comment or ""
        desc = " | ".join(filter(None, [tags, payee, comment]))

        if t.outcome > 0 and t.income > 0 and t.outcomeAccount != t.incomeAccount:
            out_cur = _currency_symbol(client, t.outcomeInstrument)
            in_cur = _currency_symbol(client, t.incomeInstrument)
            amount_str = f"{t.outcome} {out_cur} → {t.income} {in_cur}"
        elif t.outcome > 0:
            cur = _currency_symbol(client, t.outcomeInstrument)
            amount_str = f"-{t.outcome} {cur}"
        else:
            cur = _currency_symbol(client, t.incomeInstrument)
            amount_str = f"+{t.income} {cur}"

        lines.append(f"- [{t.date}] {amount_str} {desc}  (id: {t.id})")
    return "\n".join(lines) or "No transactions found."


@mcp.tool()
async def get_categories(ctx: Context[AppContext]) -> str:
    """Get the category tree (tags)."""
    client = _get_client(ctx)
    await client.sync()

    # Build tree
    children: dict[str | None, list] = {}
    for tag in client.tags.values():
        children.setdefault(tag.parent, []).append(tag)

    lines: list[str] = []

    def _walk(parent_id: str | None, indent: int) -> None:
        for tag in sorted(children.get(parent_id, []), key=lambda t: t.title):
            direction = []
            if tag.showIncome:
                direction.append("income")
            if tag.showOutcome:
                direction.append("expense")
            dir_str = f" [{', '.join(direction)}]" if direction else ""
            lines.append(f"{'  ' * indent}- {tag.title}{dir_str}  (id: {tag.id})")
            _walk(tag.id, indent + 1)

    _walk(None, 0)
    return "\n".join(lines) or "No categories found."


@mcp.tool()
async def create_transaction(
    ctx: Context[AppContext],
    type: str,
    amount: float,
    account_id: str,
    date: str | None = None,
    tag_id: str | None = None,
    payee: str | None = None,
    comment: str | None = None,
    to_account_id: str | None = None,
    to_amount: float | None = None,
) -> str:
    """Create a new transaction.

    Args:
        type: Transaction type — "expense", "income", or "transfer"
        amount: Amount in account currency
        account_id: Source account ID (or the only account for income/expense)
        date: Date (YYYY-MM-DD), defaults to today
        tag_id: Category/tag ID
        payee: Payee name
        comment: Comment
        to_account_id: Destination account ID (required for transfers)
        to_amount: Amount in destination currency (defaults to same as amount)
    """
    client = _get_client(ctx)
    await client.sync()

    if account_id not in client.accounts:
        return f"Account {account_id} not found."
    acc = client.accounts[account_id]
    tx_date = date or _today()

    data: dict = {"date": tx_date}
    if tag_id:
        data["tag"] = [tag_id]
    if payee:
        data["payee"] = payee
    if comment:
        data["comment"] = comment

    if type == "expense":
        data["outcomeAccount"] = account_id
        data["outcome"] = amount
        data["outcomeInstrument"] = acc.instrument
        data["incomeAccount"] = account_id
        data["income"] = 0
        data["incomeInstrument"] = acc.instrument
    elif type == "income":
        data["incomeAccount"] = account_id
        data["income"] = amount
        data["incomeInstrument"] = acc.instrument
        data["outcomeAccount"] = account_id
        data["outcome"] = 0
        data["outcomeInstrument"] = acc.instrument
    elif type == "transfer":
        if not to_account_id or to_account_id not in client.accounts:
            return "to_account_id is required for transfers and must be a valid account."
        to_acc = client.accounts[to_account_id]
        data["outcomeAccount"] = account_id
        data["outcome"] = amount
        data["outcomeInstrument"] = acc.instrument
        data["incomeAccount"] = to_account_id
        data["income"] = to_amount if to_amount is not None else amount
        data["incomeInstrument"] = to_acc.instrument
    else:
        return f"Unknown type '{type}'. Use expense, income, or transfer."

    try:
        tr = await client.create_transaction(data)
        return f"Created transaction {tr.id} on {tr.date}."
    except ZenMoneyError as e:
        return f"Error: {e}"


@mcp.tool()
async def update_transaction(
    ctx: Context[AppContext],
    transaction_id: str,
    amount: float | None = None,
    date: str | None = None,
    tag_id: str | None = None,
    payee: str | None = None,
    comment: str | None = None,
) -> str:
    """Update an existing transaction.

    Args:
        transaction_id: Transaction ID
        amount: New amount (updates both income/outcome depending on type)
        date: New date (YYYY-MM-DD)
        tag_id: New category/tag ID
        payee: New payee
        comment: New comment
    """
    client = _get_client(ctx)
    await client.sync()

    updates: dict = {}
    if date is not None:
        updates["date"] = date
    if tag_id is not None:
        updates["tag"] = [tag_id]
    if payee is not None:
        updates["payee"] = payee
    if comment is not None:
        updates["comment"] = comment
    if amount is not None:
        existing = client.transactions.get(transaction_id)
        if existing:
            if existing.outcome > 0 and (existing.income == 0 or existing.outcomeAccount == existing.incomeAccount):
                updates["outcome"] = amount
            elif existing.income > 0:
                updates["income"] = amount

    if not updates:
        return "Nothing to update."

    try:
        tr = await client.update_transaction(transaction_id, updates)
        return f"Updated transaction {tr.id}."
    except ZenMoneyError as e:
        return f"Error: {e}"


@mcp.tool()
async def delete_transaction(ctx: Context[AppContext], transaction_id: str) -> str:
    """Delete a transaction.

    Args:
        transaction_id: Transaction ID to delete
    """
    client = _get_client(ctx)
    try:
        await client.delete_transaction(transaction_id)
        return f"Deleted transaction {transaction_id}."
    except ZenMoneyError as e:
        return f"Error: {e}"


@mcp.tool()
async def get_budgets(
    ctx: Context[AppContext],
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """Get budgets by category.

    Args:
        date_from: Start date (YYYY-MM-DD)
        date_to: End date (YYYY-MM-DD)
    """
    client = _get_client(ctx)
    await client.sync()

    budgets = list(client.budgets.values())
    if date_from:
        budgets = [b for b in budgets if b.date >= date_from]
    if date_to:
        budgets = [b for b in budgets if b.date <= date_to]

    budgets.sort(key=lambda b: b.date)
    lines = []
    for b in budgets:
        tag_name = client.tags[b.tag].title if b.tag and b.tag in client.tags else "No category"
        parts = []
        if b.outcome > 0:
            parts.append(f"expense budget: {b.outcome}")
        if b.income > 0:
            parts.append(f"income budget: {b.income}")
        lines.append(f"- [{b.date}] {tag_name}: {', '.join(parts)}")
    return "\n".join(lines) or "No budgets found."


@mcp.tool()
async def suggest_category(ctx: Context[AppContext], payee: str) -> str:
    """Suggest a category for a payee.

    Args:
        payee: Payee name to get category suggestion for
    """
    client = _get_client(ctx)
    try:
        result = await client.suggest(payee)
        if not result:
            return f"No suggestions for '{payee}'."
        lines = []
        for item in result:
            tag_id = item.get("tag")
            tag_name = client.tags[tag_id].title if tag_id and tag_id in client.tags else tag_id
            lines.append(f"- {tag_name} (id: {tag_id})")
        return "\n".join(lines)
    except ZenMoneyError as e:
        return f"Error: {e}"


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def main() -> None:
    """Entry point for the `zenmoney-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
