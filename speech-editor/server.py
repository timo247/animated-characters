#!/usr/bin/env python3
"""
Speech Editor — petit serveur local (stdlib uniquement, aucune dépendance).

Usage:
    python3 server.py [dossier_json] [dossier_audio]

Si [dossier_json] n'est pas fourni, le dossier courant est utilisé.
Si [dossier_audio] n'est pas fourni, le dossier audio = dossier_json.
Le serveur ne lit/écrit QUE des fichiers .json situés directement dans dossier_json,
et des fichiers audio situés directement dans dossier_audio
(pas de sous-dossiers, pas de traversée de chemin).
"""
import http.server
import socketserver
import json
import os
import re
import sys
import urllib.parse
import mimetypes

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
AUDIO_DIR = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else DATA_DIR
STATIC_DIR = os.path.join(ROOT, "static")
CHAR_FILE = os.path.join(DATA_DIR, "characters.json")
PORT = int(os.environ.get("SPEECH_EDITOR_PORT", "8765"))

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus")


class Handler(http.server.BaseHTTPRequestHandler):
    # ---------- helpers ----------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _safe_path(self, name, exts, base_dir):
        """N'autorise qu'un fichier avec une des extensions données, directement dans base_dir
        (pas de sous-dossiers, pas de traversée de chemin)."""
        base = os.path.basename(name)
        if not base.lower().endswith(exts):
            return None
        path = os.path.join(base_dir, base)
        if os.path.dirname(os.path.abspath(path)) != base_dir:
            return None
        return path

    def _safe_json_path(self, name):
        return self._safe_path(name, (".json",), DATA_DIR)

    def _safe_audio_path(self, name):
        return self._safe_path(name, AUDIO_EXTS, AUDIO_DIR)

    def _serve_file_with_range(self, path, ctype):
        """Sert un fichier avec support des requêtes Range (nécessaire pour le scrub audio)."""
        file_size = os.path.getsize(path)
        range_header = self.headers.get("Range")
        if range_header:
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else file_size - 1
                end = min(end, file_size - 1)
                if start > end or start >= file_size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                with open(path, "rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = fh.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_size))
        self.end_headers()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

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
            self._send_json({"dir": DATA_DIR, "audio_dir": AUDIO_DIR})
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

        if parsed.path == "/api/audio-files":
            try:
                files = sorted(
                    f for f in os.listdir(AUDIO_DIR)
                    if f.lower().endswith(AUDIO_EXTS)
                )
            except FileNotFoundError:
                files = []
            self._send_json({"dir": AUDIO_DIR, "files": files})
            return

        if parsed.path.startswith("/audio/"):
            name = urllib.parse.unquote(parsed.path[len("/audio/"):])
            path = self._safe_audio_path(name)
            if not path or not os.path.isfile(path):
                self.send_error(404, "Fichier audio introuvable")
                return
            ctype, _ = mimetypes.guess_type(path)
            self._serve_file_with_range(path, ctype or "application/octet-stream")
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
    os.makedirs(AUDIO_DIR, exist_ok=True)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Speech Editor sur http://127.0.0.1:{PORT}")
        print(f"Dossier JSON  : {DATA_DIR}")
        print(f"Dossier audio : {AUDIO_DIR}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nArrêt du serveur.")


if __name__ == "__main__":
    main()
