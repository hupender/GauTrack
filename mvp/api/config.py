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

    def _remote_postgres(self) -> bool:
        h = self.postgres_host
        return "supabase.co" in h or "pooler.supabase.com" in h

    def db_connect_args(self) -> dict:
        """Extra libpq/psycopg kwargs (IPv4 + SSL for cloud Postgres)."""
        import socket

        args: dict = {}
        ssl = self.postgres_sslmode or ("require" if self._remote_postgres() else "")
        if ssl:
            args["sslmode"] = ssl
        if self.postgres_prefer_ipv4 or self._remote_postgres():
            try:
                for info in socket.getaddrinfo(
                    self.postgres_host,
                    self.postgres_port,
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                ):
                    args["hostaddr"] = info[4][0]
                    break
            except OSError:
                pass
        return args

    def _url(self, user: str, password: str) -> str:
        from urllib.parse import quote

        url = (
            f"postgresql+psycopg://{quote(user)}:{quote(password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
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
