#!/usr/bin/env python3
"""
Viseme timeline generator — version améliorée.

Améliorations vs v1 :
  - Décomposition en syllabes → plusieurs visèmes par mot
  - Micro-fermetures entre voyelles ouvertes consécutives (O/A)
  - Durée de fermeture pondérée par le type de visème
  - Seuil MIN_OPEN_DURATION pour éviter de coller un O/A trop court
"""

import json
import argparse
import re
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# ── Paramètres globaux ───────────────────────────────────────────────────────

VISEME_OFFSET       = -0.04   # anticipation de l'animation
MICRO_PAUSE         = 0.04    # fermeture en fin de mot
WORD_GAP_THRESHOLD  = 0.06    # silence inter-mots → CLOSED
MIN_DURATION        = 0.04    # durée minimale d'un segment
MIN_OPEN_DURATION   = 0.06    # durée minimale pour afficher O/A (sinon CLOSED)

# Pourcentage de fermeture en FIN de chaque type de visème
CLOSE_RATIO: Dict[str, float] = {
    "O":    0.35,   # bouche ronde → ferme plus tôt
    "A":    0.30,   # bouche ouverte → ferme plus tôt
    "E":    0.20,
    "F":    0.15,
    "L":    0.15,
    "CONS": 0.10,
    "CLOSED": 0.0,
}

# Visèmes "ouverts" qui nécessitent une micro-fermeture entre eux
OPEN_VISEMES = {"O", "A"}

LABIAL_CONSONANTS = re.compile(r"^[mbp]", re.IGNORECASE)


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class VisemeEvent:
    start: float
    end:   float
    viseme: str


# ── Chargement des règles ────────────────────────────────────────────────────

def load_viseme_rules(path: str):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = [
        (re.compile(r["pattern"], re.IGNORECASE), r["viseme"])
        for r in data["rules"]
    ]
    return rules, data.get("default", "CONS")


# ── Normalisation ────────────────────────────────────────────────────────────

def normalize_word(word: str) -> str:
    return re.sub(r"[^\wàâäéèêëîïôöùûüÿœ'-]", "", word.lower())


# ── Correspondance phonème → visème ─────────────────────────────────────────

def chunk_to_viseme(chunk: str, rules, default: str) -> str:
    """Retourne le visème pour un fragment (syllabe ou mot entier)."""
    for pattern, v in rules:
        if pattern.search(chunk):
            return v
    return default


# ── Découpage en syllabes (heuristique française) ───────────────────────────

VOWELS_FR = "aàâäeéèêëiîïoôöuùûüyÿœ"

def split_syllables(word: str) -> List[str]:
    """
    Découpe heuristique en syllabes pour le français.
    Chaque syllabe contient au moins une voyelle.
    Si aucune voyelle n'est trouvée, retourne le mot entier.
    """
    w = normalize_word(word)
    if not w:
        return [word]

    # Trouver les positions de voyelles
    vowel_positions = [i for i, c in enumerate(w) if c in VOWELS_FR]
    if not vowel_positions:
        return [w]

    syllables = []
    prev = 0
    for idx, vpos in enumerate(vowel_positions):
        if idx == len(vowel_positions) - 1:
            # Dernière voyelle → tout le reste
            syllables.append(w[prev:])
        else:
            next_vpos = vowel_positions[idx + 1]
            consonants_between = next_vpos - vpos - 1
            if consonants_between == 0:
                # Deux voyelles consécutives : diphtongue → garder ensemble
                continue
            elif consonants_between == 1:
                # V-C-V → coupe après la voyelle courante
                cut = vpos + 1
            else:
                # V-CC…-V → coupe au milieu des consonnes
                cut = vpos + 1 + consonants_between // 2
            syllables.append(w[prev:cut])
            prev = cut

    # Nettoyage : fusionner les syllabes sans voyelle avec la précédente
    cleaned = []
    for syl in syllables:
        if any(c in VOWELS_FR for c in syl):
            cleaned.append(syl)
        elif cleaned:
            cleaned[-1] += syl
        else:
            cleaned.append(syl)

    return cleaned if cleaned else [w]


# ── Construction de la timeline ──────────────────────────────────────────────

def _safe_append(events: List[VisemeEvent], ev: VisemeEvent) -> None:
    """Ajoute un événement seulement s'il a une durée positive minimale."""
    if ev.end - ev.start >= MIN_DURATION:
        events.append(ev)


