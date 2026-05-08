"""
Handler de áudio: STT (Groq Whisper) + TTS (ElevenLabs/Fish Audio) + heurística
de quando NÃO converter para fala (conteúdo especial: URLs, valores, telefones).

Dois provedores TTS suportados — selecionados por agent.tts_provider:
- 'elevenlabs' (default)
- 'fishaudio'
"""

import asyncio
import base64
import os
import re
import tempfile
from typing import Optional

import httpx

from utils.logger import logger
from services.usage_logger import save_usage_log


# Padrões que ficariam ruins em TTS — fallback para texto quando aparecem
_SPECIAL_CONTENT_PATTERNS = [
    r"https?://",                                                # URLs
    r"www\.",                                                    # Links www
    r"\.[a-z]{2,3}\.br\b",                                       # Domínios .com.br
    r"\b\w+\.(com|net|org|io|app)\b",                            # Domínios genéricos
    r"@",                                                        # Emails
    r"R\$\s*[\d.,]+",                                            # Valores em reais
    r"\d{1,3}(?:\.\d{3})+,\d{2}",                                # Formato BR: 1.500,00
    r"\d{5}[\-]?\d{3}",                                          # CEP
    r"\d{2}[\.\-]?\d{3}[\.\-]?\d{3}[\/\-]?\d{4}[\-]?\d{2}",      # CNPJ
    r"\d{3}[\.\-]?\d{3}[\.\-]?\d{3}[\-]?\d{2}",                  # CPF
    r"\(?\d{2}\)?\s*\d{4,5}[\-\s]?\d{4}",                        # Telefone
    r"\b(?:Rua|Av\.|Avenida|Alameda|Travessa|Praça|Rodovia|Estrada|R\.)\s",  # Endereços
]
_SPECIAL_CONTENT_RE = re.compile("|".join(_SPECIAL_CONTENT_PATTERNS), re.IGNORECASE)


def contains_special_content(text: str) -> bool:
    """True se o texto tem algum padrão que ficaria ruim em TTS."""
    return bool(_SPECIAL_CONTENT_RE.search(text or ""))


# ─────────────────────────────────────────────────────────────────────
# STT — Groq Whisper
# ─────────────────────────────────────────────────────────────────────

