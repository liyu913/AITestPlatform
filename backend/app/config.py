from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env")

    app_name: str = "RainaAI测试平台"
    mysql_dsn: str = "mysql+pymysql://raina:raina@127.0.0.1:3306/raina"
    jwt_secret: str = "change-me-in-env"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_days: int = 14
    # 百炼文本向量（OpenAI 兼容 /embeddings）；与通义同平台，API Key 通常与 LLM 相同
    embedding_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v2"
    embedding_dim: int = 1536
    # 默认：阿里云 DashScope OpenAI 兼容模式 + Qwen-VL（密钥仍填 LLM_API_KEY）
    llm_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen-vl-plus"
    # Comma-separated model ids allowed for POST /ai/generate-testcases (empty = only llm_model)
    llm_allowed_models: str = ""
    # Midscene（Node @midscene/web）。未在系统 AI 配置启用档案时回退这些值。
    midscene_model_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    midscene_model_api_key: str = ""
    midscene_model_name: str = "qwen-vl-plus"
    midscene_model_family: str = "qwen2.5-vl"
    milvus_host: str = "127.0.0.1"
    milvus_port: int = 19530
    milvus_collection: str = "raina_chunks"
    celery_broker_url: str = "redis://127.0.0.1:6379/0"
    celery_result_backend: str = "redis://127.0.0.1:6379/1"
    upload_dir: str = "./data/uploads"
    chunk_window: int = 400
    chunk_overlap: int = 80
    require_login_captcha: bool = True
    captcha_ttl_seconds: int = 180
    allow_open_registration: bool = True
    # Fernet key (url-safe base64 32 bytes) for encrypting API keys in AI profiles / env settings.
    # Local default so secrets can be stored without extra setup; override in production.
    settings_encryption_key: str = "lOaibadCJM2dC3_Ne0xhlxlD9FbzVs_M8xOGboKlyP8="
    # Android adb executable (PATH name or absolute path) for device pool scan
    adb_bin: str = "adb"


settings = Settings()
