from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Any, Optional

import os

import requests


class _OllamaChatCompletions:
    def __init__(self, client: 'OllamaClient') -> None:
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        return self._client.create(**kwargs)


class OllamaClient:
    def __init__(self, model: str, base_url: str) -> None:
        self.model = model
        self.base_url = base_url.rstrip('/')
        self.chat = type('Chat', (), {'completions': _OllamaChatCompletions(self)})()

    def create(self, **kwargs: Any) -> Any:
        payload = {
            'model': kwargs.get('model', self.model),
            'messages': kwargs.get('messages', []),
            'stream': False,
            'options': {'temperature': kwargs.get('temperature', 0)},
        }
        if 'max_tokens' in kwargs:
            payload['options']['num_predict'] = kwargs['max_tokens']
        if 'response_format' in kwargs:
            payload['format'] = 'json'

        response = requests.post(
            f'{self.base_url}/api/chat',
            json=payload,
            timeout=180,
        )
        response.raise_for_status()
        data = response.json()

        class _ChoiceMessage:
            def __init__(self, content: str) -> None:
                self.content = content

        class _Choice:
            def __init__(self, content: str) -> None:
                self.message = _ChoiceMessage(content)

        class _Response:
            def __init__(self, content: str) -> None:
                self.choices = [_Choice(content)]

        return _Response(data.get('message', {}).get('content', ''))


@dataclass(frozen=True)
class LlmConfig:
    api_key: Optional[str] = None
    model: str = 'llama-3.3-70b-versatile'
    provider: str = 'groq'
    base_url: Optional[str] = None

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> 'LlmConfig':
        if env_path is None:
            env_path = Path(__file__).resolve().parent / '.env'

        def load_manual_env(path: Path) -> None:
            if not path.exists():
                return
            with path.open('r', encoding='utf-8') as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' not in line:
                        continue
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value

        try:
            dotenv = importlib.import_module('dotenv')
            load_dotenv = getattr(dotenv, 'load_dotenv')
            if env_path.exists():
                load_dotenv(env_path)
            else:
                load_dotenv()
        except (ImportError, ModuleNotFoundError):
            load_manual_env(env_path)

        provider = os.getenv('LLM_PROVIDER', 'groq').strip().lower()
        if os.getenv('USE_OLLAMA', '').strip().lower() in {'1', 'true', 'yes', 'on'}:
            provider = 'ollama'

        model = os.getenv('GROQ_MODEL') or os.getenv('OLLAMA_MODEL') or ('qwen3.6' if provider == 'ollama' else 'llama-3.3-70b-versatile')
        base_url = os.getenv('OLLAMA_BASE_URL') or 'http://127.0.0.1:11434'

        return cls(
            api_key=os.getenv('GROQ_API_KEY'),
            model=model,
            provider=provider,
            base_url=base_url,
        )

    def validate(self) -> None:
        if self.provider == 'ollama':
            return
        if not self.api_key:
            raise ValueError('Groq credentials are missing. Set GROQ_API_KEY.')

    def get_client(self):
        if self.provider == 'ollama':
            return OllamaClient(model=self.model, base_url=self.base_url or 'http://127.0.0.1:11434')

        self.validate()

        from groq import Groq

        return Groq(api_key=self.api_key)
