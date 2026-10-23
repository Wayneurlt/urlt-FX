from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from pydantic import ValidationError

from trading.engine.lifecycle import InvalidBotTransition, allowed_actions, next_status
from trading.instruments.conversion import (
    ConversionUnavailable,
    RateBook,
    pip_value_account,
    price_distance_value,
)
from trading.instruments.spec import InstrumentRegistry, InstrumentSpec, InvalidInstrumentSpec
from trading.interfaces.types import BotKind, BotStatus, DataSource, Environment, Side
from trading.market_data.models import Candle, Quote
from trading.news.eligibility import eligible_candidates, feed_is_fresh, in_window
from trading.news.models import EconomicEvent, EventOrigin, EventStatus, Impact
from trading.news.provider import CalendarHealth
from trading.risk.circuit_breakers import ActiveBreaker, BreakerScope, BreakerType
from trading.risk.config import RiskConfig, risk_increases
from trading.risk.daily import daily_loss, daily_reference_start, drawdown_pct, max_drawdown
from trading.risk.engine import OpenRiskItem, PortfolioSnapshot, RiskEngine, TradeProposal, evaluate_halts
from trading.risk.sizing import LegPlan, size_position
from trading.strategies.news_volatility import trailing as tr
from trading.strategies.news_volatility.config import NewsVolatilityConfig, VolatilityConfig
from trading.strategies.news_volatility.cycle import CycleState, InvalidTransition, check_transition
from trading.strategies.news_volatility.volatility import evaluate_volatility, volatility_trigger_series

T0 = datetime(2025, 3, 5, 13, 0, tzinfo=UTC)
REG = InstrumentRegistry.with_defaults()
EURUSD, USDJPY, EURGBP = REG.get("EURUSD"), REG.get("USDJPY"), REG.get("EURGBP")


def q(sym, bid, ask, t=T0):
    return Quote(sym, bid, ask, t, DataSource.SCRIPTED)


# --- instruments & conversion ----------------------------------------------------------
def test_pip_and_tick_conversions():
    assert EURUSD.pips_to_price(15) == pytest.approx(0.0015)
    assert USDJPY.pips_to_price(15) == pytest.approx(0.15)
    assert EURUSD.price_to_pips(0.00105) == pytest.approx(10.5)
    assert EURUSD.round_price(1.123456) == 1.12346
    assert USDJPY.round_price_down(150.1239) == 150.123


def test_volume_rounds_down_never_up():
    assert EURUSD.normalize_volume(0.129) == 0.12
    assert EURUSD.normalize_volume(0.009) == 0.0
    assert EURUSD.normalize_volume(500) == 100
    assert EURUSD.is_valid_volume(0.12) and not EURUSD.is_valid_volume(0.125)


def test_invalid_spec_rejected():
    with pytest.raises(InvalidInstrumentSpec):
        InstrumentSpec("BAD", "EUR", "USD", 5, 0.00001, 0.0001, 0, 0.01, 100, 0.01)


def test_currency_conversion_direct_inverse_cross():
    rb = RateBook({"EURUSD": 1.10, "USDJPY": 150.0, "GBPUSD": 1.25})
    assert rb.rate("USD", "JPY") == 150.0
    assert rb.rate("JPY", "USD") == pytest.approx(1 / 150)
    assert rb.rate("EUR", "JPY") == pytest.approx(165.0)  # cross via USD
    with pytest.raises(ConversionUnavailable):
        rb.rate("CHF", "USD")


def test_pip_values_differ_by_pair_and_account_currency():
    rb = RateBook({"EURUSD": 1.10, "USDJPY": 150.0, "GBPUSD": 1.25, "EURGBP": 0.88})
    assert pip_value_account(EURUSD, "USD", rb) == pytest.approx(10.0)
    assert pip_value_account(USDJPY, "USD", rb) == pytest.approx(1000 / 150)
    assert pip_value_account(EURUSD, "EUR", rb) == pytest.approx(10 / 1.10)
    assert pip_value_account(EURGBP, "USD", rb) == pytest.approx(12.5)
    assert price_distance_value(EURUSD, 0.0010, 0.5, "USD", rb) == pytest.approx(50.0)


