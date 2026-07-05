import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from llm_config import LlmConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    monkeypatch.delenv('GROQ_MODEL', raising=False)


def test_from_env_reads_env_vars_directly(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'test-key')
    monkeypatch.setenv('GROQ_MODEL', 'llama-3.1-8b-instant')

    config = LlmConfig.from_env(env_path=Path('/nonexistent/.env'))

    assert config.api_key == 'test-key'
    assert config.model == 'llama-3.1-8b-instant'


def test_from_env_defaults_model_when_unset(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'test-key')

    config = LlmConfig.from_env(env_path=Path('/nonexistent/.env'))

    assert config.model == 'llama-3.3-70b-versatile'


def test_real_env_wins_over_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / '.env'
    env_file.write_text('GROQ_API_KEY=from-file\n')
    monkeypatch.setenv('GROQ_API_KEY', 'from-real-env')

    config = LlmConfig.from_env(env_path=env_file)

    assert config.api_key == 'from-real-env'


def test_validate_raises_when_api_key_missing():
    config = LlmConfig(api_key=None)

    with pytest.raises(ValueError):
        config.validate()


def test_validate_passes_when_api_key_present():
    config = LlmConfig(api_key='test-key')

    config.validate()


def test_from_env_uses_ollama_when_enabled(monkeypatch):
    monkeypatch.setenv('USE_OLLAMA', 'true')
    monkeypatch.setenv('OLLAMA_MODEL', 'qwen2.5:7b')
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')

    config = LlmConfig.from_env(env_path=Path('/nonexistent/.env'))

    assert config.provider == 'ollama'
    assert config.model == 'qwen2.5:7b'
    assert config.base_url == 'http://127.0.0.1:11434'


def test_validate_passes_for_ollama_without_api_key():
    config = LlmConfig(provider='ollama')

    config.validate()
