"""
Integración con QuickBooks Online (QBO) — hablando directo con su API REST.

Maneja: login OAuth2, guardado/renovación de tokens (persistidos en la misma
hoja de Google Sheets que ya usa la app para la memoria de precios, en una
pestaña separada "qb_tokens"), descarga del catálogo de productos, y creación
de Sales Receipts (Ventas) y Bills (Compras) directamente en QuickBooks.
"""
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes

TOKENS_WORKSHEET = "qb_tokens"
ACCESS_TOKEN_LIFETIME_MIN = 55  # QBO da 60 min; refrescamos un poco antes
MINOR_VERSION = 75

BASE_URLS = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com/v3/company",
    "production": "https://quickbooks.api.intuit.com/v3/company",
}


def _secret(key, default=""):
    return st.secrets.get(key, default)


QB_CLIENT_ID = _secret("QB_CLIENT_ID")
QB_CLIENT_SECRET = _secret("QB_CLIENT_SECRET")
QB_ENVIRONMENT = _secret("QB_ENVIRONMENT", "sandbox")  # "sandbox" o "production"
QB_REDIRECT_URI = _secret("QB_REDIRECT_URI")


def is_configured():
    return bool(QB_CLIENT_ID and QB_CLIENT_SECRET and QB_REDIRECT_URI)


# =========================================================
# PERSISTENCIA DE TOKENS (pestaña "qb_tokens" en el mismo Google Sheet)
# =========================================================
def _get_gsheets_conn():
    from gsheets_utils import get_gsheets_connection
    return get_gsheets_connection()


def _clean_numeric_str(val):
    """Google Sheets/pandas a veces interpreta IDs largos (ej. el Realm ID de
    QuickBooks) como número y les agrega '.0' al leerlos, o deja la comilla
    inicial que usamos para forzar texto literal. Esto revierte ambos casos."""
    s = str(val).strip()
    if s.startswith("'"):
        s = s[1:]
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _load_stored_tokens():
    try:
        conn = _get_gsheets_conn()
        df = conn.read(worksheet=TOKENS_WORKSHEET, ttl=0)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        tokens = {
            "realm_id": _clean_numeric_str(row.get("realm_id", "")),
            "access_token": str(row.get("access_token", "")).strip(),
            "refresh_token": str(row.get("refresh_token", "")).strip(),
            "expires_at": str(row.get("expires_at", "")).strip(),
            "environment": str(row.get("environment", QB_ENVIRONMENT)).strip(),
        }
        if not tokens["realm_id"] or not tokens["refresh_token"]:
            return None
        return tokens
    except Exception:
        return None


def _save_tokens(realm_id, access_token, refresh_token, expires_at, environment):
    conn = _get_gsheets_conn()
    # Google Sheets interpreta el Realm ID (puros dígitos) como número y, al
    # tener 16 dígitos, pierde precisión (supera lo que un float64 puede
    # representar exacto). La comilla inicial fuerza texto literal en Sheets.
    realm_id_text = f"'{realm_id}" if realm_id else realm_id
    df = pd.DataFrame([{
        "realm_id": realm_id_text,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "environment": environment,
    }])
    conn.update(worksheet=TOKENS_WORKSHEET, data=df)


def disconnect():
    """Borra los tokens guardados (obliga a reconectar)."""
    try:
        _save_tokens("", "", "", "", QB_ENVIRONMENT)
    except Exception:
        pass
    st.session_state.pop("qb_access_token", None)
    st.session_state.pop("qb_realm_id", None)


# =========================================================
# AUTH CLIENT / FLUJO OAUTH2
# =========================================================
def _new_auth_client():
    return AuthClient(
        client_id=QB_CLIENT_ID,
        client_secret=QB_CLIENT_SECRET,
        redirect_uri=QB_REDIRECT_URI,
        environment=QB_ENVIRONMENT,
    )


def get_authorization_url():
    auth_client = _new_auth_client()
    return auth_client.get_authorization_url([Scopes.ACCOUNTING])


