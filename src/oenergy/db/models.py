from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    number: Mapped[str] = mapped_column(String(40), primary_key=True)
    brand: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str | None] = mapped_column(String(40))
    billing_name: Mapped[str | None] = mapped_column(String(120))
    billing_email: Mapped[str | None] = mapped_column(String(160))
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)
    overdue_cents: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    agreements: Mapped[list[Agreement]] = relationship(back_populates="account")


class Agreement(Base):
    __tablename__ = "agreements"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_number: Mapped[str | None] = mapped_column(String(40), ForeignKey("accounts.number"))
    product_code: Mapped[str | None] = mapped_column(String(60))
    product_name: Mapped[str | None] = mapped_column(String(160))
    is_time_of_use: Mapped[bool] = mapped_column(Boolean, default=False)
    melo_number: Mapped[str | None] = mapped_column(String(60))
    supply_point_id: Mapped[str | None] = mapped_column(String(60))
    supply_point_external_id: Mapped[str | None] = mapped_column(String(80))
    supply_point_market: Mapped[str | None] = mapped_column(String(40))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    net_monthly_standing_charge_ct: Mapped[float | None] = mapped_column(Float)
    gross_monthly_standing_charge_ct: Mapped[float | None] = mapped_column(Float)
    vat_rate: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped[Account | None] = relationship(back_populates="agreements")
    rates: Mapped[list[TariffRate]] = relationship(back_populates="agreement", cascade="all, delete-orphan")


class TariffRate(Base):
    """A single applicable rate (work price / standing charge) of an agreement."""

    __tablename__ = "tariff_rates"
    __table_args__ = (UniqueConstraint("agreement_id", "name", "valid_from", name="uq_tariff_rate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agreement_id: Mapped[str] = mapped_column(String(40), ForeignKey("agreements.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(60))
    price_ct_per_kwh: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(40))
    gross_ct_per_kwh: Mapped[float | None] = mapped_column(Float)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(40), default="agreement_rates")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    agreement: Mapped[Agreement] = relationship(back_populates="rates")


class Property(Base):
    """The property (address) with its market location and meter."""

    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_number: Mapped[str | None] = mapped_column(String(40))
    label: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(String(200))
    malo_id: Mapped[str | None] = mapped_column(String(40))
    malo_number: Mapped[str | None] = mapped_column(String(60))
    meter_id: Mapped[str | None] = mapped_column(String(40))
    meter_number: Mapped[str | None] = mapped_column(String(60))
    melo_number: Mapped[str | None] = mapped_column(String(60))
    has_smart_meter_gateway: Mapped[bool | None] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Meter(Base):
    __tablename__ = "meters"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_number: Mapped[str | None] = mapped_column(String(40))
    number: Mapped[str | None] = mapped_column(String(60))
    melo_number: Mapped[str | None] = mapped_column(String(60))
    has_smart_meter_gateway: Mapped[bool | None] = mapped_column(Boolean)
    conversion_factor: Mapped[str | None] = mapped_column(String(40))
    active_from: Mapped[datetime | None] = mapped_column(DateTime)
    active_to: Mapped[datetime | None] = mapped_column(DateTime)
    history_start: Mapped[datetime | None] = mapped_column(DateTime)
    history_exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    history_checked_until: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    readings: Mapped[list[MeterReading]] = relationship(
        back_populates="meter", cascade="all, delete-orphan", passive_deletes=True
    )
    intervals: Mapped[list[Interval]] = relationship(
        back_populates="meter", cascade="all, delete-orphan", passive_deletes=True
    )
    daily_stats: Mapped[list[DailyStat]] = relationship(
        back_populates="meter", cascade="all, delete-orphan", passive_deletes=True
    )
    windows: Mapped[list[ReadingWindow]] = relationship(
        back_populates="meter", cascade="all, delete-orphan", passive_deletes=True
    )


