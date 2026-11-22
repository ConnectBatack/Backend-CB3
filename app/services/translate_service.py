import asyncio
import logging
import re
import unicodedata

import httpx

from app.core.config import SUPPORTED_LANGUAGES, get_settings

logger = logging.getLogger("batack.translate")

_HTML_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_title(text: str) -> str:
    """Génère un slug URL propre à partir d'un titre en texte brut."""
    normalized = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG_RE.sub("-", normalized.lower()).strip("-")
    if len(slug) < 2:
        return "projet-batack"
    return slug[:60].strip("-") or "projet-batack"


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text).strip()


async def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, bool]:
    """
    Traduit un texte via l'API Google Translate.
    Utilise Google Cloud Translation v2 si GOOGLE_TRANSLATE_API_KEY est défini,
    sinon le endpoint public translate.googleapis.com.
    Retourne (texte_traduit, succes_traduction). En cas d'échec réseau, conserve le texte source.
    """
    cleaned = (text or "").strip()
    if not cleaned or source_lang == target_lang:
        return cleaned, True

    settings = get_settings()
    api_key = (settings.GOOGLE_TRANSLATE_API_KEY or "").strip()

    async def _call(http: httpx.AsyncClient) -> str | None:
        if api_key:
            resp = await http.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": api_key},
                json={
                    "q": cleaned,
                    "source": source_lang,
                    "target": target_lang,
                    "format": "text",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            translations = (payload.get("data") or {}).get("translations") or []
            if translations and translations[0].get("translatedText"):
                return _strip_html(str(translations[0]["translatedText"]))
            return None

        resp = await http.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": source_lang,
                "tl": target_lang,
                "dt": "t",
                "q": cleaned,
            },
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            joined = "".join(str(seg[0]) for seg in payload[0] if isinstance(seg, list) and seg and seg[0])
            if joined.strip():
                return _strip_html(joined)
        return None

    try:
        if client is not None:
            translated = await _call(client)
        else:
            async with httpx.AsyncClient(timeout=8.0) as owned:
                translated = await _call(owned)
        if translated:
            return translated, True
    except Exception as exc:
        logger.warning("Traduction Google (%s -> %s) indisponible : %s", source_lang, target_lang, exc)

    return cleaned, False


async def complete_project_translations(
    *,
    source_lang: str,
    category: dict[str, str] | None = None,
    title: dict[str, str] | None = None,
    tagline: dict[str, str] | None = None,
    description: dict[str, str] | None = None,
    overwrite: bool = True,
) -> tuple[dict[str, dict[str, str]], bool]:
    """
    Complète les traductions (fr, en, es, nl) des champs d'un projet à partir de source_lang.
    Retourne (champs_completes, toutes_traductions_reussies).
    """
    fields = {
        "category": dict(category or {}),
        "title": dict(title or {}),
        "tagline": dict(tagline or {}),
        "description": dict(description or {}),
    }
    target_langs = [lang for lang in SUPPORTED_LANGUAGES if lang != source_lang]
    all_ok = True

    async with httpx.AsyncClient(timeout=8.0) as client:
        jobs: list[tuple[str, str, str]] = []
        for field_name, lang_map in fields.items():
            src_val = (lang_map.get(source_lang) or "").strip()
            if not src_val:
                continue
            for tgt in target_langs:
                existing = (lang_map.get(tgt) or "").strip()
                if overwrite or not existing:
                    jobs.append((field_name, tgt, src_val))

        results = await asyncio.gather(
            *(translate_text(src_val, source_lang, tgt, client=client) for _, tgt, src_val in jobs)
        )
        for (field_name, tgt, _), (translated, ok) in zip(jobs, results, strict=True):
            fields[field_name][tgt] = translated
            if not ok:
                all_ok = False

    return fields, all_ok

