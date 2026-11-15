"""Composition of the customer price.

A dynamic tariff is built from several rates: a fixed base component and one
or more variable components that follow the exchange price.  The API returns
those rates separately, each with its own validity window.  The price that a
customer actually pays for a slot is therefore the sum of all rates that are
valid in that window.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from oenergy.db.models import Price

DEFAULT_TZ = ZoneInfo("Europe/Berlin")

#: sources ordered by specificity - the first one that covers a slot wins
SOURCE_PRIORITY = ("agreement_rates", "forecast")


@dataclass
class Rate:
    """A single applicable rate (e.g. the base component)."""

    name: str
    net_ct: float | None = None
    gross_ct: float | None = None

    def gross(self, vat: float) -> float:
        if self.gross_ct is not None:
            return self.gross_ct
        return (self.net_ct or 0.0) * (1 + vat)


@dataclass
class RateWindow:
    """All rates that apply between ``starts_at`` and ``ends_at``."""

    starts_at: datetime
    ends_at: datetime
    source: str
    net_ct: float = 0.0
    gross_ct: float = 0.0
    rate_names: list[str] = field(default_factory=list)
    rates: list[Rate] = field(default_factory=list)

    def covers(self, at: datetime) -> bool:
        return self.starts_at <= at < self.ends_at


@dataclass
class EffectivePrice:
    """The price that applies at a point in time."""

    starts_at: datetime | None = None
    ends_at: datetime | None = None
    net_ct: float | None = None
    gross_ct: float | None = None
    source: str = "customer"
    rate_names: list[str] = field(default_factory=list)
    rates: list[Rate] = field(default_factory=list)


def lookup_rate(rows: list[Price], at: datetime) -> Price | None:
    """The rate that covers ``at`` (binary search on the start time)."""
    if not rows:
        return None
    starts = [row.starts_at for row in rows]
    index = bisect_right(starts, at) - 1
    for row in reversed(rows[: index + 1]):
        if row.starts_at <= at < row.ends_at:
            return row
    return None


def _gross(row: Price, vat: float) -> float:
    if row.gross_ct_per_kwh is not None:
        return row.gross_ct_per_kwh
    return (row.net_ct_per_kwh or 0.0) * (1 + vat)


def group_rates(rows: list[Price], vat: float = 0.19) -> list[RateWindow]:
    """Group the stored rates by validity window and sum the rates of a window."""
    grouped: dict[tuple[datetime, datetime], dict[str, list[Price]]] = {}
    for row in rows:
        key = (row.starts_at, row.ends_at)
        grouped.setdefault(key, {}).setdefault(row.source, []).append(row)

    windows: list[RateWindow] = []
    for (starts_at, ends_at), sources in grouped.items():
        for source in SOURCE_PRIORITY:
            rates = sources.get(source)
            if not rates:
                continue
            windows.append(
                RateWindow(
                    starts_at=starts_at,
                    ends_at=ends_at,
                    source=source,
                    net_ct=sum(row.net_ct_per_kwh or 0.0 for row in rates),
                    gross_ct=sum(_gross(row, vat) for row in rates),
                    rate_names=[row.rate_name for row in rates if row.rate_name],
                    rates=[
                        Rate(
                            name=row.rate_name,
                            net_ct=row.net_ct_per_kwh,
                            gross_ct=row.gross_ct_per_kwh,
                        )
                        for row in rates
                    ],
                )
            )
            break
    return sorted(windows, key=lambda window: window.starts_at)


def _static_price(rows: list[Price], at: datetime, vat: float, tz: ZoneInfo) -> EffectivePrice | None:
    """Fallback: the static rates of the product (e.g. the base component)."""
    static = [row for row in rows if row.source == "static"]
    if not static:
        return None

    local = at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    minutes = local.hour * 60 + local.minute

    def matches(row: Price) -> bool:
        if not row.active_from or not row.active_to:
            return True
        try:
            start_h, start_m = (int(part) for part in row.active_from.split(":")[:2])
            end_h, end_m = (int(part) for part in row.active_from.split(":")[:2])
        except ValueError:
            return True
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        if end <= start:
            return minutes >= start or minutes < end
        return start <= minutes < end

    matching = [row for row in static if matches(row)]
    if not matching:
        matching = static

    return EffectivePrice(
        starts_at=min(row.starts_at for row in matching),
        ends_at=max(row.ends_at for row in matching),
        net_ct=sum(row.net_ct_per_kwh or 0.0 for row in matching),
        gross_ct=sum(_gross(row, vat) for row in matching),
        source="static",
        rate_names=[row.rate_name for row in matching if row.rate_name],
        rates=[
            Rate(name=row.rate_name, net_ct=row.net_ct_per_kwh, gross_ct=row.gross_ct_per_kwh)
            for row in matching
        ],
    )


def effective_at(
    rows: list[Price],
    at: datetime,
    *,
    vat: float = 0.19,
    tz: ZoneInfo = DEFAULT_TZ,
) -> EffectivePrice | None:
    """The price that applies at ``at`` (sum of all valid rates)."""
    customer_rows = [row for row in rows if row.scope != "epex"]
    epex_rows = [row for row in rows if row.scope == "epex"]
    return compose_at(customer_rows, epex_rows, at, vat=vat, tz=tz)


def compose_at(
    customer_rows: list[Price],
    epex_rows: list[Price],
    at: datetime,
    *,
    vat: float = 0.19,
    tz: ZoneInfo = DEFAULT_TZ,
) -> EffectivePrice | None:
    """The customer price at ``at``.

    Priority:

    1. the applicable rates of the agreement (forecast / agreement rates),
    2. the fixed base component of the tariff plus the exchange price - this
       is how the dynamic tariff is calculated and it reconstructs the price
       history from the EPEX prices.
    """
    windows = group_rates(customer_rows, vat)
    covering = [window for window in windows if window.covers(at)]
    if covering:
        # the most specific window wins (the one that starts last)
        window = max(covering, key=lambda item: item.starts_at)
        return EffectivePrice(
            starts_at=window.starts_at,
            ends_at=window.ends_at,
            net_ct=window.net_ct,
            gross_ct=window.gross_ct,
            source=window.source,
            rate_names=window.rate_names,
            rates=window.rates,
        )

    base = _static_price(customer_rows, at, vat, tz)
    epex = lookup_rate(epex_rows, at)
    if base is None and epex is None:
        return None
    if base is None:
        net = epex.net_ct_per_kwh
        return EffectivePrice(
            starts_at=epex.starts_at,
            ends_at=epex.ends_at,
            net_ct=net,
            gross_ct=net * (1 + vat) if net is not None else None,
            source="epex",
            rate_names=["EPEX"],
        )
    if epex is None:
        return base

    net = (base.net_ct or 0.0) + (epex.net_ct_per_kwh or 0.0)
    return EffectivePrice(
        starts_at=epex.starts_at,
        ends_at=epex.ends_at,
        net_ct=net,
        gross_ct=net * (1 + vat),
        source="static+epex",
        rate_names=[*base.rate_names, "EPEX"],
        rates=[
            *base.rates,
            Rate(name="EPEX", net_ct=epex.net_ct_per_kwh, gross_ct=None),
        ],
    )
