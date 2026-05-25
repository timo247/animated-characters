#!/usr/bin/env python3
"""
render_episode.py
-----------------
Genere une video d'episode a partir d'un fichier de settings JSON.

Usage:
    python render_episode.py --settings episodes/episodes-settings/episode-settings.json

Systeme de coordonnees :
    Toutes les positions (x, y) = coin superieur gauche, convention Figma.

Hierarchie de scaling :
    scale_final_overlay = char_scale x overlay_scale x anim_cfg["eyes"|"mouth"|"pupils"]["scale"]

Assets yeux / bouche / pupilles par type d'animation :
    idle        -> positions/{N}/idles/eyes/{EMOTION}/
                   positions/{N}/idles/pupils/{EMOTION}/
                   positions/{N}/idles/mouths/{EMOTION}/
    transitions -> positions/{N}/transitions/eyes/{EMOTION}/
                   positions/{N}/transitions/pupils/{EMOTION}/
                   positions/{N}/transitions/mouths/{EMOTION}/
    moves       -> positions/{N}/moves/eyes/{EMOTION}/
                   positions/{N}/moves/pupils/{EMOTION}/
                   positions/{N}/moves/mouths/{EMOTION}/

Machine a etats :
    idle --> transition_out --> move --> transition_in --> idle

Flip X par phase :
    flip_x peut etre un booleen (legacy) ou un dict :
      { "idle_before": bool, "move": bool, "idle_after": bool }

Pupils :
    Le sprite "eyes" devient le fond du regard (GLANCE).
    Le sprite "pupils" est place par-dessus via pupils.anchor + pupils.offset
    dans character-settings, et gaze / gaze_timeline dans episode-settings.
    Si le fichier pupils est absent, la couche est silencieusement ignoree.
    L'offset x est inverse automatiquement quand base_flip est actif.
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

PROJECT_ROOT        = Path(__file__).parent.resolve()
EPISODES_IMAGES_DIR = PROJECT_ROOT / "episodes" / "images"
CHARACTERS_DIR      = PROJECT_ROOT / "characters"
DEFAULT_OUTPUT_DIR  = PROJECT_ROOT / "episodes" / "videos"

IDLE           = "idle"
TRANSITION_OUT = "transition_out"
MOVE           = "move"
TRANSITION_IN  = "transition_in"

STATE_TO_ANIM_KEY = {
    IDLE:           "idle",
    TRANSITION_OUT: "transitions",
    MOVE:           "moves",
    TRANSITION_IN:  "transitions",
}

STATE_TO_BASE_DIR = {
    IDLE:           "idles",
    TRANSITION_OUT: "transitions",
    MOVE:           "moves",
    TRANSITION_IN:  "transitions",
}


# ---------------------------------------------------------------------------
# Chargement des configs
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_episode_settings(settings_path):
    path = Path(settings_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        sys.exit(f"[ERREUR] Fichier de settings introuvable : {path}")
    return load_json(path)

def load_character_settings(character_id):
    path = CHARACTERS_DIR / character_id / "character-settings.json"
    if not path.exists():
        sys.exit(f"[ERREUR] character-settings.json introuvable : {path}")
    return load_json(path)


# ---------------------------------------------------------------------------
# Flip X par phase
# ---------------------------------------------------------------------------

def resolve_flip_x(flip_cfg, phase):
    """
    flip_cfg : bool (legacy) ou dict {idle_before, move, idle_after}
    phase    : "idle_before" | "move" | "idle_after"
    """
    if isinstance(flip_cfg, bool):
        return flip_cfg
    if isinstance(flip_cfg, dict):
        return bool(flip_cfg.get(phase, False))
    return False


# ---------------------------------------------------------------------------
# Chemins d'assets
# ---------------------------------------------------------------------------

def pos_dir(character_id, position):
    return CHARACTERS_DIR / character_id / "positions" / str(position)

def eye_img_path(character_id, position, state, emotion, filename):
    base = pos_dir(character_id, position)
    if state == IDLE:
        return base / "idles" / "eyes" / emotion / filename
    elif state in (TRANSITION_OUT, TRANSITION_IN):
        return base / "transitions" / "eyes" / emotion / filename
    else:
        return base / "moves" / "eyes" / emotion / filename

def pupils_img_path(character_id, position, state, emotion, filename):
    """
    positions/{N}/{state}/pupils/{EMOTION}/{filename}
    Retourne le chemin ; l'appelant verifie .exists() avant d'utiliser.
    """
    base = pos_dir(character_id, position)
    if state == IDLE:
        return base / "idles" / "pupils" / emotion / filename
    elif state in (TRANSITION_OUT, TRANSITION_IN):
        return base / "transitions" / "pupils" / emotion / filename
    else:
        return base / "moves" / "pupils" / emotion / filename

def mouth_img_path(character_id, position, state, emotion, filename):
    base = pos_dir(character_id, position)
    if state == IDLE:
        return base / "idles" / "mouths" / emotion / filename
    elif state in (TRANSITION_OUT, TRANSITION_IN):
        return base / "transitions" / "mouths" / emotion / filename
    else:
        return base / "moves" / "mouths" / emotion / filename


# ---------------------------------------------------------------------------
# Sequences temporelles
# ---------------------------------------------------------------------------

def build_eye_sequence(blink_cfg, total_frames, fps):
    """
    Sequence des fichiers yeux frame par frame (clignement aleatoire).
    La meme sequence est reutilisee pour pupils (meme index de frame).
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

