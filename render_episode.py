#!/usr/bin/env python3
"""
render_episode.py
-----------------
Génère une vidéo d'épisode à partir d'un fichier de settings JSON.

Usage:
    python render_episode.py --settings episodes/episodes-settings/episode-settings.json

Système de coordonnées :
    Toutes les positions (x, y) = coin supérieur gauche, convention Figma.

Hiérarchie de scaling :
    scale_final_overlay = char_scale × overlay_scale × anim_cfg["eyes"|"mouth"]["scale"]

Assets yeux / bouche par type d'animation :
    idle        → positions/{N}/eyes/{EMOTION}/         positions/{N}/mouths/{EMOTION}/
    transitions → positions/{N}/transitions/eyes/{EMOTION}/  transitions/mouths/{EMOTION}/
    moves       → positions/{N}/moves/eyes/{EMOTION}/        moves/mouths/{EMOTION}/

Machine à états :
    idle ──► transition_out ──► move ──► transition_in ──► idle
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
# Chemins racine
# ---------------------------------------------------------------------------
PROJECT_ROOT        = Path(__file__).parent.resolve()
EPISODES_IMAGES_DIR = PROJECT_ROOT / "episodes" / "images"
CHARACTERS_DIR      = PROJECT_ROOT / "characters"
DEFAULT_OUTPUT_DIR  = PROJECT_ROOT / "episodes" / "videos"

# États de la machine à états
IDLE           = "idle"
TRANSITION_OUT = "transition_out"
MOVE           = "move"
TRANSITION_IN  = "transition_in"

# Mapping état → clé dans character-settings (pour anchors/scale)
STATE_TO_ANIM_KEY = {
    IDLE:           "idle",
    TRANSITION_OUT: "transitions",
    MOVE:           "moves",
    TRANSITION_IN:  "transitions",
}

# Mapping état → sous-dossier des sprites de base
STATE_TO_BASE_DIR = {
    IDLE:           "idles",
    TRANSITION_OUT: "transitions",
    MOVE:           "moves",
    TRANSITION_IN:  "transitions",
}


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

def eye_img_path(character_id: str, position: int, state: str,
                 emotion: str, filename: str) -> Path:
    """
    Résout le chemin d'une image yeux selon l'état :
      - idle        → positions/{N}/idles/eyes/{EMOTION}/
      - move        → positions/{N}/moves/eyes/{EMOTION}/
      - transition  → positions/{N}/transitions/eyes/{EMOTION}/
    """
    base = pos_dir(character_id, position)
    if state == IDLE:
        return base / "idles" / "eyes" / emotion / filename
    elif state in (TRANSITION_OUT, TRANSITION_IN):
        return base / "transitions" / "eyes" / emotion / filename
    else:  # MOVE
        return base / "moves" / "eyes" / emotion / filename

def mouth_img_path(character_id: str, position: int, state: str,
                   emotion: str, filename: str) -> Path:
    """
    Résout le chemin d'une image bouche selon l'état :
      - idle        → positions/{N}/idles/mouths/{EMOTION}/
      - move        → positions/{N}/moves/mouths/{EMOTION}/
      - transition  → positions/{N}/transitions/mouths/{EMOTION}/
    """
    base = pos_dir(character_id, position)
    if state == IDLE:
        return base / "idles" / "mouths" / emotion / filename
    elif state in (TRANSITION_OUT, TRANSITION_IN):
        return base / "transitions" / "mouths" / emotion / filename
    else:  # MOVE
        return base / "moves" / "mouths" / emotion / filename


# ---------------------------------------------------------------------------
# Séquences temporelles
# ---------------------------------------------------------------------------

def build_eye_sequence(blink_cfg: dict, total_frames: int, fps: int) -> list[str]:
    """
    Séquence des fichiers yeux frame par frame (clignement aléatoire).
    Les paramètres de clignement sont désormais dans pos_cfg["blink"].
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

def build_mouth_sequence(idle_viseme: str, total_frames: int) -> list[str]:
    """Bouche idle (CLOSED) pour l'instant. Branchement futur : visèmes dynamiques."""
    return [idle_viseme] * total_frames


# ---------------------------------------------------------------------------
# Machine à états — timeline de déplacement
# ---------------------------------------------------------------------------

