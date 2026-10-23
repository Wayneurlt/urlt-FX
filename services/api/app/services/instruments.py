from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InstrumentRow
from trading.instruments.spec import InstrumentRegistry, InstrumentSpec, InvalidInstrumentSpec, default_specs

_FIELDS = (
    "symbol",
    "base_currency",
    "quote_currency",
    "digits",
    "tick_size",
    "pip_size",
    "contract_size",
    "min_volume",
    "max_volume",
    "volume_step",
    "min_stop_distance",
    "freeze_level",
    "tradable",
    "broker_symbol",
    "correlation_group",
    "source",
)


def seed_instruments(s: Session) -> None:
    for spec in default_specs():
        if s.get(InstrumentRow, spec.symbol) is None:
            s.add(InstrumentRow(**{f: getattr(spec, f) for f in _FIELDS}))


def to_spec(row: InstrumentRow) -> InstrumentSpec:
    return InstrumentSpec(**{f: getattr(row, f) for f in _FIELDS})


def load_registry(s: Session) -> tuple[InstrumentRegistry, list[str]]:
    """Registry from DB rows. Invalid rows are excluded and reported (never guessed)."""
    reg = InstrumentRegistry([])
    invalid: list[str] = []
    for row in s.scalars(select(InstrumentRow)):
        try:
            reg.add(to_spec(row))
        except InvalidInstrumentSpec as exc:
            invalid.append(str(exc))
    return reg, invalid


def upsert_broker_spec(s: Session, spec: InstrumentSpec) -> None:
    row = s.get(InstrumentRow, spec.symbol)
    values = {f: getattr(spec, f) for f in _FIELDS}
    values["source"] = "broker"
    if row is None:
        s.add(InstrumentRow(**values))
    else:
        for k, v in values.items():
            setattr(row, k, v)