def build_mouth_sequence(idle_viseme, total_frames, fps, viseme_timeline=None):
    seq = [idle_viseme] * total_frames
    if not viseme_timeline:
        return seq
    for entry in viseme_timeline:
        frame_start = int(entry["start"] * fps)
        frame_end   = min(int(entry["end"] * fps) + 1, total_frames)
        viseme_file = entry["viseme"] + ".png"
        for f in range(frame_start, frame_end):
            seq[f] = viseme_file
    return seq

def build_gaze_sequence(gaze_cfg, gaze_timeline, total_frames, fps):
    """
    Retourne une liste de total_frames tuples (offset_x, offset_y).

    Priorite :
      1. gaze_timeline : keyframes { "at_second", "x", "y" } interpoles lineairement
      2. gaze          : dict statique { "x", "y" }
      3. (0, 0)

    x positif = regard a droite, y positif = regard en bas.
    L'inversion pour le flip est geree dans composite_character.
    """
    static_x = int(gaze_cfg.get("x", 0)) if gaze_cfg else 0
    static_y = int(gaze_cfg.get("y", 0)) if gaze_cfg else 0

    if not gaze_timeline:
        return [(static_x, static_y)] * total_frames

    seq = [(static_x, static_y)] * total_frames
    kf  = sorted(gaze_timeline, key=lambda k: k["at_second"])

    for i, k in enumerate(kf):
        f_start = int(k["at_second"] * fps)
        x0, y0  = int(k["x"]), int(k["y"])
        if i + 1 < len(kf):
            f_end  = int(kf[i + 1]["at_second"] * fps)
            x1, y1 = int(kf[i + 1]["x"]), int(kf[i + 1]["y"])
            for f in range(f_start, min(f_end, total_frames)):
                t = (f - f_start) / max(f_end - f_start - 1, 1)
                seq[f] = (round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t))
        else:
            for f in range(f_start, total_frames):
                seq[f] = (x0, y0)

    return seq


# ---------------------------------------------------------------------------
# Machine a etats
# ---------------------------------------------------------------------------

