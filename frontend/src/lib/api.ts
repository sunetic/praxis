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
  tenant_role: "sys" | "user";
  attributes?: Record<string, unknown> | null;
  user: string;
  password: string;
  database: string;
}

export type DataSourceUpdateInput = Partial<DataSourceInput>

export function filterConnectableDatasources(datasources: DataSource[]): DataSource[] {
  return datasources
}

export type SqlAnalysisCategory =
  | "top_sql"
  | "slow_sql"
  | "new_sql"
  | "regressed_sql"
  | "plan_changed_sql"

export type SqlAnalysisListItem = {
  datasource_id?: number | null
  ob_tenant_id?: number | null
  tenant_name?: string | null
  ob_db_id?: number | null
  sql_id: string
  db_name?: string | null
  sql_text?: string | null
  executions?: number
  exec_ps?: number
  sum_elapsed_time_us?: number
  avg_elapsed_time_us?: number
  avg_cpu_time_us?: number
  max_elapsed_time_us?: number
  plan_count?: number
  baseline_plan_union_hash?: string | null
  current_plan_union_hash?: string | null
  regression_ratio?: number
}

export type SqlAnalysisCategoryResponse = {
  category: SqlAnalysisCategory
  datasource_id: number | null
  start_time_us: number
  end_time_us: number
  compare_start_time_us?: number | null
  compare_end_time_us?: number | null
  limit: number
  items: SqlAnalysisListItem[]
  next_cursor?: string | null
  has_more: boolean
}

export type SqlTrendPoint = {
  bucket_start_us: number
  executions: number
  avg_elapsed_time_us: number
  total_elapsed_time_us: number
  avg_execute_time_us?: number | null
}

export type SqlDetail = {
  datasource_id: number
  sql_id: string
  start_time_us: number
  end_time_us: number
  db_name?: string | null
  user_name?: string | null
  sql_text?: string | null
  executions: number
  avg_elapsed_time_us: number
  avg_execute_time_us?: number | null
  max_elapsed_time_us: number
  latest_request_time_us?: number | null
  plan_count?: number | null
}

export type SqlPlanHistoryItem = {
  tenant_id: number
  sql_id: string
  plan_id: number
  plan_hash?: number | null
  executions: number
  avg_exe_usec: number
  elapsed_time: number
  execute_time: number
  table_scan: number
  last_active_time: string
  query_sql?: string | null
}

export type SqlPlanExplainItem = {
  operator: string
  object_name?: string | null
  cost?: number | null
  cardinality?: number | null
  plan_line_id?: number | null
  parent_id?: number | null
  depth?: number | null
  property?: string | null
}

export type SqlPlanExplainResponse = {
  datasource_id: number
  sql_id: string
  plan_id?: number | null
  source: string
  items: SqlPlanExplainItem[]
}

export type SqlAnalysisSignal = {
  key: string
  severity: string
  summary: string
  evidence?: string | null
}

export type SqlLiveDiscoveryItem = {
  source_datasource_id?: number | null
  preferred_execution_datasource_id?: number | null
  tenant_id?: number | null
  tenant_name?: string | null
  db_name?: string | null
  user_name?: string | null
  sql_id: string
  sql_text?: string | null
  latest_request_time_us?: number | null
  plan_count?: number | null
}

export type SqlLiveDiscoveryResponse = {
  datasource_id: number
  start_time_us: number
  end_time_us: number
  limit: number
  items: SqlLiveDiscoveryItem[]
}

export type SqlLiveDbNamesResponse = {
  datasource_id: number
  start_time_us: number
  end_time_us: number
  items: string[]
}

export type SqlUnavailableDimension = {
  key: string
  label: string
  reason: string
}

export type SqlLiveCurrentPlanFact = {
  plan_id?: number | null
  plan_hash?: number | null
  last_active_time?: string | null
  table_scan?: number | null
  explain_source: string
  explain_item_count: number
}

export type SqlLiveFacts = {
  datasource_id: number
  sql_id: string
  start_time_us: number
  end_time_us: number
  cluster_key?: string | null
  tenant_id?: number | null
  db_name?: string | null
  user_name?: string | null
  sql_text?: string | null
  latest_request_time_us?: number | null
  current_plan: SqlLiveCurrentPlanFact
  current_plans: SqlPlanHistoryItem[]
  window_plan_total: number
  current_plan_id?: number | null
  objects: string[]
  unavailable_dimensions: SqlUnavailableDimension[]
}

export type SqlLivePlanDetail = {
  plan_id?: number | null
  plan_hash?: number | null
  last_active_time?: string | null
  table_scan?: number | null
  explain_source: string
  objects: string[]
  explain_items: SqlPlanExplainItem[]
}

export type SqlLiveAnalysisContext = {
  datasource_id: number
  sql_id: string
  start_time_us: number
  end_time_us: number
  facts: SqlLiveFacts
  signals: SqlAnalysisSignal[]
  current_plans: SqlPlanHistoryItem[]
  window_plan_total: number
  current_plan_id?: number | null
  plan_explain: SqlPlanExplainResponse
  plan_details: SqlLivePlanDetail[]
}

export type SqlLiveAnalysisAiExplainResponse = {
  datasource_id: number
  sql_id: string
  context: SqlLiveAnalysisContext
  summary: string
  risk_points: string[]
  investigation_steps: string[]
  optimization_directions: string[]
}

export type Agent = {
  id: number;
  name: string;
  description?: string;
  prompt: string;
  tools?: string[];
  skills?: string[];
  agent_type: "built_in" | "custom";
  status: string;
  created_at: string;
  updated_at: string;
}

export type AgentRunResult = {
  conversation: Conversation;
  datasource_ids: number[];
}

