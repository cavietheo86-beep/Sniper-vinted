"""
Sniper Vinted -> Discord (gratuit)
Surveille des recherches Vinted et poste les nouvelles annonces dans un salon Discord.
Tourne gratuitement sur GitHub Actions (voir .github/workflows/sniper.yml).
"""

import json
import os
import time
from pathlib import Path

import requests

# ============================================================
# TES RECHERCHES : modifie librement cette liste
# "texte" = ce que tu taperais dans Vinted
# "prix_max" = prix maximum en euros (None = pas de limite)
# ============================================================
RECHERCHES = [
    {"texte": "lot cartes pokemon", "prix_max": 40},
    {"texte": "classeur pokemon", "prix_max": 50},
    {"texte": "cartes pokemon enfance", "prix_max": 40},
    {"texte": "vieux lot pokemon", "prix_max": 50},
    {"texte": "pokemon niveau x", "prix_max": 30},
    {"texte": "pokemon prime", "prix_max": 30},
    {"texte": "pokemon legende", "prix_max": 40},
    {"texte": "lot pokemon japonais", "prix_max": 40},
    {"texte": "pokemone", "prix_max": 40},
    {"texte": "carte pokemen", "prix_max": 40},
]

BASE = "https://www.vinted.fr"
# Depuis septembre 2026, la recherche Vinted est sur un nouveau serveur
API = "https://api.vinted.fr/svc-catalogue/items"
FICHIER_VUS = Path("seen.json")
MAX_VUS = 5000  # nombre d'annonces mémorisées
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def charger_vus():
    if FICHIER_VUS.exists():
        try:
            return {i: True for i in json.loads(FICHIER_VUS.read_text())}
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def sauver_vus(vus):
    ids = list(vus.keys())[-MAX_VUS:]
    FICHIER_VUS.write_text(json.dumps(ids))


def nouvelle_session():
    """Récupère un jeton anonyme (cookie access_token_web) sur www.vinted.fr."""
    s = requests.Session()
    s.headers.update(HEADERS)
    r = s.head(f"{BASE}/", timeout=20, allow_redirects=True)
    jeton = s.cookies.get("access_token_web")
    if not jeton:  # certains serveurs ne donnent le cookie qu'avec un GET
        s.get(f"{BASE}/", timeout=20)
        jeton = s.cookies.get("access_token_web")
    if jeton:
        s.headers["Authorization"] = f"Bearer {jeton}"
    else:
        print(f"Attention : pas de jeton Vinted reçu (code {r.status_code}).")
    return s


def chercher(session, recherche):
    params = {
        "search_text": recherche["texte"],
        "order": "newest_first",
        "per_page": 30,
        "page": 1,
    }
    if recherche.get("prix_max"):  # un filtre vide provoque une erreur 400
        params["price_to"] = recherche["prix_max"]
    r = session.get(API, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("items", [])


def lien(item):
    url = item.get("url") or f"/items/{item['id']}"
    return url if url.startswith("http") else f"{BASE}{url}"  # les liens sont désormais relatifs


def prix(item):
    p = item.get("price")
    if isinstance(p, dict):
        return f"{p.get('amount', '?')} {p.get('currency_code', 'EUR')}"
    return f"{p} €"


def envoyer_discord(annonces, libelle):
    embeds = []
    for it in annonces:
        embed = {
            "title": (it.get("title") or "Annonce Vinted")[:256],
            "url": lien(it),
            "description": f"💶 **{prix(it)}**\n🔎 {libelle}",
            "color": 0x09B1BA,
        }
        photo = (it.get("photo") or {}).get("url")
        if photo:
            embed["image"] = {"url": photo}
        embeds.append(embed)

    # Discord accepte 10 embeds maximum par message
    for i in range(0, len(embeds), 10):
        r = requests.post(
            WEBHOOK,
            json={"username": "Sniper Pokémon", "embeds": embeds[i:i + 10]},
            timeout=20,
        )
        if r.status_code == 429:  # trop de messages : on patiente
            time.sleep(float(r.json().get("retry_after", 2)))
            requests.post(WEBHOOK, json={"embeds": embeds[i:i + 10]}, timeout=20)
        time.sleep(1)


def main():
    if not WEBHOOK:
        raise SystemExit("Secret DISCORD_WEBHOOK manquant.")

    vus = charger_vus()
    premier_lancement = not vus  # mémoire vide = on mémorise sans alerter
    session = nouvelle_session()

    for rech in RECHERCHES:
        try:
            items = chercher(session, rech)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                session = nouvelle_session()  # jeton expiré : on en reprend un
                try:
                    items = chercher(session, rech)
                except Exception as e2:
                    print(f"Échec « {rech['texte']} » : {e2}")
                    continue
            else:
                print(f"Échec « {rech['texte']} » : {e}")
                continue
        except Exception as e:
            print(f"Échec « {rech['texte']} » : {e}")
            continue

        nouvelles = [it for it in items if str(it["id"]) not in vus]
        for it in nouvelles:
            vus[str(it["id"])] = True

        # Au premier lancement, on mémorise sans notifier (évite 300 alertes d'un coup)
        if nouvelles and not premier_lancement:
            print(f"{len(nouvelles)} nouvelle(s) pour « {rech['texte']} »")
            envoyer_discord(nouvelles, rech["texte"])

        time.sleep(3)  # pause entre recherches pour ne pas se faire bloquer

    sauver_vus(vus)
    if premier_lancement:
        print("Premier lancement : annonces existantes mémorisées, aucune alerte envoyée.")


if __name__ == "__main__":
    main()
