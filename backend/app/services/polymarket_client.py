import asyncio
import logging
from typing import Any, Dict, Optional

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from py_clob_client.order_builder.constants import BUY
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _build_clob_client(settings, model_account: Dict[str, str]) -> ClobClient:
    """Build a ClobClient with key and derived/configured API creds (sync)."""
    private_key = (model_account.get("private_key") or "").strip()
    if not private_key:
        raise ValueError("private_key required to build ClobClient")
    sig_type = int(model_account.get("signature_type", settings.polymarket_signature_type))
    funder = model_account.get("wallet_address") or None
    client = ClobClient(
        host=settings.polymarket_base_url,
        chain_id=settings.polymarket_chain_id,
        key=private_key,
        signature_type=sig_type,
        funder=funder if sig_type > 0 else None,
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
        self._allowance_done: set[str] = set()

    def _derive_model_api_creds(self, model_name: str, model_account: Dict[str, str]) -> ApiCreds:
        private_key = (model_account.get("private_key") or "").strip()
        if not private_key:
            raise ValueError(f"Missing private_key to derive Polymarket API credentials for model: {model_name}")
        sig_type = int(model_account.get("signature_type", self.settings.polymarket_signature_type))
        funder = model_account.get("wallet_address") or None
        client = ClobClient(
            host=self.settings.polymarket_base_url,
            chain_id=self.settings.polymarket_chain_id,
            key=private_key,
            signature_type=sig_type,
            funder=funder if sig_type > 0 else None,
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
    async def fetch_markets(self, limit: int = 100) -> list[Dict[str, Any]]:
        """
        Fetch active, open markets via the Gamma /events endpoint.
        Events contain nested markets[]; we flatten and return only
        sub-markets that are active AND not closed.
        """
        url = "https://gamma-api.polymarket.com/events"
        params = {"active": "true", "closed": "false", "limit": limit}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            events = response.json()
            if not isinstance(events, list):
                events = events.get("data", []) if isinstance(events, dict) else []

        flat_markets: list[Dict[str, Any]] = []
        for event in events:
            sub_markets = event.get("markets") or []
            for m in sub_markets:
                if m.get("active") and not m.get("closed"):
                    m.setdefault("event_title", event.get("title", ""))
                    flat_markets.append(m)
        logger.info("Gamma API: %d events → %d open sub-markets", len(events), len(flat_markets))
        return flat_markets

    def _get_clob_client(self, model_name: str) -> ClobClient:
        if model_name not in self._clob_client_cache:
            model_account = self.settings.get_model_account(model_name)
            self._clob_client_cache[model_name] = _build_clob_client(self.settings, model_account)
        return self._clob_client_cache[model_name]

    def _ensure_allowances_sync(self, model_name: str, client: ClobClient, token_id: str | None = None) -> None:
        """Set USDC (collateral) and conditional-token allowances if needed (once per model)."""
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        cache_key = model_name
        if cache_key in self._allowance_done:
            return
        try:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            allowance_data = client.get_balance_allowance(params)
            allowance_val = int(allowance_data.get("allowance", 0)) if isinstance(allowance_data, dict) else 0
            if allowance_val < 10 ** 18:
                logger.info("Setting COLLATERAL allowance for %s...", model_name)
                client.update_balance_allowance(params)
        except Exception as exc:
            logger.warning("COLLATERAL allowance failed for %s: %s", model_name, exc)

        if token_id:
            try:
                params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
                allowance_data = client.get_balance_allowance(params)
                allowance_val = int(allowance_data.get("allowance", 0)) if isinstance(allowance_data, dict) else 0
                if allowance_val < 10 ** 18:
                    client.update_balance_allowance(params)
            except Exception as exc:
                logger.warning("CONDITIONAL allowance failed for %s: %s", model_name, exc)

        self._allowance_done.add(cache_key)

    def _place_order_via_clob_sync(
        self, model_name: str, token_id: str, side: str, quantity: float, price: float
    ) -> Dict[str, Any]:
        """Place order using py_clob_client create_and_post_order (sync, run in executor)."""
        from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions

        client = self._get_clob_client(model_name)

        self._ensure_allowances_sync(model_name, client, token_id=token_id)

        try:
            tick_size = client.get_tick_size(token_id)
            tick_size = str(tick_size) if tick_size is not None else "0.01"
        except Exception:
            tick_size = "0.01"
        try:
            neg_risk = bool(client.get_neg_risk(token_id))
        except Exception:
            neg_risk = False

        rounded_price = round(float(price), 4)
        rounded_size = round(float(quantity), 4)
        if rounded_size < 1:
            rounded_size = 1.0

        order_args = OrderArgs(
            token_id=token_id,
            price=rounded_price,
            size=rounded_size,
            side=BUY,
        )
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)
        response = client.create_and_post_order(order_args, options=options)
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
        if model_name in self.settings.model_names and not (model_account.get("private_key") or "").strip():
            raise ValueError(f"Missing private_key for model {model_name} in MODEL_ACCOUNT_CONFIGS")

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