export type ScheduleTargetType = "function" | "agent" | "stats_analysis" | "collector"
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
  input_payload?: Record<string, any> | null;
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
  conversation_id?: number | null;
  error_summary?: string | null;
  output_summary?: string | null;
  output_payload?: Record<string, any> | null;
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
  links?: Record<string, any>[];
};

export type SlackTemplateConfig = {
  username?: string | null;
  icon_emoji?: string | null;
  channel?: string | null;
};

export type TelegramTemplateConfig = {
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

export type ConversationCategory = "primary" | "scene" | "agent_run"

export type Conversation = {
  id: number;
  title: string;
  datasource_id?: number;
  agent_id?: number;
  active_skills?: string[];
  category: ConversationCategory;
  scene_key?: string | null;
  read_only: boolean;
  created_at: string;
  updated_at: string;
}

export type ToolCallItem = {
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: unknown;
  pending_action_token?: string | null;
  pending_action_status?: 'pending' | 'confirmed' | 'cancelled' | null;
}

export type ContentPart =
  | { type: "text"; text: string }
  | { type: "progress"; text: string; stage?: string | null }
  | { type: "tool_use"; id: string; name: string; input?: object | null; result?: unknown; pending_action_token?: string | null; pending_action_status?: "pending" | "confirmed" | "cancelled" | null }

export type Message = {
  id: number;
  conversation_id: number;
  role: string;
  content: string;
  agent_name?: string | null;
  tool_calls?: ToolCallItem[] | null;
  content_parts?: ContentPart[] | null;
  created_at: string;
}

export type PageBuildOrchestration = {
  enabled?: boolean;
  mode?: string;
  scenario_id?: string;
  required_slots?: string[];
  slots?: Record<string, any>;
  dependencies?: Array<string | Record<string, any>>;
}

export type ChatStreamEvent = {
  type:
    | "thinking"
    | "plan"
    | "assistant_progress"
    | "step_start"
    | "step_result"
    | "reflect"
    | "task_contract"
    | "progress"
    | "verification"
    | "task_state"
    | "checkpoint"
    | "context_compressed"
    | "assistant"
    | "skill_delta"
    | "error"
    | "done";
  id?: string;
  ts?: string;
  phase?: string;
  data?: any;
  meta?: Record<string, any>;
}

export type SceneAgentPayload = {
  key: string
  context?: Record<string, any>
  focus_object?: Record<string, any> | null
  tools?: string[]
  skills?: string[]
}

export type SaveAgentStreamEvent = {
  type: "save_agent_status" | "save_agent_done" | "error" | "done";
  data?: any;
}

export type ChatEvent = {
  id: number;
  conversation_id: number;
  event_type: string;
  phase?: string;
  turn_id?: string | null;
  turn_seq?: number | null;
  part_seq?: number | null;
  role?: string | null;
  agent_name?: string | null;
  payload?: Record<string, any> | null;
  created_at: string;
}

export type ChatHandoffFact = {
  label: string;
  value: string;
}

export type ChatHandoffSource = {
  page: string;
  entry: string;
  label?: string | null;
}

export type ChatHandoffPacket = {
  type: string;
  version: number;
  source: ChatHandoffSource;
  title: string;
  summary?: string | null;
  facts: ChatHandoffFact[];
  suggested_prompts: string[];
  context: Record<string, any>;
}

export type ChatHandoff = {
  id: number;
  conversation_id: number;
  status: string;
  consumed_at?: string | null;
  packet: ChatHandoffPacket;
  created_at: string;
}

export type ChatHandoffCreateInput = {
  conversation_id?: number;
  title?: string;
  datasource_id?: number;
  preferred_execution_datasource_id?: number;
  packet: ChatHandoffPacket;
}

export type ChatHandoffCreateResponse = {
  conversation: Conversation;
  handoff: ChatHandoff;
}

export type PendingAction = {
  token: string;
  action_type: string;
  status: string;
  batch_id?: string;
  sql?: string;
  sql_preview?: string;
  intent?: string;
  resolved_datasource_id?: number;
  resolved_role?: string;
  cluster_key?: string;
  tenant_fingerprint?: Record<string, string>;
  execution_fingerprint?: string;
  mode?: string;
  object_type?: string;
  object_action?: string;
  object_id?: number;
  preview?: string;
  risk_level?: string;
  confirmation_policy?: string;
  idempotency_key?: string;
  source_text?: string;
  created_at?: string | null;
}

export type BuildSession = {
  id: number;
  conversation_id?: number;
  scope_type: string;
  scope_object_type: "page" | "function" | "scheduler";
  scope_object_id: string;
  ttl_seconds: number;
  heartbeat_at: string;
  expires_at: string;
  status: "active" | "closed";
  created_at: string;
  updated_at: string;
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
  config?: Record<string, unknown> | null;
  resource_ref?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export type ServiceInput = {
  name: string;
  service_type: string;
  config?: Record<string, unknown> | null;
  resource_ref?: string | null;
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
    api.post<{success: boolean; message: string}>(`/services/${id}/test`).then(res => res.data),
  testConfig: (data: ServiceInput) =>
    api.post<{success: boolean; message: string}>('/services/test-config', data).then(res => res.data),
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

export const sqlAnalysisApi = {
  listLiveDbNames: (params: {
    datasource_id: number
    start_time_us: number
    end_time_us: number
    tenant_id?: number
    tenant_name?: string
  }) => api.get<SqlLiveDbNamesResponse>("/sql-analysis/live/db-names", { params }).then((res) => res.data),
  listLiveDiscovery: (params: {
    datasource_id: number
    start_time_us: number
    end_time_us: number
    tenant_id?: number
    tenant_name?: string
    db_name?: string
    sql_id?: string
    keyword?: string
    limit?: number
  }) => api.get<SqlLiveDiscoveryResponse>("/sql-analysis/live/discovery", { params }).then((res) => res.data),
  listLiveCategory: (params: {
    category: SqlAnalysisCategory
    datasource_id: number
    start_time_us: number
    end_time_us: number
    tenant_id?: number
    tenant_name?: string
    db_name?: string
    sql_id?: string
    keyword?: string
    limit?: number
    slow_threshold_us?: number
  }) =>
    api
      .get<SqlAnalysisCategoryResponse>(`/sql-analysis/live/categories/${params.category}`, { params })
      .then((res) => res.data),
  getLiveSqlDetail: (params: {
    datasource_id: number
    sql_id: string
    start_time_us: number
    end_time_us: number
    tenant_id?: number
  }) => api.get<SqlDetail>("/sql-analysis/live/sql-detail", { params }).then((res) => res.data),
  getLiveSqlTrend: (params: {
    datasource_id: number
    sql_id: string
    start_time_us: number
    end_time_us: number
    tenant_id?: number
    interval_seconds?: number
  }) => api.get<SqlTrendPoint[]>("/sql-analysis/live/sql-trend", { params }).then((res) => res.data),
  listLivePlanHistory: (params: {
    datasource_id: number
    sql_id: string
    tenant_id?: number
    limit?: number
  }) => api.get<SqlPlanHistoryItem[]>("/sql-analysis/live/plan-history", { params }).then((res) => res.data),
  getLivePlanExplain: (params: {
    datasource_id: number
    sql_id: string
    plan_id?: number
    sql_text?: string
    db_name?: string
  }) => {
    const { sql_text, db_name, ...queryParams } = params
    return api.post<SqlPlanExplainResponse>("/sql-analysis/live/plan-explain", { sql_text, db_name }, { params: queryParams }).then((res) => res.data)
  },
  buildLiveContext: (params: {
    datasource_id: number
    sql_id: string
    start_time_us: number
    end_time_us: number
    tenant_id?: number
  }) => api.get<SqlLiveAnalysisContext>("/sql-analysis/live/build-context", { params }).then((res) => res.data),
  explainLiveSqlWithAi: (params: {
    datasource_id: number
    sql_id: string
    start_time_us: number
    end_time_us: number
    tenant_id?: number
  }) => api.post<SqlLiveAnalysisAiExplainResponse>("/sql-analysis/live/explain-with-ai", null, { params }).then((res) => res.data),
}

export const agentsApi = {
  list: () => api.get<Agent[]>('/agents').then(res => res.data),
  get: (id: number) => api.get<Agent>(`/agents/${id}`).then(res => res.data),
  create: (data: { name: string; description?: string; prompt: string; tools?: string[]; skills?: string[] }) =>
    api.post<Agent>('/agents', data).then(res => res.data),
  update: (id: number, data: Partial<Omit<Agent, 'id' | 'status' | 'created_at' | 'updated_at'>>) =>
    api.patch<Agent>(`/agents/${id}`, data).then(res => res.data),
  run: (id: number, data: { datasource_ids?: number[]; title?: string }) =>
    api.post<AgentRunResult>(`/agents/${id}/run`, data).then(res => res.data),
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

export const conversationsApi = {
  list: (params?: { datasource_id?: number; agent_id?: number; category?: ConversationCategory; scene_key?: string }) =>
    api.get<Conversation[]>('/conversations', { params }).then(res => res.data),
  get: (id: number) => api.get<Conversation>(`/conversations/${id}`).then(res => res.data),
  create: (data: {
    title?: string;
    datasource_id?: number;
    agent_id?: number;
    active_skills?: string[];
    category?: ConversationCategory;
    scene_key?: string | null;
    read_only?: boolean;
  }) =>
    api.post<Conversation>('/conversations', data).then(res => res.data),
  update: (id: number, data: Partial<Conversation>) =>
    api.patch<Conversation>(`/conversations/${id}`, data).then(res => res.data),
  delete: (id: number) => api.delete(`/conversations/${id}`),
  createBuildSession: (
    conversationId: number,
    data: { scope_object_type: "page" | "function" | "scheduler"; scope_object_id: string; ttl_seconds?: number }
  ) =>
    api
      .post<BuildSession>(`/conversations/${conversationId}/build-sessions`, data)
      .then((res) => res.data),
  getActiveBuildSession: (conversationId: number) =>
    api
      .get<BuildSession>(`/conversations/${conversationId}/build-sessions/active`)
      .then((res) => res.data),
  heartbeatBuildSession: (conversationId: number, sessionId: number, ttl_seconds?: number) =>
    api
      .post<BuildSession>(`/conversations/${conversationId}/build-sessions/${sessionId}/heartbeat`, {
        ttl_seconds,
      })
      .then((res) => res.data),
  closeBuildSession: (conversationId: number, sessionId: number) =>
    api.delete(`/conversations/${conversationId}/build-sessions/${sessionId}`),
};

export const messagesApi = {
  list: (conversationId: number) =>
    api.get<Message[]>(`/messages/conversation/${conversationId}`).then(res => res.data),
  create: (data: { conversation_id: number; role: string; content: string }) =>
    api.post<Message>('/messages', data).then(res => res.data),
};

export const chatApi = {
  stream: (
    conversationId: number,
    content: string,
    options?: {
      signal?: AbortSignal
      timeoutMs?: number
      runDatasourceIds?: number[]
      handoffId?: number
      sceneAgent?: SceneAgentPayload
      conversationContext?: string
      locale?: string
    }
  ) => {
    const url = `${API_BASE_URL}/chat/${conversationId}/stream`
    const controller = new AbortController()
    const timeoutMs = options?.timeoutMs ?? 300000
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
    const externalSignal = options?.signal

    let onAbort: (() => void) | undefined
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort()
      } else {
        onAbort = () => controller.abort()
        externalSignal.addEventListener("abort", onAbort, { once: true })
      }
    }
    
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content,
        run_datasource_ids: Array.isArray(options?.runDatasourceIds)
          ? options?.runDatasourceIds
          : undefined,
        handoff_id: typeof options?.handoffId === "number" ? options.handoffId : undefined,
        scene_agent: options?.sceneAgent,
        conversation_context: String(options?.conversationContext || "").trim() || undefined,
        locale: options?.locale || undefined,
      }),
      signal: controller.signal,
    }).finally(() => {
      clearTimeout(timeoutId)
      if (externalSignal && onAbort) {
        externalSignal.removeEventListener("abort", onAbort)
      }
    })
  },
  saveAgentStream: (
    conversationId: number,
    payload?: { user_input?: string },
    options?: { signal?: AbortSignal; timeoutMs?: number }
  ) => {
    const url = `${API_BASE_URL}/chat/${conversationId}/save-agent/stream`
    const controller = new AbortController()
    const timeoutMs = options?.timeoutMs ?? 300000
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
    const externalSignal = options?.signal

    let onAbort: (() => void) | undefined
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort()
      } else {
        onAbort = () => controller.abort()
        externalSignal.addEventListener("abort", onAbort, { once: true })
      }
    }

    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {}),
      signal: controller.signal,
    }).finally(() => {
      clearTimeout(timeoutId)
      if (externalSignal && onAbort) {
        externalSignal.removeEventListener("abort", onAbort)
      }
    })
  },
  complete: (content: string) =>
    api.post<{content: string}>('/chat/complete', { content }).then(res => res.data),
  listEvents: (conversationId: number) =>
    api.get<ChatEvent[]>(`/chat/${conversationId}/events`).then(res => res.data),
  createHandoff: (data: ChatHandoffCreateInput) =>
    api.post<ChatHandoffCreateResponse>("/chat/handoffs", data).then((res) => res.data),
  getHandoff: (conversationId: number, handoffId: number) =>
    api.get<ChatHandoff>(`/chat/${conversationId}/handoffs/${handoffId}`).then((res) => res.data),
  consumeHandoff: (conversationId: number, handoffId: number) =>
    api.post<ChatHandoff>(`/chat/${conversationId}/handoffs/${handoffId}/consume`, {}).then((res) => res.data),
  listPendingActions: (conversationId: number) =>
    api.get<PendingAction[]>(`/chat/${conversationId}/actions/pending`).then(res => res.data),
  confirmPendingAction: (conversationId: number, token: string) =>
    api.post<{success: boolean; token: string; status: string; result: any; should_resume?: boolean; error?: string; assistant_message?: string}>(
      `/chat/${conversationId}/actions/${token}/confirm`
    ).then(res => res.data),
  cancelPendingAction: (conversationId: number, token: string) =>
    api.post<{success: boolean; token: string; status: string}>(
      `/chat/${conversationId}/actions/${token}/cancel`
    ).then(res => res.data),
};

