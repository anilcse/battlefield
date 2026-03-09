import asyncio
import logging
from typing import Any, Dict, Optional

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderType
from py_clob_client.order_builder.constants import BUY
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _build_clob_client(settings, model_account: Dict[str, str]) -> ClobClient:
    """Build a ClobClient with key and derived/configured API creds (sync)."""
    private_key = (model_account.get("private_key") or "").strip()
    if not private_key:
        raise ValueError("private_key required to build ClobClient")
    client = ClobClient(
        host=settings.polymarket_base_url,
        chain_id=settings.polymarket_chain_id,
        key=private_key,
        signature_type=2,
        funder=model_account.get("wallet_address") or None,
    )
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client


class PolymarketClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.polymarket_base_url.rstrip("/")
        self._model_api_creds_cache: dict[str, ApiCreds] = {}
        self._clob_client_cache: dict[str, ClobClient] = {}

    def _derive_model_api_creds(self, model_name: str, model_account: Dict[str, str]) -> ApiCreds:
        private_key = (model_account.get("private_key") or "").strip()
        if not private_key:
            raise ValueError(f"Missing private_key to derive Polymarket API credentials for model: {model_name}")
        client = ClobClient(
            host=self.settings.polymarket_base_url,
            chain_id=self.settings.polymarket_chain_id,
            key=private_key,
            signature_type=2,
            funder=model_account.get("wallet_address") or None,
        )
        return client.create_or_derive_api_creds()

    def _resolve_model_api_creds(self, model_name: str, model_account: Dict[str, str]) -> ApiCreds:
        cached = self._model_api_creds_cache.get(model_name)
        if cached is not None:
            return cached

        api_key = (model_account.get("polymarket_api_key") or "").strip()
        api_secret = (model_account.get("polymarket_secret") or "").strip()
        api_passphrase = (model_account.get("polymarket_passphrase") or "").strip()
        if api_key and api_secret and api_passphrase:
            creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
        else:
            creds = self._derive_model_api_creds(model_name, model_account)
        self._model_api_creds_cache[model_name] = creds
        return creds

    @retry(wait=wait_exponential(min=1, max=8), stop=stop_after_attempt(3))
    async def fetch_markets(self, limit: int = 25) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{self.base_url}/markets", params={"limit": limit})
            response.raise_for_status()
            return response.json()

    def _get_clob_client(self, model_name: str) -> ClobClient:
        if model_name not in self._clob_client_cache:
            model_account = self.settings.get_model_account(model_name)
            self._clob_client_cache[model_name] = _build_clob_client(self.settings, model_account)
        return self._clob_client_cache[model_name]

    def _place_order_via_clob_sync(
        self, model_name: str, token_id: str, side: str, quantity: float, price: float
    ) -> Dict[str, Any]:
        """Place order using py_clob_client create_and_post_order (sync, run in executor)."""
        from py_clob_client.clob_types import OrderArgs

        client = self._get_clob_client(model_name)
        try:
            tick_size = client.get_tick_size(token_id)
            tick_size = str(tick_size) if tick_size is not None else "0.01"
        except Exception:
            tick_size = "0.01"
        try:
            neg_risk = bool(client.get_neg_risk(token_id))
        except Exception:
            neg_risk = False
        order_args = OrderArgs(
            token_id=token_id,
            price=round(float(price), 4),
            size=round(float(quantity), 4),
            side=BUY,
        )
        response = client.create_and_post_order(
            order_args,
            options={"tick_size": tick_size, "neg_risk": neg_risk},
            order_type=OrderType.GTC,
        )
        if isinstance(response, dict):
            response.setdefault("source", f"live:{model_name}")
            response["external_order_id"] = response.get("orderID") or response.get("order_id") or ""
            response["status"] = response.get("status") or "submitted"
            return response
        return {"status": "submitted", "source": f"live:{model_name}", "external_order_id": str(response)}

    async def place_order(
        self,
        *,
        model_name: str,
        market_id: str,
        side: str,
        quantity: float,
        price: float,
        token_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.settings.enable_live_trading:
            return {
                "status": "simulated",
                "source": "paper",
                "external_order_id": "",
                "details": {
                    "market_id": market_id,
                    "side": side,
                    "quantity": quantity,
                    "price": price,
                },
            }

        model_account = self.settings.get_model_account(model_name)
        if model_name in self.settings.model_names and not self.settings.model_account_configs.get(model_name):
            raise ValueError(f"Missing MODEL_ACCOUNT_CONFIGS entry for model: {model_name}")

        if token_id:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: self._place_order_via_clob_sync(model_name, token_id, side, quantity, price),
            )

        creds = self._resolve_model_api_creds(model_name, model_account)
        payload = {"market_id": market_id, "side": side, "quantity": quantity, "price": price}
        headers = {
            "X-API-KEY": creds.api_key,
            "X-API-SECRET": creds.api_secret,
            "X-API-PASSPHRASE": creds.api_passphrase,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{self.base_url}/orders", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            data["source"] = f"live:{model_name}"
            return data
