"""Day 4, task 0: prove the LLM client works with one tiny real call.

Run from the repo root:
    python -m scripts.smoke_llm
"""

from pydantic import BaseModel
from rich import print

from agentforge_core.llm import LLMConfigError, get_settings, structured_call


class Ping(BaseModel):
    reply: str


def main() -> None:
    try:
        s = get_settings()
    except LLMConfigError as e:
        print(f"[red]Config problem:[/red] {e}")
        raise SystemExit(1)

    print(f"Profile: [bold]{s.profile}[/bold]  model: {s.model}  temp: {s.temperature}")

    ping, record = structured_call(
        messages=[{"role": "user", "content": 'Return JSON: {"reply": "ok"}'}],
        response_model=Ping,
    )

    print(f"[green]Model replied:[/green] {ping.reply!r}")
    print(record.as_dict())


if __name__ == "__main__":
    main()