"""get_financial_statements — yfinance primary (clean), Finnhub XBRL fallback (deeper history)."""
from __future__ import annotations

import asyncio

from ..context import RunContext
from ..envelope import SourceChain
from ..sources import finnhub, yahoo
from ..sources.errors import SourceMiss

NAME = "get_financial_statements"

SCHEMA = {
    "name": NAME,
    "description": (
        "Get the company's historical income statement, balance sheet, and cash flow — annual and "
        "quarterly. Up to 4 years annual / 4 quarters from yfinance, or up to ~15 filings from Finnhub "
        "as-reported XBRL (US public only). Returns structured periods + rows."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
        },
        "required": ["ticker"],
    },
}


def _df_to_periods_rows(df_dict: dict) -> dict:
    """yfinance period-keyed dict → {periods, rows: [{label, values}]}."""
    if not df_dict:
        return {}
    periods = list(df_dict.keys())
    seen: set[str] = set()
    line_items: list[str] = []
    for p in periods:
        for li in (df_dict.get(p) or {}).keys():
            if li not in seen:
                seen.add(li)
                line_items.append(li)
    rows = [
        {"label": li, "values": [(df_dict.get(p) or {}).get(li) for p in periods]}
        for li in line_items
    ]
    return {"periods": periods, "rows": rows}


# Canonical concept extraction from Finnhub XBRL.
# Each canonical label has a list of XBRL concept names to try (in order of preference).
_INCOME_CONCEPTS = [
    ("Revenue", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]),
    ("Cost of Revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]),
    ("Gross Profit", ["GrossProfit"]),
    ("R&D Expense", ["ResearchAndDevelopmentExpense"]),
    ("SG&A", ["SellingGeneralAndAdministrativeExpense"]),
    ("Operating Expenses", ["OperatingExpenses", "OperatingCostsAndExpenses"]),
    ("Operating Income", ["OperatingIncomeLoss"]),
    ("Interest Expense", ["InterestExpense"]),
    ("Income Before Tax", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]),
    ("Income Tax", ["IncomeTaxExpenseBenefit"]),
    ("Net Income", ["NetIncomeLoss", "ProfitLoss"]),
    ("EPS (Basic)", ["EarningsPerShareBasic"]),
    ("EPS (Diluted)", ["EarningsPerShareDiluted"]),
]

