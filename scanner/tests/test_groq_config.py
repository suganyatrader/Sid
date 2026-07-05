import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from groq_config import GroqConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    monkeypatch.delenv('GROQ_MODEL', raising=False)


def test_from_env_reads_env_vars_directly(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'test-key')
    monkeypatch.setenv('GROQ_MODEL', 'llama-3.1-8b-instant')

    config = GroqConfig.from_env(env_path=Path('/nonexistent/.env'))

    assert config.api_key == 'test-key'
    assert config.model == 'llama-3.1-8b-instant'


def test_from_env_defaults_model_when_unset(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'test-key')

    config = GroqConfig.from_env(env_path=Path('/nonexistent/.env'))

    assert config.model == 'llama-3.3-70b-versatile'


def test_real_env_wins_over_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / '.env'
    env_file.write_text('GROQ_API_KEY=from-file\n')
    monkeypatch.setenv('GROQ_API_KEY', 'from-real-env')

    config = GroqConfig.from_env(env_path=env_file)

    assert config.api_key == 'from-real-env'


def test_validate_raises_when_api_key_missing():
    config = GroqConfig(api_key=None)

    with pytest.raises(ValueError):
        config.validate()


def test_validate_passes_when_api_key_present():
    config = GroqConfig(api_key='test-key')

    config.validate()
