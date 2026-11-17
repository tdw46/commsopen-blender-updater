"""Worker process for update metadata, loopback approval, and verified package transfer."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import ssl
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

ORIGIN = "https://commsopen.shop"
MAX_ZIP = 512 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def _request(path, body=None):
    headers = {"User-Agent": "CommsOpen-Blender/1.0 (Blender extension updater)", "Accept": "application/json"}
    if body is not None:
        headers.update({"Content-Type": "application/json", "Accept": "application/zip"})
    request = Request(ORIGIN + path, data=json.dumps(body).encode() if body else None, headers=headers)
    context = ssl.create_default_context()
    # Blender's bundled Python on macOS may point at a nonexistent build-time CA bundle.
    if sys.platform == "darwin" and Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return build_opener(NoRedirect(), HTTPSHandler(context=context)).open(request, timeout=30)


def metadata(product, edition):
    route = f"/wp-json/commsopen/v1/products/{int(product)}/updates?edition={int(edition)}"
    with _request(route) as response:
        info = json.loads(response.read(32768))
    if (info["productId"], info["editionId"]) != (product, edition) or not re.fullmatch("[a-f0-9]{64}", info["revision"]):
        raise ValueError("Unexpected edition metadata.")
    return info


def authorize(product, edition, open_browser=webbrowser.open):
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback URLs carry a one-use code.

        def do_GET(self):
            path = urlsplit(self.path)
            values = parse_qs(path.query)
            code = values.get("code", [""])[0]
            allowed = (self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                       and path.path == "/callback" and values.get("state") == [state]
                       and re.fullmatch("[a-f0-9]{64}", code))
            self.send_response(200 if allowed else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if allowed:
                self.server.code = code
            self.wfile.write(b"Return to Blender to finish your update." if allowed else b"Invalid callback.")

    class Listener(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return connection, address

    with Listener(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        server.code = None
        query = urlencode(dict(product=product, edition=edition, state=state, challenge=challenge, port=server.server_port))
        if not open_browser(ORIGIN + "/blender/authorize/?" + query):
            raise RuntimeError("Could not open the default browser.")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and server.code is None:
            server.handle_request()
        if server.code is None:
            raise TimeoutError("Sign-in timed out. Start the update again.")
        return {"code": server.code, "verifier": verifier}


def download(product, edition, target):
    proof = authorize(product, edition)
    with _request("/wp-json/commsopen/v1/blender/package", proof) as response:
        digest = response.headers.get("X-CommsOpen-SHA256", "")
        revision = response.headers.get("X-CommsOpen-Revision", "")
        version = response.headers.get("X-CommsOpen-Version", "")
        if not all(re.fullmatch("[a-f0-9]{64}", v) for v in (digest, revision)) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("The server did not return a verified extension package.")
        measured = hashlib.sha256()
        size = 0
        deadline = time.monotonic() + 300
        with target.open("xb") as output:
            while chunk := response.read(256 * 1024):
                size += len(chunk)
                if size > MAX_ZIP or time.monotonic() > deadline:
                    raise ValueError("The package exceeds the transfer limit.")
                measured.update(chunk)
                output.write(chunk)
    if not hmac.compare_digest(measured.hexdigest(), digest):
        raise ValueError("Package verification failed. Retry the update.")
    return dict(version=version, revision=revision, sha256=digest)


def main():
    action, product, edition, directory = sys.argv[1:]
    root = Path(directory)
    try:
        if action == "check":
            result = metadata(int(product), int(edition))
        elif action == "install":
            result = download(int(product), int(edition), root / "update.zip")
        else:
            raise ValueError("Unknown update action.")
    except HTTPError as error:
        result = {"error": f"CommsOpen returned HTTP {error.code}. Check your edition and purchase access."}
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        result = {"error": str(error)}
    (root / "result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