def build_move_timeline(moves_cfg, total_frames, fps, char_settings, position):
    """
    Retourne une liste de total_frames dicts :
      { "state", "sprite", "x", "y", "flip_phase" }

    flip_phase : "idle_before" | "move" | "idle_after"
    """
    pos_cfg       = char_settings["positions"][str(position)]
    idle_frames   = pos_cfg["idle"]["frames"]
    idle_fps_cfg  = pos_cfg["idle"].get("fps", 8)
    trans_frames  = pos_cfg["transitions"]["frames"]
    trans_fps_cfg = pos_cfg["transitions"].get("fps", 8)
    move_frames   = pos_cfg["moves"]["frames"]
    move_fps_cfg  = pos_cfg["moves"].get("fps", 10)

    timeline = [
        {"state": IDLE, "sprite": None, "x": 0, "y": 0, "flip_phase": "idle_before"}
        for _ in range(total_frames)
    ]

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

        for tf in trans_frames:
            for _ in range(t_hold):
                if f >= total_frames: break
                timeline[f] = {"state": TRANSITION_OUT, "sprite": tf,
                               "x": from_x, "y": from_y, "flip_phase": "move"}
                f += 1

        move_dur_f = int(mv.get("move_duration_seconds", 1.0) * fps)
        move_pp    = move_frames + move_frames[-2:0:-1]
        mi, mc = 0, 0
        for mf in range(move_dur_f):
            if f >= total_frames: break
            t_lerp = mf / max(move_dur_f - 1, 1)
            timeline[f] = {
                "state":      MOVE,
                "sprite":     move_pp[mi % len(move_pp)],
                "x":          round(from_x + (to_x - from_x) * t_lerp),
                "y":          round(from_y + (to_y - from_y) * t_lerp),
                "flip_phase": "move",
            }
            mc += 1
            if mc >= m_hold:
                mc = 0
                mi += 1
            f += 1

        for tf in reversed(trans_frames):
            for _ in range(t_hold):
                if f >= total_frames: break
                timeline[f] = {"state": TRANSITION_IN, "sprite": tf,
                               "x": to_x, "y": to_y, "flip_phase": "move"}
                f += 1

        for ff in range(f, total_frames):
            if timeline[ff]["state"] == IDLE:
                timeline[ff]["x"]          = to_x
                timeline[ff]["y"]          = to_y
                timeline[ff]["flip_phase"] = "idle_after"

    return timeline


def fill_idle_positions(timeline, default_x, default_y, moves_cfg, fps):
    checkpoints = [(0, default_x, default_y)]
    for mv in sorted(moves_cfg, key=lambda m: m["at_second"]):
        checkpoints.append((int(mv["at_second"] * fps),
                            mv["to_position"]["x"], mv["to_position"]["y"]))
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
# Transform utilitaire
# ---------------------------------------------------------------------------

def transform_img(img, scale, flip_x, rotation=0.0):
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
# Resolution config overlay
# ---------------------------------------------------------------------------

def resolve_overlay_cfg(anim_cfg, base_filename, overlay_key):
    """
    Fusionne default[overlay_key] avec l'eventuelle surcharge dans frames_config.
    Supporte les sous-dicts "anchor" et "offset" (merge profond).
    """
    default_cfg = dict(anim_cfg.get("default", {}).get(overlay_key, {}))
    for sub in ("anchor", "offset"):
        if sub in default_cfg:
            default_cfg[sub] = dict(default_cfg[sub])

    for fc in anim_cfg.get("frames_config", []):
        if fc.get("frame") == base_filename and overlay_key in fc:
            override = fc[overlay_key]
            for sub in ("anchor", "offset"):
                if sub in override:
                    default_cfg.setdefault(sub, {})
                    default_cfg[sub] = {**default_cfg[sub], **override[sub]}
            for k, v in override.items():
                if k not in ("anchor", "offset"):
                    default_cfg[k] = v
            break

    return default_cfg


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

