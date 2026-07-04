import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    dry_run: bool
    database: Path
    api_token: str
    auto_publish: bool
    allowed_wordpress_hosts: tuple[str, ...]
    max_request_bytes: int
    wordpress_base_url: str
    wordpress_username: str
    wordpress_app_password: str
    wordpress_seo_plugin: str
    anthropic_api_key: str
    anthropic_model: str
    anthropic_fallback_models: tuple[str, ...]
    web_search_provider: str = ""
    tavily_api_key: str = ""
    enable_live_research: bool = False

    @property
    def wordpress_ready(self) -> bool:
        return bool(self.wordpress_base_url and self.wordpress_username and self.wordpress_app_password)

    @property
    def live_ready(self) -> bool:
        return not self.dry_run and self.wordpress_ready


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[1]
    _load_dotenv(root / ".env")
    database = Path(os.getenv("AWA_DATABASE", "backend/data/agent.sqlite3"))
    if not database.is_absolute():
        database = root / database
    wordpress_base_url = os.getenv("WORDPRESS_BASE_URL", "").rstrip("/")
    allowed_hosts = tuple(
        host.strip().lower()
        for host in os.getenv("AWA_ALLOWED_WORDPRESS_HOSTS", "").split(",")
        if host.strip()
    )
    if wordpress_base_url and not allowed_hosts:
        parsed_host = urlparse(wordpress_base_url).hostname
        allowed_hosts = (parsed_host.lower(),) if parsed_host else ()
    return Settings(
        host=os.getenv("AWA_HOST", "127.0.0.1"),
        port=int(os.getenv("AWA_PORT", "8787")),
        dry_run=os.getenv("AWA_DRY_RUN", "true").lower() in {"1", "true", "yes", "on"},
        database=database,
        api_token=os.getenv("AWA_API_TOKEN", ""),
        auto_publish=os.getenv("AWA_AUTO_PUBLISH", "false").lower() in {"1", "true", "yes", "on"},
        allowed_wordpress_hosts=allowed_hosts,
        max_request_bytes=int(os.getenv("AWA_MAX_REQUEST_BYTES", "250000")),
        wordpress_base_url=wordpress_base_url,
        wordpress_username=os.getenv("WORDPRESS_USERNAME", ""),
        wordpress_app_password=os.getenv("WORDPRESS_APP_PASSWORD", ""),
        wordpress_seo_plugin=os.getenv("WORDPRESS_SEO_PLUGIN", "generic").lower(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        anthropic_fallback_models=tuple(
            model.strip()
            for model in os.getenv(
                "ANTHROPIC_FALLBACK_MODELS",
                "claude-opus-4-8,claude-haiku-4-5,claude-fable-5",
            ).split(",")
            if model.strip()
        ),
        web_search_provider=os.getenv("AWA_WEB_SEARCH_PROVIDER", "").lower(),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        enable_live_research=os.getenv("AWA_ENABLE_LIVE_RESEARCH", "false").lower() in {"1", "true", "yes", "on"},
    )
