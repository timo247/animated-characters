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
    scale_final_overlay = char_scale x overlay_scale x anim_cfg["eye_layers"|"mouth"|"pupils"]["scale"]

Assets yeux / bouche / pupilles par type d'animation :
    idle        -> positions/{N}/idles/eyes/{EMOTION}/
                   positions/{N}/idles/mouths/{EMOTION}/
    transitions -> positions/{N}/transitions/eyes/{EMOTION}/
                   positions/{N}/transitions/mouths/{EMOTION}/
    moves       -> positions/{N}/moves/eyes/{EMOTION}/
                   positions/{N}/moves/mouths/{EMOTION}/

Structure des calques oculaires (dans eyes/{EMOTION}/) :
    FULL.png          -> blanc de l'oeil, toujours present
    PUPILS.png        -> pupille, positionnee via pupils_anchor + offset + gaze
    UPPER-EYELID.png  -> paupiere superieure
    LOWER-EYELID.png  -> paupiere inferieure

Composition selon l'etat oculaire (blink sequence) :
    OPEN.png      -> FULL + PUPILS
    HALF-OPEN.png -> FULL + PUPILS + UPPER-EYELID
    CLOSED.png    -> FULL + PUPILS + UPPER-EYELID + LOWER-EYELID

Tous les calques (sauf PUPILS) partagent le meme anchor que eye_layers.
PUPILS est positionne via pupils.anchor (relatif a eye_layers.anchor) + offset + gaze.

Machine a etats :
    idle --> transition_out --> move --> transition_in --> idle

Changement de position :
    Ajouter "to_position_id": N dans un move pour que le personnage passe
    a la position N apres la transition_in. Les assets (sprites, yeux, bouche)
    et la configuration (blink, idle_viseme) sont rechargees automatiquement.

Flip X par phase :
    flip_x peut etre un booleen (legacy) ou un dict :
      { "idle_before": bool, "move": bool, "idle_after": bool }
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

# Fichiers de calques oculaires
EYE_LAYER_FILES = {
    "full":         "FULL.png",
    "pupils":       "PUPILS.png",
    "upper_eyelid": "UPPER-EYELID.png",
    "lower_eyelid": "LOWER-EYELID.png",
}

