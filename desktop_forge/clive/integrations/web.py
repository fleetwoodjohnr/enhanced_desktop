"""Live web research and opening pages in the default browser."""
from __future__ import annotations

from .base import Capability, Guards, Integration, Tool
from .schemas import STRING

ID = "web"

CAPABILITIES = (
    Capability("search", "Search the web", "search", "low",
               "Through Ollama's web search; needs an Ollama API key."),
    Capability("read", "Read web pages", "read", "low"),
    Capability("open", "Open pages in your browser", "execute", "normal"),
)


def integration(key_state=lambda: None) -> Integration:
    def status():
        has_key = key_state()
        if has_key is False:
            return {"state": "needs_setup",
                    "detail": "Web search and page reading need an Ollama API key"}
        return {"state": "ready", "detail": ""}
    return Integration(ID, "Web & Browser", "web", "web-browser-symbolic",
                       "Search the web, read public pages and open links in your browser.",
                       CAPABILITIES, default_enabled=True, status=status,
                       # A browser window can reach everything the web tools can.
                       guards=Guards(categories=("WebBrowser",)))


def _web(ctx, name, a):
    return ctx.models.web(name, a)


def _open(_ctx, a):
    from gi.repository import Gio
    Gio.AppInfo.launch_default_for_uri(a["url"], None)
    return {"opened": a["url"]}


TOOLS = (
    Tool("web_search", "Search the live web. Cite the returned URLs.", {"query": STRING}, "search",
         lambda ctx, a: _web(ctx, "web_search", a), ID, "Searching the web…",
         describe=lambda a: a["query"]),
    Tool("web_fetch", "Read a public web page.", {"url": STRING}, "read",
         lambda ctx, a: _web(ctx, "web_fetch", a), ID, "Reading a web page…",
         describe=lambda a: a["url"]),
    Tool("open_url", "Open a public URL in the default browser.", {"url": STRING}, "open",
         _open, ID, "Opening a page…", describe=lambda a: a["url"]),
)
