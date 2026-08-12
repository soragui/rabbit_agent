"""Terminal UI — Rich-based markdown rendering with print() fallback."""
import sys
from contextlib import contextmanager

try:
    from rich import box
    from rich.console import Console as _RichConsole
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False
    Live = None  # type: ignore[assignment,misc]

_console = _RichConsole(highlight=True) if _HAS_RICH else None


def render_banner(model: str, workdir: str) -> None:
    if _HAS_RICH:
        _console.print(Rule("51agent", style="cyan"))
        _console.print(f"[bold cyan]  Model:[/] {model}  [dim]|[/]  [bold cyan]Workdir:[/] {workdir}")
        _console.print("  [dim]29 tools (incl. MCP)  ·  modules: config / tools / harness / loop[/]")
        _console.print(Rule(style="cyan"))
    else:
        print("=" * 60)
        print("  51agent")
        print(f"  Model: {model}  |  Workdir: {workdir}")
        print("  29 tools (incl. MCP)  ·  modules: config / tools / harness / loop")
        print("=" * 60)


def render_help() -> None:
    if _HAS_RICH:
        out = Text()
        out.append("\n")
        out.append("      (", style="yellow")
        out.append("\\", style="yellow")
        out.append("(", style="yellow")
        out.append("/\n", style="yellow")
        out.append("      ( -.-)\n", style="yellow")
        out.append('      o_(")(")\n', style="yellow")
        out.append("       ╰─ 51agent", style="dim")
        _console.print(out)
    else:
        print(r"""
      (\_/)
      ( -.-)
      o_(")(")
       ╰─ 51agent
""")


def render_markdown(content: str) -> None:
    if _HAS_RICH:
        md = Markdown(content, code_theme="monokai")
        _console.print(md)
    else:
        print(content)


def render_tool_use(tool_name: str, tool_input: str) -> None:
    if _HAS_RICH:
        label = Text(tool_name, style="bold yellow")
        detail = Text(tool_input, style="dim")
        _console.print(Panel(detail, title=label, border_style="yellow", padding=(0, 1)))
    else:
        print(f"  [{tool_name}] {tool_input}")


def render_error(message: str) -> None:
    if _HAS_RICH:
        _console.print(f"[bold red]✗[/] {message}")
    else:
        print(f"  [error] {message}")


def render_info(message: str) -> None:
    if _HAS_RICH:
        _console.print(f"[dim]  {message}[/]")
    else:
        print(f"  {message}")


def render_inbox(messages: list[dict]) -> None:
    if _HAS_RICH:
        table = Table(box=box.SIMPLE, border_style="magenta", show_header=True, padding=(0, 1))
        table.add_column("From", style="bold magenta")
        table.add_column("Type", style="dim")
        table.add_column("Content")
        for m in messages:
            table.add_row(m.get("from", "?"), m.get("type", "message"), str(m.get("content", ""))[:200])
        _console.print(table)
    else:
        for m in messages:
            print(f"  [inbox] {m.get('from', '?')} ({m.get('type', 'message')}): {str(m.get('content', ''))[:200]}")


@contextmanager
def spinner(label: str = "Thinking..."):
    if _HAS_RICH:
        try:
            with _console.status(f"[cyan]{label}[/]", spinner="dots"):
                yield
        except KeyboardInterrupt:
            print()
            raise
    else:
        print(f"  ... {label}")
        try:
            yield
        except KeyboardInterrupt:
            print()
            raise


@contextmanager
def streaming_renderer():
    """Context manager that renders markdown incrementally as text arrives.

    Usage:
        with streaming_renderer() as render:
            for chunk in stream:
                render(accumulated_text)
    """
    if not _HAS_RICH:
        chunks: list[str] = []
        def _add(text: str) -> None:
            chunks.append(text)
        yield _add
        print("".join(chunks))
        return

    buf: list[str] = []
    with Live(Markdown(""), console=_console, refresh_per_second=10) as live:
        def _add(text: str) -> None:
            buf.append(text)
            live.update(Markdown("".join(buf)))

        try:
            yield _add
        except KeyboardInterrupt:
            print()
            raise


def use_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def prompt(text: str) -> str:
    if _HAS_RICH:
        p = Text(f"\n{text}", style="cyan")
        _console.print(p, end="")
    else:
        print(f"\n{text}", end="")
    return input()