export const pagesApi = {
  list: () => api.get<any[]>('/pages').then((res) => res.data),
  navigation: () => api.get<any[]>('/pages/navigation').then((res) => res.data),
  create: (data: { name: string; description?: string; draft_payload?: Record<string, any> }) =>
    api.post<any>('/pages', data).then((res) => res.data),
  get: (id: number) => api.get<any>(`/pages/${id}`).then((res) => res.data),
  getPublished: (id: number) => api.get<any>(`/pages/${id}/published`).then((res) => res.data),
  update: (id: number, data: Record<string, any>) =>
    api.patch<any>(`/pages/${id}`, data).then((res) => res.data),
  delete: (id: number) => api.delete(`/pages/${id}`),
  listReleases: (id: number) => api.get<any[]>(`/pages/${id}/releases`).then((res) => res.data),
  listBuildRuns: (id: number, limit = 20) =>
    api.get<any[]>(`/pages/${id}/build-runs`, { params: { limit } }).then((res) => res.data),
  getBuildRun: (id: number, runId: string) =>
    api.get<any>(`/pages/${id}/build-runs/${runId}`).then((res) => res.data),
  listBuildRunEvents: (id: number, runId: string) =>
    api.get<any[]>(`/pages/${id}/build-runs/${runId}/events`).then((res) => res.data),
  buildRun: (
    id: number,
    prompt: string,
    conversationContext?: string,
    options?: { orchestration?: PageBuildOrchestration }
  ) =>
    api
      .post<any>(`/pages/${id}/build-runs`, {
        prompt,
        ...(conversationContext ? { conversation_context: conversationContext } : {}),
        ...(options?.orchestration ? { orchestration: options.orchestration } : {}),
      })
      .then((res) => res.data),
  buildRunStream: (
    id: number,
    prompt: string,
    conversationContext?: string,
    options?: { orchestration?: PageBuildOrchestration; signal?: AbortSignal; timeoutMs?: number }
  ) => {
    const url = `${API_BASE_URL}/pages/${id}/build-runs/stream`
    const controller = new AbortController()
    const timeoutMs = options?.timeoutMs ?? 300000
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
    const externalSignal = options?.signal

    let onAbort: (() => void) | undefined
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort()
      } else {
        onAbort = () => controller.abort()
        externalSignal.addEventListener("abort", onAbort, { once: true })
      }
    }

    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        ...(conversationContext ? { conversation_context: conversationContext } : {}),
        ...(options?.orchestration ? { orchestration: options.orchestration } : {}),
      }),
      signal: controller.signal,
    }).finally(() => {
      clearTimeout(timeoutId)
      if (externalSignal && onAbort) {
        externalSignal.removeEventListener("abort", onAbort)
      }
    })
  },
  build: (
    id: number,
    prompt: string,
    conversationContext?: string,
    options?: { orchestration?: PageBuildOrchestration }
  ) =>
    api
      .post<any>(`/pages/${id}/build-runs`, {
        prompt,
        ...(conversationContext ? { conversation_context: conversationContext } : {}),
        ...(options?.orchestration ? { orchestration: options.orchestration } : {}),
      })
      .then((res) => res.data),
  preview: (id: number) => api.post<any>(`/pages/${id}/preview`, {}).then((res) => res.data),
  freeze: (id: number, data?: Record<string, any>) =>
    api.post<any>(`/pages/${id}/freeze`, data ?? {}).then((res) => res.data),
  listSnapshots: (id: number, limit = 20) =>
    api.get<any[]>(`/pages/${id}/snapshots`, { params: { limit } }).then((res) => res.data),
  compile: (id: number, data?: Record<string, any>) =>
    api.post<any>(`/pages/${id}/compile`, data ?? {}).then((res) => res.data),
  listCompileRuns: (id: number, limit = 20) =>
    api.get<any[]>(`/pages/${id}/compile-runs`, { params: { limit } }).then((res) => res.data),
  publish: (id: number, data?: Record<string, any>) =>
    api.post<any>(`/pages/${id}/publish`, data ?? {}).then((res) => res.data),
  archive: (id: number) => api.post<any>(`/pages/${id}/archive`, {}).then((res) => res.data),
  rollback: (id: number, releaseId: number) =>
    api.post<any>(`/pages/${id}/rollback`, { release_id: releaseId }).then((res) => res.data),
}

