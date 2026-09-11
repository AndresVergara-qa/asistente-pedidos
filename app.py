import os
import json
import re
import difflib
from datetime import datetime
import pandas as pd
import streamlit as st
import google.generativeai as genai
from PIL import Image
from gsheets_utils import get_gsheets_connection
import qb_client

# =========================================================
# CONFIGURACIÓN DE PÁGINA
# =========================================================
st.set_page_config(
    page_title="Convenient Distributor | Asistente de Pedidos y Compras",
    page_icon="📦",
    layout="wide"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_API_KEY = st.secrets.get("GEMINI_API_KEY", "")

# Si Intuit nos acaba de redirigir de vuelta tras el login (?code=...&realmId=...),
# intercambiamos el código por tokens antes de dibujar el resto de la página.
if qb_client.is_configured() and qb_client.handle_oauth_callback():
    st.success("✅ QuickBooks conectado correctamente.")
    st.rerun()

st.markdown("""
<style>
.cd-hero {
    background: linear-gradient(135deg, #2A1454 0%, #3D1F8C 55%, #4A2FB8 100%);
    border-radius: 18px;
    padding: 2.2rem 2rem;
    margin-bottom: 1.5rem;
    text-align: center;
}
.cd-hero h1 {
    color: #FFFFFF;
    font-weight: 800;
    font-size: 2.1rem;
    margin: 0 0 0.3rem 0;
}
.cd-hero h1 span { color: #FFD100; }
.cd-hero p {
    color: #E4DBFA;
    font-size: 1.05rem;
    margin: 0;
}
div.stButton > button[kind="primary"], div.stButton > button[kind="primaryFormSubmit"] {
    background-color: #2A1454;
    border: none;
    border-radius: 999px;
    font-weight: 700;
}
div.stButton > button[kind="primary"]:hover {
    background-color: #4A2FB8;
}
</style>
<div class="cd-hero">
    <h1>Convenient <span>Distributor</span></h1>
    <p>🧾 Asistente Integral de Pedidos y Compras — mapeo inteligente con Inteligencia Artificial</p>
</div>
""", unsafe_allow_html=True)

# =========================================================
# MODELO DE IA — LÓGICA ÚNICA Y COMPARTIDA (Ventas + Compras)
# =========================================================
# Antes esta lista vivía duplicada en cada pestaña y se fue desincronizando
# (la de compras terminó con el orden invertido y sin el prefijo correcto,
# usando el modelo más débil primero cuando list_models() fallaba).
# Ahora hay UNA sola fuente de verdad: se pregunta a la API cuáles modelos
# están realmente disponibles para esta clave y se ordenan por VERSIÓN
# (la más alta primero), en vez de nombres fijos que quedan obsoletos cada
# vez que Google lanza una nueva generación (ya nos pasó con 2.5-pro).
GEMINI_FALLBACK_PRIORITY = [
    "gemini-flash-latest",
    "gemini-pro-latest",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

def _extract_version(name):
    m = re.search(r'(\d+(?:\.\d+)?)', name)
    return float(m.group(1)) if m else 0.0

def _priority_rank(model_name):
    n = model_name.lower()
    version = _extract_version(n)
    is_flash = "flash" in n
    is_pro = "pro" in n
    # Versión más alta primero; entre versiones iguales, flash antes que pro (más rápido)
    return (-version, 0 if is_flash else (1 if is_pro else 2), n)

def _is_usable_model(model_name):
    n = model_name.lower()
    # Solo familia Gemini: excluye Gemma (cuota/contexto mucho más chicos, no apto
    # para catálogos grandes), embeddings, generación de imagen, TTS, etc.
    if "gemini" not in n:
        return False
    if any(bad in n for bad in ["embedding", "aqa", "imagen-", "tts", "image-generation", "learnlm", "vision"]):
        return False
    return True

@st.cache_data(ttl=3600, show_spinner=False)
def get_model_candidates(_api_key_hash, _v=3):
    """Pregunta a la API qué modelos están disponibles para esta clave y los
    ordena por versión (más alta primero). Si la consulta falla, cae de
    vuelta a alias "-latest" que Google mantiene apuntando al modelo vigente,
    en vez de nombres de versión fijos que quedan obsoletos con el tiempo."""
    try:
        available = [
            m.name for m in genai.list_models()
            if "generateContent" in getattr(m, "supported_generation_methods", [])
        ]
        available = [m for m in available if _is_usable_model(m)]
        if available:
            return sorted(available, key=_priority_rank)
    except Exception:
        pass
    return GEMINI_FALLBACK_PRIORITY

def call_gemini(parts, spinner_text="🤖 Conectando con la Inteligencia Artificial..."):
    """
    Intenta los modelos disponibles en orden de prioridad (mejor primero) y
    devuelve (texto_respuesta, nombre_modelo_usado, error).
    Si ya encontramos un modelo que funciona en esta sesión, lo probamos
    primero (evita repetir el descubrimiento en cada clic). El número de
    intentos y el tiempo por intento están acotados para que un modelo lento
    o con problemas no bloquee la app por varios minutos.
    """
    last_error = ""
    preferred = st.session_state.get("working_model")
    candidates = get_model_candidates(DEFAULT_API_KEY[-8:] if DEFAULT_API_KEY else "none")
    ordered = ([preferred] if preferred else []) + [c for c in candidates if c != preferred]
    ordered = ordered[:5]  # tope de intentos para acotar la espera máxima

    with st.spinner(spinner_text):
        for i, model_name in enumerate(ordered, start=1):
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(parts, request_options={"timeout": 25})
                if response and response.text:
                    st.session_state["working_model"] = model_name
                    return response.text, model_name, None
            except Exception as err:
                last_error = f"{model_name}: {err}"
                continue
    return None, None, last_error


def parse_json_response(raw_text):
    """Limpia fences de markdown y parsea JSON. Lanza JSONDecodeError si falla."""
    cleaned = re.sub(r"^```(?:json)?", "", raw_text.strip(), flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    return json.loads(cleaned)


def safe_float(val, default=0.0):
    try:
        if val is None or val == "":
            return default
        return float(str(val).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return default


# =========================================================
# GOOGLE SHEETS (Memoria de precios — solo Ventas)
# =========================================================
def load_price_memory():
    try:
        conn = get_gsheets_connection()
        df = conn.read(ttl=0)
        if df is None or df.empty:
            return pd.DataFrame(columns=["Cliente", "SKU", "Producto", "Precio", "Fecha"])
        return df.astype(str)
    except Exception as e:
        st.warning(f"⚠️ No se pudo cargar la memoria desde Google Sheets (Usando tabla vacía). Error: {e}")
        return pd.DataFrame(columns=["Cliente", "SKU", "Producto", "Precio", "Fecha"])

def save_price_memory(cliente_nombre, df_editado):
    memory_df = load_price_memory()
    hoy = datetime.now().strftime("%Y-%m-%d")

    for _, row in df_editado.iterrows():
        sku = str(row.get("SKU", "")).strip()
        producto = str(row.get("Product/service", "")).strip()
        rate = safe_float(row.get("Rate", 0.0))

        if rate > 0 and (sku or producto):
            mask = (memory_df["Cliente"].astype(str).str.strip().str.upper() == cliente_nombre.strip().upper()) & (
                (memory_df["Producto"].astype(str).str.strip().str.upper() == producto.upper()) |
                ((memory_df["SKU"].astype(str).str.strip() != "") & (memory_df["SKU"].astype(str).str.strip() == sku))
            )
            memory_df = memory_df[~mask]

            nuevo_registro = pd.DataFrame([{
                "Cliente": cliente_nombre.strip(),
                "SKU": sku,
                "Producto": producto,
                "Precio": str(rate),
                "Fecha": hoy,
            }])
            memory_df = pd.concat([memory_df, nuevo_registro], ignore_index=True)

    try:
        conn = get_gsheets_connection()
        conn.update(data=memory_df)
        st.success(f"¡Precios de '{cliente_nombre.strip()}' guardados exitosamente en Google Sheets!")
    except Exception as e:
        st.error(f"❌ Error al guardar en Google Sheets: {e}")


# =========================================================
# FUNCIONES DE APOYO
# =========================================================
def load_dataframe(file_or_path):
    if isinstance(file_or_path, str):
        if file_or_path.endswith((".xlsx", ".xls")):
            return pd.read_excel(file_or_path)
        return pd.read_csv(file_or_path)
    if file_or_path.name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file_or_path)
    return pd.read_csv(file_or_path)

def clean_val(val):
    if pd.isna(val) or val is None:
        return ""
    val_str = str(val).strip()
    if val_str.endswith(".0"):
        val_str = val_str[:-2]
    return val_str

def normalize(s):
    return re.sub(r"\s+", " ", str(s).strip().upper())


# =========================================================
# MATCHING ROBUSTO — usado por Ventas y Compras
# =========================================================
def build_catalog_index(qb_df, prod_col):
    """Normaliza los nombres del catálogo UNA sola vez por pedido (no por producto).
    Recalcularlo por cada línea del pedido era el cuello de botella que hacía
    la app lenta con catálogos grandes (3000+ productos)."""
    return qb_df[prod_col].astype(str).apply(normalize)

def find_best_match(qb_df, prod_col, sku_col, p_name, sku_hint="", catalog_norm=None):
    """
    Busca la mejor fila del catálogo para un producto extraído por la IA.
    Prioridad: 1) SKU exacto  2) Nombre exacto  3) Nombre aproximado (fuzzy)
    Devuelve (fila_o_None, estado, nombre_alternativo_o_None)
    estado ∈ {"sku", "exacto", "aproximado", "ambiguo", "sin_match"}
    `catalog_norm` debe venir precalculado con build_catalog_index() para no
    repetir la normalización de todo el catálogo en cada llamada.
    """
    p_norm = normalize(p_name)
    sku_norm = normalize(sku_hint)

    # 1. Coincidencia exacta por SKU (la más confiable — QuickBooks la resuelve solo)
    if sku_norm and sku_col:
        m = qb_df[qb_df[sku_col].astype(str).str.strip().str.upper() == sku_norm]
        if not m.empty:
            return m.iloc[0], "sku", None

    if not p_norm:
        return None, "sin_match", None

    if catalog_norm is None:
        catalog_norm = build_catalog_index(qb_df, prod_col)

    # 2. Coincidencia exacta por nombre
    m = qb_df[catalog_norm == p_norm]
    if not m.empty:
        return m.iloc[0], "exacto", None

    # 3. Coincidencia aproximada (fuzzy) sobre TODO el catálogo
    scored = []
    for idx, cand in catalog_norm.items():
        score = difflib.SequenceMatcher(None, p_norm, cand).ratio()
        # Si uno contiene literalmente al otro (ej. sufijo "8 OZ" faltante), sube el score
        if p_norm in cand or cand in p_norm:
            score = max(score, 0.85)
        scored.append((score, idx))
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return None, "sin_match", None

    best_score, best_idx = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0

    if best_score < 0.72:
        return None, "sin_match", None

    # Si el 2do candidato quedó muy cerca del 1ro, es una llamada dudosa (ej. variantes
    # "Chick Peas" vs "Organic Chick Peas") — mejor avisar que adivinar en silencio.
    if (best_score - second_score) < 0.06 and second_score >= 0.68:
        alt_name = qb_df.loc[scored[1][1], prod_col]
        return qb_df.loc[best_idx], "ambiguo", alt_name

    return qb_df.loc[best_idx], "aproximado", None


ESTADO_LABELS = {
    "sku": "✅ SKU exacto",
    "exacto": "✅ Exacto",
    "aproximado": "🟡 Aproximado",
    "ambiguo": "⚠️ Ambiguo",
    "sin_match": "❌ Sin match",
}

def resolve_item(qb_df, prod_col, sku_col, desc_col, p_name, sku_hint="", catalog_norm=None, fallback_desc_col=None):
    """Envuelve find_best_match y arma los campos finales + etiqueta de estado.
    Si desc_col no tiene valor para la fila encontrada (ej. falta la Purchase
    Description en QuickBooks), usa fallback_desc_col — igual que hace QuickBooks
    internamente cuando falta la descripción de compra."""
    row, status, alt_name = find_best_match(qb_df, prod_col, sku_col, p_name, sku_hint, catalog_norm=catalog_norm)

    if row is not None:
        actual_pname = row[prod_col]
        sku_val = clean_val(row[sku_col]) if sku_col else ""
        desc_val = clean_val(row[desc_col]) if desc_col else ""
        if not desc_val and fallback_desc_col:
            desc_val = clean_val(row[fallback_desc_col])
    else:
        actual_pname = p_name
        sku_val = sku_hint
        desc_val = ""

    estado = ESTADO_LABELS[status]
    if status == "ambiguo" and alt_name:
        estado = f"⚠️ Ambiguo (¿o será '{alt_name}'?)"

    return actual_pname, sku_val, desc_val, estado


# =========================================================
# BARRA LATERAL
# =========================================================
st.sidebar.header("QuickBooks Online")
with st.sidebar.expander("🐞 Debug temporal QB secrets"):
    st.write("QB_CLIENT_ID presente:", bool(qb_client.QB_CLIENT_ID), "| largo:", len(qb_client.QB_CLIENT_ID))
    st.write("QB_CLIENT_SECRET presente:", bool(qb_client.QB_CLIENT_SECRET), "| largo:", len(qb_client.QB_CLIENT_SECRET))
    st.write("QB_REDIRECT_URI:", repr(qb_client.QB_REDIRECT_URI))
    st.write("QB_ENVIRONMENT:", repr(qb_client.QB_ENVIRONMENT))
    st.write("Llaves visibles en st.secrets (nombres, no valores):", list(st.secrets.keys()))
qb_connected = qb_client.is_configured() and qb_client.is_connected()

if not qb_client.is_configured():
    st.sidebar.info("QuickBooks aún no está configurado (faltan QB_CLIENT_ID/SECRET en Secrets).")
elif qb_connected:
    st.sidebar.success(f"✅ Conectado ({qb_client.QB_ENVIRONMENT}).")
    if st.sidebar.button("🔌 Desconectar QuickBooks"):
        qb_client.disconnect()
        st.rerun()
else:
    auth_url = qb_client.get_authorization_url()
    st.sidebar.link_button("🔗 Conectar con QuickBooks", auth_url)

st.sidebar.header("Datos Globales")
NUEVO_CLIENTE_OPCION = "+ Nuevo cliente..."
customer_id_actual = None
if qb_connected:
    if st.sidebar.button("🔄 Traer clientes de QuickBooks"):
        st.session_state.pop("qb_customers", None)
    if "qb_customers" not in st.session_state:
        try:
            st.session_state["qb_customers"] = qb_client.list_customers()
        except Exception as e:
            st.sidebar.error(f"Error trayendo clientes de QuickBooks: {e}")
            st.session_state["qb_customers"] = []
    qb_customers = st.session_state.get("qb_customers", [])
    nombres = [c["DisplayName"] for c in qb_customers]
    elegido = st.sidebar.selectbox(
        "Cliente (Para Ventas)",
        [NUEVO_CLIENTE_OPCION] + nombres,
        help="Elige un cliente existente en QuickBooks, o crea uno nuevo.",
    )
    if elegido == NUEVO_CLIENTE_OPCION:
        cliente_actual = st.sidebar.text_input("Nombre del nuevo cliente", value="")
    else:
        cliente_actual = elegido
        customer_id_actual = next((c["Id"] for c in qb_customers if c["DisplayName"] == elegido), None)
else:
    cliente_actual = st.sidebar.text_input(
        "Nombre del Cliente (Para Ventas)",
        value="Cliente General",
        help="COLOCAR NOMBRE EXACTO DEL CLIENTE EN QUICKBOOKS"
    )

st.sidebar.header("Archivos de Referencia")

qb_df = None
if qb_connected:
    origen_catalogo = st.sidebar.radio(
        "Origen del catálogo",
        ["QuickBooks (automático)", "Archivo manual"],
        key="origen_catalogo",
    )
else:
    origen_catalogo = "Archivo manual"

if origen_catalogo == "QuickBooks (automático)":
    if st.sidebar.button("🔄 Traer catálogo de QuickBooks"):
        st.cache_data.clear()
        st.session_state.pop("qb_catalog_df", None)
    if "qb_catalog_df" not in st.session_state:
        try:
            with st.sidebar:
                with st.spinner("Descargando catálogo de QuickBooks..."):
                    st.session_state["qb_catalog_df"] = qb_client.fetch_catalog_df()
        except Exception as e:
            import traceback
            st.sidebar.error(f"Error trayendo catálogo de QuickBooks: {e}")
            with st.sidebar.expander("🐞 Traceback completo"):
                st.code(traceback.format_exc())
            st.session_state["qb_catalog_df"] = None
    qb_df = st.session_state.get("qb_catalog_df")
    if qb_df is not None and not qb_df.empty:
        st.sidebar.success(f"✅ Catálogo QB: {len(qb_df)} productos.")
        with st.sidebar.expander("🐞 Ver productos del catálogo (debug)"):
            st.dataframe(qb_df, hide_index=True)
else:
    qb_file = st.sidebar.file_uploader(
        "Catálogo de QuickBooks",
        type=["xlsx", "xls", "csv"],
        key="qb",
        help="SUBIR INVENTARIO COMPLETO DE QUICKBOOKS (Ventas y Compras)"
    )
    if qb_file:
        try:
            qb_df = load_dataframe(qb_file)
            st.sidebar.success(f"✅ Catálogo: {len(qb_df)} productos.")
        except Exception as e:
            st.sidebar.error(f"Error cargando Catálogo: {e}")

measures_df = None
LOCAL_MEASURES = [
    os.path.join(BASE_DIR, "tabla_medidas.xlsx"),
    os.path.join(BASE_DIR, "tabla_medidas.xls"),
    os.path.join(BASE_DIR, "tabla_medidas.csv"),
]
local_found = next((f for f in LOCAL_MEASURES if os.path.exists(f)), None)

measures_file = st.sidebar.file_uploader(
    "Tabla de Medidas",
    type=["xlsx", "xls", "csv"],
    key="measures",
    help="TABLA DE MEDIDAS DE PALLETS"
)

if measures_file:
    measures_df = load_dataframe(measures_file)
    st.sidebar.info("Tabla de medidas manual cargada.")
elif local_found:
    measures_df = load_dataframe(local_found)
    st.sidebar.success("✅ 'tabla_medidas' detectada.")

if qb_df is not None:
    cols_upper = {str(c).strip().upper(): c for c in qb_df.columns}
    prod_col = next((cols_upper[k] for k in ["PRODUCT/SERVICE", "NAME", "PRODUCT/SERVICE NAME", "PRODUCT"] if k in cols_upper), qb_df.columns[0])
    sku_col = next((cols_upper[k] for k in ["SKU", "ITEM SKU"] if k in cols_upper), None)
    # QuickBooks suele traer columnas de descripción SEPARADAS para Ventas y Compras.
    # Antes usábamos una sola columna para ambas pestañas, lo que hacía que Compras
    # mostrara por error la descripción de Ventas (o viceversa).
    sales_desc_col = next((cols_upper[k] for k in ["SALES DESCRIPTION", "DESCRIPTION", "DESCRP", "MEMO/DESCRIPTION"] if k in cols_upper), None)
    purchase_desc_col = next((cols_upper[k] for k in ["PURCHASE DESCRIPTION", "PURCHASE DESC", "DESCRIPTION", "DESCRP", "MEMO/DESCRIPTION"] if k in cols_upper), None)
else:
    prod_col = sku_col = sales_desc_col = purchase_desc_col = None


def tsv_from_df(df, cols, leading_blank=False):
    lines = []
    for _, row in df.iterrows():
        vals = [str(row[c]).replace("\t", " ").strip() if row[c] not in (None, "") else "" for c in cols]
        if leading_blank:
            vals = [""] + vals
        lines.append("\t".join(vals))
    return "\n".join(lines)


# =========================================================
# PESTAÑAS PRINCIPALES
# =========================================================
tab_ventas, tab_compras = st.tabs(["🛒 Procesar Pedidos (Ventas)", "📦 Ingreso de Facturas (Compras)"])

# =========================================================
# PESTAÑA 1: VENTAS
# =========================================================
with tab_ventas:
    st.markdown("### Ingresa el detalle del pedido")
    subtab1, subtab2 = st.tabs(["📷 Subir Imagen", "📝 Pegar Texto"])

    with subtab1:
        uploaded_image = st.file_uploader("Sube la captura del pedido", type=["png", "jpg", "jpeg"])

    with subtab2:
        pasted_text = st.text_area("Pega aquí el mensaje del pedido de WhatsApp", height=150)

    if st.button("🚀 Procesar Pedido", type="primary"):
        if not DEFAULT_API_KEY:
            st.error("⚠️ Falta configurar la clave GEMINI_API_KEY en los Secrets de Streamlit Cloud.")
        elif qb_df is None:
            st.error("⚠️ Debes cargar el Catálogo de QuickBooks.")
        elif not uploaded_image and not pasted_text.strip():
            st.error("⚠️ Debes subir una imagen o pegar el texto del pedido.")
        else:
            try:
                genai.configure(api_key=DEFAULT_API_KEY.strip())

                cols_qb = [c for c in [prod_col, sku_col, sales_desc_col] if c is not None]
                catalog_csv = qb_df[cols_qb].dropna(subset=[prod_col]).to_csv(index=False)

                measures_csv = ""
                if measures_df is not None:
                    m_cols = [c for c in measures_df.columns if any(k in str(c).upper() for k in ["PROD", "PREST", "BOX", "PALLET", "MEDIDA"])]
                    measures_csv = measures_df[m_cols].dropna(how="all").to_csv(index=False) if m_cols else measures_df.to_csv(index=False)

                prompt = f"""
                Eres un experto en logística y facturación en QuickBooks para 'Convenient Distributor'.
                OBJETIVO: Extrae productos, calcula cantidades (Qty) y extrae el precio unitario (Rate)
                basándote en la información proporcionada (imagen o texto), el catálogo y la tabla de medidas.

                REGLAS DE PRODUCTOS:
                1. JACK DANIEL'S VARIETY PACK -> "JACK DANIEL'S" (CANS VARIETY PACK 2X12 OZ).
                2. CRUSH: Grape = "Crush Grape". Strawberry = "Crush STRAWBERRY 12pk x2".
                3. COCA-COLA ORIGINAL -> "COCA COLA 12PK".
                4. PRIME ICE POP -> "Prime Ice Pop 12/16.9 OZ BTL".

                IMPORTANTE — PRECISIÓN: el catálogo suele tener variantes muy parecidas de un mismo
                producto (ej. "Chick Peas" vs "Organic Chick Peas", "Green Peas" vs "Green Split Peas"
                vs "Tender Sweet Peas", "Garlic Powder" vs "Garlic Powder 8oz"). Compara letra por letra
                contra el catálogo antes de responder y elige la fila que más se parezca al texto del
                pedido, no la primera que se te ocurra.

                INSTRUCCIÓN DE PRECIO (RATE): busca en el pedido si el producto tiene un precio unitario
                asignado. Si aparece, extráelo en formato numérico (ej. 15.99). Si no aparece, coloca 0.0.

                CATÁLOGO QUICKBOOKS:
                {catalog_csv}

                TABLA DE MEDIDAS (para conversiones de pallets/cajas):
                {measures_csv}

                FORMATO DE SALIDA (JSON ESTRICTO. SOLO DEVUELVE EL ARREGLO JSON):
                [
                  {{
                    "texto_original": "texto detectado",
                    "producto_qb": "Nombre EXACTO tal cual aparece en el catálogo",
                    "sku_hint": "SKU EXACTO del catálogo para ese producto, si lo puedes identificar",
                    "qty": 50,
                    "rate": 15.99
                  }}
                ]
                """

                ai_input = [prompt]
                if uploaded_image:
                    ai_input.append(Image.open(uploaded_image))
                if pasted_text.strip():
                    ai_input.append(f"TEXTO DEL PEDIDO PROPORCIONADO:\n{pasted_text}")

                raw_text, used_model, error = call_gemini(ai_input)
                if raw_text is None:
                    st.error(f"❌ Error de conexión: {error}")
                    st.stop()

                try:
                    items = parse_json_response(raw_text)
                except json.JSONDecodeError:
                    st.error("❌ La IA no devolvió un JSON válido. Mira la respuesta cruda abajo para depurar.")
                    st.code(raw_text, language="text")
                    st.stop()

                st.session_state["ventas_raw_response"] = raw_text
                st.session_state["ventas_model_used"] = used_model

                with st.spinner("💰 Consultando Google Sheets y precios históricos..."):
                    price_mem = load_price_memory()
                    client_mem = price_mem[price_mem["Cliente"].astype(str).str.strip().str.upper() == cliente_actual.strip().upper()]

                    sku_prices, prod_prices = {}, {}
                    for _, r in client_mem.iterrows():
                        val_p = safe_float(r.get("Precio", "0"))
                        s_key = str(r.get("SKU", "")).strip()
                        p_key = str(r.get("Producto", "")).strip().upper()
                        if s_key:
                            sku_prices[s_key] = val_p
                        if p_key:
                            prod_prices[p_key] = val_p

                    catalog_norm_idx = build_catalog_index(qb_df, prod_col)
                    results = []
                    precio_notas = []
                    precios_pendientes = []  # filas sin precio que aún podríamos consultar en QB
                    for item in items:
                        p_name = str(item.get("producto_qb", "")).strip()
                        sku_hint = str(item.get("sku_hint", "")).strip()
                        extracted_rate = safe_float(item.get("rate", 0.0))

                        actual_pname, sku_val, desc_val, estado = resolve_item(
                            qb_df, prod_col, sku_col, sales_desc_col, p_name, sku_hint,
                            catalog_norm=catalog_norm_idx
                        )

                        recalled_rate = sku_prices.get(sku_val, 0.0)
                        if recalled_rate == 0.0:
                            recalled_rate = prod_prices.get(str(actual_pname).upper(), 0.0)
                        final_rate = recalled_rate if recalled_rate > 0.0 else extracted_rate

                        row_idx = len(results)
                        results.append({
                            "Estado": estado,
                            "Product/service": actual_pname,
                            "SKU": sku_val,
                            "Description": desc_val,
                            "Qty": item.get("qty", 1),
                            "Rate": final_rate,
                        })
                        if final_rate == 0.0 and qb_connected:
                            precios_pendientes.append((row_idx, actual_pname, sku_val))

                    if precios_pendientes:
                        with st.spinner(f"Consultando en QuickBooks el último precio vendido de {len(precios_pendientes)} producto(s)..."):
                            for row_idx, pname, sku_val in precios_pendientes:
                                try:
                                    item_id = qb_client._find_item_id(pname, sku_val)
                                    if not item_id:
                                        continue
                                    sugerido, nota = qb_client.suggest_price(item_id, customer_id=customer_id_actual)
                                    if sugerido:
                                        results[row_idx]["Rate"] = sugerido
                                        if nota:
                                            precio_notas.append(f"**{pname}**: {nota}")
                                except Exception:
                                    continue  # si falla la consulta de precio, se deja en 0 para revisión manual

                    st.session_state["res_df"] = pd.DataFrame(results)
                    st.session_state["precio_notas"] = precio_notas

            except Exception as err:
                st.error(f"❌ DETALLE DEL ERROR:\n\n`{type(err).__name__}: {err}`")

    if "res_df" in st.session_state:
        st.divider()
        st.subheader(f"🔍 Verificación del Pedido — Cliente: **{cliente_actual}**")

        n_revisar = st.session_state["res_df"]["Estado"].astype(str).str.startswith(("⚠️", "❌")).sum()
        if n_revisar > 0:
            st.warning(f"⚠️ {n_revisar} línea(s) necesitan revisión manual (columna Estado). Corrige el SKU/nombre en la tabla antes de copiar.")
        else:
            st.success("✅ Todas las líneas coinciden con el catálogo.")

        precio_notas = st.session_state.get("precio_notas", [])
        if precio_notas:
            st.warning("💰 Precios tomados del historial de QuickBooks — revisa antes de confirmar:\n\n" + "\n\n".join(precio_notas))

        with st.expander("🐞 Ver respuesta cruda de la IA (debug)"):
            st.caption(f"Modelo usado: {st.session_state.get('ventas_model_used', '—')}")
            st.code(st.session_state.get("ventas_raw_response", ""), language="json")

        edited_df = st.data_editor(
            st.session_state["res_df"],
            column_config={
                "Rate": st.column_config.NumberColumn("Rate ($)", format="$%.2f", min_value=0.0),
                "Estado": st.column_config.TextColumn("Estado", disabled=True),
            },
            width="stretch", hide_index=True,
        )

        if st.button("💾 Aprender y Guardar Precios"):
            save_price_memory(cliente_actual.strip(), edited_df)

        st.divider()
        st.subheader("📋 LISTO PARA QUICKBOOKS")
        st.info(
            "💡 Usa **Paste all lines** en QuickBooks y pega este texto tal cual. "
            "El SKU y la Cantidad/Rate siempre quedan correctos. El **nombre del producto** a veces "
            "queda en blanco cuando es un sub-item (ej. 'Goya:Producto') — es un comportamiento propio "
            "de QuickBooks al resolver nombres jerárquicos, no algo que la app pueda forzar. "
            "Si pasa, el SKU ya está en la fila: solo selecciona el producto del desplegable manualmente "
            "en esa línea puntual."
        )
        st.code(tsv_from_df(edited_df, ["Product/service", "SKU", "Description", "Qty", "Rate"], leading_blank=True), language="text")

        st.divider()
        if qb_connected:
            if st.button("📤 Crear Estimate en QuickBooks", type="primary"):
                try:
                    with st.spinner("Creando Estimate en QuickBooks..."):
                        estimate, faltantes = qb_client.create_estimate(
                            cliente_actual.strip(), edited_df, customer_id=customer_id_actual
                        )
                    st.success(f"✅ Estimate #{estimate.get('DocNumber', estimate.get('Id'))} creado en QuickBooks (Pending) para {cliente_actual.strip()}.")
                    if faltantes:
                        st.warning(
                            "⚠️ Estos productos no existían en el catálogo de QuickBooks — quedaron como línea de "
                            "texto en el Estimate (sin precio ni ítem vinculado). Créalos en QuickBooks y edita esa "
                            "línea manualmente: " + ", ".join(faltantes)
                        )
                except Exception as e:
                    st.error(f"❌ No se pudo crear el Estimate: {e}")
        else:
            st.caption("🔌 Conecta QuickBooks (barra lateral) para crear este Estimate directamente, en vez de copiar y pegar.")

# =========================================================
# PESTAÑA 2: COMPRAS
# =========================================================
with tab_compras:
    st.markdown("### Extraer datos de Facturas de Proveedores (Bills)")
    st.write("Sube la imagen de la factura. La IA extraerá los datos y cruzará la información con tu catálogo.")

    NUEVO_PROVEEDOR_OPCION = "+ Nuevo proveedor..."
    vendor_id_actual = None
    if qb_connected:
        if st.button("🔄 Traer proveedores de QuickBooks"):
            st.session_state.pop("qb_vendors", None)
        if "qb_vendors" not in st.session_state:
            try:
                st.session_state["qb_vendors"] = qb_client.list_vendors()
            except Exception as e:
                st.error(f"Error trayendo proveedores de QuickBooks: {e}")
                st.session_state["qb_vendors"] = []
        qb_vendors = st.session_state.get("qb_vendors", [])
        nombres_v = [v["DisplayName"] for v in qb_vendors]
        elegido_v = st.selectbox(
            "Proveedor (Vendor en QuickBooks)",
            [NUEVO_PROVEEDOR_OPCION] + nombres_v,
            help="Elige un proveedor existente en QuickBooks, o crea uno nuevo.",
        )
        if elegido_v == NUEVO_PROVEEDOR_OPCION:
            proveedor_actual = st.text_input("Nombre del nuevo proveedor", value="")
        else:
            proveedor_actual = elegido_v
            vendor_id_actual = next((v["Id"] for v in qb_vendors if v["DisplayName"] == elegido_v), None)
    else:
        proveedor_actual = st.text_input(
            "Nombre del Proveedor (Vendor en QuickBooks)",
            value="",
            help="COLOCAR NOMBRE EXACTO DEL PROVEEDOR EN QUICKBOOKS (se usa solo al crear el Bill)",
        )

    uploaded_bill = st.file_uploader("Sube la factura del proveedor", type=["png", "jpg", "jpeg"], key="bill_uploader")

    if st.button("⚡ Analizar Factura de Compra", type="primary"):
        if not DEFAULT_API_KEY:
            st.error("⚠️ Falta configurar la clave GEMINI_API_KEY en los Secrets.")
        elif qb_df is None:
            st.error("⚠️ Debes cargar el Catálogo de QuickBooks.")
        elif not uploaded_bill:
            st.error("⚠️ Debes subir una imagen de la factura del proveedor.")
        else:
            try:
                genai.configure(api_key=DEFAULT_API_KEY.strip())

                cols_qb_compras = [c for c in [prod_col, sku_col, purchase_desc_col] if c is not None]
                catalog_compras_csv = qb_df[cols_qb_compras].dropna(subset=[prod_col]).to_csv(index=False)

                prompt_compras = f"""
                Eres un experto analizando facturas de compras (Bills) y cruzando datos con inventarios.

                TAREAS:
                1. Extrae los productos reales de la factura. IGNORA: Taxes, Cupones, Freight y Pallets.
                2. Busca CADA producto en el CATÁLOGO DE QUICKBOOKS proporcionado.
                3. Debes devolver el NOMBRE EXACTO y el SKU EXACTO tal como aparecen en el Catálogo de
                   QuickBooks. No inventes nombres ni combines columnas. Ten cuidado con productos parecidos
                   entre sí (variantes orgánicas, tamaños, sabores) — compara letra por letra.

                CATÁLOGO DE QUICKBOOKS:
                {catalog_compras_csv}

                FORMATO DE SALIDA (JSON ESTRICTO):
                [
                    {{
                        "producto_qb": "Nombre del producto EXACTO extraído de la columna del catálogo",
                        "sku_qb": "SKU EXACTO extraído de la columna del catálogo",
                        "original_description": "Lo que dice la factura original del proveedor",
                        "qty": 10,
                        "cost": 15.50
                    }}
                ]
                Solo devuelve el JSON puro.
                """

                image_parts = [Image.open(uploaded_bill)]
                raw_text_compras, used_model_compras, error_compras = call_gemini(
                    [prompt_compras] + image_parts, spinner_text="📦 Analizando factura e identificando SKUs precisos..."
                )
                if raw_text_compras is None:
                    st.error(f"❌ Error al procesar la factura: {error_compras}")
                    st.stop()

                try:
                    datos_compras = parse_json_response(raw_text_compras)
                except json.JSONDecodeError:
                    st.error("❌ La IA no devolvió un JSON válido. Mira la respuesta cruda abajo para depurar.")
                    st.code(raw_text_compras, language="text")
                    st.stop()

                st.session_state["compras_raw_response"] = raw_text_compras
                st.session_state["compras_model_used"] = used_model_compras

                catalog_norm_idx_compras = build_catalog_index(qb_df, prod_col)
                results_compras = []
                for item in datos_compras:
                    p_name = str(item.get("producto_qb", "")).strip()
                    sku_qb = str(item.get("sku_qb", "")).strip()
                    cost_val = safe_float(item.get("cost", 0.0))
                    qty_val = item.get("qty", 1)

                    actual_pname, sku_val, desc_val, estado = resolve_item(
                        qb_df, prod_col, sku_col, purchase_desc_col, p_name, sku_qb,
                        catalog_norm=catalog_norm_idx_compras, fallback_desc_col=sales_desc_col
                    )
                    # Orden de prioridad para la descripción, igual que QuickBooks:
                    # 1) Purchase Description  2) Sales Description (si falta la de compra)
                    # Si ninguna de las dos existe en el catálogo, se deja vacía a propósito
                    # (no se usa el texto de la factura) para que sea evidente qué productos
                    # de tu QuickBooks no tienen descripción cargada.

                    results_compras.append({
                        "Estado": estado,
                        "Product/service": actual_pname,
                        "SKU": sku_val,
                        "Description": desc_val,
                        "Qty": qty_val,
                        "Cost": cost_val,
                    })

                st.session_state["res_compras"] = pd.DataFrame(results_compras)

            except Exception as e:
                st.error(f"❌ Error al procesar la factura: {e}")

    if "res_compras" in st.session_state:
        st.divider()
        st.success("¡Factura procesada con éxito!")

        n_revisar_c = st.session_state["res_compras"]["Estado"].astype(str).str.startswith(("⚠️", "❌")).sum()
        if n_revisar_c > 0:
            st.warning(f"⚠️ {n_revisar_c} línea(s) necesitan revisión manual (columna Estado).")
        else:
            st.success("✅ Todas las líneas coinciden con el catálogo.")

        with st.expander("👁️ Abrir / Cerrar Imagen de Factura Original", expanded=False):
            img_col, space_col = st.columns([1, 1])
            with img_col:
                st.image(uploaded_bill, use_container_width=True)

        with st.expander("🐞 Ver respuesta cruda de la IA (debug)"):
            st.caption(f"Modelo usado: {st.session_state.get('compras_model_used', '—')}")
            st.code(st.session_state.get("compras_raw_response", ""), language="json")

        st.subheader("🔍 Verificación y Edición de Compras (Bills)")
        edited_compras_df = st.data_editor(
            st.session_state["res_compras"],
            column_config={
                "Cost": st.column_config.NumberColumn("Cost ($)", format="$%.2f", min_value=0.0),
                "Estado": st.column_config.TextColumn("Estado", disabled=True),
            },
            width="stretch", hide_index=True
        )

        st.divider()
        st.subheader("📋 LISTO PARA QUICKBOOKS (BILLS)")
        st.info(
            "💡 Usa **Paste all lines** en QuickBooks y pega este texto tal cual. "
            "El SKU y la Cantidad/Cost siempre quedan correctos. El **nombre del producto** a veces "
            "queda en blanco cuando es un sub-item (ej. 'Goya:Producto') — es comportamiento propio de "
            "QuickBooks, no algo que la app pueda forzar. Si pasa, el SKU ya está en la fila: solo "
            "selecciona el producto del desplegable manualmente en esa línea puntual."
        )
        st.code(tsv_from_df(edited_compras_df, ["Product/service", "SKU", "Description", "Qty", "Cost"], leading_blank=True), language="text")

        st.divider()
        if qb_connected:
            if not proveedor_actual.strip():
                st.caption("✏️ Escribe el nombre del proveedor arriba para poder crear el Bill en QuickBooks.")
            elif st.button("📤 Crear Bill en QuickBooks", type="primary"):
                try:
                    with st.spinner("Creando Bill en QuickBooks..."):
                        bill = qb_client.create_bill(proveedor_actual.strip(), edited_compras_df, vendor_id=vendor_id_actual)
                    st.success(f"✅ Bill #{bill.get('DocNumber', bill.get('Id'))} creado en QuickBooks para {proveedor_actual.strip()}.")
                except Exception as e:
                    st.error(f"❌ No se pudo crear el Bill: {e}")
        else:
            st.caption("🔌 Conecta QuickBooks (barra lateral) para crear este Bill directamente, en vez de copiar y pegar.")

st.markdown("<br><br>", unsafe_allow_html=True)
st.divider()
with st.expander("❓ ¿Qué es esto y cómo funciona?"):
    st.markdown("""
    Esta aplicación es un **ayudante de estimados creado para Convenient Distributor**.

    Analiza pedidos (imagen o texto de WhatsApp) y facturas de proveedores, cruza la información
    contra tu catálogo de QuickBooks y la tabla de medidas de pallets, y te entrega los datos
    listos para copiar y pegar en QuickBooks.

    * **🧠 Memoria Inteligente (Ventas):** aprende y guarda precios por cliente en Google Sheets.
    * **✅ Columna Estado:** cada línea indica qué tan segura está la coincidencia con el catálogo
      (SKU exacto, exacto, aproximado, ambiguo o sin match) para que sepas exactamente qué revisar
      antes de pegar en QuickBooks.
    * **🛠️ Pegar desde SKU:** la forma más confiable de pegar en QuickBooks — deja que QB resuelva
      el nombre del producto a partir del SKU en vez de depender de un texto libre.

    ⚠️ **Importante:** aunque la IA agiliza el 90% del trabajo, siempre revisa manualmente la
    columna **Estado** antes de generar la factura final.
    """)
