"""Deterministic multilingual templates used by the offline template provider.

Every template exists in English, Spanish and French, and (where it matters) in two voices:
``analyst`` (dense, numeric, tactical) and ``casual`` (short, plain language). These are also
the fallback text whenever a cloud model is slow, failing, or switched off.
"""

from __future__ import annotations

from typing import Any

LANGS = ("en", "es", "fr")

T: dict[str, dict[str, dict[str, str]]] = {
    "kickoff": {
        "analyst": {
            "en": "{minute}' Kick-off: {team} start in possession. {home} ({home_style}) v {away} ({away_style}).",
            "es": "{minute}' Saque inicial: {team} empieza con el balón. {home} ({home_style}) vs {away} ({away_style}).",
            "fr": "{minute}' Coup d'envoi : {team} a le ballon. {home} ({home_style}) contre {away} ({away_style}).",
        },
        "casual": {
            "en": "We're under way! {home} v {away}.",
            "es": "¡Arranca el partido! {home} contra {away}.",
            "fr": "C'est parti ! {home} contre {away}.",
        },
    },
    "goal": {
        "analyst": {
            "en": "{minute}' GOAL {team}: {player}, {dist} m, xG {xg}, {speed} km/h. {score}.",
            "es": "{minute}' GOL de {team}: {player}, {dist} m, xG {xg}, {speed} km/h. {score}.",
            "fr": "{minute}' BUT pour {team} : {player}, {dist} m, xG {xg}, {speed} km/h. {score}.",
        },
        "casual": {
            "en": "GOAL! {player} scores for {team}! {score}.",
            "es": "¡GOL! ¡{player} marca para {team}! {score}.",
            "fr": "BUT ! {player} marque pour {team} ! {score}.",
        },
    },
    "shot_saved": {
        "analyst": {
            "en": "{minute}' Save. {player} ({team}) from {dist} m, xG {xg}, {speed} km/h.",
            "es": "{minute}' Parada. {player} ({team}) desde {dist} m, xG {xg}, {speed} km/h.",
            "fr": "{minute}' Arrêt. {player} ({team}) à {dist} m, xG {xg}, {speed} km/h.",
        },
        "casual": {
            "en": "Great save to deny {player}!",
            "es": "¡Gran parada ante {player}!",
            "fr": "Superbe arrêt devant {player} !",
        },
    },
    "shot_off_target": {
        "analyst": {
            "en": "{minute}' Off target: {player} ({team}), {dist} m, xG {xg}.",
            "es": "{minute}' Fuera: {player} ({team}), {dist} m, xG {xg}.",
            "fr": "{minute}' Non cadré : {player} ({team}), {dist} m, xG {xg}.",
        },
        "casual": {
            "en": "{player} fires wide.",
            "es": "{player} la manda fuera.",
            "fr": "{player} tire à côté.",
        },
    },
    "shot_blocked": {
        "analyst": {
            "en": "{minute}' Blocked: {player} ({team}), {dist} m, xG {xg}.",
            "es": "{minute}' Bloqueado: {player} ({team}), {dist} m, xG {xg}.",
            "fr": "{minute}' Contré : {player} ({team}), {dist} m, xG {xg}.",
        },
        "casual": {
            "en": "{player}'s shot is blocked!",
            "es": "¡Bloquean el disparo de {player}!",
            "fr": "La frappe de {player} est contrée !",
        },
    },
    "big_chance": {
        "analyst": {
            "en": "{minute}' Big chance missed: {player}, xG {xg}. {team} now {team_xg} xG total.",
            "es": "{minute}' Ocasión clarísima fallada: {player}, xG {xg}. {team} suma {team_xg} xG.",
            "fr": "{minute}' Grosse occasion manquée : {player}, xG {xg}. {team} à {team_xg} xG.",
        },
        "casual": {
            "en": "So close! {player} should have scored there.",
            "es": "¡Uf! {player} tenía que marcar ahí.",
            "fr": "Si près ! {player} devait marquer.",
        },
    },
    "elite_pass": {
        "analyst": {
            "en": "{minute}' Elite pass: {player} to {receiver}, {dist} m under {pressure_pct}% pressure, difficulty {pdr}/10.",
            "es": "{minute}' Pase de élite: {player} a {receiver}, {dist} m con {pressure_pct}% de presión, dificultad {pdr}/10.",
            "fr": "{minute}' Passe d'élite : {player} pour {receiver}, {dist} m sous {pressure_pct}% de pression, difficulté {pdr}/10.",
        },
        "casual": {
            "en": "What a pass from {player}!",
            "es": "¡Qué pase de {player}!",
            "fr": "Quelle passe de {player} !",
        },
    },
    "tackle": {
        "analyst": {
            "en": "{minute}' Tackle won by {player} ({team}) in the {zone}.",
            "es": "{minute}' Entrada ganada por {player} ({team}) en {zone}.",
            "fr": "{minute}' Tacle gagné par {player} ({team}) dans {zone}.",
        },
        "casual": {
            "en": "Big tackle from {player}!",
            "es": "¡Gran entrada de {player}!",
            "fr": "Gros tacle de {player} !",
        },
    },
    "tag_sprint": {
        "analyst": {
            "en": "{player}: top speed {speed} km/h (threshold {threshold}).",
            "es": "{player}: velocidad máxima {speed} km/h (umbral {threshold}).",
            "fr": "{player} : pointe à {speed} km/h (seuil {threshold}).",
        },
        "casual": {
            "en": "{player} is flying: {speed} km/h!",
            "es": "¡{player} vuela: {speed} km/h!",
            "fr": "{player} est une fusée : {speed} km/h !",
        },
    },
    "tag_shot_speed": {
        "analyst": {
            "en": "{player}: shot speed {speed} km/h (threshold {threshold}).",
            "es": "{player}: disparo a {speed} km/h (umbral {threshold}).",
            "fr": "{player} : frappe à {speed} km/h (seuil {threshold}).",
        },
        "casual": {
            "en": "Rocket! {player} hit that at {speed} km/h.",
            "es": "¡Cañonazo! {player} le pegó a {speed} km/h.",
            "fr": "Missile ! {player} a frappé à {speed} km/h.",
        },
    },
    "milestone_distance": {
        "analyst": {
            "en": "{player} passes {km} km covered ({minute}').",
            "es": "{player} supera los {km} km recorridos ({minute}').",
            "fr": "{player} dépasse {km} km parcourus ({minute}').",
        },
        "casual": {
            "en": "{player} has run {km} km already!",
            "es": "¡{player} ya ha corrido {km} km!",
            "fr": "{player} a déjà couru {km} km !",
        },
    },
    "milestone_passes": {
        "analyst": {
            "en": "{team} reach {count} completed passes ({acc}% accuracy).",
            "es": "{team} llega a {count} pases completados ({acc}% de acierto).",
            "fr": "{team} atteint {count} passes réussies ({acc}% de réussite).",
        },
        "casual": {
            "en": "{team} are keeping the ball well.",
            "es": "{team} está cuidando bien el balón.",
            "fr": "{team} garde bien le ballon.",
        },
    },
    "momentum_swing": {
        "analyst": {
            "en": "Momentum swing to {team}: index {before} → {after} in {window} min.",
            "es": "Cambio de inercia a favor de {team}: índice {before} → {after} en {window} min.",
            "fr": "Bascule de momentum pour {team} : indice {before} → {after} en {window} min.",
        },
        "casual": {
            "en": "{team} are taking over this game.",
            "es": "{team} se está haciendo con el partido.",
            "fr": "{team} prend le contrôle du match.",
        },
    },
    "rhythm_control": {
        "analyst": {
            "en": "Control phase: {team} averaging {ppp} passes per possession, {turnovers} turnovers/min.",
            "es": "Fase de control: {team} promedia {ppp} pases por posesión, {turnovers} pérdidas/min.",
            "fr": "Phase de contrôle : {team} enchaîne {ppp} passes par possession, {turnovers} pertes/min.",
        },
        "casual": {
            "en": "{team} are calmly in charge right now.",
            "es": "{team} manda con calma ahora mismo.",
            "fr": "{team} maîtrise tranquillement le jeu.",
        },
    },
    "rhythm_chaos": {
        "analyst": {
            "en": "Chaos phase: {turnovers} turnovers/min, only {ppp} passes per possession.",
            "es": "Fase de caos: {turnovers} pérdidas/min, solo {ppp} pases por posesión.",
            "fr": "Phase de chaos : {turnovers} pertes/min, seulement {ppp} passes par possession.",
        },
        "casual": {
            "en": "It's end-to-end chaos out there!",
            "es": "¡Esto es un ida y vuelta caótico!",
            "fr": "C'est le chaos, ça va d'un but à l'autre !",
        },
    },
    "pressure_shift": {
        "analyst": {
            "en": "Pressing shift: {team} up to {after} pressures/min (from {before}).",
            "es": "Cambio de presión: {team} sube a {after} presiones/min (desde {before}).",
            "fr": "Changement de pressing : {team} passe à {after} pressions/min (contre {before}).",
        },
        "casual": {
            "en": "{team} are pressing much higher now.",
            "es": "{team} presiona mucho más arriba ahora.",
            "fr": "{team} presse beaucoup plus haut maintenant.",
        },
    },
    "half_time": {
        "analyst": {
            "en": "Half-time: {score}. Possession {poss}, xG {xg_line}, pass accuracy {acc_line}.",
            "es": "Descanso: {score}. Posesión {poss}, xG {xg_line}, acierto en el pase {acc_line}.",
            "fr": "Mi-temps : {score}. Possession {poss}, xG {xg_line}, réussite des passes {acc_line}.",
        },
        "casual": {
            "en": "Half-time: {score}.",
            "es": "Descanso: {score}.",
            "fr": "Mi-temps : {score}.",
        },
    },
    "full_time": {
        "analyst": {
            "en": "Full-time: {score}. xG {xg_line}. Possession {poss}.",
            "es": "Final: {score}. xG {xg_line}. Posesión {poss}.",
            "fr": "Fin du match : {score}. xG {xg_line}. Possession {poss}.",
        },
        "casual": {
            "en": "It's all over: {score}.",
            "es": "Se acabó: {score}.",
            "fr": "C'est terminé : {score}.",
        },
    },
    "fan_scored": {
        "casual": {
            "en": "YES! Your team scores! {score}",
            "es": "¡SÍ! ¡Gol de tu equipo! {score}",
            "fr": "OUI ! Ton équipe marque ! {score}",
        },
        "analyst": {
            "en": "Your club scores: {score} (xG {xg}).",
            "es": "Marca tu club: {score} (xG {xg}).",
            "fr": "Ton club marque : {score} (xG {xg}).",
        },
    },
    "fan_conceded": {
        "casual": {
            "en": "Ouch, your team concedes. {score}. Plenty of time left.",
            "es": "Vaya, encaja tu equipo. {score}. Queda tiempo.",
            "fr": "Aïe, ton équipe encaisse. {score}. Il reste du temps.",
        },
        "analyst": {
            "en": "Your club concedes: {score} (opponent xG {xg}).",
            "es": "Encaja tu club: {score} (xG rival {xg}).",
            "fr": "Ton club encaisse : {score} (xG adverse {xg}).",
        },
    },
}

