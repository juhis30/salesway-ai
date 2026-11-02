// Typed client for the SaleSway FastAPI backend.
//
// Tokens live in localStorage for this demo. Production should move the refresh token to an
// httpOnly cookie so page scripts can't read it.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const ACCESS_KEY = "sw_access";
const REFRESH_KEY = "sw_refresh";
export const LOGOUT_EVENT = "sw:logout";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function setTokens(tokens: { access_token: string; refresh_token: string }) {
  try {
    localStorage.setItem(ACCESS_KEY, tokens.access_token);
    localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  } catch {
    /* storage unavailable: the session lasts until reload */
  }
}

export function clearTokens() {
  try {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* ignore */
  }
}

export function hasSession(): boolean {
  return read(ACCESS_KEY) !== null;
}

let refreshing: Promise<boolean> | null = null;

// Several requests can 401 at once; they share one refresh call.
function refreshTokens(): Promise<boolean> {
  refreshing ??= (async () => {
    const refresh_token = read(REFRESH_KEY);
    if (!refresh_token) return false;
    const res = await fetch(`${API_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token }),
    });
    if (!res.ok) return false;
    setTokens(await res.json());
    return true;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

type Query = Record<string, string | number | null | undefined>;

interface RequestOptions {
  method?: string;
  query?: Query;
  json?: unknown;
  form?: FormData;
}

function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = d.loc?.filter((p) => p !== "body" && p !== "query").join(".");
        return field ? `${field}: ${d.msg}` : d.msg;
      })
      .join("; ");
  }
  return `Request failed (${status})`;
}

export async function api<T>(path: string, options: RequestOptions = {}, retry = true): Promise<T> {
  const url = new URL(API_URL + path);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== null && value !== undefined && value !== "") url.searchParams.set(key, String(value));
  }

  const headers: Record<string, string> = {};
  const token = read(ACCESS_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }

  let res: Response;
  try {
    res = await fetch(url, { method: options.method ?? "GET", headers, body });
  } catch {
    throw new ApiError(0, "Cannot reach the SaleSway server. Is the backend running on port 8000?");
  }

  if (res.status === 401 && retry && token) {
    if (await refreshTokens()) return api<T>(path, options, false);
    clearTokens();
    window.dispatchEvent(new Event(LOGOUT_EVENT));
  }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new ApiError(res.status, errorMessage(data, res.status));
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

// --- Types mirroring backend schemas ------------------------------------------------------

export type Role =
  | "super_admin"
  | "org_admin"
  | "sales_head"
  | "regional_manager"
  | "sales_manager"
  | "sales_executive"
  | "analyst";

export const ROLE_LABELS: Record<Role, string> = {
  super_admin: "Super admin",
  org_admin: "Organisation admin",
  sales_head: "Sales head",
  regional_manager: "Regional manager",
  sales_manager: "Sales manager",
  sales_executive: "Sales executive",
  analyst: "Analyst",
};

export interface User {
  id: string;
  organization_id: string;
  email: string;
  full_name: string;
  role: Role;
  phone: string | null;
  team_id: string | null;
  territory_id: string | null;
  is_active: boolean;
  created_at: string;
}

export interface Organization {
  id: string;
  name: string;
  industry: string | null;
  timezone: string;
  currency: string;
  plan: string;
}

export interface Me {
  user: User;
  organization: Organization;
}

export interface Team {
  id: string;
  name: string;
}

export interface Territory {
  id: string;
  name: string;
  region: string | null;
}

export interface Page<T> {
  total: number;
  items: T[];
}

export interface Customer {
  id: string;
  code: string | null;
  name: string;
  segment: string | null;
  city: string | null;
  status: "active" | "inactive";
  territory_id: string | null;
  owner_id: string | null;
}

export interface Product {
  id: string;
  sku: string;
  name: string;
  category: string | null;
  unit_price: string | null;
}

// Money comes back as decimal strings to avoid float rounding.
export interface Overview {
  period: { start: string; end: string };
  comparison_period: { start: string; end: string };
  revenue: string;
  orders: number;
  average_order_value: string | null;
  active_customers: number;
  target: string | null;
  achievement_pct: number | null;
  target_gap: string | null;
  required_daily_run_rate: string | null;
  previous_revenue: string;
  previous_orders: number;
  comparison_available: boolean;
  revenue_growth_pct: number | null;
  orders_growth_pct: number | null;
  data_as_of: string | null;
}

export interface TrendPoint {
  period: string;
  revenue: string;
  orders: number;
  target: string | null;
}

export interface LeaderboardRow {
  rank: number;
  salesperson_id: string;
  name: string;
  revenue: string;
  orders: number;
  average_order_value: string | null;
  target: string | null;
  achievement_pct: number | null;
}

export interface TopCustomer {
  customer_id: string;
  name: string;
  segment: string | null;
  revenue: string;
  orders: number;
  last_order_date: string;
  share_pct: number | null;
}

export interface TopProduct {
  product_id: string;
  sku: string;
  name: string;
  category: string | null;
  revenue: string;
  units: string;
  orders: number;
  last_order_date: string;
  average_unit_price: string | null;
  share_pct: number | null;
}

export interface CustomerDetail extends Customer {
  phone: string | null;
  email: string | null;
  territory_name: string | null;
  owner_name: string | null;
  first_order_date: string | null;
  last_order_date: string | null;
  lifetime_revenue: string;
  lifetime_orders: number;
}

export type OrderStatus = "pending" | "confirmed" | "completed" | "cancelled" | "returned";

export interface OrderLine {
  product_id: string;
  sku: string;
  product_name: string;
  quantity: string;
  unit_price: string;
  line_amount: string;
}

export interface Order {
  id: string;
  order_number: string;
  order_date: string;
  status: OrderStatus;
  salesperson_name: string | null;
  net_amount: string;
  tax_amount: string;
  total_amount: string;
  counts_as_revenue: boolean;
  lines: OrderLine[];
}

// --- Insights -----------------------------------------------------------------------------

export type HealthStatus = "active" | "at_risk" | "dormant";

export interface CustomerHealth {
  as_of: string;
  summary: {
    active: number;
    at_risk: number;
    dormant: number;
    never_ordered: number;
    new_in_period: number;
    returning_in_period: number;
    repeat_rate_pct: number | null;
    monthly_value_at_risk: string;
  };
  rules: { dormant_days: number; at_risk_gap_multiplier: number; at_risk_min_days: number };
  customers: {
    customer_id: string;
    name: string;
    code: string | null;
    segment: string | null;
    owner_name: string | null;
    status: HealthStatus;
    reason: string;
    first_order_date: string;
    last_order_date: string;
    days_since_last_order: number;
    typical_gap_days: number | null;
    orders: number;
    monthly_value: string;
  }[];
  total: number;
}

export type ProductTrend = "rising" | "steady" | "declining" | "new" | "stopped";

export interface ProductTrends {
  period: { start: string; end: string };
  comparison_period: { start: string; end: string };
  comparison_available: boolean;
  trends_available: boolean;
  min_trend_days: number;
  threshold_pct: number;
  counts: Partial<Record<ProductTrend, number>>;
  products: {
    product_id: string;
    sku: string;
    name: string;
    category: string | null;
    revenue: string;
    previous_revenue: string;
    growth_pct: number | null;
    units: string;
    previous_units: string;
    buyers: number;
    previous_buyers: number;
    share_pct: number | null;
    trend: ProductTrend | null;
  }[];
}

export type Stage = "prospecting" | "qualification" | "proposal" | "negotiation" | "won" | "lost";

export const STAGE_LABELS: Record<Stage, string> = {
  prospecting: "Prospecting",
  qualification: "Qualification",
  proposal: "Proposal",
  negotiation: "Negotiation",
  won: "Won",
  lost: "Lost",
};

export interface Pipeline {
  as_of: string;
  period: { start: string; end: string };
  open_count: number;
  open_value: string;
  weighted_value: string;
  stalled_count: number;
  overdue_count: number;
  by_stage: { stage: Stage; count: number; value: string; weight_pct: number; weighted_value: string }[];
  won_count: number;
  won_value: string;
  lost_count: number;
  win_rate_pct: number | null;
  average_won_deal: string | null;
  average_cycle_days: number | null;
  rules: { stalled_days: number };
  deals: {
    opportunity_id: string;
    code: string;
    name: string;
    customer_id: string;
    customer_name: string | null;
    owner_name: string | null;
    stage: Stage;
    amount: string;
    opened_on: string;
    expected_close: string | null;
    last_activity_date: string | null;
    days_open: number;
    days_idle: number;
    stalled: boolean;
    overdue: boolean;
  }[];
}

export interface TeamActivity {
  period: { start: string; end: string };
  rules: { visit_to_order_days: number };
  totals: Record<"visits" | "calls" | "meetings" | "whatsapp" | "total_activities" | "follow_ups_overdue" | "orders", number>;
  people: {
    salesperson_id: string;
    name: string;
    visits: number;
    calls: number;
    meetings: number;
    whatsapp: number;
    total_activities: number;
    customers_contacted: number;
    follow_ups_overdue: number;
    follow_ups_upcoming: number;
    orders: number;
    revenue: string;
    visit_to_order_pct: number | null;
    revenue_per_visit: string | null;
  }[];
}

export type ActivityType = "visit" | "call" | "meeting" | "whatsapp";

export const ACTIVITY_LABELS: Record<ActivityType, string> = {
  visit: "Visit",
  call: "Call",
  meeting: "Meeting",
  whatsapp: "WhatsApp",
};

export interface FollowUp {
  id: string;
  activity_type: ActivityType;
  due_date: string;
  days_overdue: number;
  customer_id: string | null;
  customer_name: string | null;
  opportunity_name: string | null;
  salesperson_name: string | null;
  notes: string | null;
}

// --- Predictions ----------------------------------------------------------------------------

export interface MLModelInfo {
  id: string;
  kind: "month_end_forecast" | "next_month_forecast" | "churn" | "deal_win";
  label: string;
  version: number;
  status: "active" | "rejected" | "insufficient_data" | "retired";
  algorithm: string;
  trained_at: string;
  training_rows: number;
  metrics: Record<string, unknown>;
  notes: string | null;
}

export interface MonthEndForecast {
  available: boolean;
  reason: string | null;
  month: string | null;
  month_end: string | null;
  as_of: string | null;
  level: "company" | "individual" | null;
  target: number | null;
  method_label: string | null;
  so_far: number | null;
  forecast: number | null;
  lower: number | null;
  upper: number | null;
  chance_of_target: number | null;
  based_on_samples: number | null;
  tested_error_pct: number | null;
  pace_error_pct: number | null;
  daily: { date: string; cumulative: number }[];
  model: MLModelInfo | null;
}

export interface NextMonthForecast {
  available: boolean;
  reason: string | null;
  month: string | null;
  forecast: string | null;
  lower: string | null;
  upper: string | null;
  based_on_samples: number | null;
  tested_error_pct: number | null;
}

export interface Scores {
  available: boolean;
  reason: string | null;
  as_of: string | null;
  model: MLModelInfo | null;
  rows: { entity_id: string; name: string; owner_name: string | null; probability: number; value: string | null; signals: string[] }[];
}

export type ActionKind = "follow_up" | "win_back" | "revive_deal" | "cross_sell";

export interface NextAction {
  id: string;
  kind: ActionKind;
  title: string;
  reason: string;
  expected_value: string | null;
  score: number | null;
  status: "open" | "done" | "dismissed";
  as_of: string;
  owner_id: string;
  owner_name: string | null;
  entity_type: string;
  entity_id: string;
}

// --- Assistant & WhatsApp -------------------------------------------------------------------

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  intent: string | null;
  created_at: string;
  based_on: {
    intent: string | null;
    scope: string | null;
    period: [string, string] | null;
    comparison: [string, string] | null;
    data_as_of: string | null;
    provider: string | null;
    grounded: boolean;
  } | null;
  suggestions: string[];
}

export interface ConversationSummary {
  id: string;
  channel: "web" | "whatsapp";
  title: string | null;
  last_message_at: string;
}

export interface WhatsAppSettings {
  provider: "mock" | "meta";
  webhook_path: string;
  verify_token_set: boolean;
  app_secret_set: boolean;
  access_token_set: boolean;
  accounts: { id: string; phone_number_id: string; display_phone: string | null; enabled: boolean }[];
  users: { id: string; full_name: string; role: Role; phone: string | null; whatsapp_opt_in: boolean }[];
}

export type ImportEntity = "customers" | "products" | "orders" | "targets" | "opportunities" | "activities";

export interface ImportField {
  name: string;
  type: string;
  required: boolean;
}

export interface ImportJob {
  id: string;
  entity: ImportEntity;
  filename: string;
  status: "previewed" | "completed" | "failed";
  total_rows: number;
  valid_rows: number;
  error_rows: number;
  column_mapping: Record<string, string>;
  warnings: string[];
  result: Record<string, number> | null;
  created_at: string;
}

export interface ImportError {
  row: number;
  field: string | null;
  message: string;
}

export interface ImportPreview {
  job: ImportJob;
  unmapped_columns: string[];
  errors: ImportError[];
  sample: Record<string, string | number | null>[];
}
