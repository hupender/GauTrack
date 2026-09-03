"""Configuration. Everything comes from the environment / mvp/.env — nothing
secret is ever hard-coded (SPEC.md §1.12)."""
from __future__ import annotations

import functools
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent
ROOT_DIR = API_DIR.parent  # mvp/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- secrets ---------------------------------------------------------
    secret_key: str

    # --- database --------------------------------------------------------
    postgres_db: str = "gautrack"
    postgres_owner_user: str = "gautrack_owner"
    postgres_app_user: str = "gautrack_app"
    postgres_password: str = ""
    app_db_password: str = ""
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 55432
    # Remote Postgres (e.g. Supabase): SSL required. PaaS hosts without IPv6 need prefer_ipv4.
    postgres_sslmode: str = ""
    postgres_prefer_ipv4: int = 0
    # Optional override when POSTGRES_HOST is db.*.supabase.co (IPv6-only). Use the
    # Session pooler hostname from Supabase → Connect → Session mode (IPv4).
    postgres_pooler_host: str = ""
    # explicit overrides (used by docker-compose / CI); otherwise derived
    database_url: str | None = None
    database_url_owner: str | None = None

    # --- application -----------------------------------------------------
    photo_dir: str = str(ROOT_DIR / "data" / "photos")
    map_tiles_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    map_tiles_attribution: str = "&copy; OpenStreetMap contributors"
    seed_demo: int = 0
    # Optional fixed password for the seeded demo accounts.  Left empty, every
    # reseed mints fresh random passwords; set it and the same credentials come
    # back every time, which is what a pilot needs so a field phone does not
    # stop working the day someone refreshes the demo data.
    seed_password: str = ""
    cookie_secure: int = 1
    trusted_proxy_count: int = 0
    session_idle_minutes_field: int = 720   # 12h  (SPEC §1.4)
    session_idle_minutes_admin: int = 120   # 2h   (SPEC §1.4)
    log_level: str = "info"

    # --- login throttling (SPEC §1.4) ------------------------------------
    login_max_per_min_per_ip: int = 5
    login_max_per_hour_per_user: int = 10
    login_lockout_failures: int = 10
    login_lockout_minutes: int = 15

    # --- uploads ---------------------------------------------------------
    max_photo_bytes: int = 5 * 1024 * 1024
    max_sync_items: int = 200
    public_report_per_hour_per_ip: int = 10

    def _db_hostname(self) -> str:
        if self.postgres_pooler_host.strip():
            return self.postgres_pooler_host.strip()
        return self.postgres_host

    def _remote_postgres(self) -> bool:
        blob = " ".join(
            filter(
                None,
                [
                    self.postgres_host,
                    self.postgres_pooler_host,
                    self.database_url or "",
                    self.database_url_owner or "",
                ],
            )
        )
        return "supabase.co" in blob or "pooler.supabase.com" in blob

    def _direct_supabase_host(self) -> bool:
        h = self.postgres_host.strip()
        return h.startswith("db.") and h.endswith(".supabase.co") and not self.postgres_pooler_host.strip()

    def db_connect_args(self) -> dict:
        """Extra libpq/psycopg kwargs (IPv4 + SSL for cloud Postgres)."""
        import socket

        host = self._db_hostname()
        ssl = self.postgres_sslmode or ("require" if self._remote_postgres() else "")

        # Supavisor session pooler: never pass hostaddr (breaks tenant SNI). Hostname-only + SSL.
        if "pooler.supabase.com" in host:
            return {"sslmode": ssl or "require"}

        args: dict = {}
        if ssl:
            args["sslmode"] = ssl
        if self.postgres_prefer_ipv4 or self._remote_postgres():
            ipv4: str | None = None
            try:
                for info in socket.getaddrinfo(host, self.postgres_port, socket.AF_INET, socket.SOCK_STREAM):
                    ipv4 = info[4][0]
                    break
            except OSError:
                ipv4 = None
            if ipv4:
                args["hostaddr"] = ipv4
            elif self._direct_supabase_host():
                raise RuntimeError(
                    "POSTGRES_HOST is db.*.supabase.co (IPv6-only). Render cannot use it. "
                    "Supabase → Connect → Session pooler: set POSTGRES_HOST to the pooler "
                    "hostname and users postgres.<project-ref> and gautrack_app.<project-ref>."
                )
        return args

    def validate_supabase_pooler_env(self) -> None:
        """Fail fast on Render with a clear message (pooler usernames are easy to get wrong)."""
        host = self._db_hostname()
        if "pooler.supabase.com" not in host:
            return
        ref = "your-project-ref"
        for label, user in (
            ("POSTGRES_OWNER_USER", self.postgres_owner_user),
            ("POSTGRES_APP_USER", self.postgres_app_user),
        ):
            if "." not in user:
                raise RuntimeError(
                    f"{label} is '{user}' but Supabase pooler requires "
                    f"role.<project-ref> (e.g. postgres.{ref}). "
                    "Copy Session pooler settings from Supabase → Connect."
                )

    def _url(self, user: str, password: str) -> str:
        from urllib.parse import quote

        host = self._db_hostname()
        url = (
            f"postgresql+psycopg://{quote(user)}:{quote(password)}"
            f"@{host}:{self.postgres_port}/{self.postgres_db}"
        )
        ssl = self.postgres_sslmode or ("require" if self._remote_postgres() else "")
        if ssl:
            url += f"?sslmode={quote(ssl, safe='')}"
        return url

    @property
    def app_database_url(self) -> str:
        """URL for the low-privilege role the API runs as."""
        return self.database_url or self._url(self.postgres_app_user, self.app_db_password)

    @property
    def owner_database_url(self) -> str:
        """URL for the schema owner — migrations, seeding, chain verification."""
        return self.database_url_owner or self._url(self.postgres_owner_user, self.postgres_password)

    @property
    def is_demo(self) -> bool:
        return bool(self.seed_demo)


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