# --- sizing & risk ----------------------------------------------------------------------
def test_dual_entry_sizing_counts_both_legs():
    rb = RateBook({"EURUSD": 1.1})
    legs = [LegPlan(Side.BUY, 1.10010, 1.09860), LegPlan(Side.SELL, 1.10000, 1.10150)]
    r = size_position(EURUSD, legs, 10_000, 0.5, "USD", rb)
    # each leg loses 15 pips * $10/pip/lot = $150/lot; both = $300/lot; budget $50 -> 0.16 lots
    assert r.volume == 0.16
    assert r.planned_risk == pytest.approx(48.0)
    assert r.planned_risk <= r.budget


def test_sizing_includes_costs_and_jpy():
    rb = RateBook({"USDJPY": 150.0})
    legs = [LegPlan(Side.BUY, 150.010, 149.860)]
    no_cost = size_position(USDJPY, legs, 10_000, 0.5, "USD", rb)
    with_cost = size_position(
        USDJPY, legs, 10_000, 0.5, "USD", rb, commission_per_lot_side=3.5, slippage_points=2
    )
    assert with_cost.volume <= no_cost.volume
    assert with_cost.planned_risk <= 50.0 + 1e-9


def test_sizing_rejects_below_min_volume():
    rb = RateBook({"EURUSD": 1.1})
    r = size_position(EURUSD, [LegPlan(Side.BUY, 1.1, 1.05)], 100, 0.5, "USD", rb)
    assert r.volume == 0.0 and "too small" in r.reason


def _pf(**kw):
    base = dict(
        account_currency="USD",
        equity=10_000,
        balance=10_000,
        day_start_equity=10_000,
        peak_equity=10_000,
        free_margin=9_000,
        used_margin=0,
    )
    base.update(kw)
    return PortfolioSnapshot(**base)


def _proposal(symbol="EURUSD", bot="nv", kind=BotKind.NEWS_VOLATILITY, quote=None, now=T0):
    quote = quote or q("EURUSD", 1.10000, 1.10010)
    return TradeProposal(
        bot,
        kind,
        Environment.SIMULATION,
        symbol,
        [
            LegPlan(Side.BUY, quote.ask, round(quote.ask - 0.0015, 5)),
            LegPlan(Side.SELL, quote.bid, round(quote.bid + 0.0015, 5)),
        ],
        quote,
        now,
        commission_per_lot_side=3.5,
        est_slippage_points=1,
    )


def test_risk_engine_approves_within_limits():
    d = RiskEngine(RiskConfig()).evaluate(_proposal(), _pf(), EURUSD, RateBook({"EURUSD": 1.1}))
    assert d.approved, d.reasons
    assert 0 < d.planned_risk <= 50 and d.planned_risk_pct <= 0.5


def test_risk_engine_rejects_stale_quote_wide_spread_and_breakers():
    eng = RiskEngine(RiskConfig())
    stale = _proposal(quote=q("EURUSD", 1.1, 1.1001, T0 - timedelta(seconds=30)))
    assert "quote_freshness" in " ".join(
        eng.evaluate(stale, _pf(), EURUSD, RateBook({"EURUSD": 1.1})).reasons
    )
    wide = _proposal(quote=q("EURUSD", 1.1, 1.1005))
    assert "spread" in " ".join(eng.evaluate(wide, _pf(), EURUSD, RateBook({"EURUSD": 1.1})).reasons)
    pf = _pf(
        active_breakers=[ActiveBreaker(BreakerType.STALE_MARKET_DATA, BreakerScope.INSTRUMENT, "EURUSD", "x")]
    )
    assert not eng.evaluate(_proposal(), pf, EURUSD, RateBook({"EURUSD": 1.1})).approved
    other = _pf(
        active_breakers=[ActiveBreaker(BreakerType.STALE_MARKET_DATA, BreakerScope.INSTRUMENT, "GBPUSD", "x")]
    )
    assert eng.evaluate(_proposal(), other, EURUSD, RateBook({"EURUSD": 1.1})).approved