export const functionsApi = {
  list: () => api.get<any[]>('/functions').then((res) => res.data),
  listAllRuns: (limit = 50) =>
    api.get<any[]>('/functions/runs', { params: { limit } }).then((res) => res.data),
  create: (data: Record<string, any>) => api.post<any>('/functions', data).then((res) => res.data),
  get: (id: number) => api.get<any>(`/functions/${id}`).then((res) => res.data),
  getBySlug: (slug: string) => api.get<any>(`/functions/by-slug/${encodeURIComponent(slug)}`).then((res) => res.data),
  getByName: (name: string) => api.get<any>(`/functions/by-name/${encodeURIComponent(name)}`).then((res) => res.data),
  update: (id: number, data: Record<string, any>) =>
    api.patch<any>(`/functions/${id}`, data).then((res) => res.data),
  delete: (id: number) => api.delete(`/functions/${id}`),
  listReleases: (id: number) => api.get<any[]>(`/functions/${id}/releases`).then((res) => res.data),
  listBuildRuns: (id: number, limit = 20) =>
    api.get<any[]>(`/functions/${id}/build-runs`, { params: { limit } }).then((res) => res.data),
  getBuildRun: (id: number, runId: string) =>
    api.get<any>(`/functions/${id}/build-runs/${runId}`).then((res) => res.data),
  listBuildRunEvents: (id: number, runId: string) =>
    api.get<any[]>(`/functions/${id}/build-runs/${runId}/events`).then((res) => res.data),
  listRuns: (id: number, limit = 20) =>
    api.get<any[]>(`/functions/${id}/runs`, { params: { limit } }).then((res) => res.data),
  build: (id: number, prompt: string, options?: { ambiguity_mode?: "clarify" | "default" }) =>
    api
      .post<any>(`/functions/${id}/build`, {
        prompt,
        ambiguity_mode: options?.ambiguity_mode ?? "default",
      })
      .then((res) => res.data),
  buildChatStream: (
    id: number,
    data: Record<string, any>,
    options?: { signal?: AbortSignal; timeoutMs?: number }
  ) => {
    const url = `${API_BASE_URL}/functions/${id}/chat/stream`
    const controller = new AbortController()
    const timeoutMs = options?.timeoutMs ?? 300000
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
    const externalSignal = options?.signal

    let onAbort: (() => void) | undefined
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort()
      } else {
        onAbort = () => controller.abort()
        externalSignal.addEventListener("abort", onAbort, { once: true })
      }
    }

    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}),
      signal: controller.signal,
    }).finally(() => {
      clearTimeout(timeoutId)
      if (externalSignal && onAbort) {
        externalSignal.removeEventListener("abort", onAbort)
      }
    })
  },
  buildChat: (id: number, data: Record<string, any>, options?: { signal?: AbortSignal }) =>
    api.post<any>(`/functions/${id}/chat`, data, { signal: options?.signal }).then((res) => res.data),
  suggestInput: (
    id: number,
    data: { prompt?: string; conversation_context?: string }
  ) => api.post<any>(`/functions/${id}/suggest-input`, data).then((res) => res.data),
  strategy: (id: number, data: Record<string, any>) =>
    api.post<any>(`/functions/${id}/strategy`, data).then((res) => res.data),
  verify: (id: number, data?: Record<string, any>) =>
    api.post<any>(`/functions/${id}/verify`, data ?? {}).then((res) => res.data),
  release: (id: number, data?: Record<string, any>) =>
    api.post<any>(`/functions/${id}/release`, data ?? {}).then((res) => res.data),
  invoke: (id: number, data?: Record<string, any>) =>
    api.post<any>(`/functions/${id}/invoke`, data ?? {}).then((res) => res.data),
  duplicate: (id: number) =>
    api.post<any>(`/functions/${id}/duplicate`).then((res) => res.data),
}

