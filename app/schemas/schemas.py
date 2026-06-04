from datetime import datetime
from enum import Enum
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SKILL_VERSION_REGEX = re.compile(r"^\d+\.\d+\.\d+$")
SKILL_DATABASES = {"oceanbase", "mysql", "general"}


def _normalize_skill_source(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"builtin", "built_in"}:
        return "built_in"
    if normalized == "custom":
        return "custom"
    raise ValueError("Skill source must be one of: built_in, custom")


class DataSourceBase(BaseModel):
    name: str
    host: str
    port: int = 3306
    db_type: str = "mysql"
    cluster_key: str
    tenant_role: str = "user"
    attributes: Optional[dict] = None
    user: str
    database: str = ""

    @field_validator("tenant_role")
    @classmethod
    def validate_tenant_role(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"business", "tenant"}:
            normalized = "user"
        if normalized not in {"sys", "user"}:
            raise ValueError("tenant_role must be one of: sys, user")
        return normalized


class DataSourceCreate(DataSourceBase):
    password: str


class DataSourceUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    db_type: Optional[str] = None
    cluster_key: Optional[str] = None
    tenant_role: Optional[str] = None
    attributes: Optional[dict] = None
    user: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    status: Optional[str] = None

    @field_validator("tenant_role")
    @classmethod
    def validate_update_tenant_role(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized in {"business", "tenant"}:
            normalized = "user"
        if normalized not in {"sys", "user"}:
            raise ValueError("tenant_role must be one of: sys, user")
        return normalized

class DataSourceResponse(DataSourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    updated_at: datetime


class DataSourceConnectInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    host: str
    port: int
    db_type: str
    user: str
    password: str
    database: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ServiceBase(BaseModel):
    name: str
    service_type: str
    config: Optional[dict] = None
    resource_ref: Optional[str] = None


class ServiceCreate(ServiceBase):
    pass


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    service_type: Optional[str] = None
    config: Optional[dict] = None
    resource_ref: Optional[str] = None
    status: Optional[str] = None


class ServiceResponse(ServiceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseBase(BaseModel):
    name: str
    description: Optional[str] = None
    tags: Optional[list[str]] = None


class KnowledgeBaseCreate(KnowledgeBaseBase):
    pass


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None


class KnowledgeBaseResponse(KnowledgeBaseBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    title: str
    filename: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime


class MonitorContractTableStatus(BaseModel):
    logical_name: str
    table_name: str
    present: bool


class MonitorContractColumnStatus(BaseModel):
    table_name: str
    column_name: str
    present: bool


class MonitorContractProbeResponse(BaseModel):
    connection_ok: bool
    message: Optional[str] = None
    required_tables: list[MonitorContractTableStatus]
    missing_tables: list[str]
    required_columns: list[MonitorContractColumnStatus]
    missing_columns: list[str]
    supported_features: dict[str, bool]


class SqlMonitorCategory(str, Enum):
    TOP_SQL = "top_sql"
    SLOW_SQL = "slow_sql"
    NEW_SQL = "new_sql"
    REGRESSED_SQL = "regressed_sql"
    PLAN_CHANGED_SQL = "plan_changed_sql"


class SqlMonitorCategoryItem(BaseModel):
    datasource_id: Optional[int] = None
    ob_tenant_id: Optional[int] = None
    tenant_name: Optional[str] = None
    ob_db_id: Optional[int] = None
    sql_id: Optional[str] = None
    sql_text: Optional[str] = None
    db_name: Optional[str] = None
    executions: Optional[float] = None
    exec_ps: Optional[float] = None
    sum_elapsed_time_us: Optional[float] = None
    avg_elapsed_time_us: Optional[float] = None
    avg_cpu_time_us: Optional[float] = None
    max_elapsed_time_us: Optional[float] = None
    regression_ratio: Optional[float] = None
    plan_count: Optional[int] = None
    current_plan_union_hash: Optional[str] = None
    baseline_plan_union_hash: Optional[str] = None


class SqlMonitorCategoryResponse(BaseModel):
    category: SqlMonitorCategory
    datasource_id: int | None
    start_time_us: int
    end_time_us: int
    compare_start_time_us: Optional[int] = None
    compare_end_time_us: Optional[int] = None
    limit: int
    items: list[SqlMonitorCategoryItem]
    next_cursor: Optional[str] = None
    has_more: bool = False


class SqlTrendPoint(BaseModel):
    bucket_start_us: int
    executions: int
    avg_elapsed_time_us: float
    total_elapsed_time_us: int
    avg_execute_time_us: float | None = None


class SqlDetailResponse(BaseModel):
    datasource_id: int
    sql_id: str
    start_time_us: int
    end_time_us: int
    db_name: Optional[str] = None
    user_name: Optional[str] = None
    sql_text: Optional[str] = None
    executions: int
    avg_elapsed_time_us: float
    avg_execute_time_us: float | None = None
    max_elapsed_time_us: int
    latest_request_time_us: Optional[int] = None
    plan_count: Optional[int] = None


class SqlPlanHistoryItem(BaseModel):
    tenant_id: int
    sql_id: str
    plan_id: int
    plan_hash: Optional[int] = None
    executions: Optional[int] = None
    avg_exe_usec: Optional[float] = None
    elapsed_time: Optional[int] = None
    execute_time: Optional[int] = None
    table_scan: Optional[int] = None
    last_active_time: str
    query_sql: Optional[str] = None


class SqlPlanExplainItem(BaseModel):
    operator: str
    object_name: Optional[str] = None
    cost: Optional[int] = None
    cardinality: Optional[int] = None
    plan_line_id: Optional[int] = None
    parent_id: Optional[int] = None
    depth: Optional[int] = None
    property: Optional[str] = None


class SqlPlanExplainResponse(BaseModel):
    datasource_id: int
    sql_id: str
    plan_id: Optional[int] = None
    source: str
    items: list[SqlPlanExplainItem]


class SqlFactWindow(BaseModel):
    start_time_us: int
    end_time_us: int


class SqlFactOwnership(BaseModel):
    datasource_id: Optional[int] = None
    ob_tenant_id: Optional[int] = None
    tenant_name: Optional[str] = None
    db_name: Optional[str] = None
    user_name: Optional[str] = None


class SqlExecutionFact(BaseModel):
    executions: int
    avg_elapsed_time_us: float
    max_elapsed_time_us: int
    latest_request_time_us: Optional[int] = None


class SqlResourceFact(BaseModel):
    avg_cpu_time_us: Optional[float] = None
    total_elapsed_time_us: int
    total_cpu_time_us: Optional[int] = None


class SqlPlanFact(BaseModel):
    plan_count: int = 0
    latest_plan_id: Optional[int] = None
    latest_plan_hash: Optional[int] = None
    latest_plan_last_active_time: Optional[str] = None
    latest_table_scan: Optional[int] = None
    explain_source: str
    explain_item_count: int


class SqlFactsResponse(BaseModel):
    datasource_id: int
    sql_id: str
    window: SqlFactWindow
    ownership: SqlFactOwnership
    sql_text: Optional[str] = None
    execution: SqlExecutionFact
    resource: SqlResourceFact
    plan: SqlPlanFact


class SqlRollupBucket(BaseModel):
    bucket_start_us: int
    executions: int
    avg_elapsed_time_us: float
    total_elapsed_time_us: int
    avg_cpu_time_us: Optional[float] = None


class SqlRollupSummary(BaseModel):
    source_bucket_count: int
    sampled_bucket_count: int
    total_executions: int
    total_elapsed_time_us: int
    total_cpu_time_us: Optional[int] = None
    avg_elapsed_time_us: float
    avg_cpu_time_us: Optional[float] = None
    max_avg_elapsed_time_us: float
    latest_avg_elapsed_time_us: float


class SqlRollupResponse(BaseModel):
    datasource_id: int
    sql_id: str
    window: SqlFactWindow
    sampling_strategy: str
    sample_limit: int
    summary: SqlRollupSummary
    buckets: list[SqlRollupBucket]


class SqlAnalysisSignal(BaseModel):
    key: str
    severity: str
    summary: str
    evidence: Optional[str] = None


class SqlAnalysisContextResponse(BaseModel):
    datasource_id: int
    sql_id: str
    category: SqlMonitorCategory
    start_time_us: int
    end_time_us: int
    ob_tenant_id: Optional[int] = None
    matched_categories: list[str]
    signals: list[SqlAnalysisSignal]
    facts: Optional[SqlFactsResponse] = None
    rollup: Optional[SqlRollupResponse] = None
    detail: Optional[SqlDetailResponse] = None
    trend: list[SqlTrendPoint]
    plan_history: list[SqlPlanHistoryItem]
    plan_explain: SqlPlanExplainResponse


class SqlAnalysisAiExplainResponse(BaseModel):
    datasource_id: int
    sql_id: str
    category: SqlMonitorCategory
    context: SqlAnalysisContextResponse
    summary: str
    risk_points: list[str]
    investigation_steps: list[str]
    optimization_directions: list[str]


class SqlLiveDiscoveryItem(BaseModel):
    source_datasource_id: Optional[int] = None
    preferred_execution_datasource_id: Optional[int] = None
    tenant_id: Optional[int] = None
    tenant_name: Optional[str] = None
    db_name: Optional[str] = None
    user_name: Optional[str] = None
    sql_id: str
    sql_text: Optional[str] = None
    latest_request_time_us: Optional[int] = None
    plan_count: Optional[int] = None


class SqlLiveDiscoveryResponse(BaseModel):
    datasource_id: int
    start_time_us: int
    end_time_us: int
    limit: int
    items: list[SqlLiveDiscoveryItem]


class SqlLiveDbNamesResponse(BaseModel):
    datasource_id: int
    start_time_us: int
    end_time_us: int
    items: list[str]


class SqlUnavailableDimension(BaseModel):
    key: str
    label: str
    reason: str


class SqlLiveCurrentPlanFact(BaseModel):
    plan_id: Optional[int] = None
    plan_hash: Optional[int] = None
    last_active_time: Optional[str] = None
    table_scan: Optional[int] = None
    explain_source: str
    explain_item_count: int


class SqlLiveFactsResponse(BaseModel):
    datasource_id: int
    sql_id: str
    start_time_us: int
    end_time_us: int
    cluster_key: Optional[str] = None
    tenant_id: Optional[int] = None
    db_name: Optional[str] = None
    user_name: Optional[str] = None
    sql_text: Optional[str] = None
    latest_request_time_us: Optional[int] = None
    current_plan: SqlLiveCurrentPlanFact
    current_plans: list[SqlPlanHistoryItem]
    window_plan_total: int = 0
    current_plan_id: Optional[int] = None
    objects: list[str]
    unavailable_dimensions: list[SqlUnavailableDimension]


class SqlLivePlanDetailResponse(BaseModel):
    plan_id: Optional[int] = None
    plan_hash: Optional[int] = None
    last_active_time: Optional[str] = None
    table_scan: Optional[int] = None
    explain_source: str
    objects: list[str]
    explain_items: list[SqlPlanExplainItem]


class SqlLiveAnalysisContextResponse(BaseModel):
    datasource_id: int
    sql_id: str
    start_time_us: int
    end_time_us: int
    facts: SqlLiveFactsResponse
    signals: list[SqlAnalysisSignal]
    current_plans: list[SqlPlanHistoryItem]
    window_plan_total: int = 0
    current_plan_id: Optional[int] = None
    plan_explain: SqlPlanExplainResponse
    plan_details: list[SqlLivePlanDetailResponse] = Field(default_factory=list)


class SqlLiveAnalysisAiExplainResponse(BaseModel):
    datasource_id: int
    sql_id: str
    context: SqlLiveAnalysisContextResponse
    summary: str
    risk_points: list[str]
    investigation_steps: list[str]
    optimization_directions: list[str]


_VALID_CONVERSATION_CATEGORIES = {"primary", "scene", "agent_run", "scheduler_run"}


def _normalize_conversation_category(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _VALID_CONVERSATION_CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(sorted(_VALID_CONVERSATION_CATEGORIES))}")
    return normalized


class ConversationBase(BaseModel):
    title: str = "New Conversation"
    datasource_id: Optional[int] = None
    agent_id: Optional[int] = None
    active_skills: Optional[list[str]] = None
    category: str = "primary"
    scene_key: Optional[str] = None
    read_only: bool = False

    @field_validator("category")
    @classmethod
    def validate_conversation_category(cls, value: str) -> str:
        return _normalize_conversation_category(value)

    @field_validator("scene_key")
    @classmethod
    def validate_scene_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        return normalized or None


class ConversationCreate(ConversationBase):
    pass


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    datasource_id: Optional[int] = None
    agent_id: Optional[int] = None
    active_skills: Optional[list[str]] = None
    category: Optional[str] = None
    scene_key: Optional[str] = None
    read_only: Optional[bool] = None

    @field_validator("category")
    @classmethod
    def validate_update_conversation_category(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _normalize_conversation_category(value)

    @field_validator("scene_key")
    @classmethod
    def validate_update_scene_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        return normalized or None


class ConversationResponse(ConversationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class BuildSessionCreate(BaseModel):
    scope_object_type: str
    scope_object_id: str
    ttl_seconds: int = 1800

    @field_validator("scope_object_type")
    @classmethod
    def validate_scope_object_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"page", "function", "scheduler"}:
            raise ValueError("scope_object_type must be one of: page, function, scheduler")
        return normalized

    @field_validator("scope_object_id")
    @classmethod
    def validate_scope_object_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("scope_object_id cannot be empty")
        return normalized

    @field_validator("ttl_seconds")
    @classmethod
    def validate_ttl_seconds(cls, value: int) -> int:
        if value < 60 or value > 24 * 3600:
            raise ValueError("ttl_seconds must be between 60 and 86400")
        return value


class BuildSessionHeartbeat(BaseModel):
    ttl_seconds: Optional[int] = None

    @field_validator("ttl_seconds")
    @classmethod
    def validate_ttl_seconds(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if value < 60 or value > 24 * 3600:
            raise ValueError("ttl_seconds must be between 60 and 86400")
        return value


class BuildSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: Optional[int] = None
    scope_type: str
    scope_object_type: str
    scope_object_id: str
    ttl_seconds: int
    heartbeat_at: datetime
    expires_at: datetime
    status: str
    created_at: datetime
    updated_at: datetime


class MessageBase(BaseModel):
    conversation_id: int
    role: str
    content: str


class MessageCreate(MessageBase):
    pass


class MessageResponse(MessageBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_name: str | None = None
    tool_calls: Optional[list] = None
    content_parts: Optional[list] = None
    created_at: datetime


class ChatEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    event_type: str
    phase: Optional[str] = None
    turn_id: Optional[str] = None
    turn_seq: Optional[int] = None
    part_seq: Optional[int] = None
    role: Optional[str] = None
    agent_name: Optional[str] = None
    payload: Optional[dict] = None
    created_at: datetime


class ChatHandoffFact(BaseModel):
    label: str
    value: str


class ChatHandoffSource(BaseModel):
    page: str
    entry: str
    label: Optional[str] = None


class ChatHandoffPacket(BaseModel):
    type: str
    version: int = 1
    source: ChatHandoffSource
    title: str
    summary: Optional[str] = None
    facts: list[ChatHandoffFact] = Field(default_factory=list)
    suggested_prompts: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("type cannot be empty")
        return normalized

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be empty")
        return normalized


class ChatHandoffCreate(BaseModel):
    conversation_id: Optional[int] = None
    title: Optional[str] = None
    datasource_id: Optional[int] = None
    preferred_execution_datasource_id: Optional[int] = None
    packet: ChatHandoffPacket


class ChatHandoffResponse(BaseModel):
    id: int
    conversation_id: int
    status: str
    consumed_at: Optional[datetime] = None
    packet: ChatHandoffPacket
    created_at: datetime


class ChatHandoffCreateResponse(BaseModel):
    conversation: ConversationResponse
    handoff: ChatHandoffResponse


def _normalize_agent_type(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"builtin", "built_in"}:
        return "built_in"
    if normalized == "custom":
        return "custom"
    raise ValueError("agent_type must be one of: built_in, custom")


class AgentBase(BaseModel):
    name: str
    description: Optional[str] = None
    prompt: str
    tools: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    agent_type: str = "custom"

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: str) -> str:
        return _normalize_agent_type(value)


class SkillBase(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str
    database: str = "general"
    always_apply: bool
    prompt: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2 or len(normalized) > 64:
            raise ValueError("Skill name length must be 2-64 characters")
        if any(ch in normalized for ch in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("Skill name contains invalid path/control characters")
        return normalized

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not SKILL_VERSION_REGEX.match(value):
            raise ValueError("Skill version must use semantic version format x.y.z")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if len(value.strip()) < 8:
            raise ValueError("Skill description must be at least 8 characters")
        return value.strip()

    @field_validator("database")
    @classmethod
    def validate_database(cls, value: str) -> str:
        if value not in SKILL_DATABASES:
            raise ValueError("Skill database must be one of: oceanbase, mysql, general")
        return value

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Skill prompt cannot be empty")
        return value.strip()


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    database: Optional[str] = None
    always_apply: Optional[bool] = None
    prompt: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        if len(normalized) < 2 or len(normalized) > 64:
            raise ValueError("Skill name length must be 2-64 characters")
        if any(ch in normalized for ch in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("Skill name contains invalid path/control characters")
        return normalized

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not SKILL_VERSION_REGEX.match(value):
            raise ValueError("Skill version must use semantic version format x.y.z")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value.strip()) < 8:
            raise ValueError("Skill description must be at least 8 characters")
        return value.strip()

    @field_validator("database")
    @classmethod
    def validate_database(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in SKILL_DATABASES:
            raise ValueError("Skill database must be one of: oceanbase, mysql, general")
        return value

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("Skill prompt cannot be empty")
        return value.strip()


class SkillResponse(SkillBase):
    source: str = "custom"
    path: str = ""

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        return _normalize_skill_source(value)


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    prompt: Optional[str] = None
    tools: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    agent_type: Optional[str] = None
    status: Optional[str] = None

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _normalize_agent_type(value)


class AgentResponse(AgentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    updated_at: datetime


class AgentRunRequest(BaseModel):
    datasource_ids: list[int] = Field(default_factory=list)
    title: Optional[str] = None

    @field_validator("datasource_ids")
    @classmethod
    def validate_datasource_ids(cls, value: list[int]) -> list[int]:
        unique_ids: list[int] = []
        seen: set[int] = set()
        for item in value:
            if not isinstance(item, int) or item <= 0:
                raise ValueError("datasource_ids must contain positive integers")
            if item in seen:
                continue
            seen.add(item)
            unique_ids.append(item)
        return unique_ids

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            return None
        return normalized[:500]


class AgentRunResponse(BaseModel):
    conversation: ConversationResponse
    datasource_ids: list[int] = Field(default_factory=list)


class ToolExecutionBase(BaseModel):
    agent_id: Optional[int] = None
    conversation_id: Optional[int] = None
    tool_name: str
    parameters: Optional[dict] = None


class ToolExecutionCreate(ToolExecutionBase):
    pass


class ToolExecutionUpdate(BaseModel):
    result: Optional[str] = None
    error: Optional[str] = None


class ToolExecutionResponse(ToolExecutionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime


# ── Session & Transaction Analysis ──────────────────────────────────────────

class LiveSession(BaseModel):
    datasource_id: int
    session_id: int
    user: str
    identity_label: str
    tenant_name: str | None
    client_ip: str | None
    db: str | None
    command: str
    time_seconds: int
    state: str
    current_sql: str | None
    ob_tenant_id: int | None


class LiveSessionListResponse(BaseModel):
    datasource_id: int | None
    total: int
    active: int
    sessions: list[LiveSession]


class LiveTransaction(BaseModel):
    datasource_id: int
    trans_hash: str
    session_id: int | None
    tenant_id: int | None
    trans_type: str
    state: str
    elapsed_seconds: int
    participants: int
    sql_list: list[str]


class LiveTransactionListResponse(BaseModel):
    datasource_id: int | None
    long_transactions: list[LiveTransaction]
    pending_transactions: list[LiveTransaction]


class SessionKillResponse(BaseModel):
    session_id: int
    killed: bool
    message: str


class SessionSnapshotForAI(BaseModel):
    total: int
    active: int
    long_transaction_count: int
    pending_transaction_count: int
    user_distribution: dict[str, int]
    ip_distribution: dict[str, int]
    long_transactions: list[dict]
