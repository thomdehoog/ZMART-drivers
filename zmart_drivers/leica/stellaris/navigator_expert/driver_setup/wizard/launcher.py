"""Open the wizard in its own window.

The window is drawn by pywebview (WebView2 on Windows), the same way the ZMART
Interface draws its window. If pywebview is not installed, ``--browser`` opens
the same page in the computer's web browser instead.
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from .framework import Wizard
from .server import WizardServer


def _wait_until_stopped() -> None:
    """Keep the wizard open until Ctrl+C is pressed in the terminal."""
    threading.Event().wait()


def run(wizard: Wizard, *, how: str = "window", port: int = 0) -> int:
    """Serve *wizard* and show it until the window (or the terminal) is closed.

    *how* is ``"window"`` (a pywebview window), ``"browser"`` (the computer's
    web browser) or ``"serve"`` (only the server, for developing the page).
    The server is always stopped on the way out, also when the window fails.
    Answers 0, or 1 when pywebview is missing.
    """
    server = WizardServer(wizard, port).start()
    try:
        if how == "window":
            try:
                import webview
            except ImportError:
                print(
                    "The window needs the 'pywebview' package (pip install pywebview).\n"
                    f"Or open {server.url} in a browser, or run again with --browser.",
                    file=sys.stderr,
                )
                return 1
            webview.create_window(wizard.title, server.url, width=1180, height=820)
            webview.start()
            return 0
        if how == "browser":
            webbrowser.open(server.url)
            print(
                f"The setup wizard is open in your browser at {server.url}  (Ctrl+C to stop)"
            )
        else:
            print(f"The setup wizard is running at {server.url}  (Ctrl+C to stop)")
        _wait_until_stopped()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up a Leica STELLARIS for ZMART.")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="open in the web browser instead of a window",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="only serve the page (for developing it)",
    )
    parser.add_argument(
        "--port", type=int, default=0, help="port to listen on (default: any free one)"
    )
    args = parser.parse_args(argv)

    from .steps import leica_wizard

    how = "browser" if args.browser else "serve" if args.no_window else "window"
    return run(leica_wizard(), how=how, port=args.port)
