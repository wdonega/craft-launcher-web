#!/usr/bin/env python3
"""Serve site/ on http://localhost:8080 with the MIME types and caching the web builds need.

Usage: python3 serve.py [--port 8080] [--dir site]
localhost is a secure context, so WebGPU and the clipboard work.
"""

import argparse
import functools
import http.server
from pathlib import Path


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".json": "application/json",
        ".html": "text/html; charset=utf-8",
    }

    def end_headers(self):
        path = self.path.split("?")[0]
        if path.endswith("/") or path.endswith(".html") or "/_local/" in path:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent / "site"))
    args = ap.parse_args()
    root = Path(args.dir)
    if not (root / "index.html").exists():
        raise SystemExit(f"{root}/index.html missing: run python3 build.py first")
    handler = functools.partial(Handler, directory=str(root))
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as srv:
        print(f"ArtCraft Launcher: http://localhost:{args.port}/")
        srv.serve_forever()


if __name__ == "__main__":
    main()
