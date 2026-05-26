from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    cloudflare_api_token:       str
    cloudflare_account_id:      str
    cloudflare_zone_id:         str
    base_domain:                str = "mccollumtechnology.com"
    google_cloud_project_id:    str
    google_oauth_client_id:     str
    google_service_account_json: str  # inline JSON string or path to SA file
    admin_api_key:              str
    database_url:               str = "sqlite:///./server.db"
    fernet_key:                 str  # generate with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    sync_key:                   str = ""  # shared secret for peer sync endpoints; defaults to admin_api_key if empty
    sync_interval_seconds:      int = 300  # how often the background reconciliation loop runs

    class Config:
        env_file = ".env"
        extra   = "ignore"


settings = Settings()