def test_risk_aggregates_both_bots_and_currency_exposure():
    eng = RiskEngine(RiskConfig())
    open_items = [
        OpenRiskItem("daily", BotKind.DAILY_TRADING, "c1", "GBPUSD", "GBP", "USD", 25.0, "USD_MAJORS"),
        OpenRiskItem("nv", BotKind.NEWS_VOLATILITY, "c2", "USDJPY", "USD", "JPY", 48.0, "JPY"),
    ]
    d = eng.evaluate(_proposal(), _pf(open_items=open_items), EURUSD, RateBook({"EURUSD": 1.1}))
    names = {c.name for c in d.checks if not c.passed}
    assert "concurrent_cycles_global" in names  # 2 active across both bots
    assert "currency_risk_USD" in names  # USD already carries 73 of the 75 cap
    assert "total_open_risk" in names


def test_risk_blocks_per_instrument_and_consecutive_losses():
    eng = RiskEngine(RiskConfig())
    same = [OpenRiskItem("nv", BotKind.NEWS_VOLATILITY, "c1", "EURUSD", "EUR", "USD", 10.0)]
    d = eng.evaluate(_proposal(), _pf(open_items=same), EURUSD, RateBook({"EURUSD": 1.1}))
    assert "cycles_per_instrument" in " ".join(d.reasons)
    d = eng.evaluate(_proposal(), _pf(consecutive_losses={"nv": 3}), EURUSD, RateBook({"EURUSD": 1.1}))
    assert "consecutive_losses" in " ".join(d.reasons)


def test_risk_blocks_when_conversion_missing_or_spec_missing():
    eng = RiskEngine(RiskConfig())
    assert not eng.evaluate(_proposal(), _pf(), EURUSD, None).approved
    assert not eng.evaluate(_proposal(), _pf(), None, RateBook({"EURUSD": 1.1})).approved
    p = _proposal(symbol="EURGBP", quote=q("EURGBP", 0.85, 0.8501))
    d = eng.evaluate(p, _pf(), EURGBP, RateBook({}))  # GBP->USD unknown
    assert "conversion" in " ".join(d.reasons)


def test_daily_loss_and_drawdown_definitions():
    assert daily_loss(10_000, 9_850) == 150
    assert daily_loss(10_000, 10_100) == 0
    assert drawdown_pct(12_000, 11_400) == pytest.approx(5.0)
    assert max_drawdown([100, 120, 90, 130, 117])[0] == 30
    assert daily_reference_start(datetime(2025, 3, 5, 3, tzinfo=UTC), 5) == datetime(
        2025, 3, 4, 5, tzinfo=UTC
    )
    halts = evaluate_halts(RiskConfig(), _pf(equity=9_840, balance=9_900), Environment.SIMULATION)
    assert [h[0] for h in halts] == ["daily_loss_limit"]
    # no double counting: realized loss moved from floating to balance leaves equity unchanged
    assert daily_loss(10_000, 9_900 + 0) == daily_loss(10_000, 10_000 - 100)


def test_daily_loss_blocks_new_entry_near_limit():
    d = RiskEngine(RiskConfig()).evaluate(
        _proposal(), _pf(equity=9_870, balance=9_870), EURUSD, RateBook({"EURUSD": 1.1})
    )
    assert "daily_loss" in " ".join(d.reasons)


# --- configuration ------------------------------------------------------------------------
def test_risk_config_hard_caps_and_cross_checks():
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"news_volatility": {"max_cycle_risk_pct": 3.0}})
    with pytest.raises(ValidationError):
        RiskConfig.model_validate(
            {"global": {"max_daily_loss_pct": 1.0}, "daily_trading": {"max_daily_loss_pct": 1.2}}
        )
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"global": {"unknown_field": 1}})
    a, b = RiskConfig(), RiskConfig.model_validate({"global": {"max_daily_loss_pct": 2.0}})
    assert risk_increases(a, b) == ["global.max_daily_loss_pct"]
    assert risk_increases(b, a) == []


def test_strategy_config_validation():
    with pytest.raises(ValidationError):
        NewsVolatilityConfig(entry_mode={"news": False, "volatility": False})
    with pytest.raises(ValidationError):
        NewsVolatilityConfig(instruments=[])
    c = NewsVolatilityConfig(instrument_overrides={"USDJPY": {"trailing_distance_pips": 20}})
    assert c.trailing_pips("USDJPY") == 20 and c.trailing_pips("EURUSD") == 10