# "Why it matters" fragments produced by the Explainer, voiced per language.
WHY: dict[str, dict[str, str]] = {
    "go_ahead": {
        "en": "It puts {team} in front.",
        "es": "Pone a {team} por delante.",
        "fr": "{team} passe devant.",
    },
    "equaliser": {
        "en": "It levels the match.",
        "es": "Iguala el partido.",
        "fr": "Les deux équipes sont à égalité.",
    },
    "extends_lead": {
        "en": "It doubles down on {team}'s advantage.",
        "es": "Amplía la ventaja de {team}.",
        "fr": "{team} creuse l'écart.",
    },
    "consolation": {
        "en": "It narrows the gap for {team}.",
        "es": "{team} recorta distancias.",
        "fr": "{team} réduit l'écart.",
    },
    "against_run": {
        "en": "It came against the run of play: momentum was {momentum} against them.",
        "es": "Llegó contra corriente: la inercia era de {momentum} en su contra.",
        "fr": "Il tombe contre le cours du jeu : momentum de {momentum} contre eux.",
    },
    "with_run": {
        "en": "It rewarded sustained pressure (momentum +{momentum}).",
        "es": "Premia una presión sostenida (inercia +{momentum}).",
        "fr": "Il récompense une domination (momentum +{momentum}).",
    },
    "buildup": {
        "en": "The move took {passes} passes over {secs} seconds.",
        "es": "La jugada tuvo {passes} pases en {secs} segundos.",
        "fr": "L'action a duré {passes} passes en {secs} secondes.",
    },
    "low_xg": {
        "en": "A low-percentage chance (xG {xg}): individual quality made it.",
        "es": "Ocasión de baja probabilidad (xG {xg}): calidad individual.",
        "fr": "Occasion à faible probabilité (xG {xg}) : du talent pur.",
    },
    "high_xg": {
        "en": "A high-quality chance (xG {xg}) created by the team.",
        "es": "Una ocasión de alta calidad (xG {xg}) creada por el equipo.",
        "fr": "Une occasion de grande qualité (xG {xg}) créée collectivement.",
    },
    "miss_cost": {
        "en": "Chances like this are scored {pct}% of the time.",
        "es": "Ocasiones así acaban en gol el {pct}% de las veces.",
        "fr": "Ce genre d'occasion finit au fond {pct}% du temps.",
    },
    "swing_pressure": {
        "en": "Their pressing rose to {press} per minute and the opponent's pass accuracy fell to {acc}%.",
        "es": "Su presión subió a {press} por minuto y el acierto rival cayó al {acc}%.",
        "fr": "Leur pressing est monté à {press} par minute et la réussite adverse est tombée à {acc}%.",
    },
    "swing_territory": {
        "en": "They have {share}% of final-third entries in the last {window} minutes.",
        "es": "Tienen el {share}% de las llegadas al último tercio en los últimos {window} minutos.",
        "fr": "Ils ont {share}% des entrées dans le dernier tiers sur les {window} dernières minutes.",
    },
    "control_why": {
        "en": "Long possessions starve the opponent of the ball and lower game tempo.",
        "es": "Las posesiones largas dejan al rival sin balón y bajan el ritmo.",
        "fr": "De longues possessions privent l'adversaire du ballon et ralentissent le jeu.",
    },
    "chaos_why": {
        "en": "Frequent turnovers create transitions, where defences are least organised.",
        "es": "Las pérdidas constantes generan transiciones, donde las defensas están peor organizadas.",
        "fr": "Les pertes de balle fréquentes créent des transitions, où les défenses sont désorganisées.",
    },
    "pressure_why": {
        "en": "Higher pressing forces rushed passes and wins the ball closer to goal.",
        "es": "Presionar más arriba fuerza pases precipitados y recupera más cerca del gol.",
        "fr": "Un pressing plus haut force des passes précipitées et récupère plus près du but.",
    },
    "elite_why": {
        "en": "Only {pct}% of passes like this are completed: it broke a line under pressure.",
        "es": "Solo el {pct}% de pases así se completan: rompió una línea bajo presión.",
        "fr": "Seules {pct}% de ces passes aboutissent : une ligne cassée sous pression.",
    },
}

