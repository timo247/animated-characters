#!/usr/bin/env python3
"""
Serveur local pour l'éditeur d'episode-settings.json.

À PLACER ET LANCER depuis la racine du projet (là où se trouvent les dossiers
`characters/` et `episodes/`), par exemple :

    ~/scripts/animated_characters$ python3 episode_builder/server.py

Puis ouvrir http://localhost:8765 dans le navigateur.

Aucune dépendance externe : uniquement la stdlib Python 3.
"""
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

PORT = 8765

# Racine du projet = dossier parent de ce script (episode_builder/../)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

CHARACTERS_DIR = os.path.join(ROOT, "characters")
BACKGROUNDS_DIR = os.path.join(ROOT, "episodes", "images")
SETTINGS_OUT_DIR = os.path.join(ROOT, "episodes", "episodes-settings")

STATE_FOLDER = {"idle": "idles", "transitions": "transitions", "moves": "moves"}


def safe_join(base, *parts):
    """Empêche toute sortie de `base` via path traversal."""
    target = os.path.abspath(os.path.join(base, *parts))
    base_abs = os.path.abspath(base)
    if not (target == base_abs or target.startswith(base_abs + os.sep)):
        raise ValueError("Chemin invalide")
    return target


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[episode_builder] " + (fmt % args))

    # ---------- helpers ----------
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({"error": message}, status=status)

    def send_file(self, path, content_type=None):
        if not os.path.isfile(path):
            self.send_error_json(404, f"Fichier introuvable: {os.path.relpath(path, ROOT)}")
            return
        if content_type is None:
            content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------- routing ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path == "/" or path == "/index.html":
                self.send_file(os.path.join(SCRIPT_DIR, "index.html"), "text/html; charset=utf-8")

            elif path == "/api/characters":
                self.handle_list_characters()

            elif path.startswith("/api/character/"):
                name = unquote(path[len("/api/character/"):])
                self.handle_get_character(name)

            elif path == "/api/backgrounds":
                self.handle_list_backgrounds()

            elif path == "/api/episodes-audio":
                self.handle_list_audio()

            elif path == "/api/file":
                rel = qs.get("path", [""])[0]
                self.handle_get_file(rel)

            else:
                self.send_error_json(404, "Route inconnue")
        except ValueError as e:
            self.send_error_json(400, str(e))
        except Exception as e:  # noqa: BLE001
            self.send_error_json(500, str(e))

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/save":
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                self.handle_save(payload)
            else:
                self.send_error_json(404, "Route inconnue")
        except ValueError as e:
            self.send_error_json(400, str(e))
        except Exception as e:  # noqa: BLE001
            self.send_error_json(500, str(e))

    # ---------- handlers ----------
    def handle_list_characters(self):
        result = []
        if os.path.isdir(CHARACTERS_DIR):
            for name in sorted(os.listdir(CHARACTERS_DIR)):
                cdir = os.path.join(CHARACTERS_DIR, name)
                settings_path = os.path.join(cdir, "character-settings.json")
                if os.path.isdir(cdir) and os.path.isfile(settings_path):
                    result.append(name)
        self.send_json({"characters": result})

    def handle_get_character(self, name):
        if "/" in name or ".." in name:
            raise ValueError("Nom de personnage invalide")
        settings_path = safe_join(CHARACTERS_DIR, name, "character-settings.json")
        if not os.path.isfile(settings_path):
            self.send_error_json(404, f"Aucun character-settings.json pour '{name}'")
            return
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        positions = data.get("positions", {})
        enriched = {}
        for pos_key, pos_val in positions.items():
            states = {}
            for state_key in ("idle", "transitions", "moves"):
                state_val = pos_val.get(state_key)
                if not state_val:
                    continue
                frames = state_val.get("frames", [])
                folder = STATE_FOLDER.get(state_key, state_key)
                first_frame = frames[0] if frames else None
                preview_url = None
                if first_frame:
                    rel = os.path.join("characters", name, "positions", str(pos_key), folder, first_frame)
                    disk_path = os.path.join(ROOT, rel)
                    if os.path.isfile(disk_path):
                        preview_url = "/api/file?path=" + rel.replace(os.sep, "/")
                states[state_key] = {"frames": frames, "preview_url": preview_url}
            enriched[pos_key] = states

        self.send_json({
            "character_id": data.get("character_id", name),
            "label": data.get("label", name),
            "positions": enriched,
        })

    def handle_list_backgrounds(self):
        result = []
        if os.path.isdir(BACKGROUNDS_DIR):
            for name in sorted(os.listdir(BACKGROUNDS_DIR)):
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    result.append(name)
        self.send_json({"backgrounds": result})

    def handle_list_audio(self):
        audio_dir = os.path.join(ROOT, "episodes", "audios")
        result = []
        if os.path.isdir(audio_dir):
            for name in sorted(os.listdir(audio_dir)):
                if name.lower().endswith((".mp3", ".wav", ".m4a")):
                    result.append(os.path.join("episodes", "audios", name).replace(os.sep, "/"))
        self.send_json({"audios": result})

    def handle_get_file(self, rel_path):
        if not rel_path:
            raise ValueError("Paramètre 'path' manquant")
        disk_path = safe_join(ROOT, rel_path)
        self.send_file(disk_path)

    def handle_save(self, payload):
        filename = payload.get("filename", "").strip()
        data = payload.get("data")
        if not filename:
            self.send_error_json(400, "Nom de fichier manquant")
            return
        if data is None:
            self.send_error_json(400, "Données manquantes")
            return
        if "/" in filename or "\\" in filename or ".." in filename:
            self.send_error_json(400, "Nom de fichier invalide")
            return
        if not filename.endswith(".json"):
            filename += ".json"

        os.makedirs(SETTINGS_OUT_DIR, exist_ok=True)
        out_path = os.path.join(SETTINGS_OUT_DIR, filename)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.send_json({
            "ok": True,
            "path": os.path.relpath(out_path, ROOT).replace(os.sep, "/"),
        })


def main():
    if not os.path.isdir(CHARACTERS_DIR):
        print(f"ATTENTION: {CHARACTERS_DIR} introuvable. "
              f"Ce script doit être placé dans un sous-dossier de la racine du projet "
              f"(celle qui contient characters/ et episodes/).")
    server = ThreadingHTTPServer(("localhost", PORT), Handler)
    print(f"Racine du projet : {ROOT}")
    print(f"Éditeur disponible sur http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