def handle_oauth_callback():
    """Si la URL trae ?code=...&realmId=... (Intuit acaba de redirigir de
    vuelta tras el login), intercambia el código por tokens y los guarda.
    Devuelve True si acabó de conectar QuickBooks en esta corrida."""
    params = st.query_params
    code = params.get("code")
    realm_id = params.get("realmId")
    if not code or not realm_id:
        return False

    auth_client = _new_auth_client()
    auth_client.get_bearer_token(code, realm_id=realm_id)

    expires_at = (datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_LIFETIME_MIN)).isoformat()
    _save_tokens(
        realm_id=realm_id,
        access_token=auth_client.access_token,
        refresh_token=auth_client.refresh_token,
        expires_at=expires_at,
        environment=QB_ENVIRONMENT,
    )
    st.query_params.clear()
    st.session_state.pop("qb_access_token", None)
    st.session_state.pop("qb_realm_id", None)
    return True


def is_connected():
    tokens = _load_stored_tokens()
    return bool(tokens and tokens.get("realm_id") and tokens.get("refresh_token"))


# =========================================================
# ACCESS TOKEN VÁLIDO (con auto-refresh)
# =========================================================
def _get_valid_access_token():
    """Devuelve (access_token, realm_id) frescos, refrescando en QuickBooks
    si el access_token ya venció o está por vencer."""
    if "qb_access_token" in st.session_state and "qb_realm_id" in st.session_state:
        return st.session_state["qb_access_token"], st.session_state["qb_realm_id"]

    tokens = _load_stored_tokens()
    if not tokens:
        raise RuntimeError("QuickBooks no está conectado todavía.")

    needs_refresh = True
    try:
        expires_at = datetime.fromisoformat(tokens["expires_at"])
        needs_refresh = datetime.utcnow() >= expires_at
    except Exception:
        needs_refresh = True

    if needs_refresh:
        auth_client = _new_auth_client()
        auth_client.refresh(refresh_token=tokens["refresh_token"])
        access_token = auth_client.access_token
        expires_at_new = (datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_LIFETIME_MIN)).isoformat()
        _save_tokens(
            realm_id=tokens["realm_id"],
            access_token=access_token,
            refresh_token=auth_client.refresh_token,
            expires_at=expires_at_new,
            environment=tokens["environment"],
        )
    else:
        access_token = tokens["access_token"]

    st.session_state["qb_access_token"] = access_token
    st.session_state["qb_realm_id"] = tokens["realm_id"]
    return access_token, tokens["realm_id"]


# =========================================================
# LLAMADAS HTTP CRUDAS A LA API DE QBO
# =========================================================
def _base_url(realm_id):
    return f"{BASE_URLS.get(QB_ENVIRONMENT, BASE_URLS['sandbox'])}/{realm_id}"


def _request(method, path, params=None, json_body=None, retry=True):
    access_token, realm_id = _get_valid_access_token()
    url = f"{_base_url(realm_id)}/{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    kwargs = {"headers": headers, "params": params or {}}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        kwargs["json"] = json_body

    resp = requests.request(method, url, timeout=30, **kwargs)

    if resp.status_code == 401 and retry:
        # El access_token pudo haber vencido justo ahora; forzamos un refresh y reintentamos una vez.
        st.session_state.pop("qb_access_token", None)
        st.session_state.pop("qb_realm_id", None)
        return _request(method, path, params=params, json_body=json_body, retry=False)

    if not resp.ok:
        raise RuntimeError(f"QuickBooks API error {resp.status_code}: {resp.text[:500]}")

    if not resp.text:
        return {}
    return resp.json()


def _query(select):
    params = {"query": select, "minorversion": MINOR_VERSION}
    result = _request("GET", "query", params=params)
    return result.get("QueryResponse", {})


def _escape_sql(value):
    return str(value).replace("'", "\\'")


# =========================================================
# CATÁLOGO
# =========================================================
def fetch_catalog_df():
    """Trae los productos/servicios activos de QuickBooks como DataFrame,
    con las mismas columnas que la app ya sabe leer de un Excel manual."""
    items = _query("SELECT * FROM Item WHERE Active = true MAXRESULTS 1000").get("Item", [])

    rows = []
    for it in items:
        rows.append({
            "Product/Service": it.get("Name", ""),
            "SKU": it.get("Sku", "") or "",
            "Sales Description": it.get("Description", "") or "",
            "Purchase Description": it.get("PurchaseDesc", "") or "",
        })
    return pd.DataFrame(rows)


# =========================================================
# CLIENTES / PROVEEDORES (buscar o crear)
# =========================================================
def get_or_create_customer(display_name):
    safe_name = _escape_sql(display_name)
    matches = _query(f"SELECT * FROM Customer WHERE DisplayName = '{safe_name}'").get("Customer", [])
    if matches:
        return matches[0]
    result = _request("POST", "customer", json_body={"DisplayName": display_name})
    return result["Customer"]


