/**
 * TypeScript types mirroring docs/API.md (base /api/v1).
 * All times are ISO-8601 UTC strings. Numbers that may be absent are typed `number | null`.
 */

export type ISODate = string;

export type Environment = "simulation" | "paper" | "live";
export type DataSource = "synthetic" | "historical" | "delayed" | "live" | "scripted";
export type Role = "admin" | "viewer";

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown> | null;
  };
}

/* ---------------------------------------------------------------- Auth */

export interface User {
  id: string | number;
  username: string;
  role: Role;
  timezone: string;
}

export interface AuthResponse {
  user: User;
  csrf_token: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

/* -------------------------------------------------------------- System */

export type HealthState = "ok" | "degraded" | "down" | "unknown" | "not_configured";

export interface HealthCheck {
  name: string;
  state: HealthState;
  detail: string | null;
  checked_at: ISODate | null;
  last_success: ISODate | null;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  checks: HealthCheck[];
}

export interface Check {
  name: string;
  passed: boolean;
  detail: string | null;
}

export interface SystemStatus {
  environment: Environment;
  live_trading_enabled: boolean;
  broker: {
    name: string;
    connected: boolean;
    simulated: boolean;
    detail: string | null;
    account_id: string | null;
    margin_mode: string | null;
  };
  market_data: {
    provider: string;
    data_source: DataSource;
    last_update: ISODate | null;
    fresh: boolean;
  };
  calendar: {
    provider: string;
    available: boolean;
    origin: "sample" | "provider" | "mock" | null;
    detail: string | null;
  };
  worker: { alive: boolean; last_heartbeat: ISODate | null };
  last_reconciliation: ISODate | null;
  active_breakers: number;
  unread_notifications: number;
  server_time: ISODate;
}

export interface LiveReadiness {
  ready: boolean;
  checks: Check[];
}

export interface EnvironmentChangeRequest {
  target: Environment;
  confirm_text?: string;
}

export interface EnvironmentChangeResponse {
  environment: Environment;
  checks: Check[];
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "dead";

export interface Job {
  id: string;
  type: string;
  status: JobStatus;
  attempts: number;
  max_attempts: number;
  error: string | null;
  created_at: ISODate;
  started_at: ISODate | null;
  finished_at: ISODate | null;
  payload: Record<string, unknown> | null;
}

export type SystemMetrics = Record<string, unknown>;

/* ----------------------------------------------------------- Dashboard */

export interface AccountSummary {
  currency: string;
  balance: number | null;
  equity: number | null;
  floating_pnl: number | null;
  realized_pnl: number | null;
  daily_pnl: number | null;
  drawdown_pct: number | null;
  margin: number | null;
  free_margin: number | null;
  margin_level: number | null;
  simulated: boolean;
  data_source: DataSource | string;
  label: string | null;
  as_of: ISODate | null;
}

export interface PerformanceSummary {
  today_pnl: number | null;
  week_pnl: number | null;
  month_pnl: number | null;
  total_return_pct: number | null;
  max_drawdown_pct: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  average_trade: number | null;
  completed_trades: number | null;
  completed_cycles: number | null;
  insufficient: string[];
}

export interface BotBrief {
  id: string;
  name: string;
  kind: BotKind;
  status: BotStatus;
  daily_pnl: number | null;
}

export interface Execution {
  time: ISODate;
  cycle_id: string | null;
  bot_name: string | null;
  symbol: string;
  side: string;
  kind: "fill" | "rejected" | "close";
  price: number | null;
  volume: number | null;
  detail: string | null;
  simulated?: boolean;
}

export interface DashboardSummary {
  account: AccountSummary;
  performance: PerformanceSummary;
  open_positions: number;
  active_cycles: number;
  bots: BotBrief[];
  active_breakers: Breaker[];
  recent_alerts: Notification[];
  recent_executions: Execution[];
  upcoming_events: NewsEvent[];
  health: HealthCheck[];
}

export interface DashboardCharts {
  equity: { time: ISODate; equity: number; balance: number }[];
  daily_pnl: { date: string; pnl: number }[];
  drawdown: { time: ISODate; drawdown_pct: number }[];
  by_bot: { bot_id: string; name: string; net_pnl: number; cycles: number }[];
  by_instrument: { symbol: string; net_pnl: number; trades: number }[];
  recent_outcomes: {
    cycle_id: string;
    bot_name: string;
    symbol: string;
    net_pnl: number;
    closed_at: ISODate;
    outcome: string;
  }[];
}

/* ---------------------------------------------------------------- Bots */

export type BotKind = "news_volatility" | "daily_trading";
export type BotStatus =
  | "disabled"
  | "stopped"
  | "starting"
  | "running"
  | "paused"
  | "risk_halted"
  | "error";
export type BotAction = "enable" | "disable" | "start" | "pause" | "resume" | "stop";
export const BOT_ACTIONS: BotAction[] = ["enable", "disable", "start", "pause", "resume", "stop"];

export interface Bot {
  id: string;
  name: string;
  kind: BotKind;
  strategy_name: string;
  strategy_version: string;
  status: BotStatus;
  enabled: boolean;
  environment: Environment;
  instruments: string[];
  timeframe: string | null;
  max_risk_pct: number;
  active_cycles: number;
  open_positions: number;
  daily_pnl: number | null;
  lifetime_pnl: number | null;
  last_evaluation_at: ISODate | null;
  last_execution_at: ISODate | null;
  last_error: string | null;
  magic: number | string | null;
  config_version: number | string | null;
  allowed_actions: string[];
}

export interface ShutdownPreview {
  cycles: Cycle[];
  positions: Position[];
  policy: unknown;
}

export interface ShutdownRequest {
  liquidate: boolean;
  confirm_text?: string;
}

export interface ShutdownResponse {
  bot: Bot;
  closed_cycles: string[];
}

export interface BotConfig {
  version: number | string;
  config: Record<string, unknown>;
  effective: Record<string, unknown>;
  risk_effective: Record<string, unknown> | null;
  updated_at: ISODate | null;
  updated_by: string | null;
  schema: Record<string, unknown> | null;
}

export interface BotConfigHistoryEntry {
  version: number | string;
  config: Record<string, unknown>;
  created_at: ISODate;
  created_by: string | null;
  note: string | null;
}

export interface Evaluation {
  time: ISODate;
  symbol: string;
  mode: string;
  result: string;
  reason: string | null;
  details: Record<string, unknown> | null;
}

export interface EvaluateNowResponse {
  signals: unknown[];
  diagnostics: unknown;
  blocked: unknown[];
  entries: { cycle_id: string | null; approved: boolean; reasons: string[] }[];
}

/* ------------------------------------------- Positions, cycles, trades */

export type Side = "BUY" | "SELL" | string;

export interface Position {
  cycle_id: string;
  leg_id: string;
  bot_id: string;
  bot_name: string;
  symbol: string;
  side: Side;
  volume: number;
  entry_price: number | null;
  current_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  floating_pnl: number | null;
  opened_at: ISODate | null;
  trailing_active: boolean;
  simulated: boolean;
  environment: Environment;
}

export interface StopHistoryEntry {
  time?: ISODate;
  action?: string;
  sl?: number | null;
  previous?: number | null;
  price?: number | null;
  reason?: string | null;
  [key: string]: unknown;
}

export interface Leg {
  id: string;
  side: Side;
  client_order_id: string | null;
  intent_status: string | null;
  status: string;
  requested_volume: number | null;
  filled_volume: number | null;
  position_id: string | null;
  broker_order_id: string | null;
  fill_price: number | null;
  fill_time: ISODate | null;
  intended_sl: number | null;
  confirmed_sl: number | null;
  tp: number | null;
  exit_price: number | null;
  exit_time: ISODate | null;
  exit_reason: string | null;
  gross_pnl: number | null;
  commission: number | null;
  swap: number | null;
  net_pnl: number | null;
  floating_pnl: number | null;
  slippage_pips: number | null;
  reject_reason: string | null;
  stop_history: StopHistoryEntry[];
}

export interface Cycle {
  id: string;
  kind: "dual_entry" | "single";
  bot_id: string;
  bot_name: string;
  strategy_version: string;
  trigger_type: string;
  event_id: string | null;
  symbol: string;
  environment: Environment;
  state: string;
  volume: number | null;
  planned_risk: number | null;
  realized_pnl: number | null;
  outcome: string | null;
  created_at: ISODate;
  updated_at: ISODate | null;
  closed_at: ISODate | null;
  trailing_activated_at: ISODate | null;
  simulated: boolean;
  legs: Leg[];
  notes: string[];
  error: string | null;
}

export interface CycleDetail extends Cycle {
  risk_decision: RiskDecision | null;
  signal: Record<string, unknown> | null;
  config_snapshot: Record<string, unknown> | null;
  trailing: Record<string, unknown> | null;
  events: LogEntry[];
}

export interface TradeRow extends Leg {
  cycle_id: string;
  bot_id: string;
  bot_name: string;
  symbol: string;
  strategy_version: string;
  trigger_type: string;
  environment: Environment;
  cycle_state: string;
  simulated: boolean;
}

export interface CycleFilters {
  bot_id?: string;
  state?: string;
  active?: boolean;
  symbol?: string;
  page?: number;
  page_size?: number;
}

export interface TradeFilters {
  start?: string;
  end?: string;
  bot_id?: string;
  strategy_version?: string;
  symbol?: string;
  side?: string;
  environment?: string;
  trigger?: string;
  outcome?: "win" | "loss" | "open" | "";
  status?: string;
  cycle_id?: string;
  sort?: string;
  page?: number;
  page_size?: number;
}

export interface ManualEntryRequest {
  bot_id: string;
  symbol: string;
  side?: "BUY" | "SELL";
  stop_pips: number;
  target_pips?: number;
}

export interface PaperAccount {
  balance: number;
  equity: number;
  simulated: true;
  reset_at: ISODate | null;
  [key: string]: unknown;
}

export interface PaperResetRequest {
  initial_balance: number;
  confirm_text: string;
}

/* ---------------------------------------------------------------- Risk */

export type RiskSection = "global" | "news_volatility" | "daily_trading" | string;

export interface RiskField {
  path: string;
  section: RiskSection;
  label: string;
  description: string;
  unit: "%" | "count" | "pips" | "seconds" | "choice" | "";
  default: unknown;
  min: number | null;
  max: number | null;
  choices?: string[];
  /** Empty input is allowed and saved as null (e.g. "no extra cap" for environment limits). */
  nullable?: boolean;
}

export interface RiskConfigResponse {
  config: Record<string, unknown>;
  hard_limits: Record<string, unknown>;
  fields: RiskField[];
  updated_at: ISODate | null;
  updated_by: string | null;
  version: number | string;
}

export interface RiskChange {
  path: string;
  before: unknown;
  after: unknown;
}

export interface RiskConfigUpdateResponse {
  config: Record<string, unknown>;
  changes: RiskChange[];
  loosened: string[];
}

export interface RiskConfigHistoryEntry {
  version: number | string;
  config: Record<string, unknown>;
  created_at: ISODate;
  created_by: string | null;
  changes: RiskChange[] | null;
}

export interface RiskStatus {
  currency: string;
  equity: number | null;
  day_start_equity: number | null;
  daily_loss: number | null;
  daily_loss_limit: number | null;
  daily_loss_used_pct: number | null;
  drawdown_pct: number | null;
  drawdown_limit_pct: number | null;
  open_risk: number | null;
  open_risk_cap: number | null;
  active_cycles: number;
  max_concurrent_cycles: number | null;
  currency_exposure: { currency: string; planned_risk: number; cap: number | null }[];
  instruments: {
    symbol: string;
    spread_pips: number | null;
    max_spread_pips: number | null;
    enabled: boolean;
    quote_age_seconds: number | null;
  }[];
  halted: boolean;
  halt_reasons: string[];
  blocked_attempts_today: number;
}

export interface RiskDecision {
  id: string;
  created_at: ISODate;
  bot_id: string | null;
  bot_name: string | null;
  symbol: string;
  approved: boolean;
  volume: number | null;
  planned_risk: number | null;
  planned_risk_pct: number | null;
  checks: Check[];
  reasons: string[];
  cycle_id: string | null;
}

export interface Breaker {
  id: string;
  type: string;
  scope: "global" | "bot" | "instrument";
  scope_key: string | null;
  reason: string;
  critical: boolean;
  auto_clear: boolean;
  recovery: string | null;
  activated_at: ISODate;
  cleared_at: ISODate | null;
  cleared_by: string | null;
  clear_note: string | null;
  active: boolean;
}

/* ----------------------------------------------- Backtests & datasets */

export interface BacktestCosts {
  spread_mode: string;
  fixed_spread_pips: number;
  spread_multiplier: number;
  slippage_points: number;
  random_slippage_points: number;
  second_leg_extra_slippage_points: number;
  commission_per_lot_side: number;
  swap_long_per_lot_day: number;
  swap_short_per_lot_day: number;
}

export interface BacktestExecution {
  latency_bars: number;
  reject_probability: number;
  partial_fill_probability: number;
  partial_fill_ratio: number;
  leverage: number;
}

export interface BacktestRisk {
  risk_per_entry_pct: number;
  max_daily_loss_pct: number;
  max_consecutive_losses: number;
  max_spread_pips: number;
}

export interface BacktestRequest {
  bot_kind: BotKind;
  symbol: string;
  base_timeframe: "M1";
  start: string;
  end: string;
  initial_balance: number;
  account_currency: string;
  dataset_id: string | null;
  seed: number | null;
  strategy: Record<string, unknown>;
  costs: BacktestCosts;
  execution: BacktestExecution;
  risk: BacktestRisk;
  conversion_rates: Record<string, number>;
}

export type BacktestStatus = "queued" | "running" | "succeeded" | "failed";

export interface BacktestSummary {
  net_pnl: number | null;
  return_pct: number | null;
  max_drawdown_pct: number | null;
  trade_count: number | null;
  [key: string]: unknown;
}

export interface BacktestRun {
  id: string;
  status: BacktestStatus;
  progress: number;
  error: string | null;
  created_at: ISODate;
  finished_at: ISODate | null;
  bot_kind: BotKind;
  symbol: string;
  start: string;
  end: string;
  data_source: DataSource | string;
  summary: BacktestSummary | null;
}

export interface BacktestDetail extends BacktestRun {
  config: Record<string, unknown> | null;
  metrics: Record<string, unknown> | null;
  warnings: string[];
  assumptions: string[];
  counters: Record<string, unknown> | null;
  equity_curve: { time: ISODate; equity: number }[];
  daily_equity: { time: ISODate; equity: number }[];
  data_fingerprint: string | null;
  reproducibility: Record<string, unknown> | null;
  validation: Record<string, unknown> | null;
}

export type ValidationKind =
  | "in_out_sample"
  | "walk_forward"
  | "sensitivity"
  | "cost"
  | "slippage"
  | "drawdown"
  | "baseline";

export const VALIDATION_KINDS: ValidationKind[] = [
  "in_out_sample",
  "walk_forward",
  "sensitivity",
  "cost",
  "slippage",
  "drawdown",
  "baseline",
];

export interface ValidationRequest {
  kind: ValidationKind;
  params: Record<string, unknown>;
}

/** Ledger rows are not formally specified in docs/API.md; treated as generic records. */
export type BacktestLedgerRow = Record<string, unknown>;

export interface Dataset {
  id: string;
  symbol: string;
  timeframe: string;
  rows: number;
  start: ISODate;
  end: ISODate;
  source: string;
  quality: unknown;
  created_at: ISODate;
}

export interface DatasetImportResponse {
  dataset?: Dataset;
  quality?: unknown;
  [key: string]: unknown;
}

/* ------------------------------------------------------ Market & news */

export interface Instrument {
  symbol: string;
  source: "default" | "broker" | "user" | string;
  [key: string]: unknown;
}

export interface Quote {
  symbol: string;
  bid: number | null;
  ask: number | null;
  spread_pips: number | null;
  time: ISODate | null;
  data_source: DataSource | string;
  fresh: boolean;
}

export interface Candle {
  time: ISODate;
  open: number;
  high: number;
  low: number;
  close: number;
  spread: number | null;
}

export interface CandlesResponse {
  data_source: DataSource | string;
  candles: Candle[];
}

export interface NewsEvent {
  id: string;
  event_id: string | null;
  currency: string;
  name: string;
  scheduled_at: ISODate;
  impact: string;
  forecast: string | number | null;
  previous: string | number | null;
  actual: string | number | null;
  provider: string | null;
  origin: "sample" | "provider" | "mock" | string | null;
  status: string | null;
  processed: boolean;
}

export interface NewsProvider {
  provider: string;
  available: boolean;
  origin: string | null;
  detail: string | null;
  last_fetch: ISODate | null;
}

/* ----------------------------------------------- Integrations & AI */

export interface IntegrationField {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  value: string | null;
  is_set: boolean;
}

export interface Integration {
  key: string;
  name: string;
  category: "broker" | "market_data" | "calendar" | "notifications" | "ai";
  configured: boolean;
  enabled: boolean;
  status: "connected" | "disconnected" | "unavailable" | "unverified" | "not_configured";
  last_check: ISODate | null;
  detail: string | null;
  fields: IntegrationField[];
  docs: string;
  supports_test: boolean;
}

export interface IntegrationUpdate {
  enabled: boolean;
  settings: Record<string, string>;
  secrets: Record<string, string>;
}

export interface AiAnalyzeRequest {
  subject: "backtest" | "cycle" | "risk";
  id: string;
}

export interface AiAnalyzeResponse {
  provider: string;
  model: string;
  content: string;
  label: string;
  generated_at: ISODate;
}

/* --------------------------------------- Notifications, logs, settings */

export type Severity = "debug" | "info" | "warning" | "error" | "critical";

export interface Notification {
  id: string;
  created_at: ISODate;
  severity: Severity | string;
  kind: string;
  title: string;
  message: string;
  bot_id: string | null;
  cycle_id: string | null;
  read: boolean;
}

export interface NotificationPreferences {
  kinds: Record<string, { in_app: boolean; [key: string]: unknown }>;
  channels: Record<string, unknown>;
}

export interface LogEntry {
  id: string;
  created_at: ISODate;
  severity: Severity;
  category: string;
  message: string;
  correlation_id: string | null;
  bot_id: string | null;
  cycle_id: string | null;
  actor: string | null;
  data?: unknown;
}

export interface LogFilters {
  severity?: string;
  category?: string;
  bot_id?: string;
  cycle_id?: string;
  correlation_id?: string;
  q?: string;
  page?: number;
  page_size?: number;
}

export interface UserSettings {
  timezone: string;
  display_name: string | null;
}

export interface CreateUserRequest {
  username: string;
  password: string;
  role: Role;
}