class MeterReading(Base):
    """Raw reading as delivered by the utility (arrives with ~24h delay)."""

    __tablename__ = "meter_readings"
    __table_args__ = (
        UniqueConstraint("meter_id", "register_obis_code", "read_at", name="uq_reading"),
        Index("ix_readings_meter_read_at", "meter_id", "read_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meter_id: Mapped[str] = mapped_column(String(40), ForeignKey("meters.id", ondelete="CASCADE"))
    register_obis_code: Mapped[str | None] = mapped_column(String(40), default="")
    read_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    value: Mapped[float] = mapped_column(Float)
    type_of_read: Mapped[str | None] = mapped_column(String(40))
    origin: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str | None] = mapped_column(String(40))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    meter: Mapped[Meter] = relationship(back_populates="readings")


class Interval(Base):
    """Consumption of one slot (default 15 minutes) derived from raw readings."""

    __tablename__ = "intervals"
    __table_args__ = (
        UniqueConstraint("meter_id", "slot_start", name="uq_interval"),
        Index("ix_intervals_slot_start", "slot_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meter_id: Mapped[str] = mapped_column(String(40), ForeignKey("meters.id", ondelete="CASCADE"))
    slot_start: Mapped[datetime] = mapped_column(DateTime)
    slot_end: Mapped[datetime] = mapped_column(DateTime)
    kwh: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(20), default="measured")  # measured | estimated
    n_readings: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    meter: Mapped[Meter] = relationship(back_populates="intervals")


class DailyStat(Base):
    """Daily rollup including the completeness state (data arrives with 24h delay)."""

    __tablename__ = "daily_stats"
    __table_args__ = (
        UniqueConstraint("meter_id", "day", name="uq_daily_stat"),
        Index("ix_daily_stats_day", "day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meter_id: Mapped[str] = mapped_column(String(40), ForeignKey("meters.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    kwh: Mapped[float] = mapped_column(Float, default=0.0)
    cost_cents: Mapped[float] = mapped_column(Float, default=0.0)
    energy_cost_cents: Mapped[float] = mapped_column(Float, default=0.0)
    standing_cost_cents: Mapped[float] = mapped_column(Float, default=0.0)
    expected_slots: Mapped[int] = mapped_column(Integer, default=0)
    received_slots: Mapped[int] = mapped_column(Integer, default=0)
    estimated_slots: Mapped[int] = mapped_column(Integer, default=0)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    meter: Mapped[Meter] = relationship(back_populates="daily_stats")


class Price(Base):
    """One applicable rate of the agreement for a validity window.

    A dynamic tariff consists of several rates per window (base component,
    variable component, ...).  The effective customer price of a slot is the
    sum of all rates that are valid in that window.
    """

    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint("scope", "source", "starts_at", "rate_name", name="uq_price"),
        Index("ix_prices_starts_at", "starts_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(20), default="customer")  # customer | epex
    source: Mapped[str] = mapped_column(String(30))  # agreement_rates | forecast | static | epex
    rate_name: Mapped[str] = mapped_column(String(120), default="")
    agreement_id: Mapped[str | None] = mapped_column(String(40))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    net_ct_per_kwh: Mapped[float | None] = mapped_column(Float)
    gross_ct_per_kwh: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(30))
    active_from: Mapped[str | None] = mapped_column(String(8))
    active_to: Mapped[str | None] = mapped_column(String(8))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReadingWindow(Base):
    """A time range that was already requested from the API.

    The table is the memory of the backfill: ranges that were queried are not
    requested again (unless they stayed empty and the retry interval elapsed).
    """

    __tablename__ = "reading_windows"
    __table_args__ = (
        UniqueConstraint("meter_id", "start_at", "end_at", name="uq_reading_window"),
        Index("ix_reading_windows_meter", "meter_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meter_id: Mapped[str] = mapped_column(String(40), ForeignKey("meters.id", ondelete="CASCADE"))
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    queried_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    rows_found: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")

    meter: Mapped[Meter] = relationship(back_populates="windows")


class Bill(Base):
    """Invoice / statement as booked by the supplier (ground truth for costs)."""

    __tablename__ = "bills"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    account_number: Mapped[str | None] = mapped_column(String(40))
    period_start: Mapped[datetime | None] = mapped_column(DateTime)
    period_end: Mapped[datetime | None] = mapped_column(DateTime)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime)
    total_cents: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(10))
    raw: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")
    items: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)


class AppSetting(Base):
    """Einstellungen der Anwendung, die nicht in der .env stehen."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Token(Base):
    """Cached Kraken access token and long lived refresh token."""

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    refresh_kind: Mapped[str | None] = mapped_column(String(20))
    viewer_email: Mapped[str | None] = mapped_column(String(160))
    viewer_name: Mapped[str | None] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
