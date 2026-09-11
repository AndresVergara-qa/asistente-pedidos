"""
Integración con QuickBooks Online (QBO).

Maneja: login OAuth2, guardado/renovación de tokens (persistidos en la misma
hoja de Google Sheets que ya usa la app para la memoria de precios, en una
pestaña separada "qb_tokens"), descarga del catálogo de productos, y creación
de Sales Receipts (Ventas) y Bills (Compras) directamente en QuickBooks.
"""
import time
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from quickbooks import QuickBooks
from quickbooks.objects.item import Item
from quickbooks.objects.customer import Customer
from quickbooks.objects.vendor import Vendor
from quickbooks.objects.salesreceipt import SalesReceipt
from quickbooks.objects.detailline import SalesItemLine, SalesItemLineDetail
from quickbooks.objects.bill import Bill
from quickbooks.objects.detailline import ItemBasedExpenseLine, ItemBasedExpenseLineDetail
from quickbooks.objects.base import Ref

TOKENS_WORKSHEET = "qb_tokens"
ACCESS_TOKEN_LIFETIME_MIN = 55  # QBO da 60 min; refrescamos un poco antes


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


def _load_stored_tokens():
    try:
        conn = _get_gsheets_conn()
        df = conn.read(worksheet=TOKENS_WORKSHEET, ttl=0)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "realm_id": str(row.get("realm_id", "")).strip(),
            "access_token": str(row.get("access_token", "")).strip(),
            "refresh_token": str(row.get("refresh_token", "")).strip(),
            "expires_at": str(row.get("expires_at", "")).strip(),
            "environment": str(row.get("environment", QB_ENVIRONMENT)).strip(),
        }
    except Exception:
        return None


