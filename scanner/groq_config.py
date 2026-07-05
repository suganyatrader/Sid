from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Optional

import os


@dataclass(frozen=True)
class GroqConfig:
    api_key: Optional[str] = None
    model: str = 'llama-3.3-70b-versatile'

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> 'GroqConfig':
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

        return cls(
            api_key=os.getenv('GROQ_API_KEY'),
            model=os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile'),
        )

    def validate(self) -> None:
        if not self.api_key:
            raise ValueError('Groq credentials are missing. Set GROQ_API_KEY.')

    def get_client(self):
        self.validate()

        from groq import Groq

        return Groq(api_key=self.api_key)
