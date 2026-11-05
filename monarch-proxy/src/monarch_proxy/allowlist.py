"""The read-only tool allowlist. Anything not listed here is refused, whatever its name."""

READ_TOOLS: frozenset[str] = frozenset(
    {
        "GetAccounts",
        "GetBudget",
        "GetBusinesses",
        "GetCashFlow",
        "GetCategories",
        "GetCreditScoreHistory",
        "GetGoals",
        "GetHouseholdMembers",
        "GetInvestments",
        "GetMerchants",
        "GetNetWorthHistory",
        "GetRealEstate",
        "GetRecurring",
        "GetSpendingByCategory",
        "GetTags",
        "GetTransactions",
        "ListRules",
    }
)


def is_allowed(name: str) -> bool:
    return name in READ_TOOLS
