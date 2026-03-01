"""运行配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """应用设置对象（只读）。"""

    # 注意：必须用 default_factory 在“实例化时”读环境变量，
    # 不能在模块 import 时读，否则 load_dotenv() 后的值不会生效。
    model_provider: str = field(default_factory=lambda: os.getenv("MODEL_PROVIDER", "openai"))
    model_name: str = field(default_factory=lambda: os.getenv("MODEL_NAME", "gpt-4o-mini"))
    temperature: float = field(default_factory=lambda: float(os.getenv("MODEL_TEMPERATURE", "0")))
    model_base_url: str = field(default_factory=lambda: os.getenv("MODEL_BASE_URL", ""))
    model_api_key: str = field(default_factory=lambda: os.getenv("MODEL_API_KEY", ""))

    project_root: Path = field(default_factory=lambda: Path(os.getenv("PROJECT_ROOT", ".")).resolve())
    sessions_dir: Path = field(default_factory=lambda: Path(os.getenv("SESSIONS_DIR", ".sessions")).resolve())
    memory_dir: Path = field(default_factory=lambda: Path(os.getenv("MEMORY_DIR", ".memory")).resolve())

    max_context_chars: int = field(default_factory=lambda: int(os.getenv("MAX_CONTEXT_CHARS", "50000")))
    context_window_tokens: int = field(default_factory=lambda: int(os.getenv("CONTEXT_WINDOW_TOKENS", "24000")))
    heartbeat_minutes: int = field(default_factory=lambda: int(os.getenv("HEARTBEAT_MINUTES", "30")))
    max_concurrent_runs: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_RUNS", "4")))
    max_subagent_depth: int = field(default_factory=lambda: int(os.getenv("MAX_SUBAGENT_DEPTH", "1")))

    def ensure_dirs(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
