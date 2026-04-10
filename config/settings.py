from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Asterisk ARI
    ari_url: str = "http://127.0.0.1:8088"
    ari_username: str = "medbot"
    ari_password: str = "Medbot$ARI2024!"
    ari_app_name: str = "medbot"

    # RTP for ExternalMedia
    external_media_host: str = "127.0.0.1"
    rtp_port_start: int = 30000
    rtp_port_end: int = 30100

    # LLM (OpenRouter)
    llm_api_key: str = ""
    llm_model: str = "google/gemini-2.0-flash-001"
    llm_base_url: str = "https://openrouter.ai/api/v1"

    # ElevenLabs TTS
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_flash_v2_5"

    # STT (Groq Whisper — free, fast, great Russian)
    stt_api_key: str = ""
    stt_api_url: str = "https://api.groq.com/openai/v1/audio/transcriptions"
    stt_model: str = "whisper-large-v3"

    # Medesk
    medesk_api_key: str = ""
    medesk_base_url: str = "https://api.medesk.md/api/v2"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}
