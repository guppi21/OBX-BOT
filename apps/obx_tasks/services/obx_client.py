import uuid
import httpx
from typing import Optional
from sqlalchemy.orm import Session

from packages.shared.config import get_settings
from packages.shared.logging import get_logger
from apps.obx_core.services.wallet_service import WalletService

logger = get_logger("obx.tasks.obx_client")


class OBXCoreClient:
    """Client for distributing rewards through the OBX Core Credit engine."""

    def __init__(
        self,
        session: Optional[Session] = None,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
    ):
        settings = get_settings()
        self.session = session
        self.base_url = base_url or settings.OBX_CORE_API_URL
        self.auth_token = auth_token or settings.OBX_CORE_INTERNAL_AUTH_TOKEN

    def credit(
        self,
        discord_user_id: str,
        amount: int,
        reference_type: str,
        idempotency_key: str,
        reference_id: Optional[str] = None,
        description: Optional[str] = None,
        commit: bool = True,
    ) -> uuid.UUID:
        """Credits user through OBX Core.
        
        Uses in-process session if available, otherwise executes HTTP request to OBX Core API.
        """
        if self.session is not None:
            # Direct in-process execution without releasing transaction lock prematurely
            service = WalletService(self.session)
            entry = service.credit(
                discord_user_id=discord_user_id,
                amount=amount,
                reference_type=reference_type,
                idempotency_key=idempotency_key,
                reference_id=reference_id,
                description=description,
                commit=commit,
            )
            return entry.id

        # HTTP API call
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "X-Internal-Token": self.auth_token,
        }
        payload = {
            "discord_user_id": discord_user_id,
            "amount": amount,
            "reference_type": reference_type,
            "idempotency_key": idempotency_key,
            "reference_id": reference_id,
            "description": description,
        }

        try:
            with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
                response = client.post("/wallets/credit", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                return uuid.UUID(data["id"])
        except httpx.HTTPStatusError as exc:
            logger.error(
                "OBX Core HTTP error for user %s: %s (Status: %d)",
                discord_user_id,
                exc.response.text,
                exc.response.status_code,
            )
            raise
        except Exception as exc:
            logger.error("Failed to connect to OBX Core API: %s", exc)
            raise