_BALANCE_CONCEPTS = [
    ("Cash & Equivalents", ["CashAndCashEquivalentsAtCarryingValue", "Cash", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    ("Short-Term Investments", ["MarketableSecuritiesCurrent", "ShortTermInvestments"]),
    ("Accounts Receivable", ["AccountsReceivableNetCurrent"]),
    ("Inventory", ["InventoryNet"]),
    ("Total Current Assets", ["AssetsCurrent"]),
    ("Property, Plant & Equipment", ["PropertyPlantAndEquipmentNet"]),
    ("Goodwill", ["Goodwill"]),
    ("Intangible Assets", ["IntangibleAssetsNetExcludingGoodwill"]),
    ("Total Assets", ["Assets"]),
    ("Accounts Payable", ["AccountsPayableCurrent"]),
    ("Short-Term Debt", ["CommercialPaper", "ShortTermBorrowings", "LongTermDebtCurrent"]),
    ("Total Current Liabilities", ["LiabilitiesCurrent"]),
    ("Long-Term Debt", ["LongTermDebtNoncurrent", "LongTermDebt"]),
    ("Total Liabilities", ["Liabilities"]),
    ("Stockholders' Equity", ["StockholdersEquity"]),
]

_CASHFLOW_CONCEPTS = [
    ("Net Income", ["NetIncomeLoss", "ProfitLoss"]),
    ("D&A", ["DepreciationDepletionAndAmortization", "Depreciation", "DepreciationAndAmortization"]),
    ("Stock-Based Comp", ["ShareBasedCompensation"]),
    ("Operating Cash Flow", ["NetCashProvidedByUsedInOperatingActivities"]),
    ("Capital Expenditure", ["PaymentsToAcquirePropertyPlantAndEquipment"]),
    ("Acquisitions (net)", ["PaymentsToAcquireBusinessesNetOfCashAcquired"]),
    ("Investing Cash Flow", ["NetCashProvidedByUsedInInvestingActivities"]),
    ("Debt Issued", ["ProceedsFromIssuanceOfLongTermDebt"]),
    ("Debt Repaid", ["RepaymentsOfLongTermDebt"]),
    ("Stock Repurchased", ["PaymentsForRepurchaseOfCommonStock"]),
    ("Dividends Paid", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]),
    ("Financing Cash Flow", ["NetCashProvidedByUsedInFinancingActivities"]),
    ("Net Change in Cash", ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect", "CashAndCashEquivalentsPeriodIncreaseDecrease"]),
]


def _extract_concept(report_block: list[dict], synonyms: list[str]) -> float | None:
    """report_block is the list of {concept, label, value, unit} for one statement."""
    by_concept = {item.get("concept", "").split(":")[-1]: item.get("value") for item in (report_block or [])}
    for syn in synonyms:
        if syn in by_concept:
            v = by_concept[syn]
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                continue
    return None


def _build_statement(filings: list[dict], section_key: str, concept_map: list[tuple[str, list[str]]]) -> dict:
    """Build a periods/rows view from a list of filings.

    section_key: 'ic' | 'bs' | 'cf'
    """
    if not filings:
        return {}
    # Sort newest first
    filings = sorted(filings, key=lambda f: f.get("endDate") or "", reverse=True)
    periods = [f.get("endDate") for f in filings]
    rows = []
    for label, syns in concept_map:
        values = []
        for f in filings:
            block = (f.get("report") or {}).get(section_key) or []
            values.append(_extract_concept(block, syns))
        rows.append({"label": label, "values": values})
    return {"periods": periods, "rows": rows}


async def _from_yahoo(ctx: RunContext, ticker: str) -> dict:
    dump = await yahoo.full_dump(ctx, ticker)
    return {
        "ticker": ticker,
        "income_statement_annual": _df_to_periods_rows(dump.get("income_annual") or {}),
        "income_statement_quarterly": _df_to_periods_rows(dump.get("income_quarterly") or {}),
        "balance_sheet_annual": _df_to_periods_rows(dump.get("balance_annual") or {}),
        "balance_sheet_quarterly": _df_to_periods_rows(dump.get("balance_quarterly") or {}),
        "cash_flow_annual": _df_to_periods_rows(dump.get("cashflow_annual") or {}),
        "cash_flow_quarterly": _df_to_periods_rows(dump.get("cashflow_quarterly") or {}),
    }


async def _from_finnhub(ctx: RunContext, ticker: str) -> dict:
    """Finnhub returns as-reported XBRL — extract canonical concepts."""
    annual_t = asyncio.create_task(finnhub.financials_reported(ctx, ticker, freq="annual"))
    quarterly_t = asyncio.create_task(finnhub.financials_reported(ctx, ticker, freq="quarterly"))

    annual: list[dict] = []
    quarterly: list[dict] = []
    try:
        annual = await annual_t
    except SourceMiss:
        pass
    try:
        quarterly = await quarterly_t
    except SourceMiss:
        pass
    if not annual and not quarterly:
        raise SourceMiss("finnhub", "financials-reported", "no filings")
    return {
        "ticker": ticker,
        "income_statement_annual": _build_statement(annual, "ic", _INCOME_CONCEPTS),
        "income_statement_quarterly": _build_statement(quarterly[:8], "ic", _INCOME_CONCEPTS),
        "balance_sheet_annual": _build_statement(annual, "bs", _BALANCE_CONCEPTS),
        "balance_sheet_quarterly": _build_statement(quarterly[:8], "bs", _BALANCE_CONCEPTS),
        "cash_flow_annual": _build_statement(annual, "cf", _CASHFLOW_CONCEPTS),
        "cash_flow_quarterly": _build_statement(quarterly[:8], "cf", _CASHFLOW_CONCEPTS),
    }


async def run(ctx: RunContext, ticker: str) -> dict:
    chain = SourceChain(ctx, NAME)

    data = await chain.try_("yahoo", lambda: _from_yahoo(ctx, ticker))
    if data and any(v.get("periods") for v in data.values() if isinstance(v, dict)):
        return chain.success(data).to_dict()

    data = await chain.try_("finnhub", lambda: _from_finnhub(ctx, ticker))
    if data:
        return chain.success(data, notes="from Finnhub as-reported XBRL — line items may be sparse where companies don't tag a concept").to_dict()

    return chain.fail(hint="No financial statements available. Foreign ticker or non-public entity.").to_dict()