def build_move_timeline(moves_cfg: list[dict], total_frames: int, fps: int,
                        char_settings: dict, position: int) -> list[dict]:
    """
    Retourne une liste de `total_frames` dicts :
      { "state": str, "sprite": str, "x": int, "y": int }

    Séquences de base (idle ping-pong) pré-remplies, écrasées par move/transition.
    """
    pos_cfg       = char_settings["positions"][str(position)]
    idle_frames   = pos_cfg["idle"]["frames"]
    idle_fps_cfg  = pos_cfg["idle"].get("fps", 8)
    trans_frames  = pos_cfg["transitions"]["frames"]
    trans_fps_cfg = pos_cfg["transitions"].get("fps", 8)
    move_frames   = pos_cfg["moves"]["frames"]
    move_fps_cfg  = pos_cfg["moves"].get("fps", 10)

    # Timeline initiale : tout en idle
    timeline = [{"state": IDLE, "sprite": None, "x": 0, "y": 0}
                for _ in range(total_frames)]

    # Pré-remplissage des sprites idle (ping-pong)
    idle_hold      = max(1, round(fps / idle_fps_cfg))
    idle_ping_pong = idle_frames + idle_frames[-2:0:-1]
    idx, ctr = 0, 0
    for f in range(total_frames):
        timeline[f]["sprite"] = idle_ping_pong[idx % len(idle_ping_pong)]
        ctr += 1
        if ctr >= idle_hold:
            ctr = 0
            idx += 1

    if not moves_cfg:
        return timeline

    for mv in sorted(moves_cfg, key=lambda m: m["at_second"]):
        start_f = int(mv["at_second"] * fps)
        from_x  = mv["from_position"]["x"]
        from_y  = mv["from_position"]["y"]
        to_x    = mv["to_position"]["x"]
        to_y    = mv["to_position"]["y"]
        t_fps   = mv.get("transition_fps", trans_fps_cfg)
        m_fps   = mv.get("move_fps",       move_fps_cfg)
        t_hold  = max(1, round(fps / t_fps))
        m_hold  = max(1, round(fps / m_fps))

        f = start_f

        # TRANSITION_OUT (one-shot)
        for tf in trans_frames:
            for _ in range(t_hold):
                if f >= total_frames: break
                timeline[f] = {"state": TRANSITION_OUT, "sprite": tf,
                               "x": from_x, "y": from_y}
                f += 1

        # MOVE (ping-pong, interpolation linéaire)
        move_dur_f = int(mv.get("move_duration_seconds", 1.0) * fps)
        move_pp    = move_frames + move_frames[-2:0:-1]
        mi, mc = 0, 0
        for mf in range(move_dur_f):
            if f >= total_frames: break
            t_lerp = mf / max(move_dur_f - 1, 1)
            timeline[f] = {
                "state":  MOVE,
                "sprite": move_pp[mi % len(move_pp)],
                "x":      round(from_x + (to_x - from_x) * t_lerp),
                "y":      round(from_y + (to_y - from_y) * t_lerp),
            }
            mc += 1
            if mc >= m_hold:
                mc = 0
                mi += 1
            f += 1

        # TRANSITION_IN (one-shot, frames en ordre inverse)
        for tf in reversed(trans_frames):
            for _ in range(t_hold):
                if f >= total_frames: break
                timeline[f] = {"state": TRANSITION_IN, "sprite": tf,
                               "x": to_x, "y": to_y}
                f += 1

        # Mettre à jour la position des frames idle suivantes
        for ff in range(f, total_frames):
            if timeline[ff]["state"] == IDLE:
                timeline[ff]["x"] = to_x
                timeline[ff]["y"] = to_y

    return timeline


def fill_idle_positions(timeline: list[dict], default_x: int, default_y: int,
                        moves_cfg: list[dict], fps: int):
    """Remplit les x/y des frames IDLE restées à 0,0."""
    checkpoints = [(0, default_x, default_y)]
    for mv in sorted(moves_cfg, key=lambda m: m["at_second"]):
        checkpoints.append((
            int(mv["at_second"] * fps),
            mv["to_position"]["x"],
            mv["to_position"]["y"],
        ))

    ti, cur_x, cur_y = 0, default_x, default_y
    for f, entry in enumerate(timeline):
        while ti + 1 < len(checkpoints) and f >= checkpoints[ti + 1][0]:
            ti   += 1
            cur_x = checkpoints[ti][1]
            cur_y = checkpoints[ti][2]
        if entry["state"] == IDLE and entry["x"] == 0 and entry["y"] == 0:
            entry["x"] = cur_x
            entry["y"] = cur_y


