"""
Caché de contexto de Gemini para el catálogo completo.

Permite mandarle a la IA el catálogo completo (mejor precisión, sobre todo
para diferenciar variantes parecidas) sin pagar el costo de retransmitirlo
en cada pedido: se sube una vez, Google lo deja "pre-procesado" por un
tiempo (TTL), y cada pedido siguiente solo manda lo nuevo (imagen/texto +
referencia al caché).

Requiere fijar un modelo EXACTO (no alias tipo "-latest") — el caché queda
ligado a esa versión específica. Si algo falla (el modelo no soporta
caché, venció, etc.), quien llama debe caer al sistema de respaldo.
"""
import hashlib
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from google.generativeai import caching

from gsheets_utils import get_gsheets_connection

CACHE_WORKSHEET = "gemini_cache"
CACHE_TTL_HOURS = 12


def _catalog_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _load_cache_info():
    try:
        conn = get_gsheets_connection()
        df = conn.read(worksheet=CACHE_WORKSHEET, ttl=0)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        info = {
            "cache_name": str(row.get("cache_name", "")).strip(),
            "model": str(row.get("model", "")).strip(),
            "catalog_hash": str(row.get("catalog_hash", "")).strip(),
            "expires_at": str(row.get("expires_at", "")).strip(),
        }
        if not info["cache_name"]:
            return None
        return info
    except Exception:
        return None


def _save_cache_info(cache_name, model, catalog_hash, expires_at):
    try:
        conn = get_gsheets_connection()
        df = pd.DataFrame([{
            "cache_name": cache_name,
            "model": model,
            "catalog_hash": catalog_hash,
            "expires_at": expires_at,
        }])
        conn.update(worksheet=CACHE_WORKSHEET, data=df)
    except Exception:
        pass  # si no existe la pestaña "gemini_cache", simplemente no persiste (se recrea cada vez)


def get_or_create_cache(catalog_text, model_name, system_extra=""):
    """Devuelve un CachedContent de Gemini para este catálogo+modelo — lo
    reusa si ya existe uno vigente (mismo catálogo, mismo modelo, no
    vencido), o crea uno nuevo. Puede lanzar excepción (el modelo no
    soporta caché, catálogo muy chico para cachear, error de red, etc.) —
    quien llama debe capturarla y caer al sistema de respaldo."""
    catalog_hash = _catalog_hash(catalog_text + "|" + model_name)
    info = _load_cache_info()

    if info and info["catalog_hash"] == catalog_hash and info["model"] == model_name:
        try:
            expires_at = datetime.fromisoformat(info["expires_at"])
            if datetime.utcnow() < expires_at:
                return caching.CachedContent.get(name=info["cache_name"])
        except Exception:
            pass  # vencido, inválido o borrado del lado de Google — se crea uno nuevo abajo

    system_text = f"""
    Eres un experto en logística y facturación en QuickBooks para 'Convenient Distributor'.
    Este es el catálogo COMPLETO de productos disponibles (Nombre, SKU y Descripción):

    {catalog_text}

    {system_extra}
    """
    cached = caching.CachedContent.create(
        model=model_name,
        contents=[system_text],
        ttl=timedelta(hours=CACHE_TTL_HOURS),
    )
    expires_at_new = (datetime.utcnow() + timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    _save_cache_info(cached.name, model_name, catalog_hash, expires_at_new)
    return cached
