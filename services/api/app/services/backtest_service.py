from __future__ import annotations

import csv
import io
import json
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, get_settings
from app.core.errors import APIError, bad_request, not_found
from app.db.base import session_scope, utcnow
from app.models import BacktestMetric, BacktestRun, BacktestTrade, Dataset
from app.services.audit import audit
from app.services.instruments import load_registry
from app.workers.queue import enqueue
from trading.backtesting.engine import BacktestError, run_backtest
from trading.backtesting.models import BacktestConfig
from trading.backtesting.validation import (
    baseline_comparison,
    cost_sensitivity,
    drawdown_stress,
    in_out_of_sample,
    parameter_sensitivity,
    slippage_stress,
    walk_forward,
)
from trading.market_data.csv_importer import ImportFormatError, load_candles_csv, load_dataset, save_dataset
from trading.market_data.quality import check_candles
from trading.market_data.synthetic import SyntheticMarketData
from trading.news.provider import SampleCalendarProvider

MAX_SYNTHETIC_DAYS = 400


def _code_version() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            or "unknown"
        )
    except Exception:  # noqa: BLE001
        return "unknown"


def create_run(s: Session, body: dict[str, Any], actor: str) -> BacktestRun:
    body = dict(body)
    dataset_id = body.get("dataset_id")
    if dataset_id:
        ds = s.get(Dataset, dataset_id)
        if ds is None:
            raise not_found("dataset")
        if ds.symbol != body.get("symbol"):
            raise bad_request(f"dataset is for {ds.symbol}, not {body.get('symbol')}")
        body["data_source"] = ds.source
        body["base_timeframe"] = ds.timeframe
    else:
        body["data_source"] = "synthetic"
    try:
        cfg = BacktestConfig(**body)
    except ValidationError as exc:
        raise bad_request(
            "invalid backtest configuration",
            "validation_error",
            errors=[{"loc": [str(x) for x in e["loc"]], "msg": e["msg"]} for e in exc.errors()],
        ) from exc
    reg, _ = load_registry(s)
    if not reg.has(cfg.symbol):
        raise bad_request(f"unknown instrument {cfg.symbol}")
    if not dataset_id and (cfg.end - cfg.start) > timedelta(days=MAX_SYNTHETIC_DAYS):
        raise bad_request(f"synthetic backtests are limited to {MAX_SYNTHETIC_DAYS} days")
    run = BacktestRun(
        bot_kind=cfg.bot_kind.value,
        symbol=cfg.symbol,
        start=cfg.start,
        end=cfg.end,
        data_source=cfg.data_source,
        dataset_id=dataset_id,
        config=cfg.model_dump(mode="json"),
        created_by=actor,
        reproducibility={
            "code_version": _code_version(),
            "seed": cfg.seed,
            "synthetic_seed": get_settings().synthetic_seed if not dataset_id else None,
            "dataset_id": dataset_id,
        },
    )
    s.add(run)
    s.flush()
    enqueue(s, "backtest", {"run_id": run.id}, idempotency_key=f"backtest:{run.id}")
    audit(
        s,
        "backtest",
        f"backtest {run.id} queued ({cfg.bot_kind.value} {cfg.symbol} {cfg.start.date()}..{cfg.end.date()}, {cfg.data_source})",
        actor=actor,
    )
    return run


def _load_data(cfg: BacktestConfig, dataset_id: str | None, s: Session) -> pd.DataFrame:
    if dataset_id:
        ds = s.get(Dataset, dataset_id)
        assert ds is not None
        return load_dataset(Path(ds.path))
    reg, _ = load_registry(s)
    gen = SyntheticMarketData(seed=get_settings().synthetic_seed, registry=reg)
    return gen.candles_frame(cfg.symbol, cfg.base_timeframe, cfg.start, cfg.end)


def _events() -> list:
    p = get_settings().sample_calendar_path
    return (
        SampleCalendarProvider(p).events(
            pd.Timestamp.min.tz_localize("UTC").to_pydatetime(),
            pd.Timestamp.max.tz_localize("UTC").to_pydatetime(),
        )
        if p.exists()
        else []
    )


