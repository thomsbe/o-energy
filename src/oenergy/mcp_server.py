"""FastMCP server for read-only access to the stored energy data.

The server is mounted below ``/mcp`` in the existing FastAPI process.  It is
optional: without ``MCP_TOKEN`` (or without the ``mcp`` extra) the endpoint
stays closed.  Every tool reads through a
:func:`oenergy.db.base.readonly_session` - a write attempt fails with
``ReadOnlyViolation`` before it reaches SQLite.
"""

from __future__ import annotations

from fastmcp import FastMCP

from oenergy import __version__
from oenergy.config import Settings
from oenergy.db.base import get_database, readonly_session
from oenergy.services import mcp_tools


def build_server(settings: Settings) -> FastMCP:
    """Create the FastMCP instance with all tools and resources."""
    database = get_database()
    server = FastMCP(
        name="o-energy",
        version=__version__,
        instructions=(
            "Read-only access to the local o-energy database (Octopus Energy "
            "Germany, Dynamic Octopus). All timestamps are local ISO-8601 "
            "values with an offset, prices are ct/kWh and money is EUR. "
            "The tools never trigger a sync and never change data."
        ),
    )

    def _call(function, *args, **kwargs):
        with readonly_session(database) as session:
            return function(session, settings, *args, **kwargs)

    @server.tool
    def status() -> dict:
        """Data freshness, completeness of the last days and the last sync runs."""
        return _call(mcp_tools.get_status)

    @server.tool
    def consumption(days: int = 7) -> dict:
        """Consumption per complete local day in kWh, including the data state."""
        return _call(mcp_tools.get_consumption, days=days)

    @server.tool
    def costs(days: int = 30) -> dict:
        """Costs per complete local day and the forecast for the current month."""
        return _call(mcp_tools.get_costs, days=days)

    @server.tool
    def prices(hours: int = 48) -> dict:
        """Current and upcoming customer prices in ct/kWh plus the EPEX spot price."""
        return _call(mcp_tools.get_prices, hours=hours)

    @server.tool
    def cheapest_windows(days: int = 7, count: int = 10) -> dict:
        """The cheapest upcoming slots - useful for planning flexible loads."""
        return _call(mcp_tools.get_cheapest_windows, days=days, count=count)

    @server.tool
    def price_at(when: str) -> dict:
        """The customer price at an ISO-8601 time; a value without offset is local time."""
        return _call(mcp_tools.get_price_at, when=when)

    @server.tool
    def monthly_overview(month: str | None = None) -> dict:
        """Consumption, costs and average price for one month in the format YYYY-MM."""
        return _call(mcp_tools.get_monthly_overview, month=month)

    @server.tool
    def account() -> dict:
        """Tariff, base fee, VAT and balance - without account or meter identifiers."""
        return _call(mcp_tools.get_account)

    @server.resource("energy://status", mime_type="application/json")
    def status_resource() -> dict:
        """The current data status."""
        return _call(mcp_tools.get_status)

    @server.resource("energy://today", mime_type="application/json")
    def today_resource() -> dict:
        """Today's prices and the state of the most recent data delivery."""
        return {
            "status": _call(mcp_tools.get_status),
            "prices": _call(mcp_tools.get_prices, hours=24),
        }

    @server.resource("energy://month", mime_type="application/json")
    def month_resource() -> dict:
        """The current month at a glance."""
        return _call(mcp_tools.get_monthly_overview)

    return server


def build_http_app(settings: Settings):
    """The ASGI app for the Streamable-HTTP transport (mounted at ``/mcp``)."""
    server = build_server(settings)
    return server.http_app(path="/", stateless_http=True)
