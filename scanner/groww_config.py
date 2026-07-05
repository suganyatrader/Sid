from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Optional

import os


@dataclass(frozen=True)
class GrowwConfig:
    access_token: Optional[str] = None

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
        )
