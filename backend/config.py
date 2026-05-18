from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_client_id:     str = ""
    google_client_secret: str = ""
    secret_key:           str = "change-me"
    frontend_url:         str = "http://localhost:5173"

    class Config:
        env_file = ".env"
        extra   = "ignore"


settings = Settings()
