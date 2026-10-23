import type { Bot, DashboardCharts, DashboardSummary, Integration, RiskConfigResponse, SystemStatus } from "@/types/api";

export function makeBot(over: Partial<Bot> = {}): Bot {
  return {
    id: "bot-1",
    name: "News Breakout",
    kind: "news_volatility",
    strategy_name: "news_breakout",
    strategy_version: "1.2.0",
    status: "running",
    enabled: true,
    environment: "paper",
    instruments: ["EURUSD"],
    timeframe: "M1",
    max_risk_pct: 0.5,
    active_cycles: 0,
    open_positions: 0,
    daily_pnl: 0,
    lifetime_pnl: 12.5,
    last_evaluation_at: null,
    last_execution_at: null,
    last_error: null,
    magic: 1001,
    config_version: 3,
    allowed_actions: ["pause", "stop"],
    ...over,
  };
}

export const status: SystemStatus = {
  environment: "paper",
  live_trading_enabled: false,
  broker: { name: "sim", connected: true, simulated: true, detail: null, account_id: null, margin_mode: null },
  market_data: { provider: "synthetic", data_source: "synthetic", last_update: null, fresh: true },
  calendar: { provider: "sample", available: true, origin: "sample", detail: null },
  worker: { alive: true, last_heartbeat: null },
  last_reconciliation: null,
  active_breakers: 0,
  unread_notifications: 0,
  server_time: "2025-03-05T13:00:00+00:00",
};

export function makeSummary(over: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    account: {
      currency: "USD",
      balance: 10000,
      equity: 10012.5,
      floating_pnl: 12.5,
      realized_pnl: 0,
      daily_pnl: 12.5,
      drawdown_pct: 0,
      margin: 0,
      free_margin: 10012.5,
      margin_level: null,
      simulated: true,
      data_source: "synthetic",
      label: "Paper account",
      as_of: "2025-03-05T13:00:00+00:00",
    },
    performance: {
      today_pnl: 12.5,
      week_pnl: null,
      month_pnl: null,
      total_return_pct: null,
      max_drawdown_pct: null,
      win_rate: null,
      profit_factor: null,
      average_trade: null,
      completed_trades: 0,
      completed_cycles: 0,
      insufficient: ["week_pnl", "month_pnl", "total_return_pct", "max_drawdown_pct", "win_rate", "profit_factor", "average_trade"],
    },
    open_positions: 0,
    active_cycles: 0,
    bots: [],
    active_breakers: [],
    recent_alerts: [],
    recent_executions: [],
    upcoming_events: [],
    health: [],
    ...over,
  };
}

export const emptyCharts: DashboardCharts = {
  equity: [],
  daily_pnl: [],
  drawdown: [],
  by_bot: [],
  by_instrument: [],
  recent_outcomes: [],
};

export const riskConfig: RiskConfigResponse = {
  config: { global: { risk_per_trade_pct: 0.5, max_daily_loss_pct: 2 }, news_volatility: { max_spread_pips: 3 } },
  hard_limits: { risk_per_trade_pct: 2 },
  fields: [
    {
      path: "global.risk_per_trade_pct",
      section: "global",
      label: "Risk per trade",
      description: "Risk per entry as % of equity",
      unit: "%",
      default: 0.5,
      min: 0.01,
      max: 2,
    },
    {
      path: "global.max_daily_loss_pct",
      section: "global",
      label: "Max daily loss",
      description: "Daily loss limit",
      unit: "%",
      default: 2,
      min: 0.1,
      max: 10,
    },
    {
      path: "news_volatility.max_spread_pips",
      section: "news_volatility",
      label: "Max spread",
      description: "Max spread at entry",
      unit: "pips",
      default: 3,
      min: 0.1,
      max: 20,
    },
  ],
  updated_at: null,
  updated_by: null,
  version: 4,
};

export const brokerIntegration: Integration = {
  key: "mt5",
  name: "MetaTrader 5",
  category: "broker",
  configured: true,
  enabled: true,
  status: "unverified",
  last_check: null,
  detail: null,
  fields: [
    { name: "login", label: "Login", secret: false, required: true, value: "123456", is_set: true },
    // A misbehaving backend that leaks a secret value must still never be displayed.
    { name: "password", label: "Password", secret: true, required: true, value: "hunter2-SECRET", is_set: true },
    { name: "api_token", label: "API token", secret: true, required: false, value: null, is_set: false },
  ],
  docs: "Connect via the MT5 bridge.",
  supports_test: true,
};