# Calques a dessiner selon l'etat oculaire (ordre de composition)
EYE_STATE_LAYERS = {
    "OPEN.png":      ["full", "pupils"],
    "HALF-OPEN.png": ["full", "pupils", "upper_eyelid"],
    "CLOSED.png":    ["full", "pupils", "upper_eyelid", "lower_eyelid"],
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

def eye_layer_path(character_id, position, state, emotion, layer_filename):
    """
    Chemin vers un calque oculaire individuel.
    positions/{N}/{idles|transitions|moves}/eyes/{EMOTION}/{layer_filename}
    """
    base   = pos_dir(character_id, position)
    subdir = STATE_TO_BASE_DIR[state]
    return base / subdir / "eyes" / emotion / layer_filename

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
    Retourne une liste de noms de fichier (ex: "OPEN.png", "HALF-OPEN.png"...)
    qui determine quels calques sont composes pour chaque frame.
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

def _normalize_segments(mv, current_position):
    """
    Normalise un move en liste de segments canoniques :
      { "position", "from_x", "from_y", "to_x", "to_y", "duration_seconds", "flip_x" }

    "duration_seconds" vaut None si non defini — le calcul de la duree naturelle
    (longueur de la sequence moves du character-settings / move_fps) est fait
    dans build_move_timeline ou char_settings est disponible.

    Supporte deux syntaxes :
      - Nouvelle : mv["segments"] = [ { position, from, to, duration_seconds }, ... ]
      - Legacy   : mv avec from_position / to_position / move_duration_seconds / to_position_id
    """
    if "segments" in mv:
        result = []
        for seg in mv["segments"]:
            result.append({
                "position":         seg["position"],
                "from_x":           seg["from"]["x"],
                "from_y":           seg["from"]["y"],
                "to_x":             seg["to"]["x"],
                "to_y":             seg["to"]["y"],
                "duration_seconds": seg.get("duration_seconds", None),
                "flip_x":           seg.get("flip_x", None),
                "reverse":          seg.get("reverse", False),
                "skip_transition":  seg.get("skip_transition", False),
                "gaze_invert_x":    seg.get("gaze_invert_x", None),
                "gaze_invert_y":    seg.get("gaze_invert_y", None),
            })
        return result
    else:
        return [{
            "position":         current_position,
            "from_x":           mv["from_position"]["x"],
            "from_y":           mv["from_position"]["y"],
            "to_x":             mv["to_position"]["x"],
            "to_y":             mv["to_position"]["y"],
            "duration_seconds": mv.get("move_duration_seconds", None),
            "flip_x":           None,
            "reverse":          False,
            "skip_transition":  False,
            "gaze_invert_x":    None,
            "gaze_invert_y":    None,
        }]


def _natural_move_duration(pos_cfg_seg, m_fps, video_fps):
    """
    Duree naturelle du move = exactement N frames moves x m_hold frames vidéo.
    Calculee en frames vidéo pour eviter les erreurs d'arrondi float.

    Exemple : 4 frames moves, m_fps=10, video_fps=24
              m_hold = round(24/10) = 2
              duree = 4 * 2 = 8 frames vidéo = 8/24 s
    """
    move_frames = pos_cfg_seg["moves"]["frames"]
    m_hold = max(1, round(video_fps / m_fps))
    return len(move_frames) * m_hold  # en frames vidéo entières


def build_move_timeline(moves_cfg, total_frames, fps, char_settings, position):
    """
    Retourne une liste de total_frames dicts :
      { "state", "sprite", "x", "y", "flip_phase", "position", "_positioned" }

    Supporte la syntaxe "segments" (nouvelle) et l'ancienne syntaxe
    from_position/to_position/to_position_id (legacy).

    Pour chaque segment :
        transition_out (assets position du segment)
        -> move (lerp from -> to)
        -> transition_in (assets position du segment)
        -> [segment suivant ou idle final]

    flip_phase : "idle_before" | "move" | "idle_after"
    """
    pos_cfg      = char_settings["positions"][str(position)]
    idle_frames  = pos_cfg["idle"]["frames"]
    idle_fps_cfg = pos_cfg["idle"].get("fps", 8)

    timeline = [
        {"state": IDLE, "sprite": None, "x": 0, "y": 0,
         "flip_phase": "idle_before", "position": position, "_positioned": False}
        for _ in range(total_frames)
    ]

    # Remplissage idle initial (position de depart)
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

    current_position = position

    for mv in sorted(moves_cfg, key=lambda m: m["at_second"]):
        # FPS globaux au move
        t_fps_global = mv.get("transition_fps", None)
        m_fps_global = mv.get("move_fps",       None)

        segments = _normalize_segments(mv, current_position)
        f        = int(mv["at_second"] * fps)

        for seg_idx, seg in enumerate(segments):
            seg_pos   = seg["position"]
            from_x    = seg["from_x"]
            from_y    = seg["from_y"]
            to_x      = seg["to_x"]
            to_y      = seg["to_y"]
            seg_flip_x      = seg["flip_x"]
            reverse         = seg.get("reverse", False)
            skip_transition = seg.get("skip_transition", False)
            seg_gaze_inv_x  = seg.get("gaze_invert_x", None)
            seg_gaze_inv_y  = seg.get("gaze_invert_y", None)

            pos_cfg_seg      = char_settings["positions"][str(seg_pos)]
            trans_frames_seg = pos_cfg_seg["transitions"]["frames"]
            move_frames_seg  = pos_cfg_seg["moves"]["frames"]

            # Inverser les sequences si reverse=True
            if reverse:
                trans_out_seq = list(reversed(trans_frames_seg))
                move_seq_base = list(reversed(move_frames_seg))
                trans_in_seq  = list(trans_frames_seg)
            else:
                trans_out_seq = list(trans_frames_seg)
                move_seq_base = list(move_frames_seg)
                trans_in_seq  = list(reversed(trans_frames_seg))

            t_fps = t_fps_global if t_fps_global is not None else pos_cfg_seg["transitions"].get("fps", 8)
            m_fps = m_fps_global if m_fps_global is not None else pos_cfg_seg["moves"].get("fps", 10)
            t_hold = max(1, round(fps / t_fps))
            m_hold = max(1, round(fps / m_fps))

            # Duree du move : explicite (secondes) ou naturelle (frames vidéo exactes)
            if seg["duration_seconds"] is not None:
                move_dur_f = max(1, int(seg["duration_seconds"] * fps))
            else:
                move_dur_f = _natural_move_duration(pos_cfg_seg, m_fps, fps)

            # --- Transition out ---
            if not skip_transition:
                for tf in trans_out_seq:
                    for _ in range(t_hold):
                        if f >= total_frames:
                            break
                        timeline[f] = {
                            "state":          TRANSITION_OUT,
                            "sprite":         tf,
                            "x":              from_x,
                            "y":              from_y,
                            "flip_phase":     "move",
                            "position":       seg_pos,
                            "seg_flip_x":     seg_flip_x,
                            "seg_gaze_inv_x": seg_gaze_inv_x,
                            "seg_gaze_inv_y": seg_gaze_inv_y,
                            "_positioned":    True,
                        }
                        f += 1

            # --- Move (lerp from -> to) ---
            if move_dur_f > 0:
                mi, mc = 0, 0
                for mf in range(move_dur_f):
                    if f >= total_frames:
                        break
                    t_lerp = mf / max(move_dur_f - 1, 1)
                    timeline[f] = {
                        "state":          MOVE,
                        "sprite":         move_seq_base[mi % len(move_seq_base)],
                        "x":              round(from_x + (to_x - from_x) * t_lerp),
                        "y":              round(from_y + (to_y - from_y) * t_lerp),
                        "flip_phase":     "move",
                        "position":       seg_pos,
                        "seg_flip_x":     seg_flip_x,
                        "seg_gaze_inv_x": seg_gaze_inv_x,
                        "seg_gaze_inv_y": seg_gaze_inv_y,
                        "_positioned":    True,
                    }
                    mc += 1
                    if mc >= m_hold:
                        mc = 0
                        mi += 1
                    f += 1

            # --- Transition in (dernier segment uniquement) ---
            is_last_seg = (seg_idx == len(segments) - 1)
            if is_last_seg and not skip_transition:
                for tf in trans_in_seq:
                    for _ in range(t_hold):
                        if f >= total_frames:
                            break
                        timeline[f] = {
                            "state":          TRANSITION_IN,
                            "sprite":         tf,
                            "x":              to_x,
                            "y":              to_y,
                            "flip_phase":     "move",
                            "position":       seg_pos,
                            "seg_flip_x":     seg_flip_x,
                            "seg_gaze_inv_x": seg_gaze_inv_x,
                            "seg_gaze_inv_y": seg_gaze_inv_y,
                            "_positioned":    True,
                        }
                        f += 1

            current_position = seg_pos

        # --- Idle final apres tous les segments ---
        last_seg      = segments[-1]
        final_pos     = last_seg["position"]
        final_x       = last_seg["to_x"]
        final_y       = last_seg["to_y"]
        final_reverse = last_seg.get("reverse", False)
        pos_cfg_final = char_settings["positions"][str(final_pos)]
        idle_frames_f = pos_cfg_final["idle"]["frames"]
        idle_fps_f    = pos_cfg_final["idle"].get("fps", idle_fps_cfg)
        idle_hold_f   = max(1, round(fps / idle_fps_f))
        # Idle en ping-pong normal, ou inversé si dernier segment reverse
        idle_base     = list(reversed(idle_frames_f)) if final_reverse else list(idle_frames_f)
        idle_pp_f     = idle_base + idle_base[-2:0:-1]
        ii, ic = 0, 0
        for ff in range(f, total_frames):
            if timeline[ff]["state"] == IDLE:
                timeline[ff] = {
                    "state":       IDLE,
                    "sprite":      idle_pp_f[ii % len(idle_pp_f)],
                    "x":           final_x,
                    "y":           final_y,
                    "flip_phase":  "idle_after",
                    "position":    final_pos,
                    "_positioned": True,
                }
                ic += 1
                if ic >= idle_hold_f:
                    ic = 0
                    ii += 1

    return timeline


def fill_idle_positions(timeline, default_x, default_y, moves_cfg, fps):
    """
    Remplit x/y des frames idle initiales (non encore positionnees).
    Les frames marquees _positioned=True (ecrites par build_move_timeline)
    sont ignorees.
    """
    checkpoints = [(0, default_x, default_y)]
    for mv in sorted(moves_cfg, key=lambda m: m["at_second"]):
        # Recupere la position finale du move (dernier segment ou syntaxe legacy)
        if "segments" in mv:
            last = mv["segments"][-1]
            checkpoints.append((int(mv["at_second"] * fps),
                                last["to"]["x"], last["to"]["y"]))
        else:
            checkpoints.append((int(mv["at_second"] * fps),
                                mv["to_position"]["x"], mv["to_position"]["y"]))
    ti, cur_x, cur_y = 0, default_x, default_y
    for f, entry in enumerate(timeline):
        while ti + 1 < len(checkpoints) and f >= checkpoints[ti + 1][0]:
            ti   += 1
            cur_x = checkpoints[ti][1]
            cur_y = checkpoints[ti][2]
        if entry["state"] == IDLE and not entry.get("_positioned", False):
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
    ep_gaze_invert_x=None,
    ep_gaze_invert_y=None,
):
    """
    Assemble les calques dans cet ordre :
        base -> FULL -> PUPILS -> UPPER-EYELID -> LOWER-EYELID -> mouth

    Les calques actifs dependent de eye_file (etat du clignement) :
        OPEN.png      -> FULL + PUPILS
        HALF-OPEN.png -> FULL + PUPILS + UPPER-EYELID
        CLOSED.png    -> FULL + PUPILS + UPPER-EYELID + LOWER-EYELID

    Tous les calques oculaires (sauf PUPILS) partagent l'anchor et le scale
    de eye_layers dans character-settings.

    PUPILS est positionne via :
        pupils.anchor (relatif a eye_layers.anchor) + pupils.offset + gaze_offset
    L'offset x du gaze est inverse automatiquement quand base_flip est actif.
    """
    pos_cfg  = char_settings["positions"][str(position)]
    anim_key = STATE_TO_ANIM_KEY[state]
    anim_cfg = pos_cfg[anim_key]
    base_dir = STATE_TO_BASE_DIR[state]

    gaze_invert_x = pos_cfg.get("gaze_invert_x", False)
    gaze_invert_y = pos_cfg.get("gaze_invert_y", False)

    # Override depuis l'épisode si défini (priorité absolue sur character-settings)
    if ep_gaze_invert_x is not None:
        gaze_invert_x = bool(ep_gaze_invert_x)
    if ep_gaze_invert_y is not None:
        gaze_invert_y = bool(ep_gaze_invert_y)

    # --- Configs ---
    eye_layers_cfg = resolve_overlay_cfg(anim_cfg, base_file, "eye_layers")
    pupils_cfg     = resolve_overlay_cfg(anim_cfg, base_file, "pupils")
    mouth_cfg      = resolve_overlay_cfg(anim_cfg, base_file, "mouth")

    eye_anchor    = eye_layers_cfg.get("anchor", {})
    pupils_anchor = pupils_cfg.get("anchor", {})
    pupils_offset = pupils_cfg.get("offset", {})
    mouth_anchor  = mouth_cfg.get("anchor",  {})

    eye_scale    = float(eye_layers_cfg.get("scale",    1.0))
    pupils_scale = float(pupils_cfg.get("scale",        1.0))
    mouth_scale  = float(mouth_cfg.get("scale",         1.0))
    eye_rot      = float(eye_layers_cfg.get("rotation", 0.0))
    mouth_rot    = float(mouth_cfg.get("rotation",      0.0))

    base_flip    = global_flip_x ^ bool(anim_cfg.get("flip_x",          False))
    eye_flip     = global_flip_x ^ bool(eye_layers_cfg.get("flip_x",    False))
    pupils_flip  = global_flip_x ^ bool(pupils_cfg.get("flip_x",        False))
    mouth_flip   = global_flip_x ^ bool(mouth_cfg.get("flip_x",         False))

    # --- Base ---
    base_path = pos_dir(character_id, position) / base_dir / base_file
    if not base_path.exists():
        sys.exit(f"[ERREUR] Sprite introuvable : {base_path}")
    base   = transform_img(Image.open(base_path).convert("RGBA"), char_scale, base_flip)
    base_w = base.width

    # --- Calques oculaires ---
    # Determine quels calques dessiner selon l'etat du clignement.
    # Fallback sur ["full", "pupils"] si eye_file inconnu.
    layers_to_draw = EYE_STATE_LAYERS.get(eye_file, ["full", "pupils"])

    # Precalcul de la position commune des calques non-pupils (en pixels vidéo)
    eye_anchor_x_raw = round(eye_anchor.get("x", 0) * char_scale)
    eye_anchor_y_raw = round(eye_anchor.get("y", 0) * char_scale)

    # Precalcul de la position pupils — ancré sur eye_anchor_x_raw déjà arrondi
    # pour éviter toute divergence d'arrondi entre FULL et PUPILS
    gaze_x, gaze_y = gaze_offset

    if ep_gaze_invert_x is not None:
        effective_gaze_x = (-gaze_x) if ep_gaze_invert_x else gaze_x
    else:
        effective_gaze_x = (-gaze_x) if (base_flip ^ gaze_invert_x) else gaze_x

    if ep_gaze_invert_y is not None:
        effective_gaze_y = (-gaze_y) if ep_gaze_invert_y else gaze_y
    else:
        effective_gaze_y = (-gaze_y) if gaze_invert_y else gaze_y

    pupils_abs_x_raw = (eye_anchor_x_raw
                        + round(pupils_anchor.get("x", 0) * char_scale)
                        + round(pupils_offset.get("x",  0) * char_scale)
                        + round(effective_gaze_x * char_scale))
    pupils_abs_y_raw = (eye_anchor_y_raw
                        + round(pupils_anchor.get("y", 0) * char_scale)
                        + round(pupils_offset.get("y",  0) * char_scale)
                        + round(effective_gaze_y * char_scale))

    for layer_key in layers_to_draw:
        layer_filename = EYE_LAYER_FILES[layer_key]
        lp = eye_layer_path(character_id, position, state, eye_emotion, layer_filename)

        if not lp.exists():
            print(f"  [WARN] Calque oculaire introuvable : {lp}")
            continue

        if layer_key == "pupils":
            layer_img = transform_img(
                Image.open(lp).convert("RGBA"),
                char_scale * overlay_scale * pupils_scale,
                pupils_flip,
                eye_rot,
            )
            px = (base_w - pupils_abs_x_raw - layer_img.width) if base_flip else pupils_abs_x_raw
            py = pupils_abs_y_raw
            base.paste(layer_img, (px, py), layer_img)

        else:
            layer_img = transform_img(
                Image.open(lp).convert("RGBA"),
                char_scale * overlay_scale * eye_scale,
                eye_flip, eye_rot,
            )
            lx = (base_w - eye_anchor_x_raw - layer_img.width) if base_flip else eye_anchor_x_raw
            ly = eye_anchor_y_raw
            base.paste(layer_img, (lx, ly), layer_img)

    # --- Mouth ---
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

        # Inversion du regard depuis l'épisode — override total du character-settings
        ep_gaze_invert_x = char_cfg.get("gaze_invert_x", None)
        ep_gaze_invert_y = char_cfg.get("gaze_invert_y", None)

        timeline = build_move_timeline(moves_cfg, total_frames, fps, char_settings, position)
        fill_idle_positions(timeline, default_x, default_y, moves_cfg, fps)

        eye_seq   = build_eye_sequence(blink_cfg, total_frames, fps)
        mouth_seq = build_mouth_sequence(idle_vis, total_frames, fps, viseme_timeline)

        # Detecter les changements de position pour le log
        positions_used = sorted({tl["position"] for tl in timeline})
        if len(positions_used) > 1:
            print(f"  [INFO] {character_id} -> changement de position : {positions_used}")

        char_data.append({
            "cfg":              char_cfg,
            "settings":         char_settings,
            "timeline":         timeline,
            "eye_seq":          eye_seq,
            "mouth_seq":        mouth_seq,
            "gaze_seq":         gaze_seq,
            "char_scale":       char_scale,
            "overlay_scale":    over_scale,
            "flip_cfg":         flip_cfg,
            "ep_gaze_invert_x": ep_gaze_invert_x,
            "ep_gaze_invert_y": ep_gaze_invert_y,
        })

    pad = len(str(total_frames))
    for f in range(total_frames):
        frame = background.copy()

        for cd in char_data:
            char_cfg    = cd["cfg"]
            tl          = cd["timeline"][f]
            flip_phase  = tl.get("flip_phase", "idle_before")
            seg_flip_x  = tl.get("seg_flip_x", None)

            # seg_flip_x defini sur le segment => override total du flip_cfg personnage
            # seg_flip_x absent (None) => comportement normal via flip_cfg + flip_phase
            if seg_flip_x is not None:
                global_flip = bool(seg_flip_x)
            else:
                global_flip = resolve_flip_x(cd["flip_cfg"], flip_phase)

            # La position est resolue depuis la timeline (peut avoir change)
            active_position = tl["position"]

            # Priorité : segment > épisode > character-settings
            frame_gaze_inv_x = tl.get("seg_gaze_inv_x", None)
            frame_gaze_inv_y = tl.get("seg_gaze_inv_y", None)
            if frame_gaze_inv_x is None:
                frame_gaze_inv_x = cd["ep_gaze_invert_x"]
            if frame_gaze_inv_y is None:
                frame_gaze_inv_y = cd["ep_gaze_invert_y"]

            sprite = composite_character(
                character_id     = char_cfg["character"],
                position         = active_position,
                char_settings    = cd["settings"],
                state            = tl["state"],
                eye_emotion      = char_cfg["emotions"]["eyes"],
                mouth_emotion    = char_cfg["emotions"]["mouth"],
                base_file        = tl["sprite"],
                eye_file         = cd["eye_seq"][f],
                mouth_file       = cd["mouth_seq"][f],
                char_scale       = cd["char_scale"],
                overlay_scale    = cd["overlay_scale"],
                global_flip_x    = global_flip,
                gaze_offset      = cd["gaze_seq"][f],
                ep_gaze_invert_x = frame_gaze_inv_x,
                ep_gaze_invert_y = frame_gaze_inv_y,
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
