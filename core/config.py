"""Settings (non-secret) and secrets, loaded from JSON files.

Paths come from env vars so containers can mount secrets read-only.
Secrets are optional at load time: code that needs a key calls ``require()``,
so e.g. unit tests and the S3 check do not need LLM keys.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class BucketsCfg(_Base):
    input: str
    result: str


class ObjectStorageCfg(_Base):
    endpoint: str
    region: str = ""
    buckets: BucketsCfg
    work_prefix: str = "_work/"


class YandexCfg(_Base):
    folder_id: str = ""
    llm_base_url: str = "https://llm.api.cloud.yandex.net/v1"
    llm_model: str = "deepseek-v4-flash"
    llm_reasoning_effort: str = "none"
    llm_timeout_seconds: int = 60


class CloudruCfg(_Base):
    llm_base_url: str = ""
    llm_timeout_seconds: int = 120


class OcrCfg(_Base):
    base_url: str = "https://ocr.api.cloud.yandex.net/ocr/v1"
    model: str = "page"
    languages: list[str] = ["ru", "en"]
    dpi: int = 200
    timeout_seconds: int = 60
    max_requests_per_second: float = 1.0
    price_per_page_rub: float = 0.1321


class ProcessingCfg(_Base):
    worker_concurrency: int = 4
    max_attempts: int = 3
    max_upload_mb: int = 500
    min_text_chars_machine_readable: int = 50
    max_garbled_ratio: float = 0.05
    char_limit: int = 10000
    empty_slide_min_chars: int = 5
    ocr_max_pages: int = 60
    ocr_concurrency: int = 2


class ExportCfg(_Base):
    csv_mode: str = "both"               # auto = delimiter of the uploaded worksheet | comma | tab | both (auto + the other one as an extra file)
    score_line: str = "Итоговый балл: {total} из 100."   # first line of the participant feedback (cosmos used "Тестовый балл")
    feedback_header: str = "Обратная связь участнику:"


class StagesCfg(_Base):
    detect_enabled: bool = True          # stage 3 (AI detection, informational) on/off; admin toggle on the Settings page


class DefaultsCfg(_Base):
    """What a NEW run uses when nobody picks anything (editable by admins on the Models page)."""
    grade_model: str = "yandex-deepseek-v4-1-flash"
    detect_model: str = "yandex-deepseek-v4-1-flash"
    grade_prompt: str = "grade_short"     # prompt NAME; the latest published version is pinned at run creation
    detect_prompt: str = "ai_detect"


class SessionCfg(_Base):
    cookie_name: str = "fedlab_session"
    ttl_hours: int = 12


class DatabaseCfg(_Base):
    host: str = "postgres"
    port: int = 5432
    name: str = "fedlab"
    user: str = "fedlab"


class OAuthCfg(_Base):
    redirect_uri: str
    scopes: str = "login:info login:email"


class Settings(_Base):
    env: str = "prod"  # "dev" enables dev-login and relaxes cookie flags
    domain: str
    bootstrap_admin_emails: list[str] = []
    object_storage: ObjectStorageCfg
    yandex: YandexCfg = YandexCfg()
    cloudru: CloudruCfg = CloudruCfg()
    models: list[dict] = []
    ocr: OcrCfg = OcrCfg()
    processing: ProcessingCfg = ProcessingCfg()
    session: SessionCfg = SessionCfg()
    export: ExportCfg = ExportCfg()
    defaults: DefaultsCfg = DefaultsCfg()
    stages: StagesCfg = StagesCfg()
    run_id_start: int = 1                # dev uses 9000 so dev and production never share a run prefix in the common buckets
    incoming_prefix: str = "incoming/"
    database: DatabaseCfg = DatabaseCfg()
    oauth: OAuthCfg
    redis: dict[str, str] = {"url": "redis://redis:6379"}
    storage: dict[str, str] = {"backend": "s3"}
    budget: dict[str, float] = {"run_cap_rub": 1000}


class ApiKey(_Base):
    api_key: SecretStr = SecretStr("")
    key_id: str = ""


class Secrets(_Base):
    object_storage: dict[str, SecretStr] = Field(default_factory=dict)
    yandex_llm: ApiKey = ApiKey()
    yandex_ocr: ApiKey = ApiKey()
    cloudru_llm: ApiKey = ApiKey()
    yandex_oauth: dict[str, SecretStr] = Field(default_factory=dict)
    app: dict[str, SecretStr] = Field(default_factory=dict)

    def require(self, section: str, name: str) -> str:
        """Return a secret value or fail with a message that names the missing key."""
        value = getattr(self, section)
        raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        text = raw.get_secret_value() if isinstance(raw, SecretStr) else (raw or "")
        if not text or text.startswith("PUT_"):
            raise RuntimeError(f"Secret {section}.{name} is not set in config/secrets.json")
        return text


def _read(env: str, default: str) -> dict:
    return json.loads(Path(os.environ.get(env, default)).read_text(encoding="utf-8"))


@lru_cache
def get_settings() -> Settings:
    data = _read("FEDLAB_SETTINGS_PATH", "config/settings.json")
    if env := os.environ.get("FEDLAB_ENV"):  # compose dev override sets FEDLAB_ENV=dev
        data["env"] = env
    if start := os.environ.get("FEDLAB_RUN_ID_START"):   # dev: 9000, so dev and prod run prefixes never collide in shared buckets
        data["run_id_start"] = int(start)
    return Settings.model_validate(data)


@lru_cache
def get_secrets() -> Secrets:
    return Secrets.model_validate(_read("FEDLAB_SECRETS_PATH", "config/secrets.json"))
