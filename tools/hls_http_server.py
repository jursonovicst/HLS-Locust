import argparse
import functools
import http.server
import mimetypes
import socketserver


def build_handler(directory: str):
    """Return an HTTP handler that serves files from the given directory with HLS MIME types."""
    mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
    mimetypes.add_type("video/mp2t", ".ts")

    class HLSHandler(http.server.SimpleHTTPRequestHandler):
        # Ensure per-extension mapping is explicit regardless of host defaults.
        extensions_map = {
            **http.server.SimpleHTTPRequestHandler.extensions_map,
            ".m3u8": "application/vnd.apple.mpegurl",
            ".ts": "video/mp2t",
        }

    return functools.partial(HLSHandler, directory=directory)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve HLS output with explicit MIME types.")
    parser.add_argument("port", type=int, help="Port to listen on")
    parser.add_argument("directory", help="Directory to serve")
    args = parser.parse_args()

    handler = build_handler(args.directory)
    with socketserver.TCPServer(("", args.port), handler) as httpd:
        print(f"Serving HLS at http://localhost:{args.port}/stream.m3u8")
        print("Press Ctrl+C to stop.")
        print("")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            # Quiet shutdown for Ctrl+C instead of traceback noise.
            print("\nStopping HTTP server...")


if __name__ == "__main__":
    main()
