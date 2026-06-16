"""AlpacaBroker — the live/paper execution target (Phase 2).

Swaps in for ``SimulatedBroker`` behind the shared ``Broker`` Protocol: the Phase-1 pure
pipeline (screen -> signals -> strategy -> risk) produces an ``ExecutablePlan`` exactly as
before; this broker is the only thing that changes between backtest and live. The Alpaca
``TradingClient`` is *injected* (mirroring ``data.tiingo``'s injected ``fetch=``) so unit
tests run with a stub — no network, no installed ``alpaca-py``.

**Safety doctrine (the whole point of this module):** the live REST endpoint is selected
*only* when ``live_trading`` is True. The ``build_alpaca_broker`` composition root — the one
place that does the lazy ``alpaca-py`` import and constructs a real client — additionally
REFUSES to point at live unless ``settings.live_confirm == "I_UNDERSTAND"``. Paper is always
allowed. The guard lives in ``_assert_live_allowed`` so it can be unit-tested without the SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from core.config import ExecConfig
from core.contracts import (
    AccountState,
    ExecutableOrder,
    ExecutablePlan,
    Position,
    _is_finite_number,
)

if TYPE_CHECKING:
    from core.config import Settings

# The exact confirmation string that must be set (env ``LIVE_CONFIRM``) to allow live.
LIVE_CONFIRM_TOKEN = "I_UNDERSTAND"

# Builds the broker-native order request from a canonical (symbol, side, qty, time_in_force,
# fractional) tuple. Injected so tests stub it and the alpaca-py import stays lazy.
OrderFactory = Callable[[str, str, float, str, bool], Any]

# Builds the broker-native "list open orders" query. Injected (like ``OrderFactory``) so the
# alpaca-py import stays lazy and tests can pass a sentinel the stub client ignores.
OpenOrdersQuery = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class MarketClock:
    """The exchange clock the scheduler needs: open state + the session boundaries."""

    is_open: bool
    next_open: datetime
    next_close: datetime


class _TradingClient(Protocol):
    """The minimal Alpaca ``TradingClient`` surface this broker depends on.

    Typed as a Protocol so tests can inject a tiny stub and so the real ``alpaca-py``
    client (a structural match) is never imported at module load.
    """

    def get_account(self) -> Any:
        ...

    def get_all_positions(self) -> Any:
        ...

    def submit_order(self, order_data: Any) -> Any:
        ...

    def get_orders(self, filter: Any = None) -> Any:  # noqa: A002 - alpaca-py's param name
        ...

    def get_clock(self) -> Any:
        ...


def _to_float(value: Any, default: float = 0.0) -> float:
    """Coerce Alpaca's numeric strings (e.g. ``"100000.00"``) to a finite float defensively."""
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if _is_finite_number(out) else default


def _format_qty(qty: float, *, fractional: bool) -> str:
    """Render an order quantity as Alpaca expects it (string), honoring the fractional flag.

    Alpaca's order API takes ``qty`` as a string; whole-share accounts must not send a
    fractional value, so non-fractional config rounds down to an integer count.
    """
    if not fractional:
        return str(int(qty))
    # Trim to a sane precision; Alpaca accepts up to 9 decimal places for fractional qty.
    return f"{qty:.9f}".rstrip("0").rstrip(".")


def _default_order_factory(
    symbol: str, side: str, qty: float, time_in_force: str, fractional: bool
) -> Any:
    """Real Alpaca market-order request — the only place ``alpaca-py`` is imported for submits."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    return MarketOrderRequest(
        symbol=symbol,
        qty=float(_format_qty(qty, fractional=fractional)),
        side=OrderSide(side),
        time_in_force=TimeInForce(time_in_force),
    )


def _default_open_orders_query() -> Any:
    """Real Alpaca "list open orders" request — the only place its SDK types are imported."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    return GetOrdersRequest(status=QueryOrderStatus.OPEN)


def _assert_live_allowed(settings: Settings) -> None:
    """Sovereign live guard: raise unless live trading is both requested and confirmed.

    Paper trading needs no confirmation. Live requires BOTH ``live_trading is True`` AND
    ``live_confirm == "I_UNDERSTAND"``; anything else is a hard ``RuntimeError`` so a stray
    flag can never silently route orders to the real-money endpoint.
    """
    if not settings.live_trading:
        return
    if settings.live_confirm != LIVE_CONFIRM_TOKEN:
        raise RuntimeError(
            "Refusing to construct a LIVE Alpaca client: live_trading is True but "
            f"live_confirm != {LIVE_CONFIRM_TOKEN!r}. Set LIVE_CONFIRM to confirm live "
            "trading, or set LIVE_TRADING=false to use the paper endpoint."
        )


