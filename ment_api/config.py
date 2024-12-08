import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongodb_uri: str
    mongodb_db_name: str
    cloud_flare_url: str
    cloud_flare_key: str
    send_grid_key: str
    fal_key: str
    gcp_project_id: str
    api_url: str
    gcp_genai_key: str
    twilio_account_sid: str
    twilio_auth_token: str
    storage_bucket_name: str
    storage_video_verification_path: str
    storage_video_verification_transcoded_path: str
    storage_verification_example_media_path: str
    gcp_tasks_location: str
    gcp_tasks_verification_queue: str
    transcode_job_location: str
    video_verification_cache_size: float
    video_verification_cache_ttl: float
    pub_sub_transcoder_topic_id: str
    pub_sub_transcoder_subscription_id: str
    cloudflare_email: str
    cloudflare_api_key: str
    cloudflare_account_id: str
    cloudflare_api_token: str
    redis_host: str
    redis_port: int
    redis_password: str
    api_secret_key: str
    env: str

    model_config = SettingsConfigDict(
        env_file=("config/.env", f"config/{os.getenv('ENV', 'dev')}.env"), extra="allow"
    )


settings = Settings()
