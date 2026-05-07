"""Tests for utils/agent_validators.py — Pydantic validation of AIAgent inputs."""

import json

import pytest

from utils.agent_validators import AgentSettingsInput, QualificationField


# ─────────────────────────────────────────────────────────────────────
# Range clamps
# ─────────────────────────────────────────────────────────────────────


class TestRangeClamps:
    def test_speed_within_range_kept(self):
        v = AgentSettingsInput(name="A", prompt="P", elevenlabs_speed=1.5)
        assert v.elevenlabs_speed == 1.5

    def test_speed_below_min_clamped(self):
        v = AgentSettingsInput(name="A", prompt="P", elevenlabs_speed=0.0)
        assert v.elevenlabs_speed == 0.25

    def test_speed_above_max_clamped(self):
        v = AgentSettingsInput(name="A", prompt="P", elevenlabs_speed=10.0)
        assert v.elevenlabs_speed == 4.0

    def test_speed_garbage_falls_to_default(self):
        v = AgentSettingsInput(name="A", prompt="P", elevenlabs_speed="not a number")
        assert v.elevenlabs_speed == 1.0

    def test_stability_negative_clamped(self):
        v = AgentSettingsInput(name="A", prompt="P", elevenlabs_stability=-1.0)
        assert v.elevenlabs_stability == 0.0

    def test_similarity_above_one_clamped(self):
        v = AgentSettingsInput(name="A", prompt="P", elevenlabs_similarity=2.5)
        assert v.elevenlabs_similarity == 1.0

    def test_debounce_below_min_clamped(self):
        v = AgentSettingsInput(name="A", prompt="P", debounce_seconds=0.0)
        assert v.debounce_seconds == 0.5

    def test_debounce_above_max_clamped(self):
        v = AgentSettingsInput(name="A", prompt="P", debounce_seconds=999.0)
        assert v.debounce_seconds == 30.0

    def test_debounce_default_when_invalid(self):
        v = AgentSettingsInput(name="A", prompt="P", debounce_seconds="abc")
        assert v.debounce_seconds == 1.5


# ─────────────────────────────────────────────────────────────────────
# Required fields
# ─────────────────────────────────────────────────────────────────────


class TestRequired:
    def test_name_required(self):
        with pytest.raises(Exception):
            AgentSettingsInput(prompt="P")

    def test_prompt_required(self):
        with pytest.raises(Exception):
            AgentSettingsInput(name="A")

    def test_empty_name_rejected(self):
        with pytest.raises(Exception):
            AgentSettingsInput(name="", prompt="P")

    def test_empty_prompt_rejected(self):
        with pytest.raises(Exception):
            AgentSettingsInput(name="A", prompt="")


# ─────────────────────────────────────────────────────────────────────
# qualification_fields parsing
# ─────────────────────────────────────────────────────────────────────


class TestQualificationFields:
    def test_none_passes_through(self):
        v = AgentSettingsInput(name="A", prompt="P", qualification_fields=None)
        assert v.qualification_fields is None

    def test_empty_string_becomes_none(self):
        v = AgentSettingsInput(name="A", prompt="P", qualification_fields="")
        assert v.qualification_fields is None

    def test_json_string_parsed(self):
        raw = json.dumps([{"label": "Nome", "key": "nome"}])
        v = AgentSettingsInput(name="A", prompt="P", qualification_fields=raw)
        assert len(v.qualification_fields) == 1
        assert v.qualification_fields[0].key == "nome"

    def test_list_passes_through(self):
        v = AgentSettingsInput(
            name="A",
            prompt="P",
            qualification_fields=[{"label": "Email", "key": "email"}],
        )
        assert len(v.qualification_fields) == 1
        assert v.qualification_fields[0].label == "Email"

    def test_malformed_json_returns_none(self):
        v = AgentSettingsInput(name="A", prompt="P", qualification_fields="{not json")
        assert v.qualification_fields is None

    def test_json_object_not_array_returns_none(self):
        v = AgentSettingsInput(
            name="A", prompt="P", qualification_fields='{"label": "x"}'
        )
        assert v.qualification_fields is None

    def test_field_with_quotes_in_key_rejected(self):
        with pytest.raises(Exception):
            QualificationField(label="X", key='nome"with"quote')

    def test_field_with_newline_in_key_rejected(self):
        with pytest.raises(Exception):
            QualificationField(label="X", key="nome\ncomquebra")

    def test_auto_field_default_false(self):
        f = QualificationField(label="Tag", key="tag")
        assert f.auto is False

    def test_auto_field_can_be_true(self):
        f = QualificationField(label="Tag", key="tag", auto=True)
        assert f.auto is True


# ─────────────────────────────────────────────────────────────────────
# Empty-string-to-none normalization
# ─────────────────────────────────────────────────────────────────────


class TestEmptyStringNormalization:
    def test_pipeline_id_empty_becomes_none(self):
        v = AgentSettingsInput(
            name="A", prompt="P", qualification_pipeline_id="   "
        )
        assert v.qualification_pipeline_id is None

    def test_stage_id_with_value_kept(self):
        v = AgentSettingsInput(
            name="A", prompt="P", qualification_stage_id="stage-abc"
        )
        assert v.qualification_stage_id == "stage-abc"

    def test_summary_prompt_empty_becomes_none(self):
        v = AgentSettingsInput(
            name="A", prompt="P", qualification_summary_prompt=""
        )
        assert v.qualification_summary_prompt is None


# ─────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────


class TestDefaults:
    def test_minimal_input_uses_defaults(self):
        v = AgentSettingsInput(name="A", prompt="P")
        assert v.model == "openai/gpt-4o"
        assert v.elevenlabs_speed == 1.0
        assert v.elevenlabs_stability == 0.5
        assert v.elevenlabs_similarity == 0.75
        assert v.debounce_seconds == 1.5
        assert v.is_active is False
        assert v.qualification_enabled is False
        assert v.channel == "whatsapp"