def _save_tokens(realm_id, access_token, refresh_token, expires_at, environment):
    conn = _get_gsheets_conn()
    df = pd.DataFrame([{
        "realm_id": realm_id,
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
    st.session_state.pop("qb_client", None)


# =========================================================
# AUTH CLIENT / FLUJO OAUTH2
# =========================================================
def _new_auth_client(state_token="qb_auth"):
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
    st.session_state.pop("qb_client", None)
    return True


def is_connected():
    tokens = _load_stored_tokens()
    return bool(tokens and tokens.get("realm_id") and tokens.get("refresh_token"))


# =========================================================
# CLIENTE QUICKBOOKS (con auto-refresh de token)
# =========================================================
def get_client():
    """Devuelve un cliente QuickBooks listo para usar, refrescando el
    access_token si ya venció o está por vencer."""
    if "qb_client" in st.session_state:
        return st.session_state["qb_client"]

    tokens = _load_stored_tokens()
    if not tokens or not tokens.get("refresh_token"):
        raise RuntimeError("QuickBooks no está conectado todavía.")

    auth_client = _new_auth_client()
    auth_client.realm_id = tokens["realm_id"]

    needs_refresh = True
    try:
        expires_at = datetime.fromisoformat(tokens["expires_at"])
        needs_refresh = datetime.utcnow() >= expires_at
    except Exception:
        needs_refresh = True

    if needs_refresh:
        auth_client.refresh(refresh_token=tokens["refresh_token"])
        expires_at_new = (datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_LIFETIME_MIN)).isoformat()
        _save_tokens(
            realm_id=tokens["realm_id"],
            access_token=auth_client.access_token,
            refresh_token=auth_client.refresh_token,
            expires_at=expires_at_new,
            environment=tokens["environment"],
        )
    else:
        auth_client.access_token = tokens["access_token"]
        auth_client.refresh_token = tokens["refresh_token"]

    client = QuickBooks(
        auth_client=auth_client,
        refresh_token=auth_client.refresh_token,
        company_id=tokens["realm_id"],
        minorversion=75,
    )
    if client.session is None:
        raise RuntimeError(
            f"QuickBooks() se creó sin sesión. access_token presente={bool(auth_client.access_token)}, "
            f"refresh_token presente={bool(auth_client.refresh_token)}, needs_refresh={needs_refresh}, "
            f"realm_id={tokens['realm_id']!r}"
        )
    st.session_state["qb_client"] = client
    return client


# =========================================================
# CATÁLOGO
# =========================================================
def fetch_catalog_df():
    """Trae los productos/servicios activos de QuickBooks como DataFrame,
    con las mismas columnas que la app ya sabe leer de un Excel manual."""
    client = get_client()
    items = Item.filter(Active=True, max_results=1000, qb=client)

    rows = []
    for it in items:
        rows.append({
            "Product/Service": it.Name,
            "SKU": getattr(it, "Sku", "") or "",
            "Sales Description": getattr(it, "Description", "") or "",
            "Purchase Description": getattr(it, "PurchaseDesc", "") or "",
        })
    return pd.DataFrame(rows)


# =========================================================
# CLIENTES / PROVEEDORES (buscar o crear)
# =========================================================
def get_or_create_customer(display_name):
    client = get_client()
    matches = Customer.filter(DisplayName=display_name, qb=client)
    if matches:
        return matches[0]
    customer = Customer()
    customer.DisplayName = display_name
    customer.save(qb=client)
    return customer


def get_or_create_vendor(display_name):
    client = get_client()
    matches = Vendor.filter(DisplayName=display_name, qb=client)
    if matches:
        return matches[0]
    vendor = Vendor()
    vendor.DisplayName = display_name
    vendor.save(qb=client)
    return vendor


def _find_item_id(client, product_name, sku):
    """Busca el Item en QuickBooks por SKU (preferido) o por nombre exacto,
    justo antes de crear el documento, para asegurar que el Id sea válido."""
    if sku:
        matches = Item.filter(Sku=str(sku), qb=client)
        if matches:
            return matches[0].Id
    if product_name:
        matches = Item.filter(Name=str(product_name), qb=client)
        if matches:
            return matches[0].Id
    return None


# =========================================================
# CREAR SALES RECEIPT (Ventas)
# =========================================================
def create_sales_receipt(cliente_nombre, lineas_df):
    """lineas_df necesita columnas: Product/service, SKU, Qty, Rate.
    Devuelve el objeto SalesReceipt creado (con .Id y .DocNumber)."""
    client = get_client()
    customer = get_or_create_customer(cliente_nombre)

    receipt = SalesReceipt()
    receipt.CustomerRef = Ref()
    receipt.CustomerRef.value = customer.Id

    faltantes = []
    for _, row in lineas_df.iterrows():
        product_name = str(row.get("Product/service", "")).strip()
        sku = str(row.get("SKU", "")).strip()
        qty = float(row.get("Qty", 0) or 0)
        rate = float(row.get("Rate", 0) or 0)
        if not product_name or qty <= 0:
            continue

        item_id = _find_item_id(client, product_name, sku)
        if not item_id:
            faltantes.append(product_name)
            continue

        line = SalesItemLine()
        line.Amount = round(qty * rate, 2)
        line.SalesItemLineDetail = SalesItemLineDetail()
        line.SalesItemLineDetail.ItemRef = Ref()
        line.SalesItemLineDetail.ItemRef.value = item_id
        line.SalesItemLineDetail.Qty = qty
        line.SalesItemLineDetail.UnitPrice = rate
        receipt.Line.append(line)

    if faltantes:
        raise ValueError(
            "No se encontraron en QuickBooks (con ese SKU/nombre exacto) estos productos: "
            + ", ".join(faltantes) + ". Corrige el SKU/nombre en la tabla y vuelve a intentar."
        )
    if not receipt.Line:
        raise ValueError("No hay líneas válidas para crear el Sales Receipt.")

    receipt.save(qb=client)
    return receipt


# =========================================================
# CREAR BILL (Compras)
# =========================================================
def create_bill(vendor_nombre, lineas_df):
    """lineas_df necesita columnas: Product/service, SKU, Qty, Cost.
    Devuelve el objeto Bill creado (con .Id y .DocNumber)."""
    client = get_client()
    vendor = get_or_create_vendor(vendor_nombre)

    bill = Bill()
    bill.VendorRef = Ref()
    bill.VendorRef.value = vendor.Id

    faltantes = []
    for _, row in lineas_df.iterrows():
        product_name = str(row.get("Product/service", "")).strip()
        sku = str(row.get("SKU", "")).strip()
        qty = float(row.get("Qty", 0) or 0)
        cost = float(row.get("Cost", 0) or 0)
        if not product_name or qty <= 0:
            continue

        item_id = _find_item_id(client, product_name, sku)
        if not item_id:
            faltantes.append(product_name)
            continue

        line = ItemBasedExpenseLine()
        line.Amount = round(qty * cost, 2)
        line.ItemBasedExpenseLineDetail = ItemBasedExpenseLineDetail()
        line.ItemBasedExpenseLineDetail.ItemRef = Ref()
        line.ItemBasedExpenseLineDetail.ItemRef.value = item_id
        line.ItemBasedExpenseLineDetail.Qty = qty
        line.ItemBasedExpenseLineDetail.UnitPrice = cost
        bill.Line.append(line)

    if faltantes:
        raise ValueError(
            "No se encontraron en QuickBooks (con ese SKU/nombre exacto) estos productos: "
            + ", ".join(faltantes) + ". Corrige el SKU/nombre en la tabla y vuelve a intentar."
        )
    if not bill.Line:
        raise ValueError("No hay líneas válidas para crear el Bill.")

    bill.save(qb=client)
    return bill
