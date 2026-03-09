import asyncio
import logging
import threading
import time
from typing import Dict, List, Optional, Set

from eth_account import Account
from eth_account.messages import encode_defunct
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from sqlalchemy import select
from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware
except Exception:  # pragma: no cover
    ExtraDataToPOAMiddleware = None
try:
    from web3.middleware import geth_poa_middleware
except Exception:  # pragma: no cover
    geth_poa_middleware = None

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.auto_claim import AutoClaim

logger = logging.getLogger(__name__)

ERC1155_MIN_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]

CONDITIONAL_TOKENS_MIN_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "collateralToken", "type": "address"},
            {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

SAFE_MIN_ABI = [
    {
        "inputs": [],
        "name": "nonce",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "bytes", "name": "data", "type": "bytes"},
            {"internalType": "uint8", "name": "operation", "type": "uint8"},
            {"internalType": "uint256", "name": "safeTxGas", "type": "uint256"},
            {"internalType": "uint256", "name": "baseGas", "type": "uint256"},
            {"internalType": "uint256", "name": "gasPrice", "type": "uint256"},
            {"internalType": "address", "name": "gasToken", "type": "address"},
            {"internalType": "address payable", "name": "refundReceiver", "type": "address"},
            {"internalType": "uint256", "name": "_nonce", "type": "uint256"},
        ],
        "name": "getTransactionHash",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "bytes", "name": "data", "type": "bytes"},
            {"internalType": "enum Enum.Operation", "name": "operation", "type": "uint8"},
            {"internalType": "uint256", "name": "safeTxGas", "type": "uint256"},
            {"internalType": "uint256", "name": "baseGas", "type": "uint256"},
            {"internalType": "uint256", "name": "gasPrice", "type": "uint256"},
            {"internalType": "address", "name": "gasToken", "type": "address"},
            {"internalType": "address payable", "name": "refundReceiver", "type": "address"},
            {"internalType": "bytes", "name": "signatures", "type": "bytes"},
        ],
        "name": "execTransaction",
        "outputs": [{"internalType": "bool", "name": "success", "type": "bool"}],
        "stateMutability": "payable",
        "type": "function",
    },
]


def _build_client_for_model(settings: Settings, model_name: str) -> Optional[ClobClient]:
    account_cfg = settings.get_model_account(model_name)
    private_key = (account_cfg.get("private_key") or "").strip()
    if not private_key:
        return None
    try:
        sig_type = int(account_cfg.get("signature_type", settings.polymarket_signature_type))
        funder = account_cfg.get("wallet_address") or None
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
    except Exception as exc:
        logger.warning("Auto claimer: could not build client for model %s: %s", model_name, exc)
        return None


def _extract_condition_ids_from_trades(trades_payload: object) -> Set[str]:
    out: Set[str] = set()
    rows: List[Dict] = []
    if isinstance(trades_payload, list):
        rows = trades_payload
    elif isinstance(trades_payload, dict):
        rows = trades_payload.get("data") or []
    for trade in rows:
        market = trade.get("market")
        if isinstance(market, str) and market.startswith("0x") and len(market) == 66:
            out.add(market)
    return out


def _winning_tokens(market: Dict) -> List[Dict]:
    tokens = market.get("tokens") or []
    winners: List[Dict] = []
    for idx, token in enumerate(tokens):
        if token.get("winner") is True:
            token_id = token.get("token_id")
            if token_id:
                winners.append({"token_id": str(token_id), "index_set": idx + 1, "outcome": token.get("outcome")})
    return winners


def _connect_web3(settings: Settings) -> Optional[Web3]:
    url = (settings.polygon_rpc_url or "").strip()
    if not url:
        return None
    try:
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
        if ExtraDataToPOAMiddleware is not None:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        elif geth_poa_middleware is not None:
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        if w3.is_connected() and w3.eth.chain_id == settings.polymarket_chain_id:
            return w3
    except Exception:
        return None
    return None