# ---------------------------------------------------------------------------
# Transform utilitaire (scale + flip + rotation)
# ---------------------------------------------------------------------------

def transform_img(img, scale: float, flip_x: bool, rotation: float = 0.0):
    """
    Transformations dans l'ordre : resize → rotation → flip_x.

    Cet ordre correspond exactement au modèle Figma :
      1. L'image est d'abord redimensionnée.
      2. La rotation est appliquée sur l'orientation ORIGINALE de l'image
         (sens horaire, comme Figma). Pillow rotate étant anti-horaire,
         on passe -rotation.
      3. Le flip horizontal est appliqué EN DERNIER sur le résultat rotaté,
         ce qui évite que le flip inverse l'axe de rotation.
    """
    if scale != 1.0:
        w = max(1, round(img.width  * scale))
        h = max(1, round(img.height * scale))
        img = img.resize((w, h), Image.LANCZOS)
    if rotation:
        img = img.rotate(-rotation, resample=Image.BICUBIC, expand=True)
    if flip_x:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return img


# ---------------------------------------------------------------------------
# Résolution du config overlay par frame (héritage default + surcharge)
# ---------------------------------------------------------------------------

def resolve_overlay_cfg(anim_cfg: dict, base_filename: str, overlay_key: str) -> dict:
    """
    Retourne le dict de config d'un overlay (eyes ou mouth) pour une frame donnée.

    Logique :
      1. Partir du `default[overlay_key]` de la section d'animation.
      2. Si une entrée de `frames_config` correspond à base_filename,
         fusionner récursivement ses valeurs (seules les clés présentes écrasent).
    """
    default_cfg = dict(anim_cfg.get("default", {}).get(overlay_key, {}))
    # Copie profonde de l'anchor pour ne pas muter le default
    if "anchor" in default_cfg:
        default_cfg["anchor"] = dict(default_cfg["anchor"])

    for fc in anim_cfg.get("frames_config", []):
        if fc.get("frame") == base_filename and overlay_key in fc:
            override = fc[overlay_key]
            # Fusion : anchor est lui-même un dict, on le merge
            if "anchor" in override:
                default_cfg.setdefault("anchor", {})
                default_cfg["anchor"] = {**default_cfg["anchor"], **override["anchor"]}
            for k, v in override.items():
                if k != "anchor":
                    default_cfg[k] = v
            break

    return default_cfg


# ---------------------------------------------------------------------------
# Composite d'un personnage pour une frame
# ---------------------------------------------------------------------------

