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
                "Sensitive headers must be stored in secrets.headers: " + ", ".join(exposed)
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
        return any([self.username, self.password, self.bearer_token, self.api_key, self.headers])


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
    datasource_ids: list[int] = Field(default_factory=list)

    @field_validator("datasource_ids")
    @classmethod
    def validate_datasource_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("Datasource IDs must be positive")
        return list(dict.fromkeys(value))


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    prompt: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    agent_type: str | None = None
    status: str | None = None
    datasource_ids: list[int] | None = None

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
    datasource_ids: list[int]


# ---------------------------------------------------------------------------
# Platform Settings
# ---------------------------------------------------------------------------


class PlatformSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_base_url: str | None = None
    sql_allow_mutating: bool | None = None
    context_window_tokens: int | None = Field(default=None, ge=8_192, le=2_000_000)
    context_compression_threshold_percent: int | None = Field(default=None, ge=50, le=95)


class PlatformSettingsResponse(BaseModel):
    sql_allow_mutating: bool = False
    ai_api_key_configured: bool = False
    ai_model: str | None = None
    ai_base_url: str | None = None
    context_window_tokens: int = 128_000
    context_compression_threshold_percent: int = 75
    praxis_edition: str | None = None
