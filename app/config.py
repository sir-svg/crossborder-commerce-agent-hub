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
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