export const schedulesApi = {
  list: () => api.get<Schedule[]>('/schedules').then((res) => res.data),
  workerHealth: () => api.get<ScheduleWorkerHealth>('/schedules/worker-health').then((res) => res.data),
  create: (data: Record<string, any>) => api.post<Schedule>('/schedules', data).then((res) => res.data),
  aiCreate: (data: Record<string, any>) =>
    api.post<{ schedule: Schedule; build_summary: string }>('/schedules/ai-create', data).then((res) => res.data),
  get: (id: number) => api.get<Schedule>(`/schedules/${id}`).then((res) => res.data),
  update: (id: number, data: Record<string, any>) =>
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

// ---------------------------------------------------------------------------
// Stats Analysis (OB statistics health)
// ---------------------------------------------------------------------------

export type StatsTaskSummary = {
  total_tasks: number
  success_tasks: number
  failed_tasks: number
  failed_task_ratio_pct: number
  total_tables_planned: number
  total_tables_failed: number
}

export type StatsSchedulerWindow = {
  job_name: string
  enabled: boolean
  last_start_date?: string | null
  next_run_date?: string | null
  failure_count?: number | null
  datasource_id?: number | null
  cluster_key?: string | null
}

export type StatsOverviewResponse = {
  task_summary: StatsTaskSummary
  scheduler_windows: StatsSchedulerWindow[]
}

export type StatsFailedTableItem = {
  tenant_name?: string | null
  owner?: string | null
  table_name?: string | null
  task_start_time?: string | null
  task_end_time?: string | null
  gather_seconds?: number | null
  memory_used?: number | null
  stat_refresh_failed_list?: string | null
  status?: string | null
  datasource_id?: number | null
  cluster_key?: string | null
}

export type StatsStaleTableItem = {
  tenant_name?: string | null
  owner?: string | null
  table_name?: string | null
  last_analyzed?: string | null
  stats_state?: string | null
  datasource_id?: number | null
  cluster_key?: string | null
}

export type StatsDmlChangeItem = {
  tenant_name?: string | null
  database_name?: string | null
  table_name?: string | null
  row_change_delta?: number | null
  datasource_id?: number | null
  cluster_key?: string | null
}

export type StatsTrendPoint = {
  date: string
  avg_duration_min: number
  max_duration_min: number
  failed_tables: number
  total_tasks: number
}

export type StatsCollectionDaySummary = {
  date: string
  task_type: string
  total_tasks: number
  success_tasks: number
  failed_tasks: number
  total_tables: number
  success_tables: number
  failed_tables: number
  avg_duration_min: number
  max_duration_min: number
  cluster_key?: string | null
  tenant_name?: string | null
  datasource_id?: number | null
}

export type StatsCollectionDailySummaryResponse = {
  datasource_id: number | null
  items: StatsCollectionDaySummary[]
}

export type StatsDailyTaskItem = {
  task_id?: string | null
  task_type?: string | null
  status?: string | null
  start_time?: string | null
  end_time?: string | null
  duration_seconds?: number | null
  table_count?: number | null
  failed_count?: number | null
  cluster_key?: string | null
  tenant_name?: string | null
  datasource_id?: number | null
}

export type StatsDailyTasksResponse = {
  datasource_id: number | null
  date: string
  items: StatsDailyTaskItem[]
  total: number
  page: number
  page_size: number
}

export type StatsDailyFailedTableItem = {
  owner?: string | null
  table_name?: string | null
  failure_count: number
  latest_status?: string | null
  latest_error?: string | null
  latest_gather_seconds?: number | null
  latest_task_start_time?: string | null
  cluster_key?: string | null
  tenant_name?: string | null
  datasource_id?: number | null
}

export type StatsDailyFailedTablesResponse = {
  datasource_id: number | null
  date: string
  items: StatsDailyFailedTableItem[]
}

export type StatsColStatItem = {
  owner?: string | null
  table_name?: string | null
  column_name?: string | null
  num_distinct?: number | null
  num_buckets?: number | null
  histogram?: string | null
  sample_size?: number | null
  last_analyzed?: string | null
}

export type StatsHistogramItem = {
  owner?: string | null
  table_name?: string | null
  column_name?: string | null
  bucket_cnt?: number | null
  max_bucket_repeat?: number | null
  total_repeat?: number | null
  top_bucket_ratio?: number | null
}

export type StatsWorkbenchCard = {
  key: string
  title: string
  value: string
  status: "healthy" | "warning" | "critical" | "info"
  hint?: string | null
}

export type StatsIssueItem = {
  issue_id: string
  kind: "scheduling" | "failed_table" | "stale_stats" | "dml_change"
  severity: "high" | "medium" | "low"
  title: string
  summary: string
  datasource_id?: number | null
  cluster_key?: string | null
  tenant_name?: string | null
  database_name?: string | null
  table_name?: string | null
  facts: Record<string, unknown>
}

export type StatsTenantConfigCheck = {
  tenant_name: string
  datasource_id: number
  auto_gather_enabled?: boolean | null
  enabled_windows: number
  total_windows: number
  recent_task_count: number
  issue_type: "auto_gather_disabled" | "no_windows" | "partial_windows" | "no_recent_tasks" | "unreachable" | "healthy"
  issue_label: string
  suggestion_sql: string
}

export type StatsWorkbenchResponse = {
  datasource_id: number | null
  cluster_key: string
  overview: StatsOverviewResponse
  cards: StatsWorkbenchCard[]
  issues: StatsIssueItem[]
  warnings: string[]
  tenant_config_checks: StatsTenantConfigCheck[]
}

export type StatsDiagnosisEvidence = {
  label: string
  value: string
  source?: string | null
}

export type StatsDiagnosisAction = {
  title: string
  rationale?: string | null
  risk?: string | null
  execution_window?: string | null
}

export type StatsDiagnosisResult = {
  headline: string
  verdict: string
  reasoning: string
  evidence: StatsDiagnosisEvidence[]
  next_actions: StatsDiagnosisAction[]
  missing_facts: string[]
  diagnosis_path: string[]
  risks: string[]
}


export type StatsRiskCandidateTagItem = {
  tag_key: string
  tag_label: string
  severity: "high" | "medium" | "low"
  score: number
  summary?: string | null
  facts: Record<string, unknown>
}

export type StatsRiskCandidateItem = {
  candidate_id: number
  datasource_id: number
  cluster_key: string
  tenant_name?: string | null
  database_name: string
  table_name: string
  severity: "high" | "medium" | "low"
  score: number
  lifecycle_status: "active" | "expired" | "resolved"
  source?: string | null
  latest_summary?: string | null
  last_seen_at: string
  tags: StatsRiskCandidateTagItem[]
}

export type StatsRiskCandidatesResponse = {
  datasource_id: number
  items: StatsRiskCandidateItem[]
}

export type StatsRiskCollectResponse = {
  datasource_id: number
  collected_tables: number
  active_candidates: number
  expired_candidates: number
}

export type StatsRiskAnalyzeSubmitResponse = {
  run_id: string
  status: "pending" | "running" | "ready" | "degraded" | "needs_clarification" | "error"
}

export type StatsRiskAnalyzeStatusResponse = {
  run_id: string
  status: "pending" | "running" | "ready" | "degraded" | "needs_clarification" | "error"
  result?: StatsDiagnosisResult | null
  error_summary?: string | null
}

export type StatsRiskAnalysisStreamEvent =
  | { type: "phase"; data: { phase: string; run_id: string; status: string } }
  | { type: "delta"; data: { run_id: string; chunk: string } }
  | { type: "done"; data: { run_id: string; status: "pending" | "running" | "ready" | "degraded" | "needs_clarification" | "error"; result?: StatsDiagnosisResult | null } }
  | { type: "error"; data: { message: string } }

export type StatsRiskCollectionRunItem = {
  run_id: string
  datasource_id: number
  trigger_type: string
  status: string
  summary?: string | null
  error_summary?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export type StatsRiskCollectionRunsResponse = {
  datasource_id: number
  items: StatsRiskCollectionRunItem[]
}

export type StatsDrawerDetailField = {
  label: string
  value: string
  source?: string | null
}

export type StatsDrawerDetailSection = {
  key: string
  title: string
  description?: string | null
  fields: StatsDrawerDetailField[]
}

export type StatsDrawerHistoryRow = {
  task_id?: string | null
  owner?: string | null
  table_name?: string | null
  status?: string | null
  ret_code?: string | null
  start_time?: string | null
  end_time?: string | null
  gather_seconds?: number | null
  memory_used?: number | null
  trigger_type?: string | null
  stat_refresh_failed_list?: string | null
  properties?: string | null
  task_table_count?: number | null
  task_failed_count?: number | null
}

export type StatsDrawerDetailResponse = {
  datasource_id: number
  title: string
  object_kind: string
  severity: "high" | "medium" | "low"
  summary: string
  subtitle?: string | null
  sections: StatsDrawerDetailSection[]
  history_rows: StatsDrawerHistoryRow[]
  history_source?: string | null
  missing_facts: string[]
  chat_context: Record<string, unknown>
}

export const statsAnalysisApi = {
  getWorkbench: (params: { datasource_id?: number | null; cluster_key?: string | null; lookback_days?: number; stale_days?: number }) =>
    api.get<StatsWorkbenchResponse>('/stats-analysis/workbench', { params }).then((res) => res.data),
  getOverview: (params: { datasource_id: number; tenant_name?: string; lookback_days?: number }) =>
    api.get<StatsOverviewResponse>('/stats-analysis/overview', { params }).then((res) => res.data),
  getFailedTables: (params: { datasource_id: number; tenant_name?: string; lookback_days?: number }) =>
    api.get<{ items: StatsFailedTableItem[] }>('/stats-analysis/failed-tables', { params }).then((res) => res.data),
  getStaleTables: (params: { datasource_id: number; tenant_name?: string; stale_days?: number }) =>
    api.get<{ items: StatsStaleTableItem[] }>('/stats-analysis/stale-tables', { params }).then((res) => res.data),
  getDmlChanges: (params: { datasource_id: number; tenant_name?: string }) =>
    api.get<{ items: StatsDmlChangeItem[] }>('/stats-analysis/dml-changes', { params }).then((res) => res.data),
  getTrend: (params: { datasource_id: number; lookback_days?: number }) =>
    api.get<{ points: StatsTrendPoint[] }>('/stats-analysis/trend', { params }).then((res) => res.data),
  getDailyCollectionSummary: (params: { datasource_id?: number | null; cluster_key?: string | null; lookback_days?: number }) =>
    api.get<StatsCollectionDailySummaryResponse>('/stats-analysis/daily-collection-summary', { params }).then((res) => res.data),
  getDailyFailedTables: (params: { datasource_id?: number | null; cluster_key?: string | null; date: string }) =>
    api.get<StatsDailyFailedTablesResponse>('/stats-analysis/daily-failed-tables', { params }).then((res) => res.data),
  getDailyTasks: (params: { datasource_id?: number | null; cluster_key?: string | null; date: string; page?: number; page_size?: number; task_type?: string | null; status?: string | null }) =>
    api.get<StatsDailyTasksResponse>('/stats-analysis/daily-tasks', { params }).then((res) => res.data),
  getColStats: (params: { datasource_id: number; db_name: string; table_name: string }) =>
    api.get<{ items: StatsColStatItem[] }>('/stats-analysis/col-stats', { params }).then((res) => res.data),
  getHistogram: (params: { datasource_id: number; db_name: string; table_name: string }) =>
    api.get<{ items: StatsHistogramItem[] }>('/stats-analysis/histogram', { params }).then((res) => res.data),
  collectRiskCandidates: (payload: { datasource_id: number; lookback_days?: number; stale_days?: number }) =>
    api.post<StatsRiskCollectResponse>('/stats-analysis/risk-candidates/collect', payload).then((res) => res.data),
  listRiskCandidates: (params: { datasource_id: number; include_inactive?: boolean; lifecycle_status?: string; limit?: number }) =>
    api.get<StatsRiskCandidatesResponse>('/stats-analysis/risk-candidates', { params }).then((res) => res.data),
  listRiskCollectionRuns: (params: { datasource_id: number; limit?: number }) =>
    api.get<StatsRiskCollectionRunsResponse>('/stats-analysis/risk-candidates/collect-runs', { params }).then((res) => res.data),
  getRiskCandidate: (params: { datasource_id: number; candidate_id: number }) =>
    api.get<StatsRiskCandidateItem>(`/stats-analysis/risk-candidates/${params.candidate_id}`, { params: { datasource_id: params.datasource_id } }).then((res) => res.data),
  getDrawerDetail: (payload: { datasource_id: number; issue?: StatsIssueItem; risk_candidate?: StatsRiskCandidateItem }) =>
    api.post<StatsDrawerDetailResponse>('/stats-analysis/drawer-detail', payload).then((res) => res.data),
  submitRiskAnalysis: (params: { datasource_id: number; candidate_id: number }) =>
    api.post<StatsRiskAnalyzeSubmitResponse>(`/stats-analysis/risk-candidates/${params.candidate_id}/analysis`, null, {
      params: { datasource_id: params.datasource_id },
    }).then((res) => res.data),
  streamRiskAnalysis: async (
    params: { datasource_id: number; candidate_id: number },
    handlers: {
      onEvent?: (event: StatsRiskAnalysisStreamEvent) => void
    } = {}
  ) => {
    const query = new URLSearchParams({ datasource_id: String(params.datasource_id) }).toString()
    const response = await fetch(`${API_BASE_URL}/stats-analysis/risk-candidates/${params.candidate_id}/analysis/stream?${query}`, {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
      },
    })
    if (!response.ok || !response.body) {
      throw new Error("Stats analysis stream initialization failed")
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder("utf-8")
    let buffer = ""
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split("\n\n")
      buffer = chunks.pop() || ""
      for (const chunk of chunks) {
        const line = chunk
          .split("\n")
          .map((item) => item.trim())
          .find((item) => item.startsWith("data:"))
        if (!line) continue
        try {
          const payload = JSON.parse(line.slice(5).trim()) as StatsRiskAnalysisStreamEvent
          handlers.onEvent?.(payload)
        } catch {
          // ignore malformed stream item
        }
      }
    }
  },
  getRiskAnalysis: (runId: string) =>
    api.get<StatsRiskAnalyzeStatusResponse>(`/stats-analysis/risk-candidates/analysis/${runId}`).then((res) => res.data),
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
      message?: Record<string, any>;
      template?: Record<string, any>;
      message_type?: ChannelMessageType;
      title?: string;
      content?: string;
      dry_run?: boolean;
    }
  ) => api.post<any>(`/channels/${id}/send`, data ?? {}).then((res) => res.data),
  sendTest: (
    id: number,
    data?: {
      message?: Record<string, any>;
      template?: Record<string, any>;
      message_type?: ChannelMessageType;
      title?: string;
      content?: string;
      dry_run?: boolean;
    }
  ) => api.post<any>(`/channels/${id}/send-test`, data ?? {}).then((res) => res.data),
}