def composite_character(
    character_id, position, char_settings,
    state, eye_emotion, mouth_emotion,
    base_file, eye_file, mouth_file,
    char_scale, overlay_scale, global_flip_x,
    gaze_offset=(0, 0),
):
    """
    Assemble : base --> eyes (GLANCE) --> pupils --> mouth

    Pupils :
      - anchor  : coin centre-gauche du sprite yeux combine, en coords du sprite de base
      - offset  : decalage statique depuis character-settings
      - gaze_offset : decalage dynamique depuis episode-settings
      Quand base_flip est actif, gaze_x est inverse pour que le regard reste coherent.

    Si le fichier pupils n'existe pas pour cet etat/emotion, la couche est ignoree.
    """
    pos_cfg  = char_settings["positions"][str(position)]
    anim_key = STATE_TO_ANIM_KEY[state]
    anim_cfg = pos_cfg[anim_key]
    base_dir = STATE_TO_BASE_DIR[state]

    eye_cfg    = resolve_overlay_cfg(anim_cfg, base_file, "eyes")
    pupils_cfg = resolve_overlay_cfg(anim_cfg, base_file, "pupils")
    mouth_cfg  = resolve_overlay_cfg(anim_cfg, base_file, "mouth")

    eye_anchor    = eye_cfg.get("anchor",    {})
    pupils_anchor = pupils_cfg.get("anchor", {})
    pupils_offset = pupils_cfg.get("offset", {})
    mouth_anchor  = mouth_cfg.get("anchor",  {})

    eye_scale    = float(eye_cfg.get("scale",    1.0))
    pupils_scale = float(pupils_cfg.get("scale", 1.0))
    mouth_scale  = float(mouth_cfg.get("scale",  1.0))
    eye_rot      = float(eye_cfg.get("rotation",   0.0))
    mouth_rot    = float(mouth_cfg.get("rotation", 0.0))

    base_flip   = global_flip_x ^ bool(anim_cfg.get("flip_x",   False))
    eye_flip    = global_flip_x ^ bool(eye_cfg.get("flip_x",    False))
    pupils_flip = global_flip_x ^ bool(pupils_cfg.get("flip_x", False))
    mouth_flip  = global_flip_x ^ bool(mouth_cfg.get("flip_x",  False))

    # Base
    base_path = pos_dir(character_id, position) / base_dir / base_file
    if not base_path.exists():
        sys.exit(f"[ERREUR] Sprite introuvable : {base_path}")
    base   = transform_img(Image.open(base_path).convert("RGBA"), char_scale, base_flip)
    base_w = base.width

    # Eyes (GLANCE)
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

    # Pupils
    # Position finale (en coords du sprite de base, avant char_scale) :
    #   pupils_abs_x = eye_anchor.x + pupils_anchor.x + pupils_offset.x + gaze_x
    #   pupils_abs_y = eye_anchor.y + pupils_anchor.y + pupils_offset.y + gaze_y
    # pupils_anchor est donc RELATIF a eye_anchor (pas absolu).
    # pupils_offset est relatif a l'anchor calcule (eye_anchor + pupils_anchor).
    # gaze_offset est l'offset dynamique depuis episode-settings.
    pp = pupils_img_path(character_id, position, state, eye_emotion, eye_file)
    if pp.exists():
        pupils_img = transform_img(Image.open(pp).convert("RGBA"),
                                   char_scale * overlay_scale * pupils_scale,
                                   pupils_flip)
        gaze_x, gaze_y = gaze_offset
        # Inversion de l'offset x quand le sprite est flippe
        effective_gaze_x = (-gaze_x) if base_flip else gaze_x

        abs_x = (eye_anchor.get("x", 0)
                 + pupils_anchor.get("x", 0)
                 + pupils_offset.get("x", 0)
                 + effective_gaze_x)
        abs_y = (eye_anchor.get("y", 0)
                 + pupils_anchor.get("y", 0)
                 + pupils_offset.get("y", 0)
                 + gaze_y)

        px_raw = round(abs_x * char_scale)
        px = (base_w - px_raw - pupils_img.width) if base_flip else px_raw
        py = round(abs_y * char_scale)
        base.paste(pupils_img, (px, py), pupils_img)
    # Pas de WARN : pupils est optionnel

    # Mouth
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
# Rendu
# ---------------------------------------------------------------------------

