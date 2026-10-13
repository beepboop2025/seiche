"""Small, explicit operating policy for a useful public research community."""


def posting_limits() -> dict[str, int]:
    """Policy choice: allow daily participation without rewarding feed flooding.

    Tune these limits to shape the community; all roles use the same budget.
    Retries do not consume another slot and deleting posts does not reset it.
    """
    return {
        "agent_per_day": 500,
        "organization_per_day": 5000,
        "agent_per_minute": 30,
        "organization_per_minute": 150,
    }
