#!/usr/bin/env python3
"""
Speech Editor — petit serveur local (stdlib uniquement, aucune dépendance).

Usage:
    python3 server.py [dossier]

Si [dossier] n'est pas fourni, le dossier courant est utilisé.
Le serveur ne lit/écrit QUE des fichiers .json situés directement dans ce dossier
(pas de sous-dossiers, pas de traversée de chemin).
"""
import http.server
import socketserver
import json
import os
import sys
import urllib.parse
import mimetypes

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
STATIC_DIR = os.path.join(ROOT, "static")
CHAR_FILE = os.path.join(DATA_DIR, "characters.json")
PORT = int(os.environ.get("SPEECH_EDITOR_PORT", "8765"))


class Handler(http.server.BaseHTTPRequestHandler):
    # ---------- helpers ----------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _safe_json_path(self, name):
        """N'autorise que des fichiers .json directement dans DATA_DIR (pas de sous-dossiers)."""
        base = os.path.basename(name)
        if not base.lower().endswith(".json"):
            return None
        path = os.path.join(DATA_DIR, base)
        if os.path.dirname(os.path.abspath(path)) != DATA_DIR:
            return None
        return path

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        fpath = os.path.abspath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not fpath.startswith(STATIC_DIR) or not os.path.isfile(fpath):
            self.send_error(404, "Not found")
            return
        ctype, _ = mimetypes.guess_type(fpath)
        with open(fpath, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- routes ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/status":
            self._send_json({"dir": DATA_DIR})
            return

        if parsed.path == "/api/files":
            try:
                files = sorted(
                    f for f in os.listdir(DATA_DIR)
                    if f.lower().endswith(".json") and f != "characters.json"
                )
            except FileNotFoundError:
                files = []
            self._send_json({"dir": DATA_DIR, "files": files})
            return

        if parsed.path == "/api/load":
            name = qs.get("file", [""])[0]
            path = self._safe_json_path(name) if name else None
            if not path:
                self._send_json({"error": "nom de fichier invalide"}, 400)
                return
            if not os.path.isfile(path):
                self._send_json({"error": "fichier introuvable"}, 404)
                return
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except json.JSONDecodeError as e:
                self._send_json({"error": f"JSON invalide: {e}"}, 400)
                return
            self._send_json({"file": os.path.basename(path), "data": data})
            return

        if parsed.path == "/api/characters":
            data = {}
            if os.path.isfile(CHAR_FILE):
                try:
                    with open(CHAR_FILE, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                except json.JSONDecodeError:
                    data = {}
            self._send_json(data)
            return

        self._serve_static(parsed.path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json({"error": "corps JSON invalide"}, 400)
            return

        if parsed.path == "/api/save":
            name = payload.get("file", "")
            data = payload.get("data")
            path = self._safe_json_path(name) if name else None
            if not path or data is None:
                self._send_json({"error": "fichier ou données manquants/invalides"}, 400)
                return
            # backup avant écrasement
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as src:
                        backup_content = src.read()
                    with open(path + ".bak", "w", encoding="utf-8") as dst:
                        dst.write(backup_content)
                except OSError:
                    pass
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
            except OSError as e:
                self._send_json({"error": f"écriture impossible: {e}"}, 500)
                return
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/characters":
            try:
                with open(CHAR_FILE, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
            except OSError as e:
                self._send_json({"error": f"écriture impossible: {e}"}, 500)
                return
            self._send_json({"ok": True})
            return

        self._send_json({"error": "route inconnue"}, 404)

    def log_message(self, fmt, *args):
        pass  # silence les logs par défaut


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Speech Editor sur http://127.0.0.1:{PORT}")
        print(f"Dossier de travail : {DATA_DIR}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nArrêt du serveur.")


if __name__ == "__main__":
    main()
