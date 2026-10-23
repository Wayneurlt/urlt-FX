"""Historical candle importer.

Accepted formats:

* Generic: ``time,open,high,low,close[,spread][,volume]`` where ``time`` is
  ISO-8601. Naive timestamps are interpreted in ``source_tz`` (default UTC).
* MT5 export: ``<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>``
  where ``<SPREAD>`` is in points (ticks). MT5 exports use the broker's
  server timezone, so pass ``source_tz`` explicitly.

Prices are assumed to be BID prices unless the file says otherwise.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from trading.instruments.spec import InstrumentSpec


class ImportFormatError(ValueError):
    pass


def _detect_mt5(head: str) -> bool:
    return "<DATE>" in head and "<OPEN>" in head


def load_candles_csv(
    source: str | Path | io.StringIO,
    spec: InstrumentSpec,
    source_tz: str = "UTC",
) -> pd.DataFrame:
    if isinstance(source, str | Path):
        text = Path(source).read_text(encoding="utf-8-sig")
    else:
        text = source.getvalue()
    first = text.splitlines()[0] if text else ""
    if _detect_mt5(first):
        df = pd.read_csv(io.StringIO(text), sep="\t")
        df.columns = [c.strip("<>").lower() for c in df.columns]
        if "time" in df.columns:
            ts = pd.to_datetime(df["date"] + " " + df["time"], format="%Y.%m.%d %H:%M:%S", errors="coerce")
        else:
            ts = pd.to_datetime(df["date"], format="%Y.%m.%d", errors="coerce")
        out = pd.DataFrame(
            {"time": ts, "open": df["open"], "high": df["high"], "low": df["low"], "close": df["close"]}
        )
        if "spread" in df.columns:
            out["spread"] = df["spread"].astype(float) * spec.tick_size
        if "tickvol" in df.columns:
            out["volume"] = df["tickvol"]
    else:
        df = pd.read_csv(io.StringIO(text))
        df.columns = [c.strip().lower() for c in df.columns]
        need = {"time", "open", "high", "low", "close"}
        missing = need - set(df.columns)
        if missing:
            raise ImportFormatError(f"missing columns: {sorted(missing)}")
        out = df[
            [c for c in ("time", "open", "high", "low", "close", "spread", "volume") if c in df.columns]
        ].copy()
        out["time"] = pd.to_datetime(out["time"], errors="coerce", utc=False, format="ISO8601")

    if out["time"].isna().any():
        raise ImportFormatError(f"{int(out['time'].isna().sum())} unparseable timestamps")
    if getattr(out["time"].dt, "tz", None) is None:
        out["time"] = out["time"].dt.tz_localize(source_tz, ambiguous="raise", nonexistent="raise")
    out["time"] = out["time"].dt.tz_convert("UTC")
    for c in ("open", "high", "low", "close"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.sort_values("time", kind="stable").reset_index(drop=True)


def save_dataset(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, compression="gzip" if path.suffix == ".gz" else None)


def load_dataset(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, compression="gzip" if path.suffix == ".gz" else None)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df