def execute_run(run_id: str) -> dict[str, Any]:
    with session_scope() as s:
        run = s.get(BacktestRun, run_id)
        if run is None:
            raise ValueError("run not found")
        if run.status == "succeeded":
            return {"skipped": "already completed"}  # idempotent re-delivery
        run.status, run.started_at, run.progress, run.error = "running", utcnow(), 0.0, None
        cfg = BacktestConfig(**run.config)
        dataset_id = run.dataset_id
    try:
        with session_scope() as s:
            data = _load_data(cfg, dataset_id, s)
            reg, _ = load_registry(s)
            spec = reg.get(cfg.symbol)
        last = [0.0]

        def progress(p: float) -> None:
            if p - last[0] >= 0.05 or p >= 1.0:
                last[0] = p
                with session_scope() as ps:
                    r = ps.get(BacktestRun, run_id)
                    if r:
                        r.progress = round(p, 3)

        result = run_backtest(cfg, data, spec, _events(), progress)
    except Exception as exc:  # noqa: BLE001 - any failure must be visible on the run, never a stuck "running"
        with session_scope() as s:
            run = s.get(BacktestRun, run_id)
            assert run is not None
            run.status, run.error, run.finished_at = "failed", str(exc), utcnow()
        return {"error": str(exc)}
    with session_scope() as s:
        run = s.get(BacktestRun, run_id)
        assert run is not None
        s.query(BacktestTrade).filter(BacktestTrade.run_id == run_id).delete()
        s.query(BacktestMetric).filter(BacktestMetric.run_id == run_id).delete()
        run.metrics, run.warnings, run.assumptions, run.counters = (
            result.metrics,
            result.warnings,
            result.assumptions,
            result.counters,
        )
        run.equity_curve, run.daily_equity, run.data_fingerprint = (
            result.equity_curve,
            result.daily_equity,
            result.data_fingerprint,
        )
        run.reproducibility = {
            **(run.reproducibility or {}),
            "data_fingerprint": result.data_fingerprint,
            "bars": result.bars,
        }
        for i, t in enumerate(result.trades):
            s.add(
                BacktestTrade(
                    run_id=run_id, seq=i, kind="trade", cycle_ref=t["cycle_id"], net_pnl=t["net_pnl"], data=t
                )
            )
        for i, c in enumerate(result.cycles):
            s.add(
                BacktestTrade(
                    run_id=run_id,
                    seq=i,
                    kind="cycle",
                    cycle_ref=c["id"],
                    net_pnl=c["net_pnl"],
                    data=json.loads(json.dumps(c, default=str)),
                )
            )
        for k, v in result.metrics.items():
            if isinstance(v, int | float) and not isinstance(v, bool):
                s.add(BacktestMetric(run_id=run_id, name=k, value=float(v)))
        run.status, run.progress, run.finished_at = "succeeded", 1.0, utcnow()
    return {"trades": len(result.trades), "cycles": len(result.cycles)}


VALIDATION_KINDS = (
    "in_out_sample",
    "walk_forward",
    "sensitivity",
    "cost",
    "slippage",
    "drawdown",
    "baseline",
)


def queue_validation(
    s: Session, run_id: str, kind: str, params: dict[str, Any], actor: str
) -> dict[str, Any]:
    run = s.get(BacktestRun, run_id)
    if run is None:
        raise not_found("backtest")
    if kind not in VALIDATION_KINDS:
        raise bad_request(f"kind must be one of {VALIDATION_KINDS}")
    if run.status != "succeeded":
        raise APIError(409, "not_ready", "the base backtest must finish successfully first")
    validation = dict(run.validation or {})
    validation[kind] = {"status": "queued", "params": params}
    run.validation = validation
    job = enqueue(s, "backtest_validation", {"run_id": run_id, "kind": kind, "params": params})
    audit(s, "backtest", f"validation {kind} queued for backtest {run_id}", actor=actor)
    return {"job_id": job.id, "kind": kind, "status": "queued"}


