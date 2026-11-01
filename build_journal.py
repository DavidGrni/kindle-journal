#!/usr/bin/env python3
"""Fabrication du journal Kindle.

Trois sous-commandes :

  collect   lit les flux de sources.yaml et écrit work/candidates.json
            (métadonnées des articles récents : titre, résumé, source, lien).
  build     lit work/edition.json (sélection et textes écrits par l'agent),
            récupère le texte complet et l'image des articles retenus, puis
            écrit editions/AAAA-MM-JJ.epub et latest.epub, et élague les
            anciennes éditions.
  prune     ne garde que les N dernières éditions.

Le tri éditorial n'est pas fait ici : il est fait par l'agent qui écrit
work/edition.json entre « collect » et « build ».
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
EDITIONS = ROOT / "editions"
LATEST = ROOT / "latest.epub"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 kindle-journal/1.0"
)

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
    "août", "septembre", "octobre", "novembre", "décembre",
]


# ---------------------------------------------------------------- utilitaires

def load_config() -> dict:
    with open(ROOT / "sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg.get("fuseau", "Europe/Paris"))


def date_fr(d: dt.date) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]} {d.year}"


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def article_id(link: str) -> str:
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]


def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------------- collect

def entry_datetime(entry, zone: ZoneInfo) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime(*t[:6], tzinfo=dt.timezone.utc).astimezone(zone)
    return None


def entry_image(entry) -> str | None:
    for media in entry.get("media_content", []) or []:
        url = media.get("url")
        if url and media.get("medium", "image") in ("image", None):
            return url
    for media in entry.get("media_thumbnail", []) or []:
        if media.get("url"):
            return media["url"]
    for enc in entry.get("enclosures", []) or []:
        if enc.get("href") and str(enc.get("type", "")).startswith("image/"):
            return enc["href"]
    return None


def collect(cfg: dict) -> dict:
    zone = tz(cfg)
    now = dt.datetime.now(zone)
    max_age = dt.timedelta(hours=cfg.get("max_age_heures", 30))
    max_per_feed = cfg.get("max_par_flux", 15)
    excl = cfg.get("exclusions", {}) or {}
    title_re = [re.compile(p, re.I) for p in excl.get("titres", []) or []]
    link_re = [re.compile(p, re.I) for p in excl.get("liens", []) or []]

    def read_feed(feed: dict) -> list[dict]:
        out = []
        try:
            parsed = feedparser.parse(feed["url"], agent=USER_AGENT)
        except Exception as e:  # noqa: BLE001
            log(f"  !! {feed['nom']} ({feed['url']}) : {e}")
            return out
        if parsed.get("bozo") and not parsed.entries:
            log(f"  !! {feed['nom']} : flux illisible ({parsed.get('bozo_exception')})")
            return out
        for entry in parsed.entries:
            link = (entry.get("link") or "").strip()
            title = strip_html(entry.get("title", ""))
            if not link or not title:
                continue
            if any(r.search(title) for r in title_re) or any(r.search(link) for r in link_re):
                continue
            when = entry_datetime(entry, zone)
            if when and now - when > max_age:
                continue
            summary = strip_html(entry.get("summary") or entry.get("description") or "")
            if len(summary) > 700:
                summary = summary[:700].rsplit(" ", 1)[0] + "…"
            out.append({
                "id": article_id(link),
                "titre": title,
                "lien": link,
                "resume": summary,
                "source": feed["nom"],
                "rubrique": feed.get("rubrique", ""),
                "date": when.isoformat(timespec="minutes") if when else None,
                "image": entry_image(entry),
            })
            if len(out) >= max_per_feed:
                break
        log(f"  {feed['nom']:24} {len(out):3} articles  ({feed['url']})")
        return out

    log("Collecte des flux…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_feed, cfg["flux"]))

    seen_links: set[str] = set()
    seen_titles: set[str] = set()
    candidates = []
    for items in results:
        for it in items:
            key_link = re.sub(r"[#?].*$", "", it["lien"]).rstrip("/")
            key_title = re.sub(r"\W+", " ", it["titre"].lower()).strip()
            if key_link in seen_links or key_title in seen_titles:
                continue
            seen_links.add(key_link)
            seen_titles.add(key_title)
            candidates.append(it)
    candidates.sort(key=lambda c: c["date"] or "", reverse=True)

    data = {
        "genere_le": now.isoformat(timespec="minutes"),
        "date_edition": now.date().isoformat(),
        "nb_candidats": len(candidates),
        "candidats": candidates,
    }
    WORK.mkdir(exist_ok=True)
    with open(WORK / "candidates.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log(f"{len(candidates)} candidats écrits dans {WORK / 'candidates.json'}")
    return data


# ------------------------------------------------------------- texte complet

def download_page(url: str) -> str | None:
    """Télécharge une page avec un en-tête de navigateur ; repli sur trafilatura."""
    import urllib.request
    import trafilatura

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read(8_000_000)
            charset = r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, "replace")
    except Exception as e:  # noqa: BLE001
        log(f"  .. {url} : {e} ; nouvel essai via trafilatura")
    try:
        return trafilatura.fetch_url(url)
    except Exception as e:  # noqa: BLE001
        log(f"  !! téléchargement impossible : {url} ({e})")
        return None


def fetch_fulltext(cand: dict) -> dict:
    """Retourne {'html': str|None, 'image': url|None, 'auteur': str|None}."""
    import trafilatura

    result = {"html": None, "image": cand.get("image"), "auteur": None}
    downloaded = download_page(cand["lien"])
    if not downloaded:
        log(f"  !! page vide : {cand['lien']}")
        return result
    try:
        meta = trafilatura.bare_extraction(downloaded, url=cand["lien"], with_metadata=True)
        if meta is not None:
            if getattr(meta, "image", None):
                result["image"] = meta.image
            if getattr(meta, "author", None):
                result["auteur"] = meta.author
        result["html"] = trafilatura.extract(
            downloaded, url=cand["lien"], output_format="html",
            include_comments=False, include_links=False, include_images=False,
            include_tables=True, favor_precision=True,
        )
    except Exception as e:  # noqa: BLE001
        log(f"  !! extraction impossible : {cand['lien']} ({e})")
    return result


def html_to_xhtml_fragment(doc: str | None, title: str = "") -> str:
    """Nettoie la sortie HTML de trafilatura en fragment XHTML simple."""
    if not doc:
        return ""
    from lxml import etree, html as lhtml

    try:
        root = lhtml.fromstring(doc)
    except Exception:  # noqa: BLE001
        return "<p>" + esc(strip_html(doc)) + "</p>"
    body = root.find(".//body")
    if body is None:
        body = root
    for bad in body.xpath(".//script|.//style|.//img|.//figure|.//iframe|.//head"):
        bad.getparent().remove(bad)
    etree.strip_tags(body, "a", "span", "div", "section", "article", "main", "font", "u")
    allowed = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote",
               "em", "strong", "b", "i", "br", "table", "tr", "td", "th", "thead", "tbody",
               "hr", "sup", "sub", "cite", "q", "dl", "dt", "dd", "pre", "code"}
    for el in list(body.iter()):
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()
        if el is body:
            continue
        if tag in ("h1",):
            el.tag = "h2"
        elif tag not in allowed:
            el.tag = "p"
        el.attrib.clear()
    # Pas de paragraphe vide.
    for el in list(body.iter("p", "h2", "h3", "li")):
        if not (el.text_content() or "").strip() and len(el) == 0:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
    # Trafilatura répète souvent le titre en tête : on l'enlève.
    norm = lambda t: re.sub(r"\W+", " ", (t or "").lower()).strip()  # noqa: E731
    children = [c for c in body if isinstance(c.tag, str)]
    if children and children[0].tag in ("h2", "h3", "p") and title:
        first = norm(children[0].text_content())
        if first and (first == norm(title) or first in norm(title) or norm(title) in first):
            body.remove(children[0])
    # Blocs d'en-tête parasites (crédits, mentions) : des éléments courts sans
    # ponctuation de phrase avant le premier vrai paragraphe.
    for el in [c for c in body if isinstance(c.tag, str)]:
        txt = (el.text_content() or "").strip()
        if len(txt) < 300 and not re.search(r"[.!?…»]", txt):
            body.remove(el)
            continue
        break
    parts = []
    if body.text and body.text.strip():
        parts.append("<p>" + esc(body.text.strip()) + "</p>")
    for child in body:
        parts.append(etree.tostring(child, method="xml", encoding="unicode"))
    return "\n".join(parts)


def fetch_image(url: str | None, max_size=(600, 760)) -> bytes | None:
    """Télécharge une image, la passe en niveaux de gris et la réduit. JPEG."""
    if not url:
        return None
    import urllib.request
    from PIL import Image, ImageOps

    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(6_000_000)
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as e:  # noqa: BLE001
        log(f"  !! image ignorée : {url} ({e})")
        return None
    if im.width < 200 or im.height < 120:
        return None
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
        im = bg
    im = ImageOps.grayscale(im.convert("RGB"))
    im.thumbnail(max_size, Image.LANCZOS)
    im = ImageOps.autocontrast(im, cutoff=1)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=55, optimize=True, progressive=False)
    return out.getvalue()


# --------------------------------------------------------------- couverture

def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf" if bold
        else "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def make_cover(cfg: dict, date: dt.date, rubriques: list[dict]) -> bytes:
    from PIL import Image, ImageDraw

    W, H = 758, 1024
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    margin = 56

    d.rectangle([0, 0, W, 18], fill=0)
    y = 70
    titre = cfg.get("titre", "Le Journal")
    f_title = _font(84, bold=True)
    d.text((margin, y), titre, font=f_title, fill=0)
    y += 110
    f_sub = _font(30)
    d.text((margin, y), cfg.get("sous_titre", ""), font=f_sub, fill=60)
    y += 56
    d.line([margin, y, W - margin, y], fill=0, width=3)
    y += 28
    f_date = _font(38)
    d.text((margin, y), date_fr(date).capitalize(), font=f_date, fill=0)
    y += 70
    d.line([margin, y, W - margin, y], fill=0, width=1)
    y += 40

    # Sommaire : on réduit la taille des caractères jusqu'à ce que tout tienne.
    bottom = H - 110
    max_items = 4
    for size_rub, size_item in ((34, 26), (30, 24), (28, 22), (26, 20), (24, 18), (22, 17), (21, 16)):
        line_rub, line_item, gap = int(size_rub * 1.3), int(size_item * 1.3), int(size_item * 0.5)
        needed = sum(line_rub + gap + line_item * min(len(r["articles"]), max_items) for r in rubriques)
        if y + needed <= bottom:
            break
    else:
        max_items = 2
    f_rub = _font(size_rub, bold=True)
    f_item = _font(size_item)
    for rub in rubriques:
        if y + line_rub > bottom:
            break
        d.text((margin, y), rub["nom"], font=f_rub, fill=0)
        y += line_rub
        for art in rub["articles"][:max_items]:
            if y + line_item > bottom:
                break
            t = art["titre"]
            while d.textlength("– " + t, font=f_item) > W - 2 * margin - 20 and len(t) > 10:
                t = t[:-4].rstrip() + "…"
            d.text((margin + 16, y), "– " + t, font=f_item, fill=40)
            y += line_item
        y += gap

    nb = sum(len(r["articles"]) for r in rubriques)
    f_foot = _font(24)
    foot = f"{nb} articles · Édition du {date.strftime('%d/%m/%Y')}"
    d.line([margin, H - 90, W - margin, H - 90], fill=0, width=1)
    d.text((margin, H - 70), foot, font=f_foot, fill=60)

    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


# -------------------------------------------------------------------- build

CSS = """
@page { margin: 0; }
body { font-family: serif; line-height: 1.45; margin: 0 3%; text-align: left; }

