import os

from dotenv import load_dotenv
from pydantic_settings import (
    BaseSettings,
    GoogleSecretManagerSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

env_name = os.getenv("ENV", "dev")
load_dotenv("config/.env", override=True)
load_dotenv(f"config/.env.{env_name}", override=True)


class Settings(BaseSettings):
    mongodb_uri: str
    mongodb_db_name: str
    fal_key: str
    gcp_project_id: str
    api_url: str
    twilio_account_sid: str
    twilio_auth_token: str
    storage_bucket_name: str
    storage_video_verification_path: str
    storage_video_verification_transcoded_path: str
    gcp_tasks_location: str
    gcp_tasks_verification_queue: str
    gcp_tasks_notification_queue: str
    transcode_job_location: str
    video_verification_cache_size: float
    video_verification_cache_ttl: float
    pub_sub_transcoder_topic_id: str
    pub_sub_transcoder_subscription_id: str
    pub_sub_news_topic_id: str
    pub_sub_news_subscription_id: str
    pub_sub_check_fact_topic_id: str
    pub_sub_check_fact_subscription_id: str
    pub_sub_social_media_scrape_topic_id: str
    pub_sub_social_media_scrape_subscription_id: str
    redis_host: str
    redis_port: int
    redis_password: str
    api_secret_key: str
    livekit_api_key: str
    livekit_api_secret: str
    env: str
    gcp_genai_key: str
    livekit_url: str
    supabase_jwt_secret: str
    jina_api_key: str
    jina_base_url: str
    jina_token_limit: int = 400000
    mock_gcp_tasks_endpoint: str = ""
    mock_gcp_transcoder_endpoint: str = ""
    scrape_do_token: str
    scrape_do_base_url: str
    pub_sub_video_processor_topic_id: str
    pub_sub_video_processor_subscription_id: str
    pub_sub_translation_topic_id: str
    pub_sub_translation_subscription_id: str
    pub_sub_media_post_generator_topic_id: str
    pub_sub_media_post_generator_subscription_id: str
    scrapable_imedi_news_endpiont: str
    scrapable_publika_news_endpiont: str
    scrapable_1tv_news_endpiont: str
    scrapable_interpress_news_endpiont: str
    scrapable_netgazeti_news_endpoint: str
    scrapable_civil_news_endpoint: str
    google_maps_api_key: str
    k_revision: str = "1.0.0"
    wal_url: str

    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_tracing_environment: str

    model_config = SettingsConfigDict(
        extra="allow",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        project_id = os.getenv("GCP_PROJECT_ID")
        gcp_settings = GoogleSecretManagerSettingsSource(
            settings_cls,
            project_id=project_id,
        )
        # Priority order: init -> env -> dotenv -> gcp_secrets -> file_secrets
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            gcp_settings,
            file_secret_settings,
        )


settings = Settings()


os.environ["LANGFUSE_RELEASE"] = settings.k_revision
