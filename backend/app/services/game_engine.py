"""
Game Engine v3: Autonomous LLM trading competition on Polymarket.

Each agent:
  - Has a unique persona / strategy style
  - Sees its own trade history, portfolio, AND competitor standings
  - Independently selects which markets to trade (from a diverse shuffled pool)
  - Can trade multiple markets per round (up to 3)
  - Gets eliminated if underperforming at checkpoints
  - Ranking uses composite score: 60% profit + 40% volume
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.forecast import Forecast
from app.models.market import Market
from app.models.trade import Trade
from app.models.tournament import Tournament, TournamentEntry
from app.services.category_classifier import classify_market, market_duration_tag

# Categories with frequent orderbook changes — prioritize for trading
PRIORITY_CATEGORIES = frozenset({"sports", "weather", "celebrities", "crypto", "crypto_short_term", "economics"})
from app.services.model_router import run_model_inference
from app.services.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

AGENT_PERSONAS = {
    "openai/gpt-5": {
        "style": "Quantitative Analyst",
        "system": (
            "You are a quantitative analyst competing in a prediction market tournament. "
            "You excel at statistical reasoning and finding mispriced markets. "
            "You prefer markets where public sentiment diverges from base rates. "
            "You trade frequently and diversely. Return strict JSON only."
        ),
    },
    "anthropic/claude-sonnet-4": {
        "style": "Contrarian Strategist",
        "system": (
            "You are a contrarian strategist competing in a prediction market tournament. "
            "You look for markets where the crowd is likely wrong. "
            "You thrive on politics, social media events, and celebrity predictions. "
            "Be bold and trade actively. Return strict JSON only."
        ),
    },
    "x-ai/grok-4": {
        "style": "News & Social Alpha Trader",
        "system": (
            "You are a news-driven trader competing in a prediction market tournament. "
            "You specialise in Elon Musk tweets, tech events, breaking news, and crypto. "
            "You move fast and trade often across multiple markets. Return strict JSON only."
        ),
    },
    "google/gemini-3.1-pro-preview": {
        "style": "Diversified Portfolio Manager",
        "system": (
            "You are a diversified portfolio manager competing in a prediction market tournament. "
            "You spread risk across many markets and categories — politics, weather, crypto, sports. "
            "You never put more than 15% of balance in a single market. Return strict JSON only."
        ),
    },
    "deepseek/deepseek-v3.2-speciale": {
        "style": "High-Frequency Value Seeker",
        "system": (
            "You are a high-frequency value seeker competing in a prediction market tournament. "
            "You hunt for even small edges and trade them aggressively. "
            "You prefer short-term and crypto markets but will trade anything with an edge. "
            "Maximise trade count and volume. Return strict JSON only."
        ),
    },
}

DEFAULT_PERSONA = {
    "style": "Adaptive Trader",
    "system": (
        "You are an adaptive prediction market trader competing in a tournament. "
        "Analyse each market, develop your own strategy, and trade frequently "
        "across diverse categories. Return strict JSON only."
    ),
}

MIN_AGENTS_BEFORE_ELIMINATION = 3
ELIMINATION_BALANCE_THRESHOLD_PCT = 0.4
MAX_TRADES_PER_ROUND = 3


def _get_persona(model_name: str) -> dict:
    return AGENT_PERSONAS.get(model_name, DEFAULT_PERSONA)


def _build_agent_prompt(
    *,
    model_name: str,
    persona: dict,
    start_budget: float,
    current_balance: float,
    total_trades: int,
    total_volume: float,
    recent_trades: list[dict],
    held_market_ids: set[int],
    available_markets: list[dict],
    tournament_days_remaining: int,
    competitor_summary: str,
    resolved_positions_summary: str = "",
) -> str:
    trade_history_str = "None yet — you should start trading!" if not recent_trades else "\n".join(
        f"  - {t['side']} on \"{t['title']}\" @ ${t['price']:.2f}, qty {t['qty']:.2f} ({t['category']}) {t.get('resolution_tag', '[OPEN]')}"
        for t in recent_trades[-15:]
    )

    resolved_section = ""
    if resolved_positions_summary and resolved_positions_summary.strip() != "  None":
        resolved_section = f"=== RESOLVED MARKETS (your positions) ===\n{resolved_positions_summary}\n\n"

    markets_str = "\n".join(
        f"  [{i+1}] \"{m['title']}\" | Category: {m['category']} | Duration: {m['duration_tag']} "
        f"| YES: ${m['yes_price']:.2f} | NO: ${m['no_price']:.2f} | End: {m['end_date'] or 'unknown'} | Status: OPEN"
        + (" [ALREADY HELD]" if m["id"] in held_market_ids else "")
        for i, m in enumerate(available_markets)
    )

    return (
        f"You are agent \"{model_name}\" — a {persona['style']}.\n\n"
        f"=== TOURNAMENT STATUS ===\n"
        f"  Starting budget: ${start_budget:.0f}\n"
        f"  Your current balance: ${current_balance:.2f}\n"
        f"  Your total trades: {total_trades}\n"
        f"  Your total volume: ${total_volume:.2f}\n"
        f"  Days remaining: {tournament_days_remaining}\n"
        f"  Scoring: Profit (60%) + Volume (40%). Low performers get ELIMINATED!\n\n"
        f"=== COMPETITOR STANDINGS ===\n{competitor_summary}\n\n"
        f"=== YOUR RECENT TRADES ===\n{trade_history_str}\n"
        f"(Tags: [OPEN]=still trading; [RESOLVED: X won — YOU WON/LOST]=outcome known)\n\n"
        f"{resolved_section}"
        f"=== AVAILABLE MARKETS ({len(available_markets)}) ===\n{markets_str}\n\n"
        f"=== YOUR TASK ===\n"
        f"Pick UP TO {MAX_TRADES_PER_ROUND} markets to trade. You MUST trade at least 1 unless "
        f"you have very good reason not to. Volume matters for your score!\n"
        f"For markets marked [ALREADY HELD], only trade if you want to add to your position.\n"
        f"Consider your persona, competitors' positions, your balance, and time remaining.\n\n"
        f"Return strict JSON with key \"trades\" containing an array of trade objects.\n"
        f"Each trade object must have:\n"
        f"  market_index: integer (1-based index from list above)\n"
        f"  side: \"YES\" or \"NO\"\n"
        f"  size_usd: decimal (how much USD to risk on this trade, min 1.0, max {min(15, current_balance):.2f})\n"
        f"  confidence: decimal 0-1\n"
        f"  rationale: string (brief reasoning)\n\n"
        f"If you truly want to skip ALL markets, return: {{\"trades\": [], \"skip_reason\": \"...\"}}\n"
        f"Example: {{\"trades\": [{{\"market_index\": 3, \"side\": \"YES\", \"size_usd\": 5.0, "
        f"\"confidence\": 0.7, \"rationale\": \"Underpriced\"}}]}}\n"
    )


def _parse_trades_from_response(raw: dict, num_markets: int, max_balance: float) -> list[dict]:
    """Extract validated trade decisions from LLM JSON response."""
    trades_raw = raw.get("trades", [])
    if not isinstance(trades_raw, list):
        if raw.get("market_index") is not None:
            trades_raw = [raw]
        else:
            return []

    validated = []
    total_allocated = 0.0
    for t in trades_raw:
        if not isinstance(t, dict):
            continue
        try:
            idx = int(t.get("market_index", 0)) - 1
            if idx < 0 or idx >= num_markets:
                continue
            side = str(t.get("side", "YES")).upper()
            if side not in ("YES", "NO"):
                side = "YES"
            size = max(1.0, min(float(t.get("size_usd", 3.0)), max_balance - total_allocated))
            if size < 0.5:
                continue
            confidence = max(0.0, min(1.0, float(t.get("confidence", 0.5))))
            rationale = str(t.get("rationale", ""))[:200]
            validated.append({
                "market_index": idx,
                "side": side,
                "size_usd": round(size, 2),
                "confidence": confidence,
                "rationale": rationale,
            })
            total_allocated += size
            if len(validated) >= MAX_TRADES_PER_ROUND:
                break
        except (TypeError, ValueError):
            continue
    return validated


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
                logger.info("Tournament %s completed — final rankings set", tournament.id)
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
            logger.info("New tournament started: id=%s ends=%s models=%d",
                        tournament.id, tournament.ends_at, len(self.settings.model_names))

        return tournament

    async def _rank_entries(self, session: AsyncSession, tournament_id: int) -> None:
        """Rank active (non-eliminated) entries by composite score: 60% profit + 40% volume."""
        result = await session.execute(
            select(TournamentEntry).where(TournamentEntry.tournament_id == tournament_id)
        )
        all_entries = list(result.scalars().all())

        active = [e for e in all_entries if e.rank != -1]
        eliminated = [e for e in all_entries if e.rank == -1]

        if not active:
            return

        max_balance = max(e.current_balance_usd for e in active)
        max_volume = max((e.total_volume_usd for e in active), default=1.0) or 1.0
        start_b = active[0].starting_balance_usd or 100.0

        scored = []
        for e in active:
            profit_score = (e.current_balance_usd - start_b) / start_b
            volume_score = e.total_volume_usd / max_volume
            composite = 0.6 * profit_score + 0.4 * volume_score
            scored.append((composite, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        for rank, (score, entry) in enumerate(scored, start=1):
            entry.rank = rank

    async def _get_entry(self, session: AsyncSession, tournament_id: int, model_name: str) -> Optional[TournamentEntry]:
        result = await session.execute(
            select(TournamentEntry).where(
                TournamentEntry.tournament_id == tournament_id,
                TournamentEntry.model_name == model_name,
            )
        )
        return result.scalar_one_or_none()

    async def _get_all_entries(self, session: AsyncSession, tournament_id: int) -> list[TournamentEntry]:
        result = await session.execute(
            select(TournamentEntry)
            .where(TournamentEntry.tournament_id == tournament_id)
            .order_by(TournamentEntry.current_balance_usd.desc())
        )
        return list(result.scalars().all())

    def _resolution_tag(self, trade_side: str, market_status: str, yes_price: float, no_price: float) -> str:
        """Return resolution tag for a trade: OPEN or RESOLVED with outcome and win/loss."""
        status_lower = (market_status or "").lower()
        if status_lower not in ("closed", "resolved", "finalized"):
            return "[OPEN]"
        yes_won = float(yes_price) >= 0.95
        no_won = float(no_price) >= 0.95
        if yes_won:
            won = trade_side.upper() == "YES"
            return f"[RESOLVED: YES won — {'YOU WON' if won else 'YOU LOST'}]"
        if no_won:
            won = trade_side.upper() == "NO"
            return f"[RESOLVED: NO won — {'YOU WON' if won else 'YOU LOST'}]"
        return "[RESOLVED: outcome pending]"

    async def _get_recent_trades(self, session: AsyncSession, model_name: str, limit: int = 15) -> list[dict]:
        result = await session.execute(
            select(Trade, Market)
            .join(Market, Trade.market_id == Market.id)
            .where(Trade.model_name == model_name)
            .order_by(Trade.created_at.desc())
            .limit(limit)
        )
        rows = result.all()
        return [
            {
                "side": t.side,
                "title": m.title[:60],
                "price": float(t.price),
                "qty": float(t.quantity),
                "category": m.category,
                "market_id": m.id,
                "resolution_tag": self._resolution_tag(t.side, m.status, float(m.yes_price), float(m.no_price)),
            }
            for t, m in rows
        ]

    async def _get_held_market_ids(self, session: AsyncSession, model_name: str) -> set[int]:
        result = await session.execute(
            select(Trade.market_id).where(Trade.model_name == model_name).distinct()
        )
        return {row[0] for row in result.all()}

    async def _get_resolved_positions_summary(self, session: AsyncSession, model_name: str, limit: int = 10) -> str:
        """Summary of markets the agent traded that have resolved (outcome + win/loss)."""
        result = await session.execute(
            select(Market, Trade)
            .join(Trade, Trade.market_id == Market.id)
            .where(
                Trade.model_name == model_name,
                func.lower(Market.status).in_(["closed", "resolved", "finalized"]),
            )
            .order_by(Trade.created_at.desc())
            .limit(limit * 3)
        )
        rows = result.all()
        seen: set[int] = set()
        lines: list[str] = []
        for m, t in rows:
            if m.id in seen:
                continue
            seen.add(m.id)
            yes_won = float(m.yes_price) >= 0.95
            no_won = float(m.no_price) >= 0.95
            if yes_won:
                outcome = "YES won"
                won = t.side.upper() == "YES"
            elif no_won:
                outcome = "NO won"
                won = t.side.upper() == "NO"
            else:
                outcome = "pending"
                won = False
            result_str = f"YOU {'WON' if won else 'LOST'}" if outcome != "pending" else outcome
            lines.append(f"  - \"{m.title[:50]}\": {outcome} — {result_str}")
            if len(lines) >= limit:
                break
        return "\n".join(lines) if lines else "  None"

    async def _get_model_volume(self, session: AsyncSession, model_name: str, since: datetime) -> float:
        result = await session.execute(
            select(func.coalesce(func.sum(Trade.quantity * Trade.price), 0.0))
            .where(Trade.model_name == model_name, Trade.created_at >= since)
        )
        return float(result.scalar() or 0.0)

    def _build_competitor_summary(self, entries: list[TournamentEntry], current_model: str) -> str:
        lines = []
        for e in entries:
            if e.rank == -1:
                status = "ELIMINATED"
            elif e.model_name == current_model:
                status = "← YOU"
            else:
                status = f"Rank #{e.rank or '?'}"
            profit_pct = ((e.current_balance_usd - e.starting_balance_usd) / e.starting_balance_usd * 100
                          if e.starting_balance_usd > 0 else 0.0)
            lines.append(
                f"  {e.model_name}: ${e.current_balance_usd:.2f} "
                f"({profit_pct:+.1f}%) | {e.total_trades} trades | "
                f"${e.total_volume_usd:.2f} vol | {status}"
            )
        return "\n".join(lines) if lines else "  No competitors yet."

    async def _sync_markets(self, session: AsyncSession) -> None:
        from app.services.startup_seed import _sync_markets_once
        await _sync_markets_once(session)

    async def _resolve_paper_pnl(self, session: AsyncSession, tournament: Tournament) -> None:
        """
        For paper mode: check if any markets resolved and credit/debit the
        tournament balance for trades on those markets.
        A market is 'resolved' if status changed to 'closed'/'resolved' and
        the YES price moved to ~1.0 or ~0.0 (indicating the outcome).
        """
        result = await session.execute(
            select(Market).where(
                func.lower(Market.status).in_(["closed", "resolved", "finalized"])
            )
        )
        resolved_markets = list(result.scalars().all())
        if not resolved_markets:
            return

        for market in resolved_markets:
            yes_won = float(market.yes_price) >= 0.95
            no_won = float(market.no_price) >= 0.95
            if not yes_won and not no_won:
                continue

            result = await session.execute(
                select(Trade).where(
                    Trade.market_id == market.id,
                    Trade.status.in_(["submitted", "simulated"]),
                )
            )
            trades = list(result.scalars().all())
            for trade in trades:
                entry = await self._get_entry(session, tournament.id, trade.model_name)
                if not entry or entry.rank == -1:
                    continue

                won = (trade.side == "YES" and yes_won) or (trade.side == "NO" and no_won)
                if won:
                    payout = float(trade.quantity)
                    entry.current_balance_usd += payout
                    entry.realized_pnl_usd += payout - (float(trade.quantity) * float(trade.price))
                    logger.info("PnL resolved: model=%s market=%s WON +$%.2f", trade.model_name, market.title[:40], payout)
                trade.status = "resolved"

            await session.commit()

    async def _eliminate_underperformers(self, session: AsyncSession, tournament: Tournament) -> None:
        result = await session.execute(
            select(TournamentEntry)
            .where(
                TournamentEntry.tournament_id == tournament.id,
                TournamentEntry.rank != -1,
            )
            .order_by(TournamentEntry.current_balance_usd.desc())
        )
        active_entries = list(result.scalars().all())
        if len(active_entries) <= MIN_AGENTS_BEFORE_ELIMINATION:
            return

        worst = active_entries[-1]
        loss_pct = (worst.starting_balance_usd - worst.current_balance_usd) / worst.starting_balance_usd
        if loss_pct >= ELIMINATION_BALANCE_THRESHOLD_PCT:
            worst.rank = -1
            logger.info(
                "ELIMINATED: model=%s balance=$%.2f (lost %.0f%% of $%.0f)",
                worst.model_name, worst.current_balance_usd,
                loss_pct * 100, worst.starting_balance_usd,
            )

    async def _run_agent_round(
        self,
        session: AsyncSession,
        tournament: Tournament,
        model_name: str,
        market_pool: list[dict],
        all_entries: list[TournamentEntry],
    ) -> None:
        entry = await self._get_entry(session, tournament.id, model_name)
        if entry is None or entry.current_balance_usd <= 1.0 or entry.rank == -1:
            return

        persona = _get_persona(model_name)
        recent_trades = await self._get_recent_trades(session, model_name)
        held_ids = await self._get_held_market_ids(session, model_name)
        total_volume = await self._get_model_volume(session, model_name, tournament.started_at)
        competitor_summary = self._build_competitor_summary(all_entries, model_name)
        resolved_summary = await self._get_resolved_positions_summary(session, model_name)
        days_remaining = max(0, (tournament.ends_at - datetime.now(tz=timezone.utc)).days)

        agent_markets = list(market_pool)
        random.shuffle(agent_markets)
        agent_markets = agent_markets[:25]

        prompt = _build_agent_prompt(
            model_name=model_name,
            persona=persona,
            start_budget=self.settings.tournament_start_budget_usd,
            current_balance=entry.current_balance_usd,
            total_trades=entry.total_trades,
            total_volume=total_volume,
            recent_trades=recent_trades,
            held_market_ids=held_ids,
            available_markets=agent_markets,
            tournament_days_remaining=days_remaining,
            competitor_summary=competitor_summary,
            resolved_positions_summary=resolved_summary,
        )

        try:
            output = await run_model_inference(
                db=session,
                model_name=model_name,
                market_title="Tournament Round",
                market_context=prompt,
                system_prompt=persona["system"],
            )

            entry.total_forecasts += 1
            entry.current_balance_usd -= output.cost_usd

            trade_decisions = _parse_trades_from_response(
                output.raw_response, len(agent_markets), entry.current_balance_usd
            )

            if not trade_decisions:
                skip = output.raw_response.get("skip_reason") or output.skip_reason or "no trades selected"
                logger.info("Agent %s (%s) skipped: %s", model_name, persona["style"], skip)
                forecast_mid = agent_markets[0]["id"] if agent_markets else 0
                session.add(Forecast(
                    market_id=forecast_mid, model_name=model_name,
                    probability_yes=output.probability_yes,
                    confidence=output.confidence, rationale=output.rationale,
                    cost_usd=output.cost_usd,
                ))
                await session.commit()
                return

            for td in trade_decisions:
                chosen = agent_markets[td["market_index"]]
                side = td["side"]
                trade_size = min(td["size_usd"], entry.current_balance_usd)
                if trade_size < 0.5:
                    break

                price = chosen["yes_price"] if side == "YES" else chosen["no_price"]
                if price <= 0.01 or price >= 0.99:
                    continue

                quantity = max(5.0, trade_size / price)
                token_id = chosen["yes_token_id"] if side == "YES" else chosen["no_token_id"]

                session.add(Forecast(
                    market_id=chosen["id"], model_name=model_name,
                    probability_yes=output.probability_yes,
                    confidence=td["confidence"],
                    rationale=td["rationale"][:500],
                    cost_usd=0.0,
                ))

                try:
                    remote = await self.polymarket_client.place_order(
                        model_name=model_name,
                        market_id=chosen["polymarket_market_id"],
                        side=side, quantity=quantity, price=price,
                        token_id=token_id,
                    )

                    trade = Trade(
                        market_id=chosen["id"],
                        model_name=model_name,
                        side=side, quantity=quantity, price=price,
                        status=remote.get("status", "submitted"),
                        source=remote.get("source", "game"),
                        external_order_id=remote.get("external_order_id", ""),
                    )
                    session.add(trade)
                    entry.total_trades += 1
                    entry.total_volume_usd += trade_size
                    entry.current_balance_usd -= trade_size

                    logger.info(
                        "TRADE: agent=%s [%s] market=\"%s\" cat=%s side=%s $%.2f @ %.2f qty=%.2f | bal=$%.2f",
                        model_name, persona["style"], chosen["title"][:40],
                        chosen["category"], side, trade_size, price, quantity,
                        entry.current_balance_usd,
                    )
                except Exception as exc:
                    logger.warning("Trade failed: agent=%s market=%s: %s", model_name, chosen["title"][:40], exc)

            await session.commit()

        except Exception as exc:
            logger.warning("Agent %s round error: %s", model_name, exc)

    async def _run_round(self) -> None:
        async with SessionLocal() as session:
            tournament = await self._ensure_active_tournament(session)

            await self._sync_markets(session)
            await self._resolve_paper_pnl(session, tournament)

            result = await session.execute(
                select(Market).where(func.lower(Market.status) == "open")
            )
            all_markets = list(result.scalars().all())
            if not all_markets:
                logger.info("Game engine: no open markets after sync")
                return

            raw_pool = [
                {
                    "id": m.id,
                    "polymarket_market_id": m.polymarket_market_id,
                    "title": m.title,
                    "description": m.description,
                    "category": m.category,
                    "duration_tag": market_duration_tag(m.title, m.description, m.end_date),
                    "end_date": m.end_date,
                    "yes_price": float(m.yes_price),
                    "no_price": float(m.no_price),
                    "yes_token_id": m.yes_token_id,
                    "no_token_id": m.no_token_id,
                }
                for m in all_markets
                if 0.02 < float(m.yes_price) < 0.98
            ]

            priority = [x for x in raw_pool if (x["category"] or "").lower() in PRIORITY_CATEGORIES]
            other = [x for x in raw_pool if (x["category"] or "").lower() not in PRIORITY_CATEGORIES]
            random.shuffle(priority)
            random.shuffle(other)
            market_pool = priority + other

            if not market_pool:
                logger.info("Game engine: no markets with tradeable prices (all at extremes)")
                return

            all_entries = await self._get_all_entries(session, tournament.id)

            active_models = [
                e.model_name for e in all_entries
                if e.rank != -1 and e.current_balance_usd > 1.0
            ]

            logger.info(
                "=== Round: %d markets, %d active agents (of %d total) ===",
                len(market_pool), len(active_models), len(all_entries),
            )

            for model_name in active_models:
                await self._run_agent_round(session, tournament, model_name, market_pool, all_entries)

            await self._eliminate_underperformers(session, tournament)
            await self._rank_entries(session, tournament.id)
            await session.commit()

    async def _run_forever(self) -> None:
        while self.running:
            try:
                await self._run_round()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Game engine loop error: %s", exc)
            if self.running:
                logger.info("Next round in %ss", self.settings.game_loop_interval_seconds)
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
        "Game engine started (interval=%ss, models=%d, duration=%dd)",
        settings.game_loop_interval_seconds,
        len(settings.model_names),
        settings.tournament_duration_days,
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
