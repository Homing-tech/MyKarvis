"""配置加载：从 .env 读取，构建三个助手的企微参数。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """极简 .env 加载，避免额外依赖。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class AgentConf:
    key: str                 # 内部标识：life / fitness / mood
    display_name: str        # 企微应用显示名
    agent_id: str
    secret: str
    token: str
    aes_key: str

    @property
    def ready(self) -> bool:
        return bool(self.agent_id and self.secret and self.token and self.aes_key)


@dataclass
class Settings:
    corp_id: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    host: str
    port: int
    data_dir: Path
    timezone: str
    owner_user_id: str
    enable_scheduler: bool
    log_level: str
    agents: dict[str, AgentConf] = field(default_factory=dict)


_AGENT_META = {
    "life": "阿龙管家",
    "fitness": "埼玉教练",
    "mood": "阿尼亚督导",
}


def load_settings() -> Settings:
    data_dir = Path(env("KARVIS_DATA_DIR", str(BASE_DIR / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)

    agents: dict[str, AgentConf] = {}
    for key, display in _AGENT_META.items():
        prefix = f"WECOM_{key.upper()}_"
        agents[key] = AgentConf(
            key=key,
            display_name=display,
            agent_id=env(prefix + "AGENT_ID"),
            secret=env(prefix + "SECRET"),
            token=env(prefix + "TOKEN"),
            aes_key=env(prefix + "AESKEY"),
        )

    return Settings(
        corp_id=env("WECOM_CORP_ID"),
        deepseek_api_key=env("DEEPSEEK_API_KEY"),
        deepseek_base_url=env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=env("DEEPSEEK_MODEL", "deepseek-chat"),
        host=env("KARVIS_HOST", "0.0.0.0"),
        port=int(env("KARVIS_PORT", "9000")),
        data_dir=data_dir,
        timezone=env("KARVIS_TIMEZONE", "Asia/Shanghai"),
        owner_user_id=env("KARVIS_OWNER_USERID"),
        enable_scheduler=env("ENABLE_SCHEDULER", "0") == "1",
        log_level=env("LOG_LEVEL", "INFO"),
        agents=agents,
    )


settings = load_settings()
