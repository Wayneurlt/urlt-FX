import { api, apiUrl, setCsrfToken } from "./client";
import type {
  BotKind,
  AiAnalyzeRequest,
  AiAnalyzeResponse,
  AuthResponse,
  BacktestDetail,
  BacktestLedgerRow,
  BacktestRequest,
  BacktestRun,
  Bot,
  BotAction,
  BotConfig,
  BotConfigHistoryEntry,
  Breaker,
  CandlesResponse,
  CreateUserRequest,
  Cycle,
  CycleDetail,
  CycleFilters,
  DashboardCharts,
  DashboardSummary,
  Dataset,
  DatasetImportResponse,
  EnvironmentChangeRequest,
  EnvironmentChangeResponse,
  EvaluateNowResponse,
  Evaluation,
  HealthResponse,
  Instrument,
  Integration,
  IntegrationUpdate,
  Job,
  LiveReadiness,
  LogEntry,
  LogFilters,
  LoginRequest,
  ManualEntryRequest,
  NewsEvent,
  NewsProvider,
  Notification,
  NotificationPreferences,
  Paginated,
  PaperAccount,
  PaperResetRequest,
  Position,
  Quote,
  RiskConfigHistoryEntry,
  RiskConfigResponse,
  RiskConfigUpdateResponse,
  RiskDecision,
  RiskStatus,
  ShutdownPreview,
  ShutdownRequest,
  ShutdownResponse,
  SystemMetrics,
  SystemStatus,
  TradeFilters,
  TradeRow,
  User,
  UserSettings,
  ValidationRequest,
} from "@/types/api";

const enc = encodeURIComponent;

/* ---------------------------------------------------------------- Auth */
export const authApi = {
  async login(body: LoginRequest): Promise<AuthResponse> {
    const res = await api.post<AuthResponse>("/auth/login", body);
    setCsrfToken(res.csrf_token);
    return res;
  },
  async logout(): Promise<{ ok: boolean }> {
    const res = await api.post<{ ok: boolean }>("/auth/logout");
    setCsrfToken(null);
    return res;
  },
  async me(): Promise<AuthResponse> {
    const res = await api.get<AuthResponse>("/auth/me");
    setCsrfToken(res.csrf_token);
    return res;
  },
};

/* -------------------------------------------------------------- System */
export const systemApi = {
  health: () => api.get<HealthResponse>("/health"),
  status: () => api.get<SystemStatus>("/system/status"),
  liveReadiness: () => api.get<LiveReadiness>("/system/live-readiness"),
  setEnvironment: (body: EnvironmentChangeRequest) =>
    api.post<EnvironmentChangeResponse>("/system/environment", body),
  jobs: (params: { status?: string; page?: number; page_size?: number } = {}) =>
    api.get<Paginated<Job>>("/system/jobs", params),
  metrics: () => api.get<SystemMetrics>("/system/metrics"),
  streamUrl: () => apiUrl("/stream"),
};

/* ----------------------------------------------------------- Dashboard */
export const dashboardApi = {
  summary: () => api.get<DashboardSummary>("/dashboard/summary"),
  charts: (params: { start?: string; end?: string } = {}) =>
    api.get<DashboardCharts>("/dashboard/charts", params),
};

/* ---------------------------------------------------------------- Bots */
export const botsApi = {
  list: () => api.get<Bot[]>("/bots"),
  get: (id: string) => api.get<Bot>(`/bots/${enc(id)}`),
  action: (id: string, action: BotAction, confirm_text?: string) =>
    api.post<Bot>(`/bots/${enc(id)}/actions/${action}`, confirm_text ? { confirm_text } : {}),
  shutdownPreview: (id: string) => api.get<ShutdownPreview>(`/bots/${enc(id)}/shutdown-preview`),
  shutdown: (id: string, body: ShutdownRequest) =>
    api.post<ShutdownResponse>(`/bots/${enc(id)}/shutdown`, body),
  getConfig: (id: string) => api.get<BotConfig>(`/bots/${enc(id)}/config`),
  putConfig: (id: string, config: Record<string, unknown>, confirm_text?: string) =>
    api.put<BotConfig>(`/bots/${enc(id)}/config`, confirm_text ? { config, confirm_text } : { config }),
  configHistory: (id: string) => api.get<BotConfigHistoryEntry[]>(`/bots/${enc(id)}/config/history`),
  evaluations: (id: string, page = 1, page_size = 50) =>
    api.get<Paginated<Evaluation>>(`/bots/${enc(id)}/evaluations`, { page, page_size }),
  evaluateNow: (id: string) => api.post<EvaluateNowResponse>(`/bots/${enc(id)}/evaluate-now`),
};

