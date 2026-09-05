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
  agent_type: "built_in" | "custom";
  status: string;
  created_at: string;
  updated_at: string;
}

export type AgentRunResult = {
  conversation: Conversation;
  datasource_ids: number[];
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
  pending_action_status?: 'pending' | 'confirmed' | 'cancelled' | 'failed' | null;
}

export type ContentPart =
  | { type: "text"; text: string }
  | { type: "progress"; text: string; stage?: string | null }
  | { type: "tool_use"; id: string; name: string; input?: object | null; result?: unknown; pending_action_token?: string | null; pending_action_status?: "pending" | "confirmed" | "cancelled" | "failed" | null }

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

export type ChatContextStatus = {
  conversation_id: number
  context_window_tokens: number
  estimated_tokens: number
  used_percent: number
  compression_progress_percent: number
  compression_threshold_percent: number
  compression_threshold_tokens: number
  remaining_tokens: number
  summary_tokens: number
  recent_message_count: number
  compacted_through_message_id?: number | null
  last_compacted_at?: string | null
  token_source: "estimate" | "provider" | string
  state: "ready" | "compressing" | "compression_failed"
}

export type ContextCompressionNotice = {
  mode: string
  revision: number
  summarized_message_count: number
  summarized_turn_count: number
  duplicate_messages_omitted: number
  through_message_id?: number
  before_tokens: number
  after_tokens: number
  before_percent: number
  after_percent: number
  summary_tokens: number
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
    | "context_status"
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
  resolved_access_level?: "user" | "admin";
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
      resumeActionToken?: string
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
        resume_action_token: options?.resumeActionToken || undefined,
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
  getContextStatus: (conversationId: number) =>
    api.get<ChatContextStatus>(`/chat/${conversationId}/context`).then(res => res.data),
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

export type PlatformSettings = {
  build_engine: "reasoning" | "external_cli"
  external_cli_command: string
  external_cli_pre_flags?: string
  external_cli_post_flags?: string
  sql_allow_mutating?: boolean
  ai_action_confirmation_bypass?: boolean
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

  patch: (payload: PlatformSettingsUpdate): Promise<PlatformSettings> =>
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
