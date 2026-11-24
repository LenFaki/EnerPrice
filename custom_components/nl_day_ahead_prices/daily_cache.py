"""Pure cache-key helpers for Daily Advisor."""


def advice_time_bucket(now):
    """Use a five-minute time bucket for periodic advice refresh."""
    return int(now.timestamp() // 300)
