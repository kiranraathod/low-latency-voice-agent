"""
app/config.py — Centralised configuration via pydantic-settings.

All values are read from environment variables (or a .env file).
Fail-fast: missing required API keys raise a clear error at startup.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-level configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── API Keys ──────────────────────────────────────────────────────────────
    deepgram_api_key: str = Field(..., description="Deepgram API key")
    openai_api_key: str = Field(..., description="OpenAI API key")
    openai_base_url: str | None = Field(default=None, description="Optional Base URL")
    elevenlabs_api_key: str = Field(..., description="ElevenLabs API key")

    # ── ElevenLabs ────────────────────────────────────────────────────────────
    # Rachel — low-latency, natural voice
    elevenlabs_voice_id: str = Field(
        default="21m00Tcm4TlvDq8ikWAM",
        description="ElevenLabs voice ID",
    )
    elevenlabs_model_id: str = Field(
        default="eleven_turbo_v2_5",
        description="ElevenLabs model (turbo for lowest latency)",
    )

    # ── Deepgram ─────────────────────────────────────────────────────────────
    deepgram_model: str = Field(default="nova-2", description="Deepgram model")
    deepgram_language: str = Field(default="en-US")
    deepgram_endpointing_ms: int = Field(
        default=300,
        description="Silence (ms) before Deepgram finalises transcript",
    )
    deepgram_tts_model: str = Field(
        default="aura-asteria-en", 
        description="Deepgram Aura TTS voice model"
    )

    # ── OpenAI ───────────────────────────────────────────────────────────────
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="OpenAI model to use",
    )
    openai_max_history_turns: int = Field(
        default=10,
        description="Max conversation turns kept in session memory",
    )
    openai_timeout_s: float = Field(
        default=8.0,
        description="LLM response timeout in seconds",
    )

    # ── Server ───────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    # ── Latency / Queue Tuning ────────────────────────────────────────────────
    turn_timeout_s: float = Field(
        default=30.0,
        description="Max seconds to wait for a complete turn",
    )
    stt_queue_maxsize: int = Field(default=100)
    llm_queue_maxsize: int = Field(default=50)
    tts_queue_maxsize: int = Field(default=200)

    # ── Cost Rates (USD) ──────────────────────────────────────────────────────
    # Based on published pricing as of Q1 2025
    deepgram_cost_per_minute: float = Field(default=0.0043)
    deepgram_tts_cost_per_1k_chars: float = Field(default=0.015)
    openai_cost_per_1m_input_tokens: float = Field(default=0.15)
    openai_cost_per_1m_output_tokens: float = Field(default=0.60)
    elevenlabs_cost_per_1k_chars: float = Field(default=0.30)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return (and cache) the global settings instance.

    Using lru_cache means we only parse the environment once.
    """
    return Settings()  # type: ignore[call-arg]