def composite_character(
    character_id:  str,
    position:      int,
    char_settings: dict,
    state:         str,
    eye_emotion:   str,
    mouth_emotion: str,
    base_file:     str,
    eye_file:      str,
    mouth_file:    str,
    char_scale:    float,
    overlay_scale: float,
    global_flip_x: bool,
) -> Image.Image:
    """
    Assemble base_sprite + yeux + bouche.

    Flip X — priorité décroissante :
      1. global_flip_x (episode-settings, clé "flip_x" au niveau du personnage)
         → surcharge toutes les couches si True.
      2. flip_x par couche dans character-settings :
         anim_cfg["flip_x"]       pour le sprite de base
         eye_cfg["flip_x"]        pour les yeux
         mouth_cfg["flip_x"]      pour la bouche

    Quand la base est flippée, les anchors x des overlays sont recalculés
    automatiquement (miroir horizontal) pour rester cohérents :
        ex_flipped = base_width - ex_original - overlay_width
    """
    pos_cfg  = char_settings["positions"][str(position)]
    anim_key = STATE_TO_ANIM_KEY[state]
    anim_cfg = pos_cfg[anim_key]
    base_dir = STATE_TO_BASE_DIR[state]

    # Résolution du config overlay pour cette frame précise
    eye_cfg   = resolve_overlay_cfg(anim_cfg, base_file, "eyes")
    mouth_cfg = resolve_overlay_cfg(anim_cfg, base_file, "mouth")

    eye_anchor   = eye_cfg.get("anchor",   {})
    mouth_anchor = mouth_cfg.get("anchor", {})
    eye_scale    = float(eye_cfg.get("scale",   1.0))
    mouth_scale  = float(mouth_cfg.get("scale", 1.0))
    eye_rot      = float(eye_cfg.get("rotation",   0.0))
    mouth_rot    = float(mouth_cfg.get("rotation", 0.0))

    # Résolution flip_x par couche (global_flip_x prend le dessus si True)
    base_flip  = global_flip_x or bool(anim_cfg.get("flip_x",       False))
    eye_flip   = global_flip_x or bool(eye_cfg.get("flip_x",        False))
    mouth_flip = global_flip_x or bool(mouth_cfg.get("flip_x",      False))

    # --- Sprite de base ---
    base_path = pos_dir(character_id, position) / base_dir / base_file
    if not base_path.exists():
        sys.exit(f"[ERREUR] Sprite introuvable : {base_path}")
    base   = transform_img(Image.open(base_path).convert("RGBA"), char_scale, base_flip)
    base_w = base.width

    # --- Yeux ---
    ep = eye_img_path(character_id, position, state, eye_emotion, eye_file)
    if ep.exists():
        eye_img = transform_img(Image.open(ep).convert("RGBA"),
                                char_scale * overlay_scale * eye_scale,
                                eye_flip, eye_rot)
        ex_raw = round(eye_anchor.get("x", 0) * char_scale)
        ex = (base_w - ex_raw - eye_img.width) if base_flip else ex_raw
        ey = round(eye_anchor.get("y", 0) * char_scale)
        base.paste(eye_img, (ex, ey), eye_img)
    else:
        print(f"  [WARN] Yeux introuvables : {ep}")

    # --- Bouche ---
    mp = mouth_img_path(character_id, position, state, mouth_emotion, mouth_file)
    if mp.exists():
        mouth_img = transform_img(Image.open(mp).convert("RGBA"),
                                  char_scale * overlay_scale * mouth_scale,
                                  mouth_flip, mouth_rot)
        mx_raw = round(mouth_anchor.get("x", 0) * char_scale)
        mx = (base_w - mx_raw - mouth_img.width) if base_flip else mx_raw
        my = round(mouth_anchor.get("y", 0) * char_scale)
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

    bg_path = EPISODES_IMAGES_DIR / episode["background"]["image"]
    if not bg_path.exists():
        sys.exit(f"[ERREUR] Background introuvable : {bg_path}")
    background = Image.open(bg_path).convert("RGBA").resize((width, height))

    char_data = []
    for char_cfg in episode["characters"]:
        character_id  = char_cfg["character"]
        position      = char_cfg["position"]
        char_settings = load_character_settings(character_id)
        pos_cfg       = char_settings["positions"][str(position)]

        moves_cfg   = char_cfg.get("moves", [])
        default_x   = char_cfg["screen_position"]["x"]
        default_y   = char_cfg["screen_position"]["y"]
        char_scale  = float(char_cfg.get("scale",         1.0))
        over_scale  = float(char_cfg.get("overlay_scale", 1.0))
        global_flip = bool(char_cfg.get("flip_x",         False))

        # Clignement défini au niveau de la position (partagé entre états)
        blink_cfg  = pos_cfg["blink"]
        idle_vis   = pos_cfg["idle"]["default"]["mouth"].get("idle_viseme", "CLOSED.png")

        timeline = build_move_timeline(moves_cfg, total_frames, fps,
                                       char_settings, position)
        fill_idle_positions(timeline, default_x, default_y, moves_cfg, fps)

        eye_seq   = build_eye_sequence(blink_cfg, total_frames, fps)
        mouth_seq = build_mouth_sequence(idle_vis, total_frames)

        char_data.append({
            "cfg":           char_cfg,
            "settings":      char_settings,
            "timeline":      timeline,
            "eye_seq":       eye_seq,
            "mouth_seq":     mouth_seq,
            "char_scale":    char_scale,
            "overlay_scale": over_scale,
            "global_flip_x": global_flip,
        })

    pad = len(str(total_frames))
    for f in range(total_frames):
        frame = background.copy()

        for cd in char_data:
            char_cfg = cd["cfg"]
            tl       = cd["timeline"][f]

            sprite = composite_character(
                character_id  = char_cfg["character"],
                position      = char_cfg["position"],
                char_settings = cd["settings"],
                state         = tl["state"],
                eye_emotion   = char_cfg["emotions"]["eyes"],
                mouth_emotion = char_cfg["emotions"]["mouth"],
                base_file     = tl["sprite"],
                eye_file      = cd["eye_seq"][f],
                mouth_file    = cd["mouth_seq"][f],
                char_scale    = cd["char_scale"],
                overlay_scale = cd["overlay_scale"],
                global_flip_x = cd["global_flip_x"],
            )

            frame.paste(sprite, (tl["x"], tl["y"]), sprite)

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
