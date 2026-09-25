import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

export const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export type DataSource = {
  id: number;
  name: string;
  host: string;
  port: number;
  db_type: string;
  cluster_key: string;
  access_level?: "user" | "admin";
  tenant_role: "sys" | "user";
  attributes?: Record<string, unknown> | null;
  user: string;
  database: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export type DataSourceInput = {
  name: string;
  host: string;
  port: number;
  db_type: string;
  cluster_key: string;
  access_level: "user" | "admin";
  tenant_role: "sys" | "user";
  attributes?: Record<string, unknown> | null;
  user: string;
  password: string;
  database: string;
}

export type DataSourceUpdateInput = Partial<DataSourceInput>

// Presentation contract used by the restored assistant-ui tool disclosure.
// Native Agent runs populate approvals from ToolBlock instead of the retired Chat API.
export type PendingAction = {
  token: string
  action_type: string
  status: string
  batch_id?: string
  sql?: string
  sql_preview?: string
  intent?: string
  resolved_datasource_id?: number
  resolved_role?: string
  resolved_access_level?: "user" | "admin"
  cluster_key?: string
  tenant_fingerprint?: Record<string, string>
  execution_fingerprint?: string
  mode?: string
  object_type?: string
  object_action?: string
  object_id?: number
  preview?: string
  risk_level?: string
  confirmation_policy?: string
  idempotency_key?: string
  source_text?: string
  created_at?: string | null
}

export function filterConnectableDatasources(datasources: DataSource[]): DataSource[] {
  return datasources
}

export type Agent = {
  id: number;
  name: string;
  description?: string;
  prompt: string;
  tools?: string[];
  skills?: string[];
  datasource_ids: number[];
  agent_type: "built_in" | "custom";
  status: string;
  created_at: string;
  updated_at: string;
}

export type ScheduleTargetType = "function" | "agent" | "collector"
export type UserScheduleTargetType = "function" | "agent"

export type Schedule = {
  id: number;
  name: string;
  description?: string | null;
  kind: "built_in" | "custom";
  status: "active" | "paused";
  target_type: ScheduleTargetType;
  target_id?: number | null;
  schedule_type: "cron" | "interval";
  cron_expression?: string | null;
  interval_seconds?: number | null;
  timezone: string;
  datasource_id?: number | null;
  function_id?: number | null;
  function_release_id?: number | null;
  input_payload?: Record<string, unknown> | null;
  input_prompt?: string | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
  max_retries: number;
  retry_backoff_seconds: number;
  created_at: string;
  updated_at: string;
}

export type ScheduleRun = {
  id: number;
  schedule_id: number;
  run_id: string;
  status: string;
  trigger_type: string;
  attempt: number;
  retry_count: number;
  max_retries: number;
  correlation_id?: string | null;
  target_type?: string | null;
  runtime_run_id?: string | null;
  runtime_status?: string | null;
  conversation_id?: string | null;
  error_summary?: string | null;
  output_summary?: string | null;
  output_payload?: Record<string, unknown> | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
}

export type ScheduleWorkerHealth = {
  running: boolean;
  shutting_down: boolean;
  job_count: number;
  autostart: boolean;
  refresh_interval_seconds?: number;
  job_coalesce?: boolean;
  job_misfire_grace_seconds?: number;
  job_max_instances?: number;
}

export type ScheduleRunsPage = {
  items: ScheduleRun[];
  total: number;
  limit: number;
  offset: number;
}

export type ChannelProvider = "dingtalk" | "feishu" | "wechat" | "slack" | "telegram";
export type ChannelStatus = "active" | "inactive";
export type ChannelMessageType = "text" | "markdown" | "actionCard" | "feedCard";

export type ChannelSecurityConfig = {
  mode: "keyword" | "sign" | "ip";
  keyword?: string | null;
  secret?: string | null;
  ip_whitelist?: string[];
};

export type ChannelTemplateConfig = {
  type: ChannelMessageType;
  title?: string;
  body?: string;
  at_all?: boolean;
  at_user_ids?: string[];
  links?: Record<string, unknown>[];
};

export type SlackTemplateConfig = {
  body?: string;
  username?: string | null;
  icon_emoji?: string | null;
  channel?: string | null;
};

export type TelegramTemplateConfig = {
  body?: string;
  parse_mode?: "Markdown" | "HTML" | "";
  disable_notification?: boolean;
};

export type ChannelConfig = {
  webhook_url?: string;
  security?: ChannelSecurityConfig;
  template?: ChannelTemplateConfig | SlackTemplateConfig | TelegramTemplateConfig;
  bot_token?: string;
  chat_id?: string;
};

export type Channel = {
  id: number;
  name: string;
  provider: ChannelProvider;
  description?: string | null;
  status: ChannelStatus;
  config: ChannelConfig;
  created_at: string;
  updated_at: string;
};

export type ChannelInput = {
  name: string;
  provider: ChannelProvider;
  description?: string;
  status?: ChannelStatus;
  config: ChannelConfig;
};

export type Skill = {
  name: string;
  version: string;
  description: string;
  database: string;
  always_apply: boolean;
  prompt: string;
  source: "built_in" | "custom";
  path?: string;
}

export type SkillInput = {
  name: string;
  version: string;
  description: string;
  database: string;
  always_apply: boolean;
  prompt: string;
}

export type SkillUpdateInput = Partial<SkillInput>

export type PageRecord = {
  id: number
  name: string
  description?: string | null
  status?: string
  current_release_id?: number | null
  updated_at?: string
}

export type PageNavigationItem = PageRecord & {
  path: string
  entry_type: "published" | "workspace"
}

export type FunctionRecord = {
  id: number
  name: string
  slug?: string
  description?: string | null
  kind?: string
  status?: string
  draft_code?: string
  draft_dependencies?: Record<string, unknown> | null
  updated_at?: string
}

export type FunctionRunRecord = {
  id: number
  run_id?: string
  function_id?: number
  function_name?: string
  function_slug?: string
  status?: string
  duration_ms?: number
  input_summary?: string
  output_summary?: string
  error_class?: string
  error_message?: string
  started_at?: string
  finished_at?: string
  created_at?: string
}

export type FunctionReleaseRecord = {
  id: number
  function_id?: number
  version?: number
  created_at?: string
  [key: string]: unknown
}

export type FunctionInputSuggestion = {
  payload: Record<string, unknown>
  rationale: string
  missing_information: string[]
  assumptions: string[]
  source?: Record<string, unknown>
  runtime_path?: "production" | "draft"
}

export type ApiJsonValue = string | number | boolean | null | ApiJsonValue[] | { [key: string]: ApiJsonValue }

export type FunctionInvokeResult = {
  status?: string
  duration_ms?: number
  run_id?: string
  output?: ApiJsonValue
  error_message?: string
  error_class?: string
  error_code?: string
  runtime_path?: string
}

export const datasourcesApi = {
  list: () => api.get<DataSource[]>('/datasources').then(res => res.data),
  get: (id: number) => api.get<DataSource>(`/datasources/${id}`).then(res => res.data),
  create: (data: DataSourceInput) =>
    api.post<DataSource>('/datasources', data).then(res => res.data),
  update: (id: number, data: DataSourceUpdateInput) =>
    api.patch<DataSource>(`/datasources/${id}`, data).then(res => res.data),
  delete: (id: number) => api.delete(`/datasources/${id}`),
  test: (data: DataSourceInput) =>
    api.post<{success: boolean; message: string}>('/datasources/test', data).then(res => res.data),
  testById: (id: number) =>
    api.post<{success: boolean; message: string}>(`/datasources/${id}/test`).then(res => res.data),
};

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

export type Service = {
  id: number;
  name: string;
  service_type: string;
  config: ServiceHTTPConfig;
  resource_ref?: string | null;
  has_credentials: boolean;
  knowledge_base_ids: number[];
  status: string;
  created_at: string;
  updated_at: string;
}

export type ServiceInput = {
  name: string;
  service_type: string;
  config: ServiceHTTPConfig;
  secrets?: ServiceSecretConfig | null;
  resource_ref?: string | null;
  knowledge_base_ids?: number[];
}

export type ServiceHTTPConfig = {
  base_url: string;
  auth_type: "none" | "basic" | "bearer" | "api_key";
  api_key_header: string;
  default_headers: Record<string, string>;
  health_check_path: string;
  health_check_method: "GET" | "POST";
  response_format: "auto" | "json" | "text";
  timeout_seconds: number;
  verify_tls: boolean;
  use_environment_proxy: boolean;
  max_response_bytes: number;
}

export type ServiceSecretConfig = {
  username?: string;
  password?: string;
  bearer_token?: string;
  api_key?: string;
  headers?: Record<string, string>;
}

export type ServiceUpdateInput = Partial<ServiceInput> & { status?: string }

export const servicesApi = {
  list: () => api.get<Service[]>('/services').then(res => res.data),
  get: (id: number) => api.get<Service>(`/services/${id}`).then(res => res.data),
  create: (data: ServiceInput) =>
    api.post<Service>('/services', data).then(res => res.data),
  update: (id: number, data: ServiceUpdateInput) =>
    api.patch<Service>(`/services/${id}`, data).then(res => res.data),
  delete: (id: number) => api.delete(`/services/${id}`),
  test: (id: number) =>
    api.post<{success: boolean; message: string; http_status?: number | null}>(`/services/${id}/test`).then(res => res.data),
  testConfig: (data: ServiceInput) =>
    api.post<{success: boolean; message: string; http_status?: number | null}>('/services/test-config', data).then(res => res.data),
  testUpdateConfig: (id: number, data: ServiceUpdateInput) =>
    api.post<{success: boolean; message: string; http_status?: number | null}>(`/services/${id}/test-config`, data).then(res => res.data),
};

export type KnowledgeBase = {
  id: number;
  name: string;
  description?: string | null;
  tags?: string[] | null;
  source?: string | null;
  pack_id?: string | null;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export type KnowledgeBaseInput = {
  name: string;
  description?: string | null;
  tags?: string[] | null;
}

export type KnowledgeDocument = {
  id: number;
  kb_id: number;
  title: string;
  filename: string;
  size_bytes: number;
  created_at: string;
  updated_at: string;
}

export type KnowledgeDocumentDetail = KnowledgeDocument & {
  content: string;
}

export const knowledgeApi = {
  list: () => api.get<KnowledgeBase[]>('/knowledge-bases').then(res => res.data),
  get: (id: number) => api.get<KnowledgeBase>(`/knowledge-bases/${id}`).then(res => res.data),
  create: (data: KnowledgeBaseInput) =>
    api.post<KnowledgeBase>('/knowledge-bases', data).then(res => res.data),
  update: (id: number, data: Partial<KnowledgeBaseInput>) =>
    api.patch<KnowledgeBase>(`/knowledge-bases/${id}`, data).then(res => res.data),
  delete: (id: number) => api.delete(`/knowledge-bases/${id}`),
  listDocuments: (kbId: number) =>
    api.get<KnowledgeDocument[]>(`/knowledge-bases/${kbId}/documents`).then(res => res.data),
  getDocument: (kbId: number, docId: number) =>
    api.get<KnowledgeDocumentDetail>(`/knowledge-bases/${kbId}/documents/${docId}`).then(res => res.data),
  uploadDocument: (kbId: number, file: File) => {
    const formData = new FormData();
    const relPath = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
    const name = relPath ? relPath.split('/').slice(1).join('/') || file.name : file.name;
    formData.append('file', file, name);
    return api.post<KnowledgeDocument>(`/knowledge-bases/${kbId}/documents`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then(res => res.data);
  },
  deleteDocument: (kbId: number, docId: number) =>
    api.delete(`/knowledge-bases/${kbId}/documents/${docId}`),
};

export type PackVersion = {
  branch: string;
  label: string;
}

export type KnowledgePack = {
  id: string;
  name: string;
  description: string;
  tags: string[];
  db_type?: string | null;
  repo_url: string;
  branch: string;
  subdirectory: string;
  license: string;
  source_url?: string | null;
  estimated_doc_count: number;
  estimated_size_mb: number;
  versions?: PackVersion[] | null;
  default_version?: string | null;
  status: 'available' | 'downloading' | 'installed' | 'error';
  kb_id?: number | null;
  error_message?: string | null;
}

export type KnowledgePackInstallStatus = {
  pack_id: string;
  status: string;
  progress_message?: string | null;
  kb_id?: number | null;
  error_message?: string | null;
}

export const knowledgePackApi = {
  list: () => api.get<KnowledgePack[]>('/knowledge-packs').then(res => res.data),
  install: (packId: string) =>
    api.post<KnowledgePackInstallStatus>(`/knowledge-packs/${packId}/install`).then(res => res.data),
  status: (packId: string) =>
    api.get<KnowledgePackInstallStatus>(`/knowledge-packs/${packId}/status`).then(res => res.data),
  uninstall: (packId: string) => api.delete(`/knowledge-packs/${packId}`),
};

export const agentsApi = {
  list: () => api.get<Agent[]>('/agents').then(res => res.data),
  get: (id: number) => api.get<Agent>(`/agents/${id}`).then(res => res.data),
  create: (data: { name: string; description?: string; prompt: string; tools?: string[]; skills?: string[]; datasource_ids?: number[] }) =>
    api.post<Agent>('/agents', data).then(res => res.data),
  update: (id: number, data: Partial<Omit<Agent, 'id' | 'status' | 'created_at' | 'updated_at'>>) =>
    api.patch<Agent>(`/agents/${id}`, data).then(res => res.data),
  delete: (id: number) => api.delete(`/agents/${id}`),
};

export const skillsApi = {
  list: (params?: { query?: string }) =>
    api.get<Skill[]>('/skills', { params }).then(res => res.data),
  get: (name: string) => api.get<Skill>(`/skills/${encodeURIComponent(name)}`).then(res => res.data),
  create: (data: SkillInput) =>
    api.post<Skill>('/skills', data).then(res => res.data),
  update: (name: string, data: SkillUpdateInput) =>
    api.patch<Skill>(`/skills/${encodeURIComponent(name)}`, data).then(res => res.data),
  delete: (name: string) => api.delete(`/skills/${encodeURIComponent(name)}`),
};


export const pagesApi = {
  list: () => api.get<PageRecord[]>('/pages').then(res => res.data),
  navigation: () => api.get<PageNavigationItem[]>('/pages/navigation').then(res => res.data),
  create: (data: { name: string; description?: string }) => api.post<PageRecord>('/pages', data).then(res => res.data),
  get: (id: number) => api.get<PageRecord>(`/pages/${id}`).then(res => res.data),
  update: (id: number, data: { name: string; description?: string }) => api.patch<PageRecord>(`/pages/${id}`, data).then(res => res.data),
  delete: (id: number) => api.delete(`/pages/${id}`),
  archive: (id: number) => api.post(`/pages/${id}/archive`, {}).then(res => res.data),
}

export const functionsApi = {
  list: () => api.get<FunctionRecord[]>('/functions').then((res) => res.data),
  listAllRuns: (limit = 50) =>
    api.get<FunctionRunRecord[]>('/functions/runs', { params: { limit } }).then((res) => res.data),
  create: (data: { name?: string; description?: string }) => api.post<FunctionRecord>('/functions', data).then((res) => res.data),
  get: (id: number) => api.get<FunctionRecord>(`/functions/${id}`).then((res) => res.data),
  getBySlug: (slug: string) => api.get<FunctionRecord>(`/functions/by-slug/${encodeURIComponent(slug)}`).then((res) => res.data),
  update: (id: number, data: { name?: string; description?: string }) =>
    api.patch<FunctionRecord>(`/functions/${id}`, data).then((res) => res.data),
  delete: (id: number) => api.delete(`/functions/${id}`),
  listReleases: (id: number) => api.get<FunctionReleaseRecord[]>(`/functions/${id}/releases`).then((res) => res.data),
  listRuns: (id: number, limit = 20) =>
    api.get<FunctionRunRecord[]>(`/functions/${id}/runs`, { params: { limit } }).then((res) => res.data),
  suggestInput: (
    id: number,
    data: { prompt?: string; runtime_path?: "production" | "draft" }
  ) => api.post<FunctionInputSuggestion>(`/functions/${id}/suggest-input`, data).then((res) => res.data),
  invoke: (id: number, data?: Record<string, unknown>) =>
    api.post<FunctionInvokeResult>(`/functions/${id}/invoke`, data ?? {}).then((res) => res.data),
  cancelRun: (id: number, runId: string) =>
    api.post<FunctionInvokeResult>(`/functions/${id}/runs/${encodeURIComponent(runId)}/cancel`).then((res) => res.data),
  duplicate: (id: number) =>
    api.post<FunctionRecord>(`/functions/${id}/duplicate`).then((res) => res.data),
}

export const schedulesApi = {
  list: () => api.get<Schedule[]>('/schedules').then((res) => res.data),
  workerHealth: () => api.get<ScheduleWorkerHealth>('/schedules/worker-health').then((res) => res.data),
  create: (data: Record<string, unknown>) => api.post<Schedule>('/schedules', data).then((res) => res.data),
  aiCreate: (data: Record<string, unknown>) =>
    api.post<{ schedule: Schedule; build_summary: string }>('/schedules/ai-create', data).then((res) => res.data),
  get: (id: number) => api.get<Schedule>(`/schedules/${id}`).then((res) => res.data),
  update: (id: number, data: Record<string, unknown>) =>
    api.patch<Schedule>(`/schedules/${id}`, data).then((res) => res.data),
  delete: (id: number) => api.delete(`/schedules/${id}`),
  listRuns: (id: number, limit = 20) =>
    api.get<ScheduleRun[]>(`/schedules/${id}/runs`, { params: { limit } }).then((res) => res.data),
  listRunsPage: (id: number, options?: { limit?: number; offset?: number }) =>
    api
      .get<ScheduleRun[]>(`/schedules/${id}/runs`, {
        params: {
          limit: options?.limit ?? 20,
          offset: options?.offset ?? 0,
        },
      })
      .then((res) => {
        const items = Array.isArray(res.data) ? res.data : []
        const total = Number(res.headers["x-total-count"] ?? items.length)
        const limit = Number(res.headers["x-limit"] ?? options?.limit ?? 20)
        const offset = Number(res.headers["x-offset"] ?? options?.offset ?? 0)
        return {
          items,
          total: Number.isFinite(total) ? total : items.length,
          limit: Number.isFinite(limit) ? limit : 20,
          offset: Number.isFinite(offset) ? offset : 0,
        } as ScheduleRunsPage
      }),
  listAllRunsPage: (options?: { limit?: number; offset?: number; schedule_id?: number }) =>
    api
      .get<ScheduleRun[]>('/schedules/runs', {
        params: {
          limit: options?.limit ?? 20,
          offset: options?.offset ?? 0,
          schedule_id: options?.schedule_id,
        },
      })
      .then((res) => {
        const items = Array.isArray(res.data) ? res.data : []
        const total = Number(res.headers["x-total-count"] ?? items.length)
        const limit = Number(res.headers["x-limit"] ?? options?.limit ?? 20)
        const offset = Number(res.headers["x-offset"] ?? options?.offset ?? 0)
        return {
          items,
          total: Number.isFinite(total) ? total : items.length,
          limit: Number.isFinite(limit) ? limit : 20,
          offset: Number.isFinite(offset) ? offset : 0,
        } as ScheduleRunsPage
      }),
  build: (id: number, prompt: string) =>
    api.post<{ schedule: Schedule; build_summary: string }>(`/schedules/${id}/build`, { prompt }).then((res) => res.data),
  pause: (id: number) => api.post<Schedule>(`/schedules/${id}/pause`, {}).then((res) => res.data),
  resume: (id: number) => api.post<Schedule>(`/schedules/${id}/resume`, {}).then((res) => res.data),
  disable: (id: number) => api.post<Schedule>(`/schedules/${id}/disable`, {}).then((res) => res.data),
  enable: (id: number) => api.post<Schedule>(`/schedules/${id}/enable`, {}).then((res) => res.data),
  runNow: (id: number) => api.post<{ schedule_id: number; run_id: string; trace_id: string; schedule_run_id?: number }>(`/schedules/${id}/run-now`, {}).then((res) => res.data),
  repairRun: (scheduleId: number, runId: number) =>
    api.post<ScheduleRun>(`/schedules/${scheduleId}/runs/${runId}/repair`, {}).then((res) => res.data),
}

export const channelsApi = {
  list: (params?: { provider?: ChannelProvider; status?: ChannelStatus }) =>
    api.get<Channel[]>('/channels', { params }).then((res) => res.data),
  get: (id: number) => api.get<Channel>(`/channels/${id}`).then((res) => res.data),
  create: (data: ChannelInput) => api.post<Channel>('/channels', data).then((res) => res.data),
  update: (id: number, data: Partial<ChannelInput>) =>
    api.patch<Channel>(`/channels/${id}`, data).then((res) => res.data),
  delete: (id: number) => api.delete(`/channels/${id}`),
  send: (
    id: number,
    data?: {
      message?: Record<string, unknown>;
      template?: Record<string, unknown>;
      message_type?: ChannelMessageType;
      title?: string;
      content?: string;
      dry_run?: boolean;
    }
  ) => api.post<Record<string, unknown>>(`/channels/${id}/send`, data ?? {}).then((res) => res.data),
  sendTest: (
    id: number,
    data?: {
      message?: Record<string, unknown>;
      template?: Record<string, unknown>;
      message_type?: ChannelMessageType;
      title?: string;
      content?: string;
      dry_run?: boolean;
    }
  ) => api.post<Record<string, unknown>>(`/channels/${id}/send-test`, data ?? {}).then((res) => res.data),
}

export type PlatformSettings = {
  sql_allow_mutating?: boolean
  ai_api_key_configured: boolean
  ai_model?: string
  ai_base_url?: string
  context_window_tokens: number
  context_compression_threshold_percent: number
  praxis_edition?: string
}

export type PlatformSettingsUpdate = Omit<Partial<PlatformSettings>, 'ai_api_key_configured'> & {
  ai_api_key?: string
}

export const settingsApi = {
  get: (): Promise<PlatformSettings> =>
    api.get('/settings').then((res) => res.data),

  patch: (payload: PlatformSettingsUpdate): Promise<PlatformSettings> =>
    api.patch('/settings', payload).then((res) => res.data),

}

// ── Capabilities ──────────────────────────────────────────────

export type ToolInfo = {
  name: string
  description: string
  parameters: Record<string, unknown>
}

export type CapabilitiesResponse = {
  tools: ToolInfo[]
}

export const capabilitiesApi = {
  list: (): Promise<CapabilitiesResponse> =>
    api.get('/capabilities').then((res) => res.data),
}

// ── Onboarding API ──────────────────────────────────────────────

export type OnboardingStatus = {
  completed: boolean
}

export type LlmConfig = {
  llm_provider: string
  llm_api_key: string
  llm_model: string
  llm_base_url?: string
}

export const onboardingApi = {
  getStatus: (): Promise<OnboardingStatus> =>
    api.get('/onboarding/status').then((res) => res.data),

  complete: (llm_config: LlmConfig): Promise<OnboardingStatus> =>
    api.post('/onboarding/complete', { llm_config }).then((res) => res.data),
}