async def transcribe_audio(audio_url: str, groq_api_key: str) -> Optional[str]:
    """Baixa o áudio da URL e transcreve via Groq Whisper. Retorna None em erro."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            audio_resp = await client.get(audio_url)
            if audio_resp.status_code != 200:
                logger.error(f"Erro ao baixar áudio: status={audio_resp.status_code}")
                return None
            audio_bytes = audio_resp.content

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(tmp_path, "rb") as f:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {groq_api_key}"},
                        data={
                            "model": "whisper-large-v3",
                            "language": "pt",
                            "response_format": "text",
                        },
                        files={"file": ("audio.ogg", f, "audio/ogg")},
                    )

                if resp.status_code == 200:
                    transcription = resp.text.strip()
                    logger.info(
                        f"Áudio transcrito ({len(transcription)} chars): {transcription[:80]}..."
                    )
                    return transcription
                logger.error(
                    f"Groq Whisper falhou: status={resp.status_code} body={resp.text[:200]}"
                )
                return None
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Exceção ao transcrever áudio: {e}")
        return None


def resolve_groq_key(agent_groq_key: Optional[str]) -> Optional[str]:
    """
    Resolve a chave Groq para uso: agent.groq_api_key se houver,
    senão a chave global do SystemSettings.
    """
    if agent_groq_key:
        return agent_groq_key
    try:
        from data.database import SessionLocal
        from data.models import SystemSettings
        db = SessionLocal()
        try:
            ss = db.query(SystemSettings).first()
            if ss and ss.admin_groq_api_key:
                return ss.admin_groq_api_key
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Erro ao ler Groq key global: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────
# TTS — ElevenLabs
# ─────────────────────────────────────────────────────────────────────

async def synthesize_speech(
    text: str,
    api_key: str,
    voice_id: str,
    speed: float = 1.0,
    stability: float = 0.5,
    similarity: float = 0.75,
    location_id: Optional[str] = None,
) -> Optional[str]:
    """
    Gera áudio via ElevenLabs e retorna data URL (data:audio/ogg;base64,...).
    None em erro. Registra uso na tabela usage_logs se location_id for passado.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=opus_48000_128",
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": max(0.0, min(stability, 1.0)),
                        "similarity_boost": max(0.0, min(similarity, 1.0)),
                        "speed": max(0.25, min(speed, 4.0)),
                    },
                },
            )

        if resp.status_code != 200:
            logger.error(f"ElevenLabs erro: {resp.status_code} {resp.text[:200]}")
            return None

        b64 = base64.b64encode(resp.content).decode("utf-8")
        if location_id:
            try:
                await asyncio.to_thread(
                    save_usage_log,
                    location_id=location_id,
                    service="elevenlabs",
                    characters=len(text),
                )
            except Exception as e_log:
                logger.warning(f"Falha usage log ElevenLabs: {e_log}")
        return f"data:audio/ogg;base64,{b64}"
    except Exception as e:
        logger.error(f"Exceção em ElevenLabs: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────
# TTS — Fish Audio
# ─────────────────────────────────────────────────────────────────────

async def synthesize_speech_fishaudio(
    text: str,
    api_key: str,
    voice_id: str,
    model: str = "s1",
    speed: float = 1.0,
    location_id: Optional[str] = None,
) -> Optional[str]:
    """
    Gera áudio via Fish Audio e retorna data URL (data:audio/ogg;base64,...).
    None em erro. Registra uso na tabela usage_logs se location_id for passado.

    Fish Audio API: POST https://api.fish.audio/v1/tts
    Header `model: s1|s2-pro` define a engine.
    """
    try:
        clamped_speed = max(0.5, min(float(speed or 1.0), 2.0))
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.fish.audio/v1/tts",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "model": model or "s1",
                },
                json={
                    "text": text,
                    "reference_id": voice_id,
                    "format": "opus",
                    "prosody": {"speed": clamped_speed},
                },
            )

        if resp.status_code != 200:
            logger.error(f"Fish Audio erro: {resp.status_code} {resp.text[:200]}")
            return None

        b64 = base64.b64encode(resp.content).decode("utf-8")
        if location_id:
            try:
                await asyncio.to_thread(
                    save_usage_log,
                    location_id=location_id,
                    service="fishaudio",
                    characters=len(text),
                )
            except Exception as e_log:
                logger.warning(f"Falha usage log Fish Audio: {e_log}")
        return f"data:audio/ogg;base64,{b64}"
    except Exception as e:
        logger.error(f"Exceção em Fish Audio: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────
# Dispatcher — escolhe provider via agent_config
# ─────────────────────────────────────────────────────────────────────

async def synthesize_for_agent(text: str, agent_config) -> Optional[str]:
    """
    Roteia para o provider TTS configurado no agente.
    Retorna data URL do áudio ou None se desligado/falha.
    """
    provider = (getattr(agent_config, "tts_provider", "elevenlabs") or "elevenlabs").lower()
    location_id = getattr(agent_config, "location_id", None)

    if provider == "fishaudio":
        api_key = getattr(agent_config, "fishaudio_api_key", None)
        voice_id = getattr(agent_config, "fishaudio_voice_id", None)
        if not api_key or not voice_id:
            return None
        return await synthesize_speech_fishaudio(
            text=text,
            api_key=api_key,
            voice_id=voice_id,
            model=getattr(agent_config, "fishaudio_model", "s1") or "s1",
            speed=float(getattr(agent_config, "fishaudio_speed", 1.0) or 1.0),
            location_id=location_id,
        )

    # default → elevenlabs
    api_key = getattr(agent_config, "elevenlabs_api_key", None)
    voice_id = getattr(agent_config, "elevenlabs_voice_id", None)
    if not api_key or not voice_id:
        return None
    return await synthesize_speech(
        text=text,
        api_key=api_key,
        voice_id=voice_id,
        speed=float(getattr(agent_config, "elevenlabs_speed", 1.0) or 1.0),
        stability=float(getattr(agent_config, "elevenlabs_stability", 0.5) or 0.5),
        similarity=float(getattr(agent_config, "elevenlabs_similarity", 0.75) or 0.75),
        location_id=location_id,
    )
