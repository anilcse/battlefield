"""
Game Engine: runs a competitive 30-day tournament where all configured models
receive the exact same market data and prompt, forecast simultaneously, and
auto-trade when they find edge. PnL is tracked per-model.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.forecast import Forecast
from app.models.market import Market
from app.models.trade import Trade
from app.models.tournament import Tournament, TournamentEntry
from app.services.category_classifier import market_duration_tag
from app.services.model_router import ModelOutput, run_model_inference
from app.services.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

COMPETITION_PROMPT_TEMPLATE = (
    "You are competing in a prediction market forecasting tournament.\n"
    "Your goal is to maximize your portfolio over 30 days starting with ${start_budget}.\n"
    "Your current balance is ${current_balance}. Every decision matters.\n\n"
    "MARKET DETAILS:\n"
    "  Title: {title}\n"
    "  Description: {description}\n"
    "  Category: {category}\n"
    "  Duration type: {duration_tag}\n"
    "  End date: {end_date}\n"
    "  Current YES price: {yes_price}\n"
    "  Current NO price: {no_price}\n"
    "  Market status: {status}\n\n"
    "DECISION REQUIRED:\n"
    "1. First decide if you should trade this market category at all.\n"
    "   Consider: Is this category in your area of expertise? "
    "Do you have an information edge? Is the risk/reward worth it given your remaining balance?\n"
    "2. If yes, provide your probability estimate.\n\n"
    "Return strict JSON only with these keys:\n"
    "  should_trade: boolean (true if you want to trade this market, false to skip)\n"
    "  skip_reason: string (if should_trade is false, explain why you are skipping)\n"
    "  probability_yes: decimal 0-1 (your probability the market resolves YES)\n"
    "  confidence: decimal 0-1 (how confident you are in your estimate)\n"
    "  rationale: string (your reasoning)\n"
)


class GameEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.running = False
        self.polymarket_client = PolymarketClient()

    async def _ensure_active_tournament(self, session: AsyncSession) -> Tournament:
        result = await session.execute(
            select(Tournament).where(Tournament.status == "active").order_by(Tournament.created_at.desc())
        )
        tournament = result.scalar_one_or_none()

        if tournament is not None:
            if datetime.now(tz=timezone.utc) > tournament.ends_at:
                tournament.status = "completed"
                await self._rank_entries(session, tournament.id)
                await session.commit()
                logger.info("Tournament %s completed", tournament.id)
                tournament = None

        if tournament is None:
            now = datetime.now(tz=timezone.utc)
            tournament = Tournament(
                name=f"Tournament {now.strftime('%Y-%m-%d')}",
                status="active",
                duration_days=self.settings.tournament_duration_days,
                start_budget_usd=self.settings.tournament_start_budget_usd,
                started_at=now,
                ends_at=now + timedelta(days=self.settings.tournament_duration_days),
            )
            session.add(tournament)
            await session.flush()

            for model_name in self.settings.model_names:
                entry = TournamentEntry(
                    tournament_id=tournament.id,
                    model_name=model_name,
                    starting_balance_usd=self.settings.tournament_start_budget_usd,
                    current_balance_usd=self.settings.tournament_start_budget_usd,
                )
                session.add(entry)
            await session.commit()
            await session.refresh(tournament)
            logger.info("New tournament started: id=%s ends=%s", tournament.id, tournament.ends_at)

        return tournament

    async def _rank_entries(self, session: AsyncSession, tournament_id: int) -> None:
        result = await session.execute(
            select(TournamentEntry)
            .where(TournamentEntry.tournament_id == tournament_id)
            .order_by(TournamentEntry.current_balance_usd.desc())
        )
        entries = list(result.scalars().all())
        for rank, entry in enumerate(entries, start=1):
            entry.rank = rank

    async def _get_entry(self, session: AsyncSession, tournament_id: int, model_name: str) -> Optional[TournamentEntry]:
        result = await session.execute(
            select(TournamentEntry).where(
                TournamentEntry.tournament_id == tournament_id,
                TournamentEntry.model_name == model_name,
            )
        )
        return result.scalar_one_or_none()

    async def _run_round(self) -> None:
        async with SessionLocal() as session:
            tournament = await self._ensure_active_tournament(session)

            result = await session.execute(select(Market).where(Market.status == "open"))
            markets = list(result.scalars().all())

            if not markets:
                logger.info("Game engine: no open markets to forecast")
                return

            logger.info("Game engine: scanning %d open market(s) for forecasts and trades", len(markets))
            for market in markets:
                duration_tag = market_duration_tag(market.title, market.description, market.end_date)

                tradeable_forecasts: dict[str, ModelOutput] = {}
                for model_name in self.settings.model_names:
                    entry = await self._get_entry(session, tournament.id, model_name)
                    if entry is None or entry.current_balance_usd <= 0:
                        continue

                    context = COMPETITION_PROMPT_TEMPLATE.format(
                        start_budget=self.settings.tournament_start_budget_usd,
                        current_balance=round(entry.current_balance_usd, 2),
                        title=market.title,
                        description=market.description,
                        category=market.category,
                        duration_tag=duration_tag,
                        end_date=market.end_date or "unknown",
                        yes_price=market.yes_price,
                        no_price=market.no_price,
                        status=market.status,
                    )

                    try:
                        output = await run_model_inference(
                            db=session,
                            model_name=model_name,
                            market_title=market.title,
                            market_context=context,
                        )

                        forecast_row = Forecast(
                            market_id=market.id,
                            model_name=model_name,
                            probability_yes=output.probability_yes,
                            confidence=output.confidence,
                            rationale=output.rationale,
                            cost_usd=output.cost_usd,
                        )
                        session.add(forecast_row)
                        entry.total_forecasts += 1
                        entry.current_balance_usd -= output.cost_usd

                        should_trade = output.should_trade
                        if not should_trade:
                            logger.info(
                                "Game skip: model=%s market=%s category=%s reason=%s",
                                model_name, market.id, market.category,
                                output.skip_reason or "model declined",
                            )
                            continue

                        tradeable_forecasts[model_name] = output

                    except Exception as exc:
                        logger.warning("Game engine: model %s forecast failed on market %s: %s", model_name, market.id, exc)

                await session.commit()

                for model_name, output in tradeable_forecasts.items():
                    entry = await self._get_entry(session, tournament.id, model_name)
                    if entry is None or entry.current_balance_usd <= self.settings.game_trade_size_usd * 0.5:
                        continue

                    edge_yes = output.probability_yes - float(market.yes_price)
                    edge_no = (1.0 - output.probability_yes) - float(market.no_price)

                    side: Optional[str] = None
                    price: float = 0.0
                    if edge_yes >= self.settings.game_edge_threshold and output.confidence >= 0.5:
                        side = "YES"
                        price = float(market.yes_price)
                    elif edge_no >= self.settings.game_edge_threshold and output.confidence >= 0.5:
                        side = "NO"
                        price = float(market.no_price)

                    if side is None or price <= 0:
                        continue

                    trade_notional = min(self.settings.game_trade_size_usd, entry.current_balance_usd)
                    quantity = trade_notional / price

                    try:
                        remote = await self.polymarket_client.place_order(
                            model_name=model_name,
                            market_id=market.polymarket_market_id,
                            side=side,
                            quantity=quantity,
                            price=price,
                        )
                        trade = Trade(
                            market_id=market.id,
                            model_name=model_name,
                            side=side,
                            quantity=quantity,
                            price=price,
                            status=remote.get("status", "submitted"),
                            source=remote.get("source", "game"),
                            external_order_id=remote.get("external_order_id", ""),
                        )
                        session.add(trade)

                        entry.total_trades += 1
                        entry.current_balance_usd -= trade_notional
                        entry.unrealized_pnl_usd += quantity * (
                            (float(market.yes_price) - price) if side == "YES" else (float(market.no_price) - price)
                        )

                        logger.info(
                            "Game trade: model=%s market=%s cat=%s side=%s qty=%.4f price=%.4f edge=%.4f",
                            model_name, market.id, market.category, side, quantity, price,
                            edge_yes if side == "YES" else edge_no,
                        )
                    except Exception as exc:
                        logger.warning("Game engine: trade failed model=%s market=%s: %s", model_name, market.id, exc)

                await session.commit()

            await self._rank_entries(session, tournament.id)
            await session.commit()

    async def _run_forever(self) -> None:
        while self.running:
            try:
                logger.info("Game engine: starting round (scanning markets, running models, executing trades)")
                await self._run_round()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Game engine loop error: %s", exc)
            if self.running:
                logger.info("Game engine: round complete; next round in %s seconds", self.settings.game_loop_interval_seconds)
                await asyncio.sleep(self.settings.game_loop_interval_seconds)


_game_engine: Optional[GameEngine] = None
_game_task: Optional[asyncio.Task] = None


async def start_game_engine() -> None:
    global _game_engine, _game_task
    settings = get_settings()
    if not settings.game_loop_enabled:
        return
    if _game_engine is not None:
        return
    _game_engine = GameEngine()
    _game_engine.running = True
    _game_task = asyncio.create_task(_game_engine._run_forever())
    logger.info(
        "Game engine started (interval=%ss, models=%s)",
        settings.game_loop_interval_seconds,
        len(settings.model_names),
    )


async def stop_game_engine() -> None:
    global _game_engine, _game_task
    if _game_engine is None:
        return
    _game_engine.running = False
    if _game_task is not None:
        _game_task.cancel()
        try:
            await _game_task
        except asyncio.CancelledError:
            pass
        _game_task = None
    _game_engine = None
