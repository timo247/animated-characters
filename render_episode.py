#!/usr/bin/env python3
"""
render_episode.py
-----------------
Génère une vidéo d'épisode à partir d'un fichier de settings JSON.

Usage:
    python render_episode.py --settings episodes/episodes-settings/episode-settings.json

Système de coordonnées :
    Toutes les positions (x, y) correspondent au coin supérieur gauche de l'image,
    exactement comme dans Figma. Aucune logique d'alignement — ce que tu mets
    dans le JSON est ce qui apparaît à l'écran.

Hiérarchie de scaling :
    1. Le sprite idle est redimensionné selon `scale` (episode-settings).
    2. Les yeux et la bouche sont redimensionnés selon `scale` × `overlay_scale`
       (les deux depuis episode-settings).
    3. Les positions (x, y) dans character-settings sont en pixels dans
       l'image ORIGINALE non scalée → multipliées par `scale` automatiquement.
"""

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------------
# Chemins racine (le script est à la racine du projet)
# ---------------------------------------------------------------------------
PROJECT_ROOT       = Path(__file__).parent.resolve()
EPISODES_IMAGES_DIR = PROJECT_ROOT / "episodes" / "images"
CHARACTERS_DIR      = PROJECT_ROOT / "characters"
DEFAULT_OUTPUT_DIR  = PROJECT_ROOT / "episodes" / "videos"


