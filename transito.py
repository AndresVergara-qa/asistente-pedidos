"""
Registro de POs enviadas a proveedores, pendientes de recibir ("en tránsito").

La mayoría de proveedores son informales (email/WhatsApp), así que no hay
un lugar único en QuickBooks donde esto viva de forma confiable — se lleva
aquí, en la misma hoja de Google Sheets que ya usa la app (pestaña
"pos_transito", debe existir de antemano igual que "gemini_cache" o
"app_settings").

Cuando se crea un Bill en la pestaña Compras, auto_reconcile() intenta
cerrar automáticamente las entradas pendientes de ese proveedor/SKU, para
no depender de la memoria de quién recibió qué.
"""
from datetime import datetime

import pandas as pd
import streamlit as st

from gsheets_utils import get_gsheets_connection

TRANSITO_WORKSHEET = "pos_transito"
COLUMNS = ["id", "proveedor", "producto", "sku", "cantidad", "fecha_envio", "estado", "fecha_recibido", "bill_id"]


def _load():
    try:
        conn = get_gsheets_connection()
        df = conn.read(worksheet=TRANSITO_WORKSHEET, ttl=0)
        if df is None or df.empty:
            return pd.DataFrame(columns=COLUMNS)
        for c in COLUMNS:
            if c not in df.columns:
                df[c] = ""
        df["cantidad"] = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0)
        df["id"] = pd.to_numeric(df["id"], errors="coerce")
        df = df.dropna(subset=["id"])
        df["id"] = df["id"].astype(int)
        return df[COLUMNS]
    except Exception:
        return pd.DataFrame(columns=COLUMNS)


def _save(df):
    try:
        conn = get_gsheets_connection()
        conn.update(worksheet=TRANSITO_WORKSHEET, data=df)
        return True
    except Exception:
        return False


def load_transito_df():
    """Devuelve el registro completo (pendientes y recibidos)."""
    return _load()


def add_entry(proveedor, producto, sku, cantidad, fecha_envio=None):
    df = _load()
    new_id = int(df["id"].max()) + 1 if not df.empty else 1
    nueva = pd.DataFrame([{
        "id": new_id,
        "proveedor": str(proveedor).strip(),
        "producto": str(producto).strip(),
        "sku": str(sku).strip(),
        "cantidad": float(cantidad or 0),
        "fecha_envio": fecha_envio or datetime.now().strftime("%Y-%m-%d"),
        "estado": "pendiente",
        "fecha_recibido": "",
        "bill_id": "",
    }])
    df = pd.concat([df, nueva], ignore_index=True)
    return _save(df)


def mark_received(entry_id, bill_id=""):
    df = _load()
    mask = df["id"] == int(entry_id)
    df.loc[mask, "estado"] = "recibido"
    df.loc[mask, "fecha_recibido"] = datetime.now().strftime("%Y-%m-%d")
    if bill_id:
        df.loc[mask, "bill_id"] = str(bill_id)
    return _save(df)


def get_pending_qty_by_sku():
    """{SKU (mayúsculas): cantidad_pendiente_total} solo de entradas 'pendiente'."""
    df = _load()
    if df.empty:
        return {}
    pend = df[(df["estado"] == "pendiente") & (df["sku"].astype(str).str.strip() != "")]
    if pend.empty:
        return {}
    return pend.groupby(pend["sku"].astype(str).str.strip().str.upper())["cantidad"].sum().to_dict()


def auto_reconcile(proveedor, lineas_df, bill_id=""):
    """Al crear un Bill, cierra automáticamente las entradas pendientes de
    ese proveedor que coincidan por SKU (más antigua primero), descontando
    la cantidad recibida. lineas_df necesita columnas 'SKU' y 'Qty' (las
    mismas que ya usa create_bill). Devuelve cuántas entradas quedaron
    totalmente cerradas."""
    df = _load()
    if df.empty:
        return 0

    proveedor_norm = str(proveedor).strip().upper()
    cerradas = 0
    hoy = datetime.now().strftime("%Y-%m-%d")

    for _, row in lineas_df.iterrows():
        sku = str(row.get("SKU", "")).strip().upper()
        qty_recibida = float(row.get("Qty", 0) or 0)
        if not sku or qty_recibida <= 0:
            continue

        candidatos = df[
            (df["proveedor"].astype(str).str.strip().str.upper() == proveedor_norm)
            & (df["sku"].astype(str).str.strip().str.upper() == sku)
            & (df["estado"] == "pendiente")
        ].sort_values("fecha_envio")

        for idx in candidatos.index:
            if qty_recibida <= 0:
                break
            cantidad_pendiente = float(df.loc[idx, "cantidad"])
            if qty_recibida >= cantidad_pendiente:
                df.loc[idx, "estado"] = "recibido"
                df.loc[idx, "fecha_recibido"] = hoy
                if bill_id:
                    df.loc[idx, "bill_id"] = str(bill_id)
                qty_recibida -= cantidad_pendiente
                cerradas += 1
            else:
                # Recepción parcial: se descuenta de esta entrada y queda
                # pendiente por el resto, en vez de cerrarla de una vez.
                df.loc[idx, "cantidad"] = cantidad_pendiente - qty_recibida
                qty_recibida = 0

    _save(df)
    return cerradas