def render_frames(episode, frames_dir, visemes_data=None):
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

        moves_cfg  = char_cfg.get("moves", [])
        default_x  = char_cfg["screen_position"]["x"]
        default_y  = char_cfg["screen_position"]["y"]
        char_scale = float(char_cfg.get("scale",         1.0))
        over_scale = float(char_cfg.get("overlay_scale", 1.0))
        flip_cfg   = char_cfg.get("flip_x", False)

        blink_cfg = pos_cfg["blink"]
        idle_vis  = pos_cfg["idle"]["default"]["mouth"].get("idle_viseme", "CLOSED.png")

        speaker         = char_cfg.get("speaker")
        viseme_timeline = None
        if speaker and visemes_data:
            viseme_timeline = visemes_data.get(speaker)
            if viseme_timeline is None:
                print(f"  [WARN] Speaker '{speaker}' introuvable dans le fichier visemes.")
            else:
                print(f"  [INFO] {character_id} -> speaker '{speaker}' "
                      f"({len(viseme_timeline)} visemes charges)")

        gaze_cfg      = char_cfg.get("gaze")
        gaze_timeline = char_cfg.get("gaze_timeline")
        gaze_seq      = build_gaze_sequence(gaze_cfg, gaze_timeline, total_frames, fps)
        if gaze_cfg or gaze_timeline:
            print(f"  [INFO] {character_id} -> gaze actif "
                  f"({'timeline' if gaze_timeline else 'statique'})")

        timeline = build_move_timeline(moves_cfg, total_frames, fps, char_settings, position)
        fill_idle_positions(timeline, default_x, default_y, moves_cfg, fps)

        eye_seq   = build_eye_sequence(blink_cfg, total_frames, fps)
        mouth_seq = build_mouth_sequence(idle_vis, total_frames, fps, viseme_timeline)

        char_data.append({
            "cfg":           char_cfg,
            "settings":      char_settings,
            "timeline":      timeline,
            "eye_seq":       eye_seq,
            "mouth_seq":     mouth_seq,
            "gaze_seq":      gaze_seq,
            "char_scale":    char_scale,
            "overlay_scale": over_scale,
            "flip_cfg":      flip_cfg,
        })

    pad = len(str(total_frames))
    for f in range(total_frames):
        frame = background.copy()

        for cd in char_data:
            char_cfg    = cd["cfg"]
            tl          = cd["timeline"][f]
            flip_phase  = tl.get("flip_phase", "idle_before")
            global_flip = resolve_flip_x(cd["flip_cfg"], flip_phase)

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
                global_flip_x = global_flip,
                gaze_offset   = cd["gaze_seq"][f],
            )

            frame.paste(sprite, (tl["x"], tl["y"]), sprite)

        out_path = frames_dir / f"frame_{str(f).zfill(pad)}.png"
        frame.convert("RGB").save(out_path)

        if f % fps == 0:
            print(f"  Frame {f + 1}/{total_frames}", end="\r")

    print(f"\n  {total_frames} frames generees.")
    return width, height


# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------

def assemble_video(frames_dir, output_path, fps, width, height):
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

    print(f"\n[FFMPEG] Assemblage -> {output_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[FFMPEG STDERR]", result.stderr)
        sys.exit("[ERREUR] ffmpeg a echoue.")
    print("[OK] Video generee :", output_path)


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def load_visemes(visemes_path):
    if not visemes_path:
        return None
    path = Path(visemes_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        sys.exit(f"[ERREUR] Fichier visemes introuvable : {path}")
    data = load_json(path)
    print(f"  [INFO] Visemes charges : {path.name} ({list(data.keys())} speakers)")
    return data


def main():
    parser = argparse.ArgumentParser(description="Render an episode from a JSON settings file.")
    parser.add_argument("--settings",    required=True)
    parser.add_argument("--visemes",     default=None)
    parser.add_argument("--keep-frames", action="store_true")
    args = parser.parse_args()

    episode      = load_episode_settings(args.settings)
    visemes_data = load_visemes(args.visemes)

    output_dir  = DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / episode["output"]["filename"]

    speakers = [c.get("speaker", "-") for c in episode["characters"]]
    print(f"[render_episode] Episode   : {episode['episode_id']}")
    print(f"  Resolution  : {episode['output']['resolution']}")
    print(f"  FPS         : {episode['output']['fps']}")
    print(f"  Duree       : {episode['output']['duration_seconds']}s")
    print(f"  Sortie      : {output_path}")
    print(f"  Personnages : {[c['character'] for c in episode['characters']]}")
    print(f"  Speakers    : {speakers}")
    if visemes_data:
        print(f"  Visemes     : {args.visemes}")

    with tempfile.TemporaryDirectory() as tmp:
        frames_dir = Path(tmp) / "frames"
        frames_dir.mkdir()

        print("\n[1/2] Rendu des frames...")
        width, height = render_frames(episode, frames_dir, visemes_data)

        print("\n[2/2] Assemblage ffmpeg...")
        assemble_video(frames_dir, output_path, episode["output"]["fps"], width, height)

        if args.keep_frames:
            kept = output_dir / "frames_debug"
            shutil.copytree(frames_dir, kept, dirs_exist_ok=True)
            print(f"[DEBUG] Frames conservees dans : {kept}")


if __name__ == "__main__":
    main()