# ---------------------------------------------------------------------------
# Chargement des configs
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_episode_settings(settings_path: str) -> dict:
    path = Path(settings_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        sys.exit(f"[ERREUR] Fichier de settings introuvable : {path}")
    return load_json(path)


def load_character_settings(character_id: str) -> dict:
    path = CHARACTERS_DIR / character_id / "character-settings.json"
    if not path.exists():
        sys.exit(f"[ERREUR] character-settings.json introuvable : {path}")
    return load_json(path)


# ---------------------------------------------------------------------------
# Chemins d'assets
# ---------------------------------------------------------------------------

def pos_dir(character_id: str, position: int) -> Path:
    return CHARACTERS_DIR / character_id / "positions" / str(position)

def idle_img_path(character_id: str, position: int, filename: str) -> Path:
    return pos_dir(character_id, position) / "idles" / filename

def eye_img_path(character_id: str, position: int, emotion: str, filename: str) -> Path:
    return pos_dir(character_id, position) / "eyes" / emotion / filename

def mouth_img_path(character_id: str, position: int, emotion: str, filename: str) -> Path:
    return pos_dir(character_id, position) / "mouths" / emotion / filename


# ---------------------------------------------------------------------------
# Séquences temporelles
# ---------------------------------------------------------------------------

def build_idle_sequence(idle_cfg: dict, total_frames: int, fps: int) -> list[str]:
    """Ping-pong sur les frames idle (1→N→1→…)."""
    frames   = idle_cfg["frames"]
    idle_fps = idle_cfg.get("fps", 8)
    hold     = max(1, round(fps / idle_fps))  # frames vidéo par frame idle

    # Ping-pong : [1,2,3,4] → [1,2,3,4,3,2]
    ping_pong = frames + frames[-2:0:-1]

    sequence, idx, counter = [], 0, 0
    while len(sequence) < total_frames:
        sequence.append(ping_pong[idx % len(ping_pong)])
        counter += 1
        if counter >= hold:
            counter = 0
            idx    += 1
    return sequence


def build_eye_sequence(blink_cfg: dict, total_frames: int, fps: int) -> list[str]:
    """
    Frame-par-frame des fichiers yeux.
    Au repos : blink_cfg["sequence"][0] (= OPEN).
    """
    open_file = blink_cfg["sequence"][0]
    interval  = blink_cfg["interval_seconds"]
    jitter    = blink_cfg.get("interval_jitter_seconds", 1.0)
    seq       = blink_cfg["sequence"]
    frame_dur = blink_cfg["frame_duration_seconds"]

    eye_seq = [open_file] * total_frames

    t = interval + random.uniform(-jitter, jitter)
    while True:
        start = int(t * fps)
        if start >= total_frames:
            break
        for i, ef in enumerate(seq):
            f = start + int(i * frame_dur * fps)
            if f < total_frames:
                eye_seq[f] = ef
        t += interval + random.uniform(-jitter, jitter)

    return eye_seq


def build_mouth_sequence(mouth_cfg: dict, total_frames: int) -> list[str]:
    """
    Bouche idle pour l'instant (toujours CLOSED).
    Branchement futur : liste de visèmes générés dynamiquement.
    """
    return [mouth_cfg.get("idle_viseme", "CLOSED.png")] * total_frames


# ---------------------------------------------------------------------------
# Composite d'un personnage pour une frame
# ---------------------------------------------------------------------------

def scale_img(img: Image.Image, scale: float) -> Image.Image:
    if scale == 1.0:
        return img
    w = max(1, round(img.width  * scale))
    h = max(1, round(img.height * scale))
    return img.resize((w, h), Image.LANCZOS)


def composite_character(
    character_id:  str,
    position:      int,
    char_settings: dict,
    eye_emotion:   str,
    mouth_emotion: str,
    idle_file:     str,
    eye_file:      str,
    mouth_file:    str,
    char_scale:    float,
    overlay_scale: float,
) -> Image.Image:
    """
    Retourne un sprite RGBA : idle scalé + yeux + bouche.

    Convention de positionnement (identique à Figma) :
      - Les (x, y) dans character-settings = coin supérieur gauche
        de l'overlay, exprimés dans l'espace de l'image originale.
      - Ils sont multipliés par char_scale pour suivre le resize du sprite.

    Hiérarchie de scale pour les overlays :
      scale_final = char_scale × overlay_scale (episode-settings)
                                × eyes/mouth.scale (character-settings)
      - char_scale       : taille globale du personnage
      - overlay_scale    : ajustement rapide global yeux+bouche (épisode)
      - eyes/mouth.scale : réglage fin différentiel par overlay (character)
    """
    pos_cfg      = char_settings["positions"][str(position)]
    eye_cfg      = pos_cfg["eyes"]
    mouth_cfg    = pos_cfg["mouth"]
    eye_anchor   = eye_cfg["anchor"]
    mouth_anchor = mouth_cfg["anchor"]

    # Scales différentiels définis dans character-settings (défaut 1.0)
    eye_asset_scale   = float(eye_cfg.get("scale", 1.0))
    mouth_asset_scale = float(mouth_cfg.get("scale", 1.0))

    # --- Idle (base du sprite) ---
    base_path = idle_img_path(character_id, position, idle_file)
    if not base_path.exists():
        sys.exit(f"[ERREUR] Idle introuvable : {base_path}")
    base = scale_img(Image.open(base_path).convert("RGBA"), char_scale)

    # --- Yeux ---
    ep = eye_img_path(character_id, position, eye_emotion, eye_file)
    if ep.exists():
        eye_img = scale_img(
            Image.open(ep).convert("RGBA"),
            char_scale * overlay_scale * eye_asset_scale
        )
        ex = round(eye_anchor["x"] * char_scale)
        ey = round(eye_anchor["y"] * char_scale)
        base.paste(eye_img, (ex, ey), eye_img)
    else:
        print(f"  [WARN] Yeux introuvables : {ep}")

    # --- Bouche ---
    mp = mouth_img_path(character_id, position, mouth_emotion, mouth_file)
    if mp.exists():
        mouth_img = scale_img(
            Image.open(mp).convert("RGBA"),
            char_scale * overlay_scale * mouth_asset_scale
        )
        mx = round(mouth_anchor["x"] * char_scale)
        my = round(mouth_anchor["y"] * char_scale)
        base.paste(mouth_img, (mx, my), mouth_img)
    else:
        print(f"  [WARN] Bouche introuvable : {mp}")

    return base


# ---------------------------------------------------------------------------
# Rendu de toutes les frames
# ---------------------------------------------------------------------------

def render_frames(episode: dict, frames_dir: Path) -> tuple[int, int]:
    fps           = episode["output"]["fps"]
    duration      = episode["output"]["duration_seconds"]
    total_frames  = fps * duration
    width, height = episode["output"]["resolution"]

    # Background
    bg_path = EPISODES_IMAGES_DIR / episode["background"]["image"]
    if not bg_path.exists():
        sys.exit(f"[ERREUR] Background introuvable : {bg_path}")
    background = Image.open(bg_path).convert("RGBA").resize((width, height))

    # Pré-calcul des séquences pour chaque personnage
    char_sequences = []
    for char_cfg in episode["characters"]:
        character_id  = char_cfg["character"]
        position      = char_cfg["position"]
        char_settings = load_character_settings(character_id)
        pos_cfg       = char_settings["positions"][str(position)]

        char_sequences.append({
            "cfg":           char_cfg,
            "settings":      char_settings,
            "idle_seq":      build_idle_sequence(pos_cfg["idle"], total_frames, fps),
            "eye_seq":       build_eye_sequence(pos_cfg["eyes"]["blink"], total_frames, fps),
            "mouth_seq":     build_mouth_sequence(pos_cfg["mouth"], total_frames),
            # Scaling
            "char_scale":    float(char_cfg.get("scale", 1.0)),
            "overlay_scale": float(char_cfg.get("overlay_scale", 1.0)),
            # Position Figma : coin supérieur gauche du sprite sur le background
            "screen_x":      char_cfg["screen_position"]["x"],
            "screen_y":      char_cfg["screen_position"]["y"],
        })

    # Génération frame par frame
    pad = len(str(total_frames))
    for f in range(total_frames):
        frame = background.copy()

        for cs in char_sequences:
            char_cfg = cs["cfg"]

            sprite = composite_character(
                character_id  = char_cfg["character"],
                position      = char_cfg["position"],
                char_settings = cs["settings"],
                eye_emotion   = char_cfg["emotions"]["eyes"],
                mouth_emotion = char_cfg["emotions"]["mouth"],
                idle_file     = cs["idle_seq"][f],
                eye_file      = cs["eye_seq"][f],
                mouth_file    = cs["mouth_seq"][f],
                char_scale    = cs["char_scale"],
                overlay_scale = cs["overlay_scale"],
            )

            # Placement coin supérieur gauche — convention Figma
            frame.paste(sprite, (cs["screen_x"], cs["screen_y"]), sprite)

        out_path = frames_dir / f"frame_{str(f).zfill(pad)}.png"
        frame.convert("RGB").save(out_path)

        if f % fps == 0:
            print(f"  Frame {f + 1}/{total_frames}", end="\r")

    print(f"\n  {total_frames} frames générées.")
    return width, height


# ---------------------------------------------------------------------------
# Assemblage ffmpeg
# ---------------------------------------------------------------------------

def assemble_video(frames_dir: Path, output_path: Path, fps: int, width: int, height: int):
    png_count = len([p for p in frames_dir.iterdir() if p.suffix == ".png"])
    pad       = len(str(png_count))
    pattern   = str(frames_dir / f"frame_%0{pad}d.png")

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", pattern,
        "-vf", f"scale={width}:{height}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-crf", "18",
        str(output_path),
    ]

    print(f"\n[FFMPEG] Assemblage → {output_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[FFMPEG STDERR]", result.stderr)
        sys.exit("[ERREUR] ffmpeg a échoué.")
    print("[OK] Vidéo générée :", output_path)


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Render an episode from a JSON settings file.")
    parser.add_argument("--settings", required=True,
                        help="Chemin vers le fichier episode-settings.json")
    parser.add_argument("--keep-frames", action="store_true",
                        help="Conserver les frames PNG après rendu (debug)")
    args = parser.parse_args()

    episode     = load_episode_settings(args.settings)
    output_dir  = DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / episode["output"]["filename"]

    print(f"[render_episode] Épisode   : {episode['episode_id']}")
    print(f"  Résolution  : {episode['output']['resolution']}")
    print(f"  FPS         : {episode['output']['fps']}")
    print(f"  Durée       : {episode['output']['duration_seconds']}s")
    print(f"  Sortie      : {output_path}")
    print(f"  Personnages : {[c['character'] for c in episode['characters']]}")

    with tempfile.TemporaryDirectory() as tmp:
        frames_dir = Path(tmp) / "frames"
        frames_dir.mkdir()

        print("\n[1/2] Rendu des frames…")
        width, height = render_frames(episode, frames_dir)

        print("\n[2/2] Assemblage ffmpeg…")
        assemble_video(frames_dir, output_path, episode["output"]["fps"], width, height)

        if args.keep_frames:
            kept = output_dir / "frames_debug"
            shutil.copytree(frames_dir, kept, dirs_exist_ok=True)
            print(f"[DEBUG] Frames conservées dans : {kept}")


if __name__ == "__main__":
    main()
