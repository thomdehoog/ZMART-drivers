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


def run(wizard: Wizard, *, window: bool = True, port: int = 0) -> int:
    """Serve *wizard* and show it until the window (or the terminal) is closed."""
    server = WizardServer(wizard, port).start()
    try:
        if window:
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
        else:
            print(f"The setup wizard is running at {server.url}  (Ctrl+C to stop)")
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up a Leica STELLARIS 5 for ZMART.")
    parser.add_argument("--browser", action="store_true", help="open in the web browser instead of a window")
    parser.add_argument("--no-window", action="store_true", help="only serve the page (for developing it)")
    parser.add_argument("--port", type=int, default=0, help="port to listen on (default: any free one)")
    args = parser.parse_args(argv)

    from .steps import leica_wizard

    wizard = leica_wizard()
    if args.browser:
        server = WizardServer(wizard, args.port).start()
        webbrowser.open(server.url)
        print(f"The setup wizard is open in your browser at {server.url}  (Ctrl+C to stop)")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
        return 0
    return run(wizard, window=not args.no_window, port=args.port)
