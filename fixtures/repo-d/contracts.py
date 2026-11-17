# Points contracts (repo-d) - synthetic client/server function pairs + event-schema + shared-table constants


# Client stub for points balance service
class PointsBalanceClient:
    """Client stub for PointsBalance service calls."""
    
    def get_balance(self, account_id):
        # Stub implementation - returns synthetic balance
        return {"account_id": account_id, "balance": 150}


# Server stub for points balance
class PointsBalanceServer:
    """Server stub for points balance operations."""
    
    def credit_points(self, account_id, points):
        # Stub implementation - credits points to account
        return {"account_id": account_id, "operation": "credit", "points": points}


# Event schema definition for points transaction events
EVENT_SCHEMA = {
    "points_transaction": {
        "fields": [
            {"name": "account_id", "type": "string"},
            {"name": "transaction_type", "type": "enum", "values": ["earn", "redeem", "expire"]},
            {"name": "points_amount", "type": "integer"},
            {"name": "reason", "type": "string", "optional": True}
        ]
    }
}


# Shared table name constants (used by both client and server)
SHARED_TABLES = {
    "account_points": "accounts_points",
    "transaction_log": "points_transactions",
    "balance_history": "points_balance_history"
}


# Same-name-different-meaning symbol vs refund (different "window" concept)
POINTS_WINDOW_SIZE = 7  # days for points expiry calculation
REFUND_WINDOW_SIZE = 30  # days for refund eligibility (from refund)

# Different-name-related-meaning case (related concept but different name)
POINTS_EXPIRY_PERIOD = "7 days"  # points expiry duration
REFUND_ELIGIBILITY_WINDOW = "30 days"  # refund window duration


# Synthetic client/server call pair (intra-repo-d only)
points_balance_client = PointsBalanceClient()
points_balance_server = PointsBalanceServer()


def sync_points_balance(account_id):
    """Synthetic sync operation between client and server."""
    balance = points_balance_client.get_balance(account_id)
    if balance["balance"] > 0:
        return points_balance_server.credit_points(account_id, balance["balance"])
    return None