def get_or_create_vendor(display_name):
    safe_name = _escape_sql(display_name)
    matches = _query(f"SELECT * FROM Vendor WHERE DisplayName = '{safe_name}'").get("Vendor", [])
    if matches:
        return matches[0]
    result = _request("POST", "vendor", json_body={"DisplayName": display_name})
    return result["Vendor"]


def _find_item_id(product_name, sku):
    """Busca el Item en QuickBooks por SKU (preferido) o por nombre exacto,
    justo antes de crear el documento, para asegurar que el Id sea válido."""
    if sku:
        matches = _query(f"SELECT * FROM Item WHERE Sku = '{_escape_sql(sku)}'").get("Item", [])
        if matches:
            return matches[0]["Id"]
    if product_name:
        matches = _query(f"SELECT * FROM Item WHERE Name = '{_escape_sql(product_name)}'").get("Item", [])
        if matches:
            return matches[0]["Id"]
    return None


# =========================================================
# CREAR ESTIMATE (Ventas) — cotización pendiente, no un cobro
# =========================================================
def create_estimate(cliente_nombre, lineas_df):
    """lineas_df necesita columnas: Product/service, SKU, Qty, Rate.
    Devuelve el Estimate creado (dict, con Id y DocNumber). Queda como
    'Pending' en QuickBooks — no registra ningún cobro."""
    customer = get_or_create_customer(cliente_nombre)

    lines = []
    faltantes = []
    for _, row in lineas_df.iterrows():
        product_name = str(row.get("Product/service", "")).strip()
        sku = str(row.get("SKU", "")).strip()
        qty = float(row.get("Qty", 0) or 0)
        rate = float(row.get("Rate", 0) or 0)
        if not product_name or qty <= 0:
            continue

        item_id = _find_item_id(product_name, sku)
        if not item_id:
            faltantes.append(product_name)
            continue

        lines.append({
            "Amount": round(qty * rate, 2),
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {
                "ItemRef": {"value": item_id},
                "Qty": qty,
                "UnitPrice": rate,
            },
        })

    if faltantes:
        raise ValueError(
            "No se encontraron en QuickBooks (con ese SKU/nombre exacto) estos productos: "
            + ", ".join(faltantes) + ". Corrige el SKU/nombre en la tabla y vuelve a intentar."
        )
    if not lines:
        raise ValueError("No hay líneas válidas para crear el Estimate.")

    body = {
        "CustomerRef": {"value": customer["Id"]},
        "TxnStatus": "Pending",
        "Line": lines,
    }
    result = _request("POST", "estimate", json_body=body)
    return result["Estimate"]


# =========================================================
# CREAR BILL (Compras)
# =========================================================
def create_bill(vendor_nombre, lineas_df):
    """lineas_df necesita columnas: Product/service, SKU, Qty, Cost.
    Devuelve el Bill creado (dict, con Id y DocNumber)."""
    vendor = get_or_create_vendor(vendor_nombre)

    lines = []
    faltantes = []
    for _, row in lineas_df.iterrows():
        product_name = str(row.get("Product/service", "")).strip()
        sku = str(row.get("SKU", "")).strip()
        qty = float(row.get("Qty", 0) or 0)
        cost = float(row.get("Cost", 0) or 0)
        if not product_name or qty <= 0:
            continue

        item_id = _find_item_id(product_name, sku)
        if not item_id:
            faltantes.append(product_name)
            continue

        lines.append({
            "Amount": round(qty * cost, 2),
            "DetailType": "ItemBasedExpenseLineDetail",
            "ItemBasedExpenseLineDetail": {
                "ItemRef": {"value": item_id},
                "Qty": qty,
                "UnitPrice": cost,
            },
        })

    if faltantes:
        raise ValueError(
            "No se encontraron en QuickBooks (con ese SKU/nombre exacto) estos productos: "
            + ", ".join(faltantes) + ". Corrige el SKU/nombre en la tabla y vuelve a intentar."
        )
    if not lines:
        raise ValueError("No hay líneas válidas para crear el Bill.")

    body = {
        "VendorRef": {"value": vendor["Id"]},
        "Line": lines,
    }
    result = _request("POST", "bill", json_body=body)
    return result["Bill"]