LABELS: dict[str, dict[str, str]] = {
    "possession": {"en": "Possession", "es": "Posesión", "fr": "Possession"},
    "pass_accuracy": {"en": "Pass accuracy", "es": "Acierto en el pase", "fr": "Passes réussies"},
    "pass_difficulty": {
        "en": "Avg pass difficulty",
        "es": "Dificultad media del pase",
        "fr": "Difficulté moy. des passes",
    },
    "xg": {"en": "xG", "es": "xG", "fr": "xG"},
    "shots": {"en": "Shots", "es": "Disparos", "fr": "Tirs"},
    "distance_km": {"en": "Distance (km)", "es": "Distancia (km)", "fr": "Distance (km)"},
    "pressures": {"en": "Pressures", "es": "Presiones", "fr": "Pressions"},
    "top_shot_speed": {"en": "Top shot speed", "es": "Disparo más rápido", "fr": "Frappe la plus rapide"},
    "who_is_on_top": {"en": "Who's on top", "es": "Quién domina", "fr": "Qui domine"},
    "your_player": {"en": "Your player", "es": "Tu jugador", "fr": "Ton joueur"},
    "defensive_third": {"en": "defensive third", "es": "su tercio defensivo", "fr": "son tiers défensif"},
    "middle_third": {"en": "middle third", "es": "el medio campo", "fr": "le milieu de terrain"},
    "attacking_third": {"en": "final third", "es": "el último tercio", "fr": "le dernier tiers"},
    "balanced": {"en": "Evenly poised", "es": "Igualado", "fr": "Équilibré"},
    "recap": {"en": "Match recap", "es": "Resumen del partido", "fr": "Résumé du match"},
}