/* ------------------------------------------- Positions, cycles, trades */
export const tradingApi = {
  positions: () => api.get<Position[]>("/positions"),
  cycles: (filters: CycleFilters = {}) => api.get<Paginated<Cycle>>("/cycles", filters),
  cycle: (id: string) => api.get<CycleDetail>(`/cycles/${enc(id)}`),
  closeCycle: (id: string, confirm_text: string) =>
    api.post<CycleDetail>(`/cycles/${enc(id)}/close`, { confirm_text }),
  trades: (filters: TradeFilters = {}) => api.get<Paginated<TradeRow>>("/trades", filters),
  tradesExportUrl: (filters: TradeFilters = {}) => {
    // Export ignores pagination.
    const { page: _p, page_size: _ps, ...rest } = filters;
    return apiUrl("/trades/export.csv", rest);
  },
  manualEntry: (body: ManualEntryRequest) => api.post<unknown>("/paper/manual-entry", body),
  paperAccount: () => api.get<PaperAccount>("/paper/account"),
  paperReset: (body: PaperResetRequest) => api.post<PaperAccount>("/paper/reset", body),
};

/* ---------------------------------------------------------------- Risk */
export const riskApi = {
  getConfig: () => api.get<RiskConfigResponse>("/risk/config"),
  putConfig: (config: Record<string, unknown>, confirm_text?: string) =>
    api.put<RiskConfigUpdateResponse>("/risk/config", confirm_text ? { config, confirm_text } : { config }),
  history: () => api.get<RiskConfigHistoryEntry[]>("/risk/config/history"),
  status: () => api.get<RiskStatus>("/risk/status"),
  decisions: (params: { approved?: boolean | ""; bot_id?: string; page?: number; page_size?: number } = {}) =>
    api.get<Paginated<RiskDecision>>("/risk/decisions", params),
  breakers: (params: { active?: boolean; page?: number; page_size?: number } = {}) =>
    api.get<Paginated<Breaker>>("/risk/breakers", params),
  clearBreaker: (id: string, confirm_text: string, note: string) =>
    api.post<Breaker>(`/risk/breakers/${enc(id)}/clear`, { confirm_text, note }),
};

/* ----------------------------------------------- Backtests & datasets */
export const backtestApi = {
  create: (body: BacktestRequest) => api.post<BacktestRun>("/backtests", body),
  list: (page = 1, page_size = 20, bot_kind?: BotKind) =>
    api.get<Paginated<BacktestRun>>("/backtests", { page, page_size, bot_kind }),
  get: (id: string) => api.get<BacktestDetail>(`/backtests/${enc(id)}`),
  trades: (id: string, page = 1, page_size = 50) =>
    api.get<Paginated<BacktestLedgerRow>>(`/backtests/${enc(id)}/trades`, { page, page_size }),
  cycles: (id: string, page = 1, page_size = 50) =>
    api.get<Paginated<BacktestLedgerRow>>(`/backtests/${enc(id)}/cycles`, { page, page_size }),
  exportUrl: (id: string) => apiUrl(`/backtests/${enc(id)}/export.csv`),
  validate: (id: string, body: ValidationRequest) =>
    api.post<unknown>(`/backtests/${enc(id)}/validation`, body),
  datasets: () => api.get<Dataset[]>("/datasets"),
  importDataset: (form: FormData) => api.upload<DatasetImportResponse>("/datasets/import", form),
};

/* ------------------------------------------------------ Market & news */
export const marketApi = {
  instruments: () => api.get<Instrument[]>("/market/instruments"),
  quotes: () => api.get<Quote[]>("/market/quotes"),
  candles: (params: { symbol: string; timeframe?: string; start?: string; end?: string }) =>
    api.get<CandlesResponse>("/market/candles", params),
  newsEvents: (params: { start?: string; end?: string } = {}) => api.get<NewsEvent[]>("/news/events", params),
  newsProvider: () => api.get<NewsProvider>("/news/provider"),
};

/* ----------------------------------------------- Integrations & AI */
export const integrationsApi = {
  list: () => api.get<Integration[]>("/integrations"),
  update: (key: string, body: IntegrationUpdate) => api.put<Integration>(`/integrations/${enc(key)}`, body),
  test: (key: string) => api.post<Integration>(`/integrations/${enc(key)}/test`),
  analyze: (body: AiAnalyzeRequest) => api.post<AiAnalyzeResponse>("/ai/analyze", body),
};

/* --------------------------------------- Notifications, logs, settings */
export const notificationsApi = {
  list: (params: { unread?: boolean; page?: number; page_size?: number } = {}) =>
    api.get<Paginated<Notification>>("/notifications", params),
  markRead: (id: string) => api.post<unknown>(`/notifications/${enc(id)}/read`),
  readAll: () => api.post<unknown>("/notifications/read-all"),
  preferences: () => api.get<NotificationPreferences>("/notifications/preferences"),
  savePreferences: (body: NotificationPreferences) =>
    api.put<NotificationPreferences>("/notifications/preferences", body),
};

export const logsApi = {
  list: (filters: LogFilters = {}) => api.get<Paginated<LogEntry>>("/logs", filters),
  get: (id: string) => api.get<LogEntry>(`/logs/${enc(id)}`),
};

export const settingsApi = {
  get: () => api.get<UserSettings>("/settings"),
  put: (body: Partial<UserSettings>) => api.put<UserSettings>("/settings", body),
  users: () => api.get<User[] | Paginated<User>>("/users"),
  createUser: (body: CreateUserRequest) => api.post<User>("/users", body),
};