def build_word_events(
    start: float,
    end: float,
    text: str,
    rules,
    default: str,
    prev_viseme: Optional[str],
) -> List[VisemeEvent]:
    """
    Génère les VisemeEvents pour un seul mot.
    - Découpe en syllabes
    - Gère les consonnes labiales initiales
    - Insère des micro-fermetures entre visèmes ouverts consécutifs
    - Applique un close_ratio en fin de chaque syllabe
    """
    events: List[VisemeEvent] = []
    duration = end - start
    if duration < MIN_DURATION:
        return events

    norm = normalize_word(text)

    # ── Consonne labiale initiale (b/m/p) → courte fermeture ──────────────
    if LABIAL_CONSONANTS.search(norm):
        lip_d = min(0.04, duration * 0.25)
        _safe_append(events, VisemeEvent(start, start + lip_d, "CLOSED"))
        start += lip_d
        duration = end - start

    # ── Découpage en syllabes ──────────────────────────────────────────────
    syllables = split_syllables(text)
    n = len(syllables)
    cursor = start
    last_v = prev_viseme  # visème précédant le mot courant

    for i, syl in enumerate(syllables):
        syl_dur = duration / n          # durée égale par syllabe (heuristique)
        syl_start = cursor
        syl_end   = cursor + syl_dur

        viseme = chunk_to_viseme(syl, rules, default)

        # Micro-fermeture si deux visèmes ouverts se suivent
        if last_v in OPEN_VISEMES and viseme in OPEN_VISEMES:
            bridge = min(0.04, syl_dur * 0.2)
            _safe_append(events, VisemeEvent(syl_start, syl_start + bridge, "CLOSED"))
            syl_start += bridge

        # O/A trop courts → ne pas les afficher (CLOSED par défaut)
        if viseme in OPEN_VISEMES and (syl_end - syl_start) < MIN_OPEN_DURATION:
            viseme = "CLOSED"

        # Fermeture en fin de syllabe
        ratio = CLOSE_RATIO.get(viseme, 0.15)
        close_d = min(MICRO_PAUSE, (syl_end - syl_start) * ratio)
        main_end = max(syl_start + MIN_DURATION, syl_end - close_d)

        _safe_append(events, VisemeEvent(syl_start, main_end, viseme))
        if close_d >= MIN_DURATION:
            _safe_append(events, VisemeEvent(main_end, syl_end, "CLOSED"))

        last_v  = viseme
        cursor  = syl_end

    return events


def build_timeline(words: List[dict], rules, default: str) -> List[dict]:
    timeline: List[VisemeEvent] = []
    previous_end: Optional[float] = None
    last_viseme:  Optional[str]   = None

    for w in words:
        raw_start = max(0.0, w["start"] + VISEME_OFFSET)
        raw_end   = w["end"]
        text      = w["word"]

        if raw_end - raw_start < MIN_DURATION:
            continue

        # Silence inter-mots
        if previous_end is not None:
            gap = raw_start - previous_end
            if gap > WORD_GAP_THRESHOLD:
                timeline.append(VisemeEvent(previous_end, raw_start, "CLOSED"))
                last_viseme = "CLOSED"

        evs = build_word_events(raw_start, raw_end, text, rules, default, last_viseme)
        timeline.extend(evs)

        if evs:
            last_viseme = evs[-1].viseme
        previous_end = raw_end

    return [
        {"start": round(e.start, 3), "end": round(e.end, 3), "viseme": e.viseme}
        for e in timeline
    ]


# ── Point d'entrée ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Génère une timeline de visèmes à partir d'un JSON de transcription."
    )
    parser.add_argument("input_json",    help="Fichier JSON d'entrée (transcription)")
    parser.add_argument("output_json",   help="Fichier JSON de sortie (timeline visèmes)")
    parser.add_argument("viseme_config", help="Fichier JSON de mapping visèmes")
    args = parser.parse_args()

    rules, default = load_viseme_rules(args.viseme_config)
    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))

    speakers: Dict[str, List[dict]] = {}
    for segment in data.get("segments", []):
        for word in segment.get("words", []):
            spk = word.get("speaker", "UNKNOWN")
            speakers.setdefault(spk, []).append(word)

    output = {}
    for spk, words in speakers.items():
        output[spk] = build_timeline(words, rules, default)

    Path(args.output_json).write_text(
        json.dumps(output, indent=2, ensure_ascii=False)
    )
    print(f"✓ Timelines générées pour {len(output)} speaker(s).")
    for spk, evs in output.items():
        print(f"  {spk}: {len(evs)} événements")


if __name__ == "__main__":
    main()