// ── Session & Transaction Analysis ──────────────────────────────────────────

export type LiveSession = {
  datasource_id: number
  session_id: number
  user: string
  identity_label: string
  tenant_name: string | null
  client_ip: string | null
  db: string | null
  command: string
  time_seconds: number
  state: "ACTIVE" | "SLEEP"
  current_sql: string | null
  ob_tenant_id: number | null
}

export type LiveSessionListResponse = {
  datasource_id: number | null
  total: number
  active: number
  sessions: LiveSession[]
}

export type LiveTransaction = {
  datasource_id: number
  trans_hash: string
  session_id: number | null
  tenant_id: number | null
  trans_type: string
  state: "ACTIVE" | "PENDING_COMMIT"
  elapsed_seconds: number
  participants: number
  sql_list: string[]
}

export type LiveTransactionListResponse = {
  datasource_id: number | null
  long_transactions: LiveTransaction[]
  pending_transactions: LiveTransaction[]
}

export type SessionSnapshotForAI = {
  total: number
  active: number
  long_transaction_count: number
  pending_transaction_count: number
  user_distribution: Record<string, number>
  ip_distribution: Record<string, number>
  long_transactions: Array<{
    trans_type: string
    elapsed_seconds: number
    sql_list: string[]
  }>
}

export const sessionAnalysisApi = {
  listSessions: (params: { datasource_id?: number | null; cluster_key?: string | null; tenant_id?: number; tenant_name?: string }) =>
    api.get<LiveSessionListResponse>('/session-analysis/live/sessions', { params }).then((res) => res.data),

  listTransactions: (params: { datasource_id?: number | null; cluster_key?: string | null; tenant_id?: number; tenant_name?: string }) =>
    api.get<LiveTransactionListResponse>('/session-analysis/live/transactions', { params }).then((res) => res.data),

  killSession: (datasource_id: number, session_id: number) =>
    api.post<{ session_id: number; killed: boolean; message: string }>(
      `/session-analysis/live/sessions/${session_id}/kill`,
      null,
      { params: { datasource_id } }
    ).then((res) => res.data),

  analyzeStream: (snapshot: SessionSnapshotForAI, signal?: AbortSignal): Promise<Response> =>
    fetch(`${API_BASE_URL}/session-analysis/live/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(snapshot),
      signal,
    }),
}

// ── Settings API ────────────────────────────────────────────────────────

export type PlatformSettings = {
  build_engine: "pi_lite" | "external_cli"
  external_cli_command: string
  sql_allow_mutating?: boolean
  [key: string]: unknown
}

export type EngineTestResult = {
  ok: boolean
  message: string
  suggested_command?: string
  flags_added?: string[]
  env_issues?: string[]
  raw_cost?: number
}

export const settingsApi = {
  get: (): Promise<PlatformSettings> =>
    api.get('/settings').then((res) => res.data),

  patch: (payload: Partial<PlatformSettings>): Promise<PlatformSettings> =>
    api.patch('/settings', payload).then((res) => res.data),

  testEngine: (command?: string): Promise<EngineTestResult> =>
    api.post('/settings/test-engine', { command: command || "" }).then((res) => res.data),
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
