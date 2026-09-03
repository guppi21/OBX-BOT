from fastapi import Header, HTTPException, status
from packages.shared.config import get_settings


def verify_admin_token(
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
    authorization: str | None = Header(None),
) -> bool:
    """Verifies that the request carries a valid internal admin authentication token."""
    settings = get_settings()
    expected = settings.OBX_CORE_INTERNAL_AUTH_TOKEN

    token = x_internal_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()

    if not token or token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Valid internal admin authentication token required.",
        )
    return True
