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
    # --- Collections / classeurs (vendeurs qui liquident sans connaitre la valeur) ---
    {"texte": "collection cartes pokemon", "prix_max": 80},
    {"texte": "collection pokemon", "prix_max": 80},
    {"texte": "classeur cartes pokemon", "prix_max": 60},
    {"texte": "lot cartes pokemon", "prix_max": 40},
    {"texte": "cartes pokemon anciennes", "prix_max": 50},
    # --- Cartes niveau X (Diamant & Perle / Platine) ---
    {"texte": "pokemon niveau x", "prix_max": 15},
    {"texte": "pokemon lv x", "prix_max": 15},
    {"texte": "pokemon lvl x", "prix_max": 15},
    {"texte": "lot niveau x pokemon", "prix_max": 40},
    # --- Cartes Prime / Legende (HeartGold SoulSilver) ---
    {"texte": "pokemon prime", "prix_max": 12},
    {"texte": "carte prime pokemon", "prix_max": 12},
    {"texte": "lot prime pokemon", "prix_max": 40},
    {"texte": "pokemon legende", "prix_max": 25},
    {"texte": "cartes pokemon heartgold soulsilver", "prix_max": 30},
    {"texte": "cartes pokemon diamant perle", "prix_max": 30},
]

# Ne garder que les vendeurs bases en France (True / False)
FRANCE_UNIQUEMENT = True

# ============================================================
# MOTS INTERDITS : une annonce dont le titre contient un de ces mots est ignorée
# (en minuscules, sans accent). Ajoute ou retire des mots librement.
# ============================================================
EXCLURE = [
    "peluche", "figurine", "funko", "jeu video", "switch", "nintendo ds",
    "gameboy", "game boy", "3ds", "ds", "t-shirt", "tee-shirt", "sweat", "pyjama", "casquette",
    "sac", "trousse", "cartable", "gourde", "puzzle", "lego", "livre", "dvd",
    "poster", "sticker", "autocollant", "pokeball", "costume", "deguisement",
    "boite vide", "classeur vide", "portfolio vide", "proxy", "fake",
    "custom", "metal", "gold card", "carte doree", "tcg pocket", "code",
    "pochette", "sleeve", "protege", "toploader",
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
    params["currency"] = "EUR"  # sinon l'API peut répondre en dollars
    r = session.get(API, params=params, timeout=20)
    if r.status_code == 400:  # paramètre refusé : on réessaie sans
        params.pop("currency")
        r = session.get(API, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("items", [])


PAYS_VENDEURS = {}  # mémoire des pays déjà vérifiés pendant ce lancement


def chercher_cle(obj, cles):
    """Cherche une clé (ex. country_iso_code) n'importe où dans un dictionnaire."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in cles and isinstance(v, str) and v:
                return v
            trouve = chercher_cle(v, cles)
            if trouve:
                return trouve
    return None


def pays_vendeur(session, item):
    """Renvoie le code pays du vendeur (ex. 'FR'), ou None si inconnu."""
    cles = {"country_iso_code", "country_code"}
    direct = chercher_cle(item, cles)
    if direct:
        return direct.upper()
    user = item.get("user") or {}
    uid = user.get("id") or item.get("user_id")
    if not uid:
        return None
    if uid in PAYS_VENDEURS:
        return PAYS_VENDEURS[uid]
    pays = None
    for url in (f"{BASE}/api/v2/users/{uid}", f"https://api.vinted.fr/api/v2/users/{uid}"):
        try:
            r = session.get(url, timeout=15)
            if r.ok:
                pays = chercher_cle(r.json(), cles)
                if pays:
                    pays = pays.upper()
                    break
        except Exception:
            continue
        time.sleep(0.5)
    PAYS_VENDEURS[uid] = pays
    return pays


def est_francaise(session, item):
    if not FRANCE_UNIQUEMENT:
        return True
    pays = pays_vendeur(session, item)
    if pays:
        return pays == "FR"
    # Pays inconnu : on se rabat sur la devise (les vendeurs UK/US sont en GBP/USD)
    devise = (item.get("price") or {}).get("currency_code") if isinstance(item.get("price"), dict) else None
    return devise in (None, "EUR")


def sans_accent(texte):
    import unicodedata
    t = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def est_exclue(item):
    titre = f" {sans_accent(item.get('title') or '')} "
    return any(f" {mot} " in titre or f" {mot}s " in titre for mot in EXCLURE)


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

    ok = True
    # Discord accepte 10 embeds maximum par message
    for i in range(0, len(embeds), 10):
        lot = embeds[i:i + 10]
        payload = {"username": "Sniper Pokémon", "embeds": lot}
        r = requests.post(WEBHOOK, json=payload, timeout=20)
        if r.status_code == 429:  # trop de messages : on patiente
            try:
                attente = float(r.json().get("retry_after", 2))
            except ValueError:
                attente = 2
            time.sleep(attente)
            r = requests.post(WEBHOOK, json=payload, timeout=20)
        if r.status_code == 400:  # message riche refusé : on envoie en texte simple
            texte = "\n".join(f"{e['title']} — {e['description'].splitlines()[0]}\n{e['url']}" for e in lot)
            r = requests.post(
                WEBHOOK,
                json={"username": "Sniper Pokémon", "content": texte[:1900]},
                timeout=20,
            )
        if r.status_code >= 300:
            print(f"Erreur Discord {r.status_code} : {r.text[:300]}")
            ok = False
        else:
            print(f"Envoyé dans Discord : {len(lot)} annonce(s)")
        time.sleep(1)
    return ok


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

        # Au premier lancement, on mémorise sans notifier (évite 300 alertes d'un coup)
        envoye = True
        a_envoyer = [it for it in nouvelles if not est_exclue(it)]
        if len(a_envoyer) < len(nouvelles):
            print(f"{len(nouvelles) - len(a_envoyer)} annonce(s) ignorée(s) (mots interdits)")
        if not premier_lancement:
            avant = len(a_envoyer)
            a_envoyer = [it for it in a_envoyer if est_francaise(session, it)]
            if len(a_envoyer) < avant:
                print(f"{avant - len(a_envoyer)} annonce(s) ignorée(s) (vendeur hors France)")
        if a_envoyer and not premier_lancement:
            print(f"{len(a_envoyer)} nouvelle(s) pour « {rech['texte']} »")
            envoye = envoyer_discord(a_envoyer, rech["texte"])

        # On ne mémorise que ce qui a bien été envoyé (sinon on réessaie au prochain passage)
        if envoye:
            for it in nouvelles:
                vus[str(it["id"])] = True

        time.sleep(3)  # pause entre recherches pour ne pas se faire bloquer

    sauver_vus(vus)
    if premier_lancement:
        print("Premier lancement : annonces existantes mémorisées, aucune alerte envoyée.")


if __name__ == "__main__":
    main()