RECAP: dict[str, dict[str, str]] = {
    "headline_win": {
        "en": "{winner} beat {loser} {score}",
        "es": "{winner} vence a {loser} {score}",
        "fr": "{winner} bat {loser} {score}",
    },
    "headline_draw": {
        "en": "{home} and {away} share the points, {score}",
        "es": "{home} y {away} se reparten los puntos, {score}",
        "fr": "{home} et {away} se neutralisent, {score}",
    },
    "scorers": {
        "en": "Goals: {list}.",
        "es": "Goles: {list}.",
        "fr": "Buteurs : {list}.",
    },
    "no_goals": {
        "en": "No goals, but plenty to analyse.",
        "es": "Sin goles, pero mucho que analizar.",
        "fr": "Pas de but, mais beaucoup à analyser.",
    },
    "xg_story_fair": {
        "en": "The xG ({xg_line}) says the result was fair.",
        "es": "El xG ({xg_line}) dice que el resultado fue justo.",
        "fr": "Les xG ({xg_line}) confirment un résultat logique.",
    },
    "xg_story_unfair": {
        "en": "The xG ({xg_line}) suggests {team} deserved more.",
        "es": "El xG ({xg_line}) sugiere que {team} mereció más.",
        "fr": "Les xG ({xg_line}) suggèrent que {team} méritait mieux.",
    },
    "possession": {
        "en": "{team} had {pct}% of the ball.",
        "es": "{team} tuvo el {pct}% del balón.",
        "fr": "{team} a eu {pct}% de possession.",
    },
    "key_moments": {
        "en": "Key moments: {list}.",
        "es": "Momentos clave: {list}.",
        "fr": "Moments clés : {list}.",
    },
    "potm": {
        "en": "Player of the match: {player} ({team}), rating {rating}.",
        "es": "Jugador del partido: {player} ({team}), nota {rating}.",
        "fr": "Homme du match : {player} ({team}), note {rating}.",
    },
    "focus": {
        "en": "Your player {player}: {passes} passes ({acc}%), {shots} shots, {km} km, top speed {top} km/h.",
        "es": "Tu jugador {player}: {passes} pases ({acc}%), {shots} disparos, {km} km, punta {top} km/h.",
        "fr": "Ton joueur {player} : {passes} passes ({acc}%), {shots} tirs, {km} km, pointe {top} km/h.",
    },
    "fan_win": {
        "en": "A great day for your club.",
        "es": "Un gran día para tu club.",
        "fr": "Une grande journée pour ton club.",
    },
    "fan_loss": {
        "en": "A tough one for your club; the numbers show where to improve.",
        "es": "Un día duro para tu club; los números muestran dónde mejorar.",
        "fr": "Journée difficile pour ton club ; les chiffres montrent où progresser.",
    },
    "fan_draw": {
        "en": "A point for your club.",
        "es": "Un punto para tu club.",
        "fr": "Un point pour ton club.",
    },
}

STYLE_NAMES: dict[str, dict[str, str]] = {
    "possession": {"en": "possession", "es": "posesión", "fr": "possession"},
    "pressing": {"en": "high press", "es": "presión alta", "fr": "pressing haut"},
    "direct": {"en": "direct", "es": "directo", "fr": "direct"},
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "?"


def fmt(template: str, params: dict[str, Any]) -> str:
    return template.format_map(_SafeDict(params))


def render(key: str, mode: str, lang: str, params: dict[str, Any]) -> str:
    variants = T[key]
    by_mode = variants.get(mode) or next(iter(variants.values()))
    return fmt(by_mode.get(lang) or by_mode["en"], params)


def why(code: str, lang: str, params: dict[str, Any]) -> str:
    entry = WHY[code]
    return fmt(entry.get(lang) or entry["en"], params)


def label(key: str, lang: str) -> str:
    entry = LABELS[key]
    return entry.get(lang) or entry["en"]


def recap(key: str, lang: str, params: dict[str, Any]) -> str:
    entry = RECAP[key]
    return fmt(entry.get(lang) or entry["en"], params)


def style_name(style: str, lang: str) -> str:
    return STYLE_NAMES.get(style, {}).get(lang, style)