# --- volatility & news ----------------------------------------------------------------------
def _candles(n, burst_at=None, start=T0 - timedelta(minutes=400)):
    rng = np.random.default_rng(3)
    out, px = [], 1.1
    for i in range(n):
        hot = burst_at is not None and i >= burst_at  # sustained burst, as in a real volatility event
        r = 0.0002 * (8 if hot else 1)
        o = px
        c = o + rng.normal(0, r / 3) + (r if hot else 0)
        h, lo = max(o, c) + r / 2, min(o, c) - r / 2
        out.append(
            Candle(start + timedelta(minutes=i), round(o, 5), round(h, 5), round(lo, 5), round(c, 5), 0.00008)
        )
        px = c
    return out


def test_volatility_detector_triggers_on_burst_only():
    cfg = VolatilityConfig()
    calm = _candles(300)
    now = calm[-1].time + timedelta(minutes=1)
    assert not evaluate_volatility(calm, now, EURUSD, cfg).triggered
    burst = _candles(300, burst_at=296)
    sig = evaluate_volatility(burst, now, EURUSD, cfg)
    assert sig.triggered, sig.as_dict()


def test_volatility_ignores_unfinished_candle_and_insufficient_data():
    cfg = VolatilityConfig()
    burst = _candles(300, burst_at=299)
    still_open = burst[-1].time + timedelta(seconds=30)  # last candle not closed yet
    assert not evaluate_volatility(burst, still_open, EURUSD, cfg).triggered
    assert evaluate_volatility(burst[:50], still_open, EURUSD, cfg).rules[0].name == "data_sufficient"


def test_vectorised_detector_matches_live_detector():
    cfg = VolatilityConfig(atr_ratio_min=1.3, range_multiple_min=1.5, expansion_min_pips=2)
    cs = _candles(400, burst_at=350)
    arr = volatility_trigger_series(
        np.array([c.high for c in cs]),
        np.array([c.low for c in cs]),
        np.array([c.close for c in cs]),
        EURUSD,
        cfg,
    )
    for i in (250, 300, 349, 350, 351, 399):
        live = evaluate_volatility(cs[: i + 1], cs[i].time + timedelta(minutes=1), EURUSD, cfg).triggered
        assert live == bool(arr[i]), i


def _event(eid="e1", ccy="USD", at=T0, impact=Impact.HIGH, status=EventStatus.SCHEDULED):
    return EconomicEvent(eid, ccy, "Non-Farm Payrolls", at, impact, "mock", EventOrigin.MOCK, status)


def test_news_window_and_dedupe():
    cfg = NewsVolatilityConfig().news
    ev = _event()
    assert in_window(ev, T0 - timedelta(seconds=60), cfg) and not in_window(
        ev, T0 - timedelta(seconds=61), cfg
    )
    specs = [EURUSD, USDJPY, EURGBP]
    cands = eligible_candidates("nv", [ev], specs, T0, cfg, set())
    assert {c.symbol for c in cands} == {"EURUSD", "USDJPY"}
    keys = {c.dedupe_key for c in cands}
    assert eligible_candidates("nv", [ev], specs, T0, cfg, keys) == []
    assert eligible_candidates("nv", [_event(impact=Impact.LOW)], specs, T0, cfg, set()) == []
    assert eligible_candidates("nv", [_event(status=EventStatus.CANCELLED)], specs, T0, cfg, set()) == []


def test_stale_or_missing_calendar_blocks_news():
    cfg = NewsVolatilityConfig().news
    assert not feed_is_fresh(CalendarHealth(False, None, "down"), T0, cfg)[0]
    assert not feed_is_fresh(CalendarHealth(True, T0 - timedelta(days=2), "old"), T0, cfg)[0]
    assert feed_is_fresh(CalendarHealth(True, T0 - timedelta(minutes=5), "ok"), T0, cfg)[0]


# --- trailing ---------------------------------------------------------------------------------
def test_buy_trailing_uses_highest_bid():
    st = tr.activate(Side.BUY, q("EURUSD", 1.10200, 1.10210), 0.0010, 1.09860, T0.isoformat())
    assert st.water_mark == 1.10200
    assert tr.next_stop(st, q("EURUSD", 1.10300, 1.10310), EURUSD) == pytest.approx(1.10200)
    st.confirmed_sl = 1.10200
    assert tr.next_stop(st, q("EURUSD", 1.10250, 1.10260), EURUSD) is None  # never down
    assert st.water_mark == 1.10300


