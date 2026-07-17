import contextlib
import re
import sys
import os
from collections.abc import AsyncIterator
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from mcp.server.sse import SseServerTransport
from starlette.requests import Request
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from markitdown import MarkItDown
import uvicorn

# Initialize FastMCP server for MarkItDown (SSE)
mcp = FastMCP("markitdown")


@mcp.tool()
async def convert_to_markdown(uri: str) -> str:
    """Convert a resource described by an http:, https:, file: or data: URI to markdown.

    Preserves document structure (headings, lists, tables, links, emphasis) as
    Markdown. Use this when the structure matters to the task.
    """
    return MarkItDown(enable_plugins=check_plugins_enabled()).convert_uri(uri).markdown


@mcp.tool()
async def convert_to_text(uri: str) -> str:
    """Convert a resource described by an http:, https:, file: or data: URI to plain text.

    Same extraction as `convert_to_markdown`, but Markdown formatting (heading
    markers, emphasis, link/image syntax, table pipes, code fences, list bullets,
    etc.) is stripped away, leaving only the readable text. This typically produces
    fewer tokens than the Markdown output. Use this when you only need the textual
    content and not the document's structure.
    """
    markdown = MarkItDown(enable_plugins=check_plugins_enabled()).convert_uri(uri).markdown
    return _markdown_to_text(markdown)


def _markdown_to_text(markdown: str) -> str:
    """Best-effort strip of Markdown formatting to leave readable plain text.

    This is intentionally lightweight and dependency-free: it removes the most
    common Markdown syntax so the result is cheaper (in tokens) to pass to a model
    while keeping the underlying text intact.
    """
    text = markdown

    # Remove fenced code blocks' fences, keeping the code contents.
    text = re.sub(r"^[ \t]*```[^\n]*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*~~~[^\n]*\n?", "", text, flags=re.MULTILINE)

    # Images: ![alt](url) -> alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Links: [text](url) -> text
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Reference-style links: [text][ref] -> text
    text = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", text)

    lines = []
    for line in text.split("\n"):
        # Horizontal rules -> drop
        if re.match(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", line):
            continue
        # Heading markers: "## Title" -> "Title"
        line = re.sub(r"^[ \t]*#{1,6}[ \t]+", "", line)
        # Blockquote markers: "> quote" -> "quote"
        line = re.sub(r"^[ \t]*>[ \t]?", "", line)
        # List bullets and numbers: "- item" / "1. item" -> "item"
        line = re.sub(r"^[ \t]*([-*+]|\d+[.)])[ \t]+", "", line)
        # Table rows: drop separator rows, turn "| a | b |" into "a  b"
        stripped = line.strip()
        if stripped.startswith("|"):
            if re.match(r"^\|[\s:\-|]+\|?$", stripped):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            line = "  ".join(cells)
        lines.append(line)
    text = "\n".join(lines)

    # Inline emphasis and code markers.
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)  # bold
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)  # italic
    text = re.sub(r"~~(.*?)~~", r"\1", text)  # strikethrough
    text = re.sub(r"`+([^`]*)`+", r"\1", text)  # inline code

    # Escaped Markdown punctuation: "\*" -> "*"
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>~|])", r"\1", text)

    # Collapse runs of 3+ newlines into a paragraph break.
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def check_plugins_enabled() -> bool:
    return os.getenv("MARKITDOWN_ENABLE_PLUGINS", "false").strip().lower() in (
        "true",
        "1",
        "yes",
    )


def create_starlette_app(mcp_server: Server, *, debug: bool = False) -> Starlette:
    sse = SseServerTransport("/messages/")
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        event_store=None,
        json_response=True,
        stateless=True,
    )

    async def handle_sse(request: Request) -> None:
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    async def handle_streamable_http(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Context manager for session manager."""
        async with session_manager.run():
            print("Application started with StreamableHTTP session manager!")
            try:
                yield
            finally:
                print("Application shutting down...")

    return Starlette(
        debug=debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/mcp", app=handle_streamable_http),
            Mount("/messages/", app=sse.handle_post_message),
        ],
        lifespan=lifespan,
    )


# Main entry point
def main():
    import argparse

    mcp_server = mcp._mcp_server

    parser = argparse.ArgumentParser(description="Run a MarkItDown MCP server")

    parser.add_argument(
        "--http",
        action="store_true",
        help="Run the server with Streamable HTTP and SSE transport rather than STDIO (default: False)",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="(Deprecated) An alias for --http (default: False)",
    )
    parser.add_argument(
        "--host", default=None, help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Port to listen on (default: 3001)"
    )
    args = parser.parse_args()

    use_http = args.http or args.sse

    if not use_http and (args.host or args.port):
        parser.error(
            "Host and port arguments are only valid when using streamable HTTP or SSE transport (see: --http)."
        )
        sys.exit(1)

    if use_http:
        host = args.host if args.host else "127.0.0.1"
        if args.host and args.host not in ("127.0.0.1", "localhost"):
            print(
                "\n"
                "WARNING: The server is being bound to a non-localhost interface "
                f"({host}).\n"
                "This exposes the server to other machines on the network or Internet.\n"
                "The server has NO authentication and runs with your user's privileges.\n"
                "Any process or user that can reach this interface can read files and\n"
                "fetch network resources accessible to this user.\n"
                "Only proceed if you understand the security implications.\n",
                file=sys.stderr,
            )
        starlette_app = create_starlette_app(mcp_server, debug=True)
        uvicorn.run(
            starlette_app,
            host=host,
            port=args.port if args.port else 3001,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
