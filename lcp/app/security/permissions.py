from ..protocol.errors import LCPError
from .auth import Principal


def require_account(principal: Principal) -> None:
    if principal.kind != "account" or not principal.user_id:
        raise LCPError("LCP_AUTH_REQUIRED", "This method requires an account session")