def execute_validation(run_id: str, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as s:
        run = s.get(BacktestRun, run_id)
        assert run is not None
        cfg = BacktestConfig(**run.config)
        data = _load_data(cfg, run.dataset_id, s)
        reg, _ = load_registry(s)
        spec = reg.get(cfg.symbol)
        trade_pnls = [
            t.net_pnl
            for t in s.scalars(
                select(BacktestTrade).where(BacktestTrade.run_id == run_id, BacktestTrade.kind == "trade")
            )
        ]
    ev = _events()
    try:
        if kind == "in_out_sample":
            res: Any = in_out_of_sample(cfg, data, spec, ev, float(params.get("in_sample_ratio", 0.7)))
        elif kind == "walk_forward":
            res = walk_forward(
                cfg,
                data,
                spec,
                params.get("param_grid") or {},
                int(params.get("folds", 4)),
                float(params.get("train_ratio", 0.7)),
                params.get("objective", "net_pnl"),
                ev,
            )
        elif kind == "sensitivity":
            if not params.get("param") or not params.get("values"):
                raise ValueError("sensitivity needs 'param' (dotted path) and 'values'")
            res = parameter_sensitivity(cfg, data, spec, params["param"], list(params["values"]), ev)
        elif kind == "cost":
            res = cost_sensitivity(
                cfg, data, spec, tuple(params.get("spread_multipliers", (1.0, 1.5, 2.0, 3.0))), ev
            )
        elif kind == "slippage":
            res = slippage_stress(
                cfg, data, spec, tuple(params.get("slippage_points", (0, 2, 5, 10, 20))), ev
            )
        elif kind == "drawdown":
            res = drawdown_stress(trade_pnls, cfg.initial_balance, int(params.get("runs", 1000)), cfg.seed)
        else:
            res = baseline_comparison(cfg, data, spec, ev)
        status = "succeeded"
    except (BacktestError, ValueError) as exc:
        res, status = {"error": str(exc)}, "failed"
    with session_scope() as s:
        run = s.get(BacktestRun, run_id)
        assert run is not None
        v = dict(run.validation or {})
        v[kind] = {
            "status": status,
            "params": params,
            "result": json.loads(json.dumps(res, default=str)),
            "finished_at": utcnow().isoformat(),
        }
        run.validation = v
    return {"status": status}


def run_out(r: BacktestRun, detail: bool = False) -> dict[str, Any]:
    m = r.metrics or {}
    out: dict[str, Any] = {
        "id": r.id,
        "status": r.status,
        "progress": r.progress,
        "error": r.error,
        "created_at": r.created_at,
        "finished_at": r.finished_at,
        "bot_kind": r.bot_kind,
        "symbol": r.symbol,
        "start": r.start,
        "end": r.end,
        "data_source": r.data_source,
        "summary": {
            k: m.get(k)
            for k in (
                "net_pnl",
                "return_pct",
                "max_drawdown_pct",
                "trade_count",
                "cycle_count",
                "win_rate",
                "profit_factor",
            )
        }
        if m
        else None,
    }
    if detail:
        out.update(
            {
                "config": r.config,
                "metrics": m,
                "warnings": r.warnings,
                "assumptions": r.assumptions,
                "counters": r.counters,
                "equity_curve": r.equity_curve,
                "daily_equity": r.daily_equity,
                "data_fingerprint": r.data_fingerprint,
                "reproducibility": r.reproducibility,
                "validation": r.validation,
                "created_by": r.created_by,
            }
        )
    return out


def trades_csv(s: Session, run_id: str) -> str:
    rows = [
        t.data
        for t in s.scalars(
            select(BacktestTrade)
            .where(BacktestTrade.run_id == run_id, BacktestTrade.kind == "trade")
            .order_by(BacktestTrade.seq)
        )
    ]
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue()


def import_dataset(
    s: Session, content: bytes, filename: str, symbol: str, timeframe: str, source_tz: str, actor: str
) -> dict[str, Any]:
    reg, _ = load_registry(s)
    if not reg.has(symbol):
        raise bad_request(f"unknown instrument {symbol}")
    if len(content) > 200 * 1024 * 1024:
        raise bad_request("file too large (max 200 MB)")
    spec = reg.get(symbol)
    try:
        df = load_candles_csv(io.StringIO(content.decode("utf-8-sig")), spec, source_tz)
    except (ImportFormatError, UnicodeDecodeError, ValueError) as exc:
        raise bad_request(f"could not parse file: {exc}") from exc
    report = check_candles(df, timeframe, spec, utcnow())
    if not report.ok:
        return {"dataset": None, "quality": report.as_dict(), "imported": False}
    ds = Dataset(
        symbol=symbol,
        timeframe=timeframe,
        rows=len(df),
        start=df["time"].iloc[0].to_pydatetime(),
        end=df["time"].iloc[-1].to_pydatetime(),
        source="historical",
        path="",
        quality=report.as_dict(),
        created_by=actor,
    )
    s.add(ds)
    s.flush()
    path = get_settings().data_dir / "datasets" / f"{ds.id}.csv.gz"
    save_dataset(df, path)
    ds.path = str(path)
    audit(
        s,
        "market_data",
        f"dataset {ds.id} imported from {Path(filename).name}: {symbol} {timeframe} {len(df)} rows",
        actor=actor,
    )
    return {"dataset": dataset_out(ds), "quality": report.as_dict(), "imported": True}


def dataset_out(d: Dataset) -> dict[str, Any]:
    return {
        "id": d.id,
        "symbol": d.symbol,
        "timeframe": d.timeframe,
        "rows": d.rows,
        "start": d.start,
        "end": d.end,
        "source": d.source,
        "quality": d.quality,
        "created_at": d.created_at,
    }