class AlpacaBroker:
    """Live/paper broker over an injected Alpaca ``TradingClient``. Implements ``Broker``."""

    def __init__(
        self,
        client: _TradingClient,
        *,
        execution: ExecConfig,
        live_trading: bool,
        live_confirm: str | None,
        order_factory: OrderFactory = _default_order_factory,
        open_orders_query: OpenOrdersQuery = _default_open_orders_query,
    ) -> None:
        self._client = client
        self._execution = execution
        self._live_trading = live_trading
        self._live_confirm = live_confirm
        self._order_factory = order_factory
        self._open_orders_query = open_orders_query
        # Order ids of the most recent submit(), kept for the recorder/audit layer.
        self.last_orders: tuple[str, ...] = ()

    @property
    def is_live(self) -> bool:
        """True only when this broker is configured to hit the live endpoint."""
        return self._live_trading

    @property
    def base_url(self) -> str:
        """The REST endpoint this broker targets — live ONLY when ``live_trading`` is True."""
        if self._live_trading:
            return self._execution.live_base_url
        return self._execution.paper_base_url

    # -- Broker Protocol --------------------------------------------------------------
    def submit(self, plan: ExecutablePlan) -> None:
        """Submit each approved order as a fractional market order; never submit nothing.

        An empty plan (no orders) is a no-op — we never round-trip to the broker with
        nothing to do. Submitted order ids are stashed on ``self.last_orders`` for §9.
        """
        if not plan.orders:
            return
        order_ids: list[str] = []
        for order in plan.orders:
            submitted = self._client.submit_order(self._build_order(order))
            order_id = getattr(submitted, "id", None)
            if order_id is not None:
                order_ids.append(str(order_id))
        self.last_orders = tuple(order_ids)

    def account_state(self) -> AccountState:
        """Map the Alpaca account + positions to ``AccountState`` (with string coercion)."""
        account = self._client.get_account()
        equity = _to_float(getattr(account, "equity", None))
        last_equity = getattr(account, "last_equity", None)
        day_pnl = equity - _to_float(last_equity) if last_equity is not None else 0.0
        positions = tuple(
            Position(
                symbol=str(getattr(p, "symbol", "")),
                qty=_to_float(getattr(p, "qty", None)),
                avg_price=_to_float(getattr(p, "avg_entry_price", None)),
            )
            for p in self._client.get_all_positions()
        )
        return AccountState(
            cash=_to_float(getattr(account, "cash", None)),
            equity=equity,
            buying_power=_to_float(getattr(account, "buying_power", None)),
            positions=positions,
            day_pnl=day_pnl,
        )

    def open_orders(self) -> tuple[str, ...]:
        """Symbols with a pending (unfilled) open order — used to avoid stacking duplicates.

        The cycle sizes against *filled* positions only, so without this a re-run while orders
        are still queued would submit the same buys again. Returns the symbols so the runner can
        skip them.
        """
        orders = self._client.get_orders(filter=self._open_orders_query())
        symbols = {str(getattr(o, "symbol", "")) for o in orders}
        symbols.discard("")
        return tuple(sorted(symbols))

    def market_clock(self) -> MarketClock:
        """Map Alpaca's exchange clock to ``MarketClock`` (open state + session boundaries)."""
        clock = self._client.get_clock()
        return MarketClock(
            is_open=bool(getattr(clock, "is_open", False)),
            next_open=clock.next_open,
            next_close=clock.next_close,
        )

    # -- internals --------------------------------------------------------------------
    def _build_order(self, order: ExecutableOrder) -> Any:
        """Build the broker-native order request for one ``ExecutableOrder`` via the factory."""
        return self._order_factory(
            order.symbol,
            order.side,
            order.qty,
            self._execution.time_in_force,
            self._execution.fractional,
        )


def build_alpaca_broker(settings: Settings) -> AlpacaBroker:
    """Composition root: construct an ``AlpacaBroker`` from ``Settings`` (lazy SDK import here).

    This is the ONLY place the real ``alpaca-py`` client is imported and built. It enforces the
    sovereign live guard (``_assert_live_allowed``) before constructing anything, then points the
    ``TradingClient`` at paper unless ``live_trading`` is True.
    """
    _assert_live_allowed(settings)

    from alpaca.trading.client import TradingClient

    client = TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=not settings.live_trading,
    )
    return AlpacaBroker(
        client,
        execution=settings.execution,
        live_trading=settings.live_trading,
        live_confirm=settings.live_confirm,
    )
