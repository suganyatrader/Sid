from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Optional

import os


@dataclass(frozen=True)
class GrowwConfig:
    access_token: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> 'GrowwConfig':
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
            access_token=os.getenv('GROWW_ACCESS_TOKEN'),
            api_key=os.getenv('GROWW_API_KEY'),
            api_secret=os.getenv('GROWW_API_SECRET'),
        )

    def validate(self) -> None:
        if self.access_token:
            return
        if self.api_key and self.api_secret:
            return
        raise ValueError(
            'Groww credentials are missing. Set GROWW_ACCESS_TOKEN or both GROWW_API_KEY and GROWW_API_SECRET.'
        )

    def get_access_token(self) -> str:
        self.validate()
        if self.access_token:
            return self.access_token

        from growwapi import GrowwAPI

        return GrowwAPI.get_access_token(api_key=self.api_key, secret=self.api_secret)
