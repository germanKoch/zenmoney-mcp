from __future__ import annotations

from pydantic import BaseModel, Field


class User(BaseModel, extra="allow"):
    id: int
    parent: int | None = None


class Instrument(BaseModel, extra="allow"):
    id: int
    title: str
    shortTitle: str = ""
    symbol: str = ""
    rate: float = 1.0


class Account(BaseModel, extra="allow"):
    id: str
    title: str
    type: str | None = None
    instrument: int | None = None
    balance: float = 0
    archive: bool = False
    savings: bool | None = None
    enableCorrection: bool | None = None
    startBalance: float | None = None
    creditLimit: float | None = None


class Tag(BaseModel, extra="allow"):
    id: str
    title: str
    parent: str | None = None
    showIncome: bool = False
    showOutcome: bool = False
    budgetIncome: bool = False
    budgetOutcome: bool = False
    icon: str | None = None
    color: int | None = None


class Merchant(BaseModel, extra="allow"):
    id: str
    title: str


class Transaction(BaseModel, extra="allow"):
    id: str
    user: int | None = None
    date: str
    income: float = 0
    outcome: float = 0
    incomeInstrument: int | None = None
    outcomeInstrument: int | None = None
    incomeAccount: str
    outcomeAccount: str
    tag: list[str] | None = None
    merchant: str | None = None
    payee: str | None = None
    comment: str | None = None
    deleted: bool = False
    hold: bool | None = None
    created: int = 0
    changed: int = 0


class Budget(BaseModel, extra="allow"):
    tag: str | None = None
    date: str
    income: float = 0
    outcome: float = 0
    changed: int = 0


class Deletion(BaseModel, extra="allow"):
    id: str
    object: str
    stamp: int
    user: int


class DiffResponse(BaseModel, extra="allow"):
    serverTimestamp: int
    user: list[User] = Field(default_factory=list)
    instrument: list[Instrument] = Field(default_factory=list)
    account: list[Account] = Field(default_factory=list)
    tag: list[Tag] = Field(default_factory=list)
    merchant: list[Merchant] = Field(default_factory=list)
    transaction: list[Transaction] = Field(default_factory=list)
    budget: list[Budget] = Field(default_factory=list)
    deletion: list[Deletion] = Field(default_factory=list)