def test_sell_trailing_uses_lowest_ask():
    st = tr.activate(Side.SELL, q("EURUSD", 1.09790, 1.09800), 0.0010, 1.10150, T0.isoformat())
    assert st.water_mark == 1.09800
    assert tr.next_stop(st, q("EURUSD", 1.09690, 1.09700), EURUSD) == pytest.approx(
        1.09800
    )  # 1.09700 + 10 pips
    st.confirmed_sl = 1.09800
    assert tr.next_stop(st, q("EURUSD", 1.09740, 1.09750), EURUSD) is None


def test_trailing_respects_min_stop_distance_and_min_step():
    spec = InstrumentSpec(
        "EURUSD", "EUR", "USD", 5, 0.00001, 0.0001, 100_000, 0.01, 100, 0.01, min_stop_distance=0.0005
    )
    st = tr.activate(Side.BUY, q("EURUSD", 1.1, 1.1001), 0.0002, 1.098, T0.isoformat())
    cand = tr.next_stop(st, q("EURUSD", 1.1, 1.1001), spec)
    assert cand is not None and cand <= 1.1 - 0.0005  # clamped below bid - stops level
    st.confirmed_sl = cand
    assert tr.next_stop(st, q("EURUSD", 1.10002, 1.10012), spec, min_step=0.0001) is None


def test_protective_only_helper():
    assert tr.is_protective_improvement(Side.BUY, 1.1, 1.1001, EURUSD)
    assert not tr.is_protective_improvement(Side.BUY, 1.1, 1.0999, EURUSD)
    assert tr.is_protective_improvement(Side.SELL, 1.1, 1.0999, EURUSD)
    assert not tr.is_protective_improvement(Side.SELL, 1.1, 1.1001, EURUSD)


# --- state machines -------------------------------------------------------------------------------
def test_cycle_transitions():
    check_transition(CycleState.BOTH_LEGS_OPEN, CycleState.ONE_LEG_STOPPED)
    check_transition(CycleState.ONE_LEG_STOPPED, CycleState.TRAILING_ACTIVE)
    with pytest.raises(InvalidTransition):
        check_transition(
            CycleState.BOTH_LEGS_OPEN, CycleState.TRAILING_ACTIVE
        )  # must confirm a stop-out first
    with pytest.raises(InvalidTransition):
        check_transition(CycleState.COMPLETED, CycleState.CLOSING)


def test_bot_lifecycle_transitions():
    assert next_status(BotStatus.DISABLED, "enable") is BotStatus.STOPPED
    assert next_status(BotStatus.STOPPED, "start") is BotStatus.STARTING
    assert next_status(BotStatus.RUNNING, "pause") is BotStatus.PAUSED
    assert next_status(BotStatus.RISK_HALTED, "stop") is BotStatus.STOPPED
    with pytest.raises(InvalidBotTransition):
        next_status(BotStatus.DISABLED, "start")
    with pytest.raises(InvalidBotTransition):
        next_status(BotStatus.RUNNING, "disable")
    assert allowed_actions(BotStatus.RUNNING) == ["pause", "stop"]


def test_loosening_environment_and_instrument_caps_is_detected():
    base = RiskConfig()
    live_up = RiskConfig.model_validate(
        {
            "environments": {
                **{k: v.model_dump() for k, v in base.environments.items()},
                "live": {"max_cycle_risk_pct": 1.0, "max_trade_risk_pct": 0.25, "max_daily_loss_pct": 1.5},
            }
        }
    )
    assert risk_increases(base, live_up) == ["environments.live.max_cycle_risk_pct"]
    removed = RiskConfig.model_validate(
        {"environments": {**{k: v.model_dump() for k, v in base.environments.items()}, "live": {}}}
    )
    assert set(risk_increases(base, removed)) == {
        "environments.live.max_cycle_risk_pct",
        "environments.live.max_trade_risk_pct",
        "environments.live.max_daily_loss_pct",
    }
    wider = RiskConfig.model_validate({"instruments": {"EURUSD": {"max_spread_pips": 6}}})
    assert risk_increases(base, wider) == ["instruments.EURUSD.max_spread_pips"]
    assert risk_increases(live_up, base) == []  # tightening needs no confirmation
