from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    app_name: str = "Mobgi Dashboard"
    fastapi_app_key: str = ""
    secret_key: str = ""
    database_url: str = "sqlite:///./mobgi_dashboard.db"
    mobgi_email: str = "775355788@qq.com"
    mobgi_password: str = ""
    cors_origins: list[str] = ["*"]

    class Config:
        env_file = BASE_DIR / ".env"
        case_sensitive = False

settings = Settings()
