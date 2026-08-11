"""Terminal UI — Rich-based markdown rendering and styled output."""
import sys
from contextlib import contextmanager

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from rich.table import Table
from rich import box
from rich.status import Status

console = Console(highlight=True)


@contextmanager
def spinner(label: str = "Thinking..."):
    """Show an animated spinner while waiting for an AI response."""
    with console.status(f"[cyan]{label}[/]", spinner="dots"):
        yield


def render_markdown(content: str) -> None:
    """Render an LLM markdown response with Rich formatting."""
    md = Markdown(content, code_theme="monokai")
    console.print(md)


def render_tool_use(tool_name: str, tool_input: str) -> None:
    """Log a tool invocation compactly."""
    label = Text(tool_name, style="bold yellow")
    detail = Text(tool_input, style="dim")
    console.print(Panel(detail, title=label, border_style="yellow", padding=(0, 1)))


def render_error(message: str) -> None:
    """Render an error message."""
    console.print(f"[bold red]✗[/] {message}")


def render_info(message: str) -> None:
    """Render an informational message."""
    console.print(f"[dim]  {message}[/]")


def render_banner(model: str, workdir: str) -> None:
    """Render the startup banner."""
    console.print(Rule("Coding Agent", style="cyan"))
    console.print(f"[bold cyan]  Model:[/] {model}  [dim]|[/]  [bold cyan]Workdir:[/] {workdir}")
    console.print("  [dim]29 tools (incl. MCP)  ·  modules: config / tools / harness / loop[/]")
    console.print(Rule(style="cyan"))


def render_help() -> None:
    """Render a friendly ASCII rabbit."""
    out = Text()
    out.append("\n")
    out.append("      (", style="yellow")
    out.append("\\", style="yellow")
    out.append("(", style="yellow")
    out.append("/\n", style="yellow")
    out.append("      ( -.-)\n", style="yellow")
    out.append('      o_(")(")\n', style="yellow")
    out.append("       ╰─ coding agent", style="dim")
    console.print(out)


def render_inbox(messages: list[dict]) -> None:
    """Render inbox messages."""
    table = Table(box=box.SIMPLE, border_style="magenta", show_header=True, padding=(0, 1))
    table.add_column("From", style="bold magenta")
    table.add_column("Type", style="dim")
    table.add_column("Content")
    for m in messages:
        table.add_row(m.get("from", "?"), m.get("type", "message"), str(m.get("content", ""))[:200])
    console.print(table)


def use_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def prompt(text: str) -> str:
    """Print a styled prompt and return user input."""
    p = Text(f"\n{text}", style="cyan")
    console.print(p, end="")
    return input()
