from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_client_id:        str = ""
    google_client_secret:    str = ""
    secret_key:              str = "change-me"
    frontend_url:            str = "http://localhost:5173"
    provisioning_server_url: str = ""  # e.g. http://192.168.1.10:8080; empty = skip provisioning
    fqdn:                    str = ""
    tunnel_token:            str = ""
    tunnel_id:               str = ""

    class Config:
        env_file = ".env"
        extra   = "ignore"


settings = Settings()
