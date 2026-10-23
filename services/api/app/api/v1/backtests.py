from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.common import PageParams, paginate
from app.auth.deps import Principal, current_principal, require_admin
from app.core.errors import not_found
from app.db.base import get_db
from app.models import BacktestRun, BacktestTrade, Dataset
from app.services import backtest_service

router = APIRouter(tags=["backtests"])


@router.post("/backtests")
def create(
    body: dict[str, Any],
    p: Principal = Depends(require_admin),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    run = backtest_service.create_run(s, body, p.name)
    return backtest_service.run_out(run)


@router.get("/backtests")
def list_runs(
    bot_kind: str | None = None,
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    q = select(BacktestRun).order_by(BacktestRun.created_at.desc())
    if bot_kind:
        q = q.where(BacktestRun.bot_kind == bot_kind)
    return paginate(s, q, p, backtest_service.run_out)


def _run(s: Session, run_id: str) -> BacktestRun:
    r = s.get(BacktestRun, run_id)
    if r is None:
        raise not_found("backtest")
    return r


@router.get("/backtests/{run_id}")
def detail(
    run_id: str, _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    return backtest_service.run_out(_run(s, run_id), detail=True)


@router.get("/backtests/{run_id}/trades")
def trades(
    run_id: str,
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    _run(s, run_id)
    q = (
        select(BacktestTrade)
        .where(BacktestTrade.run_id == run_id, BacktestTrade.kind == "trade")
        .order_by(BacktestTrade.seq)
    )
    return paginate(s, q, p, lambda t: t.data)


@router.get("/backtests/{run_id}/cycles")
def cycles(
    run_id: str,
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    _run(s, run_id)
    q = (
        select(BacktestTrade)
        .where(BacktestTrade.run_id == run_id, BacktestTrade.kind == "cycle")
        .order_by(BacktestTrade.seq)
    )
    return paginate(s, q, p, lambda t: t.data)


@router.get("/backtests/{run_id}/export.csv", response_class=PlainTextResponse)
def export(
    run_id: str, _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> PlainTextResponse:
    _run(s, run_id)
    return PlainTextResponse(
        backtest_service.trades_csv(s, run_id),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=backtest-{run_id}.csv"},
    )


class ValidationIn(BaseModel):
    kind: str
    params: dict[str, Any] = {}


@router.post("/backtests/{run_id}/validation")
def validation(
    run_id: str,
    body: ValidationIn,
    p: Principal = Depends(require_admin),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    return backtest_service.queue_validation(s, run_id, body.kind, body.params, p.name)


@router.get("/datasets")
def datasets(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> list[dict]:
    return [
        backtest_service.dataset_out(d)
        for d in s.scalars(select(Dataset).order_by(Dataset.created_at.desc()))
    ]


@router.post("/datasets/import")
async def import_dataset(
    file: UploadFile = File(...),
    symbol: str = Form(...),
    timeframe: str = Form("M1"),
    source_tz: str = Form("UTC"),
    p: Principal = Depends(require_admin),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    content = await file.read()
    return backtest_service.import_dataset(
        s, content, file.filename or "upload.csv", symbol, timeframe, source_tz, p.name
    )