/* --- Article --- */
p.kicker { font-family: sans-serif; font-size: 0.78em; letter-spacing: 0.12em;
           text-transform: uppercase; color: #333; margin: 0.4em 0 0.3em 0;
           border-bottom: 1px solid #000; padding-bottom: 0.25em; text-align: left; }
h1.titre { font-size: 1.55em; line-height: 1.15; font-weight: bold;
           margin: 0.35em 0 0.35em 0; text-align: left; }
p.meta { font-family: sans-serif; font-size: 0.78em; color: #444; margin: 0 0 0.9em 0; text-align: left; }
p.chapeau { font-size: 1.02em; font-weight: bold; line-height: 1.35;
            margin: 0 0 1em 0; text-align: left; }
img.illustration { max-width: 100%; height: auto; display: block; margin: 0.2em auto 1em auto; }
div.texte p { margin: 0 0 0.55em 0; text-align: justify; text-indent: 1.3em; }
div.texte p.premier { text-indent: 0; }
div.texte h2 { font-size: 1.12em; margin: 1.1em 0 0.45em 0; text-align: left; text-indent: 0; }
div.texte h3 { font-size: 1.02em; margin: 0.9em 0 0.35em 0; text-align: left; }
div.texte ul, div.texte ol { margin: 0.4em 0 0.8em 1.4em; }
div.texte li { margin-bottom: 0.25em; text-align: left; }
div.texte blockquote { margin: 0.7em 1.4em; font-style: italic; }
div.texte table { border-collapse: collapse; margin: 0.6em 0; font-size: 0.9em; }
div.texte td, div.texte th { border: 1px solid #999; padding: 0.2em 0.4em; }
p.fin { text-align: center; margin: 1.2em 0 0.8em 0; font-size: 1.1em; }
p.note { font-family: sans-serif; font-size: 0.8em; color: #444; margin: 1em 0 0.5em 0;
         border-top: 1px dotted #777; padding-top: 0.5em; text-align: left; }
p.source { font-family: sans-serif; font-size: 0.76em; color: #444; margin: 0;
           text-align: left; word-break: break-all; }

/* --- Sommaire --- */
.sommaire h1 { font-size: 2.2em; line-height: 1; margin: 0.3em 0 0.1em 0; text-align: center; }
.sommaire p.soustitre { font-family: sans-serif; font-size: 0.85em; letter-spacing: 0.15em;
                        text-transform: uppercase; text-align: center; color: #333; margin: 0; }
.sommaire p.date { text-align: center; font-style: italic; margin: 0.5em 0 0.9em 0;
                   border-top: 2px solid #000; border-bottom: 1px solid #000; padding: 0.45em 0; }
.sommaire p.edito { font-style: italic; margin: 0 0 1.2em 0; text-align: justify; }
.sommaire h2 { font-family: sans-serif; font-size: 0.85em; letter-spacing: 0.12em;
               text-transform: uppercase; margin: 1.2em 0 0.4em 0;
               border-bottom: 1px solid #000; padding-bottom: 0.2em; }
.sommaire p.entree { margin: 0 0 0.75em 0; text-align: left; line-height: 1.3; }
.sommaire p.entree a { text-decoration: none; font-weight: bold; color: #000; }
.sommaire span.num { font-family: sans-serif; font-weight: bold; color: #555; }
.sommaire span.chapeau { display: block; font-size: 0.9em; color: #222; margin-top: 0.1em; }
.sommaire span.source { display: block; font-family: sans-serif; font-size: 0.72em; color: #555; }
.sommaire p.pied { font-family: sans-serif; font-size: 0.75em; color: #555; text-align: center;
                   margin-top: 1.5em; border-top: 1px solid #000; padding-top: 0.5em; }
"""


def truncate_fragment(frag: str, max_chars: int) -> tuple[str, bool]:
    """Coupe le texte à la fin d'un paragraphe au-delà de max_chars. Retourne (xhtml, coupé)."""
    if not frag or max_chars <= 0:
        return frag, False
    from lxml import etree

    try:
        root = etree.fromstring("<div>" + frag + "</div>")
    except etree.XMLSyntaxError:
        return frag, False
    kept, total, cut = [], 0, False
    for el in root:
        txt = "".join(el.itertext())
        if kept and total + len(txt) > max_chars:
            cut = True
            break
        kept.append(el)
        total += len(txt)
        if total >= max_chars:
            cut = True
            break
    # Ne pas finir sur un intertitre.
    while kept and kept[-1].tag in ("h2", "h3"):
        kept.pop(); cut = True
    out = "\n".join(etree.tostring(e, method="xml", encoding="unicode") for e in kept)
    return out, cut


def mark_first_paragraph(frag: str) -> str:
    return re.sub(r"^(\s*)<p>", r'\1<p class="premier">', frag, count=1)


def drop_duplicate_lead(frag: str, resume: str) -> str:
    """Retire le premier paragraphe s'il répète le résumé du flux (chapô de la page)."""
    if not frag or not resume:
        return frag
    m = re.match(r"\s*<p>(.*?)</p>", frag, re.S)
    if not m:
        return frag
    norm = lambda t: re.sub(r"\W+", " ", html.unescape(re.sub(r"<[^>]+>", "", t)).lower()).strip()  # noqa: E731
    a, b = norm(m.group(1)), norm(resume)
    if a and (a[:80] == b[:80] or a in b or b in a):
        return frag[m.end():].lstrip()
    return frag


def reading_time(text: str) -> str:
    minutes = max(1, round(len(text.split()) / 200))
    return f"{minutes} min"


def load_edition(cfg: dict) -> tuple[dict, dict]:
    with open(WORK / "candidates.json", encoding="utf-8") as f:
        cand_data = json.load(f)
    with open(WORK / "edition.json", encoding="utf-8") as f:
        edition = json.load(f)
    by_id = {c["id"]: c for c in cand_data["candidats"]}

    errors = []
    rubriques = edition.get("rubriques") or []
    if not rubriques:
        errors.append("edition.json ne contient aucune rubrique")
    seen = set()
    for rub in rubriques:
        if not rub.get("nom"):
            errors.append("rubrique sans nom")
        for art in rub.get("articles") or []:
            aid = art.get("id")
            if aid not in by_id:
                errors.append(f"id inconnu dans edition.json : {aid!r}")
            elif aid in seen:
                errors.append(f"id en double : {aid}")
            seen.add(aid)
            if not art.get("titre"):
                errors.append(f"titre manquant pour {aid}")
            if not art.get("chapeau"):
                errors.append(f"chapeau manquant pour {aid}")
    if not seen:
        errors.append("aucun article sélectionné")
    if errors:
        for e in errors:
            log("ERREUR : " + e)
        sys.exit(2)
    return edition, by_id


def build(cfg: dict, skip_fetch: bool = False) -> Path:
    from ebooklib import epub

    zone = tz(cfg)
    edition, by_id = load_edition(cfg)
    date = dt.date.fromisoformat(edition.get("date") or dt.datetime.now(zone).date().isoformat())
    titre_journal = cfg.get("titre", "Le Journal")

    # Récupération du texte complet et des images, en parallèle.
    selected = [(rub, art) for rub in edition["rubriques"] for art in rub.get("articles", [])]
    log(f"Récupération de {len(selected)} articles…")

    def prepare(item):
        rub, art = item
        cand = by_id[art["id"]]
        full = {"html": None, "image": cand.get("image"), "auteur": None}
        if not skip_fetch:
            full = fetch_fulltext(cand)
        body = html_to_xhtml_fragment(full["html"], cand["titre"])
        body = drop_duplicate_lead(body, cand.get("resume", ""))
        texte_court = len(strip_html(body)) < 600
        body, coupe = truncate_fragment(body, int(cfg.get("longueur_max_article", 4000)))
        body = mark_first_paragraph(body)
        image = None if skip_fetch else fetch_image(full["image"])
        return rub, art, cand, body, texte_court, coupe, image, full.get("auteur")

    with ThreadPoolExecutor(max_workers=6) as pool:
        prepared = list(pool.map(prepare, selected))

    book = epub.EpubBook()
    book.set_identifier(f"kindle-journal-{date.isoformat()}")
    book.set_title(f"{titre_journal} – {date_fr(date)}")
    book.set_language("fr")
    book.add_author(titre_journal)
    book.add_metadata("DC", "date", date.isoformat())
    book.add_metadata("DC", "description", cfg.get("sous_titre", ""))

    cover_png = make_cover(cfg, date, edition["rubriques"])
    book.set_cover("cover.png", cover_png, create_page=True)

    style = epub.EpubItem(uid="style", file_name="style/journal.css",
                          media_type="text/css", content=CSS.encode("utf-8"))
    book.add_item(style)

    chapters = []
    toc = []
    sommaire_parts = [
        f'<h1>{esc(titre_journal)}</h1>',
        f'<p class="soustitre">{esc(cfg.get("sous_titre", ""))}</p>',
        f'<p class="date">{esc(date_fr(date).capitalize())}</p>',
    ]
    if edition.get("edito"):
        sommaire_parts.append(f'<p class="edito">{esc(edition["edito"])}</p>')

    n = 0
    current_rub = None
    rub_links = []
    total_words = 0
    for rub, art, cand, body, texte_court, coupe, image, auteur in prepared:
        n += 1
        fname = f"art{n:02d}.xhtml"
        if rub is not current_rub:
            if current_rub is not None:
                toc.append((epub.Section(current_rub["nom"]), rub_links))
            current_rub = rub
            rub_links = []
            sommaire_parts.append(f'<h2>{esc(rub["nom"])}</h2>')
        when = ""
        if cand.get("date"):
            try:
                w = dt.datetime.fromisoformat(cand["date"]).astimezone(zone)
                when = w.strftime("%d/%m à %Hh%M")
            except ValueError:
                when = ""
        plain = strip_html(body) if body else cand["resume"]
        total_words += len(plain.split())
        duree = reading_time(plain)
        meta = " · ".join(x for x in [cand["source"], auteur or "", when, "lecture " + duree] if x)

        parts = [f'<p class="kicker">{esc(rub["nom"])}</p>',
                 f'<h1 class="titre">{esc(art["titre"])}</h1>',
                 f'<p class="meta">{esc(meta)}</p>',
                 f'<p class="chapeau">{esc(art["chapeau"])}</p>']
        if image:
            img_name = f"images/img{n:02d}.jpg"
            book.add_item(epub.EpubItem(uid=f"img{n:02d}", file_name=img_name,
                                        media_type="image/jpeg", content=image))
            parts.append(f'<img class="illustration" src="{img_name}" alt=""/>')
        if body:
            parts.append('<div class="texte">' + body + '</div>')
            if texte_court:
                parts.append('<p class="note">Texte intégral indisponible (article réservé '
                             'aux abonnés ou page protégée). Résumé fourni par la source :</p>')
                parts.append(f'<p>{esc(cand["resume"])}</p>')
            elif coupe:
                parts.append('<p class="note">Article abrégé pour la lecture du matin. '
                             'La suite est en ligne.</p>')
        else:
            parts.append(f'<div class="texte"><p class="premier">{esc(cand["resume"])}</p></div>')
            parts.append('<p class="note">Texte intégral indisponible.</p>')
        parts.append('<p class="fin">◆</p>')
        parts.append(f'<p class="source">{esc(cand["source"])} — {esc(cand["lien"])}</p>')

        ch = epub.EpubHtml(title=art["titre"], file_name=fname, lang="fr")
        ch.content = ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>'
                      + esc(art["titre"]) + '</title></head><body>'
                      + "\n".join(parts) + "</body></html>")
        ch.add_item(style)
        book.add_item(ch)
        chapters.append(ch)
        rub_links.append(epub.Link(fname, art["titre"], f"art{n:02d}"))
        sommaire_parts.append(
            f'<p class="entree"><span class="num">{n}.</span> <a href="{fname}">{esc(art["titre"])}</a>'
            f'<span class="chapeau">{esc(art["chapeau"])}</span>'
            f'<span class="source">{esc(cand["source"])} · {duree}</span></p>'
        )
    sommaire_parts.append(f'<p class="pied">{n} articles · environ {reading_time(" ".join(["mot"] * total_words))} de lecture</p>')
    if current_rub is not None:
        toc.append((epub.Section(current_rub["nom"]), rub_links))

    sommaire = epub.EpubHtml(title="Sommaire", file_name="sommaire.xhtml", lang="fr")
    sommaire.content = ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Sommaire'
                        '</title></head><body><div class="sommaire">'
                        + "\n".join(sommaire_parts) + "</div></body></html>")
    sommaire.add_item(style)
    book.add_item(sommaire)

    book.toc = [epub.Link("sommaire.xhtml", "Sommaire", "sommaire")] + toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["cover", sommaire] + chapters
    book.guide = [{"type": "toc", "title": "Sommaire", "href": "sommaire.xhtml"}]

    EDITIONS.mkdir(exist_ok=True)
    out = EDITIONS / f"{date.isoformat()}.epub"
    epub.write_epub(str(out), book, {"epub3_pages": False})
    shutil.copyfile(out, LATEST)
    log(f"Édition écrite : {out} ({out.stat().st_size // 1024} Ko), copiée dans {LATEST.name}")
    prune(cfg)
    return out


def prune(cfg: dict) -> None:
    keep = int(cfg.get("editions_conservees", 30))
    files = sorted(EDITIONS.glob("????-??-??.epub"))
    for old in files[:-keep] if keep > 0 else []:
        old.unlink()
        log(f"Édition supprimée : {old.name}")


# --------------------------------------------------------------------- main

def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect", help="lit les flux et écrit work/candidates.json")
    pb = sub.add_parser("build", help="assemble l'EPUB à partir de work/edition.json")
    pb.add_argument("--sans-telechargement", action="store_true",
                    help="n'utilise que les résumés RSS (test rapide)")
    sub.add_parser("prune", help="supprime les éditions au-delà du nombre conservé")
    args = p.parse_args(argv)

    cfg = load_config()
    t0 = time.time()
    if args.cmd == "collect":
        data = collect(cfg)
        # Résumé lisible sur la sortie standard, pour l'agent.
        print(f"Édition du {data['date_edition']} — {data['nb_candidats']} candidats")
        for c in data["candidats"]:
            print(f"[{c['id']}] ({c['source']} / {c['rubrique']}) {c['titre']}")
    elif args.cmd == "build":
        out = build(cfg, skip_fetch=args.sans_telechargement)
        print(out)
    elif args.cmd == "prune":
        prune(cfg)
    log(f"Terminé en {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
