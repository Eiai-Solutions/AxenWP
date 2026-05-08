"""
Validadores Pydantic para os DTOs de input do AIAgent.

Centraliza ranges, defaults e parsing de qualification_fields que estavam
espalhados em admin/ai_agent.py com clamps inline (max(0.25, min(..., 4.0))).
"""

import json
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QualificationField(BaseModel):
    """Um campo de qualificação configurado pelo cliente."""

    label: str
    key: str
    ghl_field_id: Optional[str] = None
    auto: bool = False

    @field_validator("key")
    @classmethod
    def key_must_be_simple(cls, v: str) -> str:
        # Chave usada como índice no JSON [QUALIFIED_DATA] — não pode ter aspas/quebras
        if not v or any(c in v for c in ['"', "'", "\n", "\r"]):
            raise ValueError("key inválida (não pode ter aspas ou quebras de linha)")
        return v.strip()


class AgentSettingsInput(BaseModel):
    """
    Snapshot validado dos campos do formulário "Save Agent Settings".

    Aplica todos os clamps de range e parsing de JSON num único lugar.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1)
    model: str = Field(default="openai/gpt-4o", max_length=100)
    api_key: Optional[str] = None
    tts_provider: str = Field(default="elevenlabs", max_length=20)
    elevenlabs_api_key: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None
    elevenlabs_speed: float = Field(default=1.0, ge=0.25, le=4.0)
    elevenlabs_stability: float = Field(default=0.5, ge=0.0, le=1.0)
    elevenlabs_similarity: float = Field(default=0.75, ge=0.0, le=1.0)
    fishaudio_api_key: Optional[str] = None
    fishaudio_voice_id: Optional[str] = None
    fishaudio_model: str = Field(default="s1", max_length=20)
    fishaudio_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    groq_api_key: Optional[str] = None
    is_active: bool = False
    debounce_seconds: float = Field(default=1.5, ge=0.5, le=30.0)

    qualification_enabled: bool = False
    qualification_pipeline_id: Optional[str] = None
    qualification_stage_id: Optional[str] = None
    qualification_fields: Optional[List[QualificationField]] = None
    qualification_summary_prompt: Optional[str] = None

    channel: str = Field(default="whatsapp", min_length=1, max_length=30)

    # ── Coercion: aceita float fora do range em vez de erro, faz clamp ──
    # FastAPI Form sempre passa esses como float, mas se vier algo extremo
    # preferimos clamp silencioso (UX) em vez de 422.
    @field_validator("elevenlabs_speed", mode="before")
    @classmethod
    def clamp_speed(cls, v):
        try:
            return max(0.25, min(float(v), 4.0))
        except (TypeError, ValueError):
            return 1.0

    @field_validator("elevenlabs_stability", "elevenlabs_similarity", mode="before")
    @classmethod
    def clamp_unit_interval(cls, v):
        try:
            return max(0.0, min(float(v), 1.0))
        except (TypeError, ValueError):
            return 0.5

    @field_validator("fishaudio_speed", mode="before")
    @classmethod
    def clamp_fish_speed(cls, v):
        # Fish Audio aceita 0.5–2.0 no prosody.speed
        try:
            return max(0.5, min(float(v), 2.0))
        except (TypeError, ValueError):
            return 1.0

    @field_validator("tts_provider", mode="before")
    @classmethod
    def normalize_tts_provider(cls, v):
        if not v:
            return "elevenlabs"
        v = str(v).strip().lower()
        return v if v in ("elevenlabs", "fishaudio") else "elevenlabs"

    @field_validator("fishaudio_model", mode="before")
    @classmethod
    def normalize_fish_model(cls, v):
        if not v:
            return "s1"
        v = str(v).strip().lower()
        return v if v in ("s1", "s2", "s2-pro", "speech-1.6") else "s1"

    @field_validator("debounce_seconds", mode="before")
    @classmethod
    def clamp_debounce(cls, v):
        try:
            return max(0.5, min(float(v), 30.0))
        except (TypeError, ValueError):
            return 1.5

    @field_validator("qualification_fields", mode="before")
    @classmethod
    def parse_qualification_fields(cls, v):
        """Aceita tanto lista (já parseada) quanto JSON-string (vinda do form)."""
        if v is None or v == "":
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else None
            except (json.JSONDecodeError, ValueError):
                return None
        return None

    @field_validator(
        "qualification_pipeline_id",
        "qualification_stage_id",
        "qualification_summary_prompt",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, v):
        """Form com campo vazio chega como '' — normaliza para None."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v
