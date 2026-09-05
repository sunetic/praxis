import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.datasource.access import normalize_access_level

SKILL_VERSION_REGEX = re.compile(r"^\d+\.\d+\.\d+$")
SKILL_DATABASES = {"oceanbase", "mysql", "postgresql", "general"}


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
    access_level: Literal["user", "admin"] = "user"
    tenant_role: str = "user"
    attributes: dict | None = None
    user: str
    database: str = ""

    @model_validator(mode="before")
    @classmethod
    def infer_access_level_from_legacy_role(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "access_level" in data:
            return data
        legacy_role = data.get("tenant_role")
        if legacy_role is None:
            return data
        normalized = dict(data)
        normalized["access_level"] = normalize_access_level(str(legacy_role))
        return normalized

    @field_validator("access_level", mode="before")
    @classmethod
    def validate_access_level(cls, value: str) -> str:
        return normalize_access_level(value)

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
    name: str | None = None
    host: str | None = None
    port: int | None = None
    db_type: str | None = None
    cluster_key: str | None = None
    access_level: Literal["user", "admin"] | None = None
    tenant_role: str | None = None
    attributes: dict | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = None
    status: str | None = None

    @model_validator(mode="before")
    @classmethod
    def infer_update_access_level_from_legacy_role(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "access_level" in data:
            return data
        legacy_role = data.get("tenant_role")
        if legacy_role is None:
            return data
        normalized = dict(data)
        normalized["access_level"] = normalize_access_level(str(legacy_role))
        return normalized

    @field_validator("access_level", mode="before")
    @classmethod
    def validate_update_access_level(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return normalize_access_level(value)

    @field_validator("tenant_role")
    @classmethod
    def validate_update_tenant_role(cls, value: str | None) -> str | None:
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


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ServiceHttpConfig(BaseModel):
    base_url: str
    auth_type: Literal["none", "basic", "bearer", "api_key"] = "none"
    api_key_header: str = "X-API-Key"
    default_headers: dict[str, str] = Field(default_factory=dict)
    health_check_path: str = "/-/ready"
    health_check_method: Literal["GET", "POST"] = "GET"
    response_format: Literal["auto", "json", "text"] = "auto"
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    verify_tls: bool = True
    use_environment_proxy: bool = False
    max_response_bytes: int = Field(default=262_144, ge=1_024, le=1_048_576)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain credentials, query parameters, or fragments")
        return normalized

    @field_validator("health_check_path")
    @classmethod
    def validate_health_check_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("/") or normalized.startswith("//"):
            raise ValueError("health_check_path must be a relative API path beginning with /")
        return normalized

    @field_validator("api_key_header")
    @classmethod
    def validate_api_key_header(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("api_key_header cannot be empty")
        return normalized

    @field_validator("default_headers")
    @classmethod
    def validate_default_headers(cls, value: dict[str, str]) -> dict[str, str]:
        sensitive_names = {
            "authorization",
            "proxy-authorization",
            "cookie",
            "set-cookie",
            "x-api-key",
        }
        normalized = _validate_service_headers(value)
        exposed = sorted(name for name in normalized if name.lower() in sensitive_names)
        if exposed:
            raise ValueError(
                "Sensitive headers must be stored in secrets.headers: "
                + ", ".join(exposed)
            )
        return normalized


class ServiceSecretConfig(BaseModel):
    username: str = ""
    password: str = ""
    bearer_token: str = ""
    api_key: str = ""
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_service_headers(value)

    def has_values(self) -> bool:
        return any(
            [self.username, self.password, self.bearer_token, self.api_key, self.headers]
        )


def _validate_service_headers(value: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = raw_name.strip()
        if not name or "\r" in name or "\n" in name:
            raise ValueError("HTTP header names must be non-empty single-line values")
        if "\r" in raw_value or "\n" in raw_value:
            raise ValueError(f"HTTP header {name} must be a single-line value")
        normalized[name] = raw_value
    return normalized


def _translate_legacy_service_payload(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    translated = dict(data)
    raw_config = translated.get("config")
    if not isinstance(raw_config, dict) or "base_url" in raw_config:
        return translated

    host = str(raw_config.get("host") or "").strip()
    if not host:
        return translated
    scheme = str(raw_config.get("scheme") or "http").strip().lower()
    port = int(raw_config.get("port") or 8080)
    config = {
        "base_url": f"{scheme}://{host}:{port}",
        "auth_type": "basic" if raw_config.get("user") else "none",
        "health_check_path": str(raw_config.get("health_check_path") or "/api/v2/time"),
        "response_format": "json",
    }
    translated["config"] = config
    if "secrets" not in translated and (raw_config.get("user") or raw_config.get("password")):
        translated["secrets"] = {
            "username": str(raw_config.get("user") or ""),
            "password": str(raw_config.get("password") or ""),
        }
    return translated


class ServiceBase(BaseModel):
    name: str
    service_type: str
    config: ServiceHttpConfig
    resource_ref: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Service name cannot be empty")
        return normalized

    @field_validator("service_type")
    @classmethod
    def validate_service_type(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        if not re.fullmatch(r"[a-z][a-z0-9_.]{1,49}", normalized):
            raise ValueError("service_type must be a lowercase identifier")
        return normalized

    @field_validator("resource_ref")
    @classmethod
    def validate_resource_ref(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        prefix, separator, identifier = normalized.partition(":")
        if separator != ":" or prefix not in {"cluster", "datasource"} or not identifier:
            raise ValueError("resource_ref must use cluster:<key> or datasource:<id>")
        if prefix == "datasource" and not identifier.isdigit():
            raise ValueError("datasource resource_ref must contain a numeric ID")
        return normalized


class ServiceCreate(ServiceBase):
    secrets: ServiceSecretConfig | None = None
    knowledge_base_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def translate_legacy_payload(cls, data: Any) -> Any:
        return _translate_legacy_service_payload(data)


class ServiceUpdate(BaseModel):
    name: str | None = None
    service_type: str | None = None
    config: ServiceHttpConfig | None = None
    secrets: ServiceSecretConfig | None = None
    resource_ref: str | None = None
    status: str | None = None
    knowledge_base_ids: list[int] | None = None

    @model_validator(mode="before")
    @classmethod
    def translate_legacy_payload(cls, data: Any) -> Any:
        return _translate_legacy_service_payload(data)

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Service name cannot be empty")
        return normalized

    @field_validator("service_type")
    @classmethod
    def validate_optional_service_type(cls, value: str | None) -> str | None:
        return None if value is None else ServiceBase.validate_service_type(value)

    @field_validator("resource_ref")
    @classmethod
    def validate_optional_resource_ref(cls, value: str | None) -> str | None:
        return ServiceBase.validate_resource_ref(value)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"active", "inactive"}:
            raise ValueError("status must be active or inactive")
        return normalized


class ServiceResponse(ServiceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    has_credentials: bool = False
    knowledge_base_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ServiceTestResponse(BaseModel):
    success: bool
    message: str
    http_status: int | None = None


class KnowledgeBaseBase(BaseModel):
    name: str
    description: str | None = None
    tags: list[str] | None = None


class KnowledgeBaseCreate(KnowledgeBaseBase):
    pass


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class KnowledgeBaseResponse(KnowledgeBaseBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_count: int = 0
    source: str | None = None
    pack_id: str | None = None
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


class PackVersion(BaseModel):
    branch: str
    label: str


class KnowledgePackResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str
    tags: list[str] = []
    db_type: str | None = None
    type: str = "git"
    repo_url: str = ""
    branch: str = ""
    subdirectory: str = ""
    license: str = ""
    source_url: str | None = None
    estimated_doc_count: int = 0
    estimated_size_mb: float = 0.0
    versions: list[PackVersion] | None = None
    default_version: str | None = None
    status: str = "available"
    kb_id: int | None = None
    error_message: str | None = None


class KnowledgePackInstallStatus(BaseModel):
    pack_id: str
    status: str
    progress_message: str | None = None
    kb_id: int | None = None
    error_message: str | None = None


_VALID_CONVERSATION_CATEGORIES = {"primary", "scene", "agent_run", "scheduler_run"}


def _normalize_conversation_category(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _VALID_CONVERSATION_CATEGORIES:
        raise ValueError(
            f"category must be one of: {', '.join(sorted(_VALID_CONVERSATION_CATEGORIES))}"
        )
    return normalized


class ConversationBase(BaseModel):
    title: str = "New Conversation"
    datasource_id: int | None = None
    agent_id: int | None = None
    active_skills: list[str] | None = None
    category: str = "primary"
    scene_key: str | None = None
    read_only: bool = False

    @field_validator("category")
    @classmethod
    def validate_conversation_category(cls, value: str) -> str:
        return _normalize_conversation_category(value)

    @field_validator("scene_key")
    @classmethod
    def validate_scene_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        return normalized or None


class ConversationCreate(ConversationBase):
    pass


class ConversationUpdate(BaseModel):
    title: str | None = None
    datasource_id: int | None = None
    agent_id: int | None = None
    active_skills: list[str] | None = None
    category: str | None = None
    scene_key: str | None = None
    read_only: bool | None = None

    @field_validator("category")
    @classmethod
    def validate_update_conversation_category(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_conversation_category(value)

    @field_validator("scene_key")
    @classmethod
    def validate_update_scene_key(cls, value: str | None) -> str | None:
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
    ttl_seconds: int | None = None

    @field_validator("ttl_seconds")
    @classmethod
    def validate_ttl_seconds(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if value < 60 or value > 24 * 3600:
            raise ValueError("ttl_seconds must be between 60 and 86400")
        return value


class BuildSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int | None = None
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
    tool_calls: list | None = None
    content_parts: list | None = None
    created_at: datetime


class ChatEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    event_type: str
    phase: str | None = None
    turn_id: str | None = None
    turn_seq: int | None = None
    part_seq: int | None = None
    role: str | None = None
    agent_name: str | None = None
    payload: dict | None = None
    created_at: datetime


class ChatHandoffFact(BaseModel):
    label: str
    value: str


class ChatHandoffSource(BaseModel):
    page: str
    entry: str
    label: str | None = None


class ChatHandoffPacket(BaseModel):
    type: str
    version: int = 1
    source: ChatHandoffSource
    title: str
    summary: str | None = None
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
    conversation_id: int | None = None
    title: str | None = None
    datasource_id: int | None = None
    preferred_execution_datasource_id: int | None = None
    packet: ChatHandoffPacket


class ChatHandoffResponse(BaseModel):
    id: int
    conversation_id: int
    status: str
    consumed_at: datetime | None = None
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
    description: str | None = None
    prompt: str
    tools: list[str] | None = None
    skills: list[str] | None = None
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
            raise ValueError("Skill database must be one of: oceanbase, mysql, postgresql, general")
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
    name: str | None = None
    version: str | None = None
    description: str | None = None
    database: str | None = None
    always_apply: bool | None = None
    prompt: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
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
    def validate_version(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not SKILL_VERSION_REGEX.match(value):
            raise ValueError("Skill version must use semantic version format x.y.z")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value.strip()) < 8:
            raise ValueError("Skill description must be at least 8 characters")
        return value.strip()

    @field_validator("database")
    @classmethod
    def validate_database(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in SKILL_DATABASES:
            raise ValueError("Skill database must be one of: oceanbase, mysql, postgresql, general")
        return value

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str | None) -> str | None:
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
    name: str | None = None
    description: str | None = None
    prompt: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    agent_type: str | None = None
    status: str | None = None

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: str | None) -> str | None:
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
    title: str | None = None

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
    def validate_title(cls, value: str | None) -> str | None:
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
    agent_id: int | None = None
    conversation_id: int | None = None
    tool_name: str
    parameters: dict | None = None


class ToolExecutionCreate(ToolExecutionBase):
    pass


class ToolExecutionUpdate(BaseModel):
    result: str | None = None
    error: str | None = None


class ToolExecutionResponse(ToolExecutionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    result: str | None = None
    error: str | None = None
    created_at: datetime


# Chat Stream
# ---------------------------------------------------------------------------


class SceneAgentRequest(BaseModel):
    key: str
    context: dict[str, Any] = Field(default_factory=dict)
    focus_object: dict[str, Any] | None = None
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class ChatStreamRequest(BaseModel):
    content: str = ""
    run_datasource_ids: list[int] | None = None
    handoff_id: int | None = None
    scene_agent: SceneAgentRequest | None = None
    conversation_context: str | None = None
    locale: str | None = None
    resume_action_token: str | None = None

    @field_validator("resume_action_token")
    @classmethod
    def _normalize_resume_action_token(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

class ChatCompleteRequest(BaseModel):
    content: str = ""


class ChatContextStatusResponse(BaseModel):
    conversation_id: int
    context_window_tokens: int
    estimated_tokens: int
    used_percent: float
    compression_progress_percent: float = 0
    compression_threshold_percent: int
    compression_threshold_tokens: int
    remaining_tokens: int
    summary_tokens: int = 0
    recent_message_count: int = 0
    compacted_through_message_id: int | None = None
    last_compacted_at: datetime | None = None
    token_source: str = "estimate"
    state: Literal["ready", "compressing", "compression_failed"] = "ready"


# ---------------------------------------------------------------------------
# Platform Settings
# ---------------------------------------------------------------------------


class PlatformSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_base_url: str | None = None
    build_engine: str | None = None
    external_cli_command: str | None = None
    external_cli_pre_flags: str | None = None
    external_cli_post_flags: str | None = None
    sql_allow_mutating: bool | None = None
    ai_action_confirmation_bypass: bool | None = None
    context_window_tokens: int | None = Field(default=None, ge=8_192, le=2_000_000)
    context_compression_threshold_percent: int | None = Field(default=None, ge=50, le=95)

    @field_validator("build_engine")
    @classmethod
    def validate_build_engine(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"reasoning", "external_cli"}:
            raise ValueError("build_engine must be one of: reasoning, external_cli")
        return normalized


class PlatformSettingsResponse(BaseModel):
    build_engine: str = "reasoning"
    external_cli_command: str = ""
    external_cli_pre_flags: str = ""
    external_cli_post_flags: str = ""
    sql_allow_mutating: bool = False
    ai_action_confirmation_bypass: bool = False
    ai_api_key_configured: bool = False
    ai_model: str | None = None
    ai_base_url: str | None = None
    context_window_tokens: int = 128_000
    context_compression_threshold_percent: int = 75
    praxis_edition: str | None = None


class SettingsEngineTestRequest(BaseModel):
    command: str = ""


class SettingsEngineTestResponse(BaseModel):
    ok: bool
    message: str
    suggested_command: str | None = None
    flags_added: list[str] = Field(default_factory=list)
    env_issues: list[str] = Field(default_factory=list)