class AutoClaimer:
    def __init__(self, loop_seconds: int = 600):
        self.loop_seconds = loop_seconds
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.settings = get_settings()

    async def _claimed_before(self, condition_id: str, index_set: int, model_name: str) -> bool:
        async with SessionLocal() as session:
            result = await session.execute(
                select(AutoClaim).where(
                    AutoClaim.condition_id == condition_id,
                    AutoClaim.index_set == index_set,
                    AutoClaim.model_name == model_name,
                    AutoClaim.status == "claimed",
                )
            )
            return result.scalar_one_or_none() is not None

    async def _record_claim(
        self,
        condition_id: str,
        index_set: int,
        model_name: str,
        token_id: str,
        amount_redeemed: str,
        status: str,
        tx_hash: str = "",
        error: str = "",
    ) -> None:
        async with SessionLocal() as session:
            result = await session.execute(
                select(AutoClaim).where(
                    AutoClaim.condition_id == condition_id,
                    AutoClaim.index_set == index_set,
                    AutoClaim.model_name == model_name,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = AutoClaim(
                    condition_id=condition_id,
                    index_set=index_set,
                    model_name=model_name,
                    token_id=token_id,
                    amount_redeemed=amount_redeemed,
                    tx_hash=tx_hash,
                    status=status,
                    error=error,
                )
                session.add(row)
            else:
                row.token_id = token_id
                row.amount_redeemed = amount_redeemed
                row.tx_hash = tx_hash
                row.status = status
                row.error = error
            await session.commit()

    async def _credit_tournament_balance(self, model_name: str, amount_str: str) -> None:
        """Credit redeemed USDC back to the active tournament entry for this model."""
        try:
            amount = float(amount_str) / 1e6  # USDC has 6 decimals
            if amount <= 0:
                return
        except (ValueError, TypeError):
            return
        try:
            async with SessionLocal() as session:
                from app.models.tournament import Tournament, TournamentEntry
                t_result = await session.execute(
                    select(Tournament).where(Tournament.status == "active").order_by(Tournament.created_at.desc())
                )
                tournament = t_result.scalar_one_or_none()
                if not tournament:
                    return
                e_result = await session.execute(
                    select(TournamentEntry).where(
                        TournamentEntry.tournament_id == tournament.id,
                        TournamentEntry.model_name == model_name,
                    )
                )
                entry = e_result.scalar_one_or_none()
                if entry:
                    entry.current_balance_usd += amount
                    entry.realized_pnl_usd += amount
                    logger.info(
                        "Auto claim credited $%.2f to tournament balance for model=%s (new bal=$%.2f)",
                        amount, model_name, entry.current_balance_usd,
                    )
                    await session.commit()
        except Exception as exc:
            logger.warning("Auto claim: failed to credit tournament balance for %s: %s", model_name, exc)

    def _loop(self) -> None:
        models_with_key = [
            name for name in self.settings.model_names
            if (self.settings.get_model_account(name).get("private_key") or "").strip()
        ]
        if not models_with_key:
            logger.warning("Auto claimer disabled: no model in MODEL_ACCOUNT_CONFIGS has private_key set")
            return

        null_addr = Web3.to_checksum_address("0x0000000000000000000000000000000000000000")

        while self.running:
            try:
                logger.info("Auto claimer: scanning for claimable positions (models: %s)", ", ".join(models_with_key))
                w3 = _connect_web3(self.settings)
                if not w3:
                    logger.error("Auto claimer: cannot connect to POLYGON_RPC_URL from .env")
                    time.sleep(self.loop_seconds)
                    continue

                for model_name in models_with_key:
                    logger.info("Auto claimer: scanning trades for model=%s", model_name)
                    client = _build_client_for_model(self.settings, model_name)
                    if not client:
                        continue
                    account_cfg = self.settings.get_model_account(model_name)
                    account = Account.from_key(account_cfg["private_key"])
                    signer = Web3.to_checksum_address(account.address)  # EOA derived from private_key
                    wallet = Web3.to_checksum_address(account_cfg["wallet_address"]) if account_cfg.get("wallet_address") else None  # Polymarket proxy (positions live here; can differ from signer)

                    conditional_addr = Web3.to_checksum_address(client.get_conditional_address())
                    collateral_addr = Web3.to_checksum_address(client.get_collateral_address())
                    conditional = w3.eth.contract(address=conditional_addr, abi=CONDITIONAL_TOKENS_MIN_ABI)
                    erc1155 = w3.eth.contract(address=conditional_addr, abi=ERC1155_MIN_ABI)
                    safe_contract = w3.eth.contract(address=wallet, abi=SAFE_MIN_ABI) if wallet else None

                    condition_ids: Set[str] = set()
                    try:
                        trades = client.get_trades()
                        condition_ids |= _extract_condition_ids_from_trades(trades)
                    except Exception as exc:
                        logger.error("Auto claimer get_trades failed for %s: %s", model_name, exc)
                        continue

                    for condition_id in condition_ids:
                        try:
                            market = client.get_market(condition_id)
                        except Exception:
                            continue
                        if not market or not market.get("closed", False):
                            continue

                        winners = _winning_tokens(market)
                        for winner in winners:
                            token_id = winner["token_id"]
                            index_set = int(winner["index_set"])
                            if asyncio.run(self._claimed_before(condition_id, index_set, model_name)):
                                continue

                            signer_balance = erc1155.functions.balanceOf(signer, int(token_id)).call()
                            wallet_balance = 0
                            if wallet and wallet.lower() != signer.lower():
                                wallet_balance = erc1155.functions.balanceOf(wallet, int(token_id)).call()

                            use_safe_flow = bool(wallet and wallet.lower() != signer.lower() and int(wallet_balance) > 0)
                            if int(signer_balance) <= 0 and not use_safe_flow:
                                continue

                            try:
                                nonce = w3.eth.get_transaction_count(account.address, "pending")
                                redeem_data_hex = conditional.functions.redeemPositions(
                                    collateral_addr, b"\x00" * 32, bytes.fromhex(condition_id[2:]), [index_set]
                                )._encode_transaction_data()
                                redeem_data = bytes.fromhex(redeem_data_hex[2:])

                                if use_safe_flow and safe_contract is not None:
                                    safe_nonce = safe_contract.functions.nonce().call()
                                    safe_tx_hash = safe_contract.functions.getTransactionHash(
                                        conditional_addr,
                                        0,
                                        redeem_data,
                                        0,
                                        0,
                                        0,
                                        0,
                                        null_addr,
                                        null_addr,
                                        safe_nonce,
                                    ).call()
                                    signed_safe = account.sign_message(encode_defunct(hexstr=safe_tx_hash.hex()))
                                    safe_v = signed_safe.v + 4 if signed_safe.v in (27, 28) else signed_safe.v
                                    safe_signature = (
                                        int(signed_safe.r).to_bytes(32, "big")
                                        + int(signed_safe.s).to_bytes(32, "big")
                                        + bytes([safe_v])
                                    )

                                    tx = safe_contract.functions.execTransaction(
                                        conditional_addr,
                                        0,
                                        redeem_data,
                                        0,
                                        0,
                                        0,
                                        0,
                                        null_addr,
                                        null_addr,
                                        safe_signature,
                                    ).build_transaction(
                                        {
                                            "from": account.address,
                                            "nonce": nonce,
                                            "chainId": self.settings.polymarket_chain_id,
                                        }
                                    )
                                    amount_redeemed = str(wallet_balance)
                                else:
                                    tx = conditional.functions.redeemPositions(
                                        collateral_addr,
                                        b"\x00" * 32,
                                        bytes.fromhex(condition_id[2:]),
                                        [index_set],
                                    ).build_transaction(
                                        {
                                            "from": account.address,
                                            "nonce": nonce,
                                            "chainId": self.settings.polymarket_chain_id,
                                        }
                                    )
                                    amount_redeemed = str(signer_balance)

                                gas_estimate = w3.eth.estimate_gas(tx)
                                tx["gas"] = int(gas_estimate * 1.2)
                                tx["maxFeePerGas"] = w3.eth.gas_price
                                tx["maxPriorityFeePerGas"] = min(w3.eth.gas_price, Web3.to_wei(40, "gwei"))

                                signed = account.sign_transaction(tx)
                                raw_tx = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
                                tx_hash = w3.eth.send_raw_transaction(raw_tx)
                                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
                                status = "claimed" if receipt.status == 1 else "failed"
                                tx_hash_hex = receipt.transactionHash.hex()

                                asyncio.run(
                                    self._record_claim(
                                        condition_id=condition_id,
                                        index_set=index_set,
                                        model_name=model_name,
                                        token_id=token_id,
                                        amount_redeemed=amount_redeemed,
                                        status=status,
                                        tx_hash=tx_hash_hex,
                                        error="" if status == "claimed" else "redeem tx reverted",
                                    )
                                )
                                if status == "claimed":
                                    logger.info(
                                        "Auto claimer claimed: model=%s condition=%s index_set=%s amount=%s tx=%s",
                                        model_name,
                                        condition_id,
                                        index_set,
                                        amount_redeemed,
                                        tx_hash_hex,
                                    )
                                    asyncio.run(self._credit_tournament_balance(model_name, amount_redeemed))
                            except Exception as claim_exc:
                                asyncio.run(
                                    self._record_claim(
                                        condition_id=condition_id,
                                        index_set=index_set,
                                        model_name=model_name,
                                        token_id=token_id,
                                        amount_redeemed=str(wallet_balance if use_safe_flow else signer_balance),
                                        status="failed",
                                        tx_hash="",
                                        error=str(claim_exc),
                                    )
                                )
                                logger.error("Auto claimer claim failed for %s %s: %s", model_name, condition_id, claim_exc)
            except Exception as loop_exc:
                logger.error("Auto claimer loop error: %s", loop_exc)

            time.sleep(self.loop_seconds)

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="auto-claimer")
        self._thread.start()
        logger.info("Auto claimer started (interval=%ss)", self.loop_seconds)

    def stop(self) -> None:
        self.running = False


_auto_claimer: Optional[AutoClaimer] = None


def start_auto_claimer() -> None:
    global _auto_claimer
    settings = get_settings()
    if not settings.auto_claim_enabled:
        return
    if _auto_claimer is None:
        _auto_claimer = AutoClaimer(loop_seconds=settings.auto_claim_interval_seconds)
    _auto_claimer.start()


def stop_auto_claimer() -> None:
    if _auto_claimer is None:
        return
    _auto_claimer.stop()
