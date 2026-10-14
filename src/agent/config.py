from dataclasses import dataclass, field
from pathlib import Path
import tomllib

GUARDRAIL_PATHS: tuple[str, ...] = (
    "src/agent/executor.py",
    "src/agent/approval_gate.py",
    "src/agent/telegram.py",
)

@dataclass
class Config:
    ollama_base_url: str
    default_model: str
    workdir: Path
    always_ask: list[str]
    max_steps: int
    command_timeout: int
    bind: str
    telegram_bot_token: str | None = None
    telegram_allowed_chat_ids: list[int] = field(default_factory=list)
    # context budget: the model's window (Ollama num_ctx), room kept for thinking + the answer,
    # and how much of a request the recent chat turns may take
    context_window: int = 32768
    reply_reserve: int = 6144
    history_tokens: int = 8000
    embed_model: str = "nomic-embed-text"
    cross_chat_recall: bool = True       # bring relevant turns from the user's other chats
    # a separate chat backend (vLLM) for one model; blank = all chat on Ollama
    chat_base_url: str = ""
    chat_model: str = ""
    chat_temperature: float | None = None
    chat_repetition_penalty: float | None = None
    chat_top_p: float | None = None
    chat_vision: bool = True             # the chat backend model can see images

def load_config(path: Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    data = tomllib.loads(path.read_text())
    return Config(
        ollama_base_url=str(data["ollama_base_url"]),
        default_model=str(data["default_model"]),
        workdir=Path(str(data["workdir"])).expanduser(),
        always_ask=list(data.get("always_ask", [])),
        max_steps=int(data.get("max_steps", 40)),
        command_timeout=int(data.get("command_timeout", 120)),
        bind=str(data.get("bind", "127.0.0.1:8700")),
        telegram_bot_token=(str(data["telegram_bot_token"]) or None)
            if data.get("telegram_bot_token") else None,
        telegram_allowed_chat_ids=[int(x) for x in data.get("telegram_allowed_chat_ids", [])],
        context_window=int(data.get("context_window", 32768)),
        reply_reserve=int(data.get("reply_reserve", 6144)),
        history_tokens=int(data.get("history_tokens", 8000)),
        embed_model=str(data.get("embed_model", "nomic-embed-text")),
        cross_chat_recall=bool(data.get("cross_chat_recall", True)),
        chat_base_url=str(data.get("chat_base_url", "")),
        chat_model=str(data.get("chat_model", "")),
        chat_temperature=(float(data["chat_temperature"]) if data.get("chat_temperature") is not None else None),
        chat_repetition_penalty=(float(data["chat_repetition_penalty"]) if data.get("chat_repetition_penalty") is not None else None),
        chat_top_p=(float(data["chat_top_p"]) if data.get("chat_top_p") is not None else None),
        chat_vision=bool(data.get("chat_vision", True)),
    )
