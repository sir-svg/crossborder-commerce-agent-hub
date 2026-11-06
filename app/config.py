from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_path: str = "data/commerce.db"
    memory_backend: str = "sqlite"
    redis_url: str = "redis://localhost:6379/0"
    knowledge_backend: str = "local"
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    supplier_url: str = ""
    logistics_url: str = ""
    mcp_url: str = ""
    llm_base_url: str = (
        "https://ws-wx3zg6zl1sf52rk4.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "deepseek-v4.1-flash-1017"
    llm_timeout_seconds: float = 40
    workflow_timeout_seconds: int = 240
    public_demo: bool = False
    demo_username: str = "demo"
    demo_password: SecretStr = SecretStr("")
    demo_temporary_data: bool = False
