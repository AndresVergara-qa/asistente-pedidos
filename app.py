import os
import time
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
    if any(bad in n for bad in [
        "embedding", "aqa", "imagen-", "tts", "image", "learnlm", "vision",
        "transcribe", "audio", "live", "native-audio", "computer-use", "robotics",
    ]):
        return False
    # Las variantes "-lite" leen peor las imágenes (se comprobó que confunden
    # formatos de producto, ej. "12pk cans" vs "2lts x8") — la precisión del
    # pedido importa más que ganar unos segundos, así que se excluyen.
    if "lite" in n:
        return False
    return True

@st.cache_data(ttl=3600, show_spinner=False)
def get_model_candidates(_api_key_hash, _v=6):
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

GEMINI_SETTINGS_WORKSHEET = "app_settings"


def load_working_model():
    """Último modelo de Gemini que funcionó, guardado en Sheets (persiste
    entre sesiones/días). Devuelve (modelo_o_None, fecha_YYYY-MM-DD_o_None)."""
    try:
        conn = get_gsheets_connection()
        df = conn.read(worksheet=GEMINI_SETTINGS_WORKSHEET, ttl=0)
        if df is None or df.empty:
            return None, None
        row = df.iloc[0]
        model = str(row.get("working_model", "")).strip()
        date = str(row.get("date", "")).strip()
        return (model or None), (date or None)
    except Exception:
        return None, None


def save_working_model(model_name):
    try:
        conn = get_gsheets_connection()
        df = pd.DataFrame([{"working_model": model_name, "date": datetime.now().strftime("%Y-%m-%d")}])
        conn.update(worksheet=GEMINI_SETTINGS_WORKSHEET, data=df)
    except Exception:
        pass  # si no existe la pestaña "app_settings" en el Sheet, simplemente no persiste el atajo


def call_gemini(parts, spinner_text="🤖 Conectando con la Inteligencia Artificial..."):
    """
    Intenta los modelos disponibles en orden de prioridad (mejor primero) y
    devuelve (texto_respuesta, nombre_modelo_usado, error).

    Atajo diario: si un modelo ya funcionó HOY (guardado en Sheets, persiste
    entre sesiones), se prueba primero — pero nunca se descarta el resto de
    la lista: si ese falla, se sigue bajando por prioridad normal, y cada día
    se vuelve a intentar desde el modelo más nuevo primero (para no quedar
    pegado en un modelo más débil si el mejor ya se recuperó).
    El timeout por intento está acotado para que un modelo lento o con
    problemas no bloquee la app por varios minutos.
    """
    last_error = ""
    today = datetime.now().strftime("%Y-%m-%d")

    if "persisted_working_model" not in st.session_state:
        st.session_state["persisted_working_model"] = load_working_model()
    persisted_model, persisted_date = st.session_state["persisted_working_model"]

    candidates = get_model_candidates(DEFAULT_API_KEY[-8:] if DEFAULT_API_KEY else "none")

    preferred = st.session_state.get("working_model")
    if not preferred and persisted_date == today and persisted_model in candidates:
        preferred = persisted_model

    ordered = ([preferred] if preferred else []) + [c for c in candidates if c != preferred]
    ordered = ordered[:5]  # tope de intentos para acotar la espera máxima

    attempts = []  # [(model_name, segundos, "ok"/"error: ...")]
    with st.spinner(spinner_text):
        for i, model_name in enumerate(ordered, start=1):
            t_attempt = time.perf_counter()
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(parts, request_options={"timeout": 25})
                if response and response.text:
                    attempts.append((model_name, time.perf_counter() - t_attempt, "ok"))
                    st.session_state["working_model"] = model_name
                    st.session_state["last_gemini_attempts"] = attempts
                    if persisted_model != model_name or persisted_date != today:
                        save_working_model(model_name)
                        st.session_state["persisted_working_model"] = (model_name, today)
                    return response.text, model_name, None
            except Exception as err:
                attempts.append((model_name, time.perf_counter() - t_attempt, f"error: {err}"))
                last_error = f"{model_name}: {err}"
                continue
    st.session_state["last_gemini_attempts"] = attempts
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


def get_top_candidates(qb_df, prod_col, sku_col, p_name, sku_hint="", catalog_norm=None, top_n=10):
    """Devuelve hasta top_n filas del catálogo más parecidas a p_name (por
    similitud de texto), para mandarle a la IA solo esas en vez del catálogo
    completo. Prioriza una coincidencia exacta de SKU si sku_hint viene.
    Sigue comparando contra el catálogo COMPLETO — no se recorta nada, solo
    se elige qué mostrarle a la IA."""
    p_norm = normalize(p_name)
    sku_norm = normalize(sku_hint)
    candidates = []
    seen = set()

    if sku_norm and sku_col:
        m = qb_df[qb_df[sku_col].astype(str).str.strip().str.upper() == sku_norm]
        for idx in m.index:
            candidates.append(qb_df.loc[idx])
            seen.add(idx)

    if p_norm:
        if catalog_norm is None:
            catalog_norm = build_catalog_index(qb_df, prod_col)
        scored = []
        for idx, cand in catalog_norm.items():
            if idx in seen:
                continue
            score = difflib.SequenceMatcher(None, p_norm, cand).ratio()
            if p_norm in cand or cand in p_norm:
                score = max(score, 0.85)
            scored.append((score, idx))
        scored.sort(key=lambda x: x[0], reverse=True)
        for score, idx in scored[: max(0, top_n - len(candidates))]:
            if score > 0.35:  # sin un mínimo de parecido, no vale la pena mostrarlo
                candidates.append(qb_df.loc[idx])

    return candidates[:top_n]


def build_disambiguation_prompt(raw_items, candidates_per_item, prod_col, sku_col, desc_col):
    """Arma el prompt del paso 2 (desambiguación): para cada producto leído
    del pedido, le muestra a la IA solo sus candidatos del catálogo (no el
    catálogo completo) para que elija cuál es el correcto."""
    bloques = []
    for i, (ritem, cands) in enumerate(zip(raw_items, candidates_per_item), start=1):
        if cands:
            cand_lines = "\n".join(
                f"   - SKU: {clean_val(c[sku_col]) if sku_col else '(sin SKU)'} | Nombre: {c[prod_col]} | Descripción: {clean_val(c[desc_col]) if desc_col else ''}"
                for c in cands
            )
        else:
            cand_lines = "   (no se encontró ningún candidato parecido en el catálogo)"
        texto_leido = str(ritem.get("producto_leido", "")).strip()
        bloques.append(f"{i}. Producto del pedido: \"{texto_leido}\"\n   Candidatos del catálogo:\n{cand_lines}")

    return f"""
    Para cada producto de un pedido, elige CUÁL candidato del catálogo de QuickBooks es el correcto
    (o ninguno, si de verdad no corresponde a ese producto).

    REGLAS DE PRODUCTOS:
    1. JACK DANIEL'S VARIETY PACK -> "JACK DANIEL'S" (CANS VARIETY PACK 2X12 OZ).
    2. CRUSH: Grape = "Crush Grape". Strawberry = "Crush STRAWBERRY 12pk x2".
    3. COCA-COLA ORIGINAL -> "COCA COLA 12PK".
    4. PRIME ICE POP -> "Prime Ice Pop 12/16.9 OZ BTL".

    IMPORTANTE — PRECISIÓN: los candidatos suelen incluir variantes muy parecidas de un mismo
    producto (ej. "Chick Peas" vs "Organic Chick Peas", "Garlic Powder" vs "Garlic Powder 8oz").
    Compara letra por letra (nombre Y descripción) antes de elegir. Si ningún candidato es
    realmente el producto correcto, responde sku_elegido: null.

    PRODUCTOS Y SUS CANDIDATOS:
    {chr(10).join(bloques)}

    FORMATO DE SALIDA (JSON ESTRICTO, un objeto por producto EN EL MISMO ORDEN de arriba,
    SOLO DEVUELVE EL ARREGLO):
    [
      {{"indice": 1, "sku_elegido": "SKU exacto del candidato elegido, o null", "producto_elegido": "nombre exacto del candidato elegido, o null"}}
    ]
    """


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


def sync_edited_rows(edited_df, editor_key, base_session_key, qb_df, prod_col, sku_col, desc_col, fallback_desc_col=None):
    """Cuando el usuario cambia el Producto o el SKU a mano en la tabla
    (el desplegable buscable estilo QuickBooks), esto vuelve a llenar el
    resto de la fila (SKU/Description/Estado) desde el catálogo para que no
    quede desincronizada — igual que hace QuickBooks al elegir un ítem del
    desplegable. También lo persiste en la tabla base (session_state) y
    fuerza un rerun, porque si no, la grilla sigue mostrando el valor viejo
    en las columnas que el usuario no tocó directamente (Streamlit solo
    refresca visualmente la celda que el usuario editó, no las demás)."""
    diff = st.session_state.get(editor_key, {})
    changes = dict(diff.get("edited_rows", {}))
    added_rows = diff.get("added_rows", [])
    for i, added in enumerate(added_rows):
        changes[len(edited_df) - len(added_rows) + i] = added

    if not changes:
        return edited_df

    base_df = st.session_state.get(base_session_key)
    tocó_base = False

    for row_idx, changed_cols in changes.items():
        if row_idx not in edited_df.index:
            continue
        match = None
        if "Product/service" in changed_cols and changed_cols["Product/service"]:
            m = qb_df[qb_df[prod_col].astype(str) == str(changed_cols["Product/service"])]
            if not m.empty:
                match = m.iloc[0]
        elif "SKU" in changed_cols and sku_col and changed_cols["SKU"]:
            m = qb_df[qb_df[sku_col].astype(str).str.strip() == str(changed_cols["SKU"]).strip()]
            if not m.empty:
                match = m.iloc[0]

        if match is not None:
            desc_val = clean_val(match[desc_col]) if desc_col else ""
            if not desc_val and fallback_desc_col:
                desc_val = clean_val(match[fallback_desc_col])
            sku_val = clean_val(match[sku_col]) if sku_col else ""
            name_val = match[prod_col]
            estado_val = "✅ SKU exacto" if "SKU" in changed_cols else "✅ Exacto"

            edited_df.at[row_idx, "Product/service"] = name_val
            edited_df.at[row_idx, "SKU"] = sku_val
            edited_df.at[row_idx, "Description"] = desc_val
            edited_df.at[row_idx, "Estado"] = estado_val

            if base_df is not None and row_idx in base_df.index:
                # Solo marcar cambio (y por lo tanto forzar rerun) si algo realmente
                # es distinto — si no, con el mismo diff ya aplicado de una corrida
                # anterior, entraríamos en un loop infinito de reruns.
                # IMPORTANTE: el nombre del producto SIEMPRE se reescribe en la base
                # (no solo cuando cambió el SKU) — en modo num_rows="dynamic",
                # Streamlit NO preserva la edición de "Product/service" tras un
                # rerun forzado por nosotros, así que hay que fijarlo aquí también
                # o el nombre "rebota" de vuelta al original.
                ya_igual = (
                    str(base_df.at[row_idx, "Product/service"]) == str(name_val)
                    and str(base_df.at[row_idx, "SKU"]) == str(sku_val)
                    and str(base_df.at[row_idx, "Description"]) == str(desc_val)
                    and str(base_df.at[row_idx, "Estado"]) == str(estado_val)
                )
                if not ya_igual:
                    base_df.at[row_idx, "Product/service"] = name_val
                    base_df.at[row_idx, "SKU"] = sku_val
                    base_df.at[row_idx, "Description"] = desc_val
                    base_df.at[row_idx, "Estado"] = estado_val
                    tocó_base = True

    if tocó_base:
        st.session_state[base_session_key] = base_df
        st.rerun()

    return edited_df


# =========================================================
# BARRA LATERAL
# =========================================================
dark_mode = st.sidebar.toggle("🌙 Modo oscuro", value=False)
if dark_mode:
    st.markdown("""
    <style>
    [data-testid="stAppViewContainer"], [data-testid="stHeader"], [data-testid="stBottomBlockContainer"] {
        background-color: #150B26 !important;
    }
    section[data-testid="stSidebar"] {
        background-color: #0F0720 !important;
    }
    body, p, span, label, li, .stMarkdown, [data-testid="stMetricValue"],
    h1, h2, h3, h4, h5, h6 {
        color: #EDE6FA !important;
    }
    input, textarea, select, div[data-baseweb="select"] > div,
    div[data-baseweb="input"], div[data-baseweb="textarea"] {
        background-color: #241640 !important;
        color: #EDE6FA !important;
    }
    div[data-testid="stDataFrame"], div[data-testid="stTable"] {
        background-color: #1C1130 !important;
    }
    [data-testid="stFileUploaderDropzone"], .stExpander, div[data-testid="stExpander"] {
        background-color: #1C1130 !important;
        border-color: #3D2A66 !important;
    }
    code, .stCodeBlock, pre {
        background-color: #1C1130 !important;
        color: #EDE6FA !important;
    }
    </style>
    """, unsafe_allow_html=True)

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
else:
    if qb_connected:
        st.sidebar.success(f"✅ Conectado ({qb_client.QB_ENVIRONMENT}).")
        if st.sidebar.button("🔌 Desconectar QuickBooks"):
            try:
                qb_client.disconnect()
                st.sidebar.info("Desconectado. Si sigue apareciendo 'Conectado', revisa la pestaña 'qb_tokens' de tu Google Sheet.")
            except Exception as e:
                st.sidebar.error(f"No se pudo desconectar (falló el guardado en Sheets): {e}")
            st.rerun()
    # El botón para (re)conectar siempre está disponible — así nunca quedas
    # bloqueado si el estado "Conectado" no coincide con la realidad (ej. un
    # refresh token inválido, o un desconectar que falló al guardar).
    try:
        auth_url = qb_client.get_authorization_url()
        st.sidebar.link_button(
            "🔄 Reconectar con QuickBooks" if qb_connected else "🔗 Conectar con QuickBooks",
            auth_url,
        )
    except Exception as e:
        st.sidebar.error(f"No se pudo generar el link de conexión: {e}")

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
                t_inicio = time.perf_counter()
                timing = {}
                genai.configure(api_key=DEFAULT_API_KEY.strip())

                measures_csv = ""
                if measures_df is not None:
                    m_cols = [c for c in measures_df.columns if any(k in str(c).upper() for k in ["PROD", "PREST", "BOX", "PALLET", "MEDIDA"])]
                    measures_csv = measures_df[m_cols].dropna(how="all").to_csv(index=False) if m_cols else measures_df.to_csv(index=False)
                timing["1_preparar_medidas"] = time.perf_counter() - t_inicio

                # =====================================================
                # PASO 1: leer el pedido SIN el catálogo (prompt chico, rápido)
                # =====================================================
                prompt_extraccion = f"""
                Eres un experto leyendo pedidos de productos (imagen o texto de WhatsApp) para
                'Convenient Distributor'. Lee el pedido y extrae CADA producto mencionado, con su
                cantidad (Qty) y precio unitario (Rate) si aparece.

                Escribe el nombre del producto tal como lo entiendes del pedido — NO intentes adivinar
                el nombre exacto de ningún catálogo, solo describe qué producto es lo más claro posible.

                TABLA DE MEDIDAS (para convertir pallets/cajas a unidades si hace falta):
                {measures_csv}

                INSTRUCCIÓN DE PRECIO (RATE): si el pedido trae un precio unitario, extráelo en formato
                numérico (ej. 15.99). Si no aparece, coloca 0.0.

                FORMATO DE SALIDA (JSON ESTRICTO. SOLO DEVUELVE EL ARREGLO JSON):
                [
                  {{
                    "texto_original": "texto detectado",
                    "producto_leido": "el producto tal como lo entendiste, en tus palabras",
                    "qty": 50,
                    "rate": 15.99
                  }}
                ]
                """

                ai_input = [prompt_extraccion]
                if uploaded_image:
                    ai_input.append(Image.open(uploaded_image))
                if pasted_text.strip():
                    ai_input.append(f"TEXTO DEL PEDIDO PROPORCIONADO:\n{pasted_text}")

                t_antes_gemini1 = time.perf_counter()
                raw_text, used_model, error = call_gemini(ai_input, spinner_text="🤖 Leyendo el pedido...")
                timing["2a_extraccion_ia"] = time.perf_counter() - t_antes_gemini1
                timing["gemini_intentos_extraccion"] = st.session_state.get("last_gemini_attempts", [])
                if raw_text is None:
                    st.error(f"❌ Error de conexión (leyendo el pedido): {error}")
                    st.session_state["timing_ventas"] = timing
                    with st.expander(f"⏱️ Modelos probados ({len(timing['gemini_intentos_extraccion'])} intento(s), todos fallaron)"):
                        for modelo, segundos, resultado in timing["gemini_intentos_extraccion"]:
                            st.caption(f"↳ {modelo}: {segundos:.2f}s — {resultado}")
                    st.stop()

                try:
                    raw_items = parse_json_response(raw_text)
                except json.JSONDecodeError:
                    st.error("❌ La IA no devolvió un JSON válido al leer el pedido. Mira la respuesta cruda abajo para depurar.")
                    st.code(raw_text, language="text")
                    st.session_state["timing_ventas"] = timing
                    st.stop()

                st.session_state["ventas_raw_response"] = raw_text
                st.session_state["ventas_model_used"] = used_model

                # =====================================================
                # PASO 2a: candidatos locales por producto (Python, sin IA — sigue
                # comparando contra el catálogo COMPLETO, solo elige qué mostrarle a la IA)
                # =====================================================
                t_antes_candidatos = time.perf_counter()
                catalog_norm_idx = build_catalog_index(qb_df, prod_col)
                candidates_per_item = [
                    get_top_candidates(qb_df, prod_col, sku_col, str(ri.get("producto_leido", "")).strip(), catalog_norm=catalog_norm_idx)
                    for ri in raw_items
                ]
                timing["2b_candidatos_locales"] = time.perf_counter() - t_antes_candidatos

                # =====================================================
                # PASO 2b: la IA elige, por producto, cuál candidato es el correcto
                # (prompt chico: solo los candidatos de este pedido, no el catálogo entero)
                # =====================================================
                elecciones = {}
                t_antes_gemini2 = time.perf_counter()
                if raw_items:
                    prompt_desambiguacion = build_disambiguation_prompt(raw_items, candidates_per_item, prod_col, sku_col, sales_desc_col)
                    raw_text2, used_model2, error2 = call_gemini([prompt_desambiguacion], spinner_text="🔎 Confirmando productos contra el catálogo...")
                    timing["gemini_intentos_desambiguacion"] = st.session_state.get("last_gemini_attempts", [])
                    if raw_text2 is None:
                        st.warning(f"⚠️ No se pudo confirmar contra el catálogo ({error2}); se usa el matching local automático como respaldo.")
                    else:
                        try:
                            for e in parse_json_response(raw_text2):
                                elecciones[e.get("indice")] = e
                        except json.JSONDecodeError:
                            st.warning("⚠️ La IA no devolvió JSON válido al confirmar productos; se usa el matching local automático como respaldo.")
                timing["2c_desambiguacion_ia"] = time.perf_counter() - t_antes_gemini2

                # Se arma "items" con la misma forma que antes, para no tocar el resto del
                # flujo (memoria de precios, matching final, etc.) — si la IA no confirmó un
                # producto, se deja el texto leído para que el matching local (fuzzy) lo intente.
                items = []
                for i, ri in enumerate(raw_items, start=1):
                    eleccion = elecciones.get(i, {})
                    items.append({
                        "texto_original": ri.get("texto_original", ""),
                        "producto_qb": eleccion.get("producto_elegido") or ri.get("producto_leido", ""),
                        "sku_hint": eleccion.get("sku_elegido") or "",
                        "qty": ri.get("qty", 1),
                        "rate": ri.get("rate", 0.0),
                    })

                t_antes_memoria = time.perf_counter()
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

                        if extracted_rate > 0.0:
                            # El pedido trae un precio explícito — eso siempre manda.
                            final_rate = extracted_rate
                        elif qb_connected:
                            # Con QuickBooks conectado, su historial real es la fuente de
                            # verdad (más confiable/actualizada que la memoria local en
                            # Sheets) — se intenta abajo; si no encuentra nada, cae a Sheets.
                            final_rate = 0.0
                        else:
                            final_rate = recalled_rate

                        row_idx = len(results)
                        results.append({
                            "Estado": estado,
                            "Product/service": actual_pname,
                            "SKU": sku_val,
                            "Description": desc_val,
                            "Qty": item.get("qty", 1),
                            "Rate": final_rate,
                        })
                        if extracted_rate == 0.0 and qb_connected:
                            precios_pendientes.append((row_idx, actual_pname, sku_val, recalled_rate))
                    timing["3_memoria_y_matching"] = time.perf_counter() - t_antes_memoria

                    t_antes_precios_qb = time.perf_counter()
                    if precios_pendientes:
                        with st.spinner(f"Consultando en QuickBooks el último precio vendido de {len(precios_pendientes)} producto(s)..."):
                            for row_idx, pname, sku_val, recalled_rate in precios_pendientes:
                                try:
                                    item_id = qb_client._find_item_id(pname, sku_val)
                                    sugerido, nota = qb_client.suggest_price(item_id, customer_id=customer_id_actual) if item_id else (None, None)
                                    if sugerido:
                                        results[row_idx]["Rate"] = sugerido
                                        if nota:
                                            precio_notas.append(f"**{pname}**: {nota}")
                                    elif recalled_rate > 0.0:
                                        results[row_idx]["Rate"] = recalled_rate
                                        precio_notas.append(f"**{pname}**: Sin historial en QuickBooks; se usó el precio guardado localmente (Sheets): **${recalled_rate:.2f}**.")
                                except Exception:
                                    if recalled_rate > 0.0:
                                        results[row_idx]["Rate"] = recalled_rate  # QuickBooks falló, cae a la memoria local
                    timing["4_precios_desde_qb"] = time.perf_counter() - t_antes_precios_qb
                    timing["4_precios_lineas_consultadas"] = len(precios_pendientes)

                    st.session_state["res_df"] = pd.DataFrame(results)
                    st.session_state["precio_notas"] = precio_notas

                timing["total"] = time.perf_counter() - t_inicio
                st.session_state["timing_ventas"] = timing

            except Exception as err:
                st.session_state["timing_ventas"] = timing if "timing" in locals() else {}
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
            n_alertas = sum(1 for n in precio_notas if "⚠️" in n)
            titulo = f"💰 {len(precio_notas)} precio(s) tomados del historial de QuickBooks"
            if n_alertas:
                titulo += f" — {n_alertas} con posible cambio de precio ⚠️"
            with st.expander(titulo):
                for nota in precio_notas:
                    st.markdown(nota)

        timing = st.session_state.get("timing_ventas", {})
        if timing:
            with st.expander(f"⏱️ Tiempos de este procesamiento (total: {timing.get('total', 0):.1f}s)"):
                st.write(f"1. Preparar tabla de medidas: **{timing.get('1_preparar_medidas', 0):.2f}s**")
                st.write(f"2a. Leer el pedido (IA, sin catálogo): **{timing.get('2a_extraccion_ia', 0):.2f}s**")
                for modelo, segundos, resultado in timing.get("gemini_intentos_extraccion", []):
                    st.caption(f"　　↳ {modelo}: {segundos:.2f}s — {resultado}")
                st.write(f"2b. Buscar candidatos locales por producto: **{timing.get('2b_candidatos_locales', 0):.2f}s**")
                st.write(f"2c. Confirmar productos contra el catálogo (IA): **{timing.get('2c_desambiguacion_ia', 0):.2f}s**")
                for modelo, segundos, resultado in timing.get("gemini_intentos_desambiguacion", []):
                    st.caption(f"　　↳ {modelo}: {segundos:.2f}s — {resultado}")
                st.write(f"3. Memoria de precios (Sheets) + matching contra catálogo: **{timing.get('3_memoria_y_matching', 0):.2f}s**")
                st.write(f"4. Precios consultados en vivo a QuickBooks: **{timing.get('4_precios_desde_qb', 0):.2f}s** "
                         f"({timing.get('4_precios_lineas_consultadas', 0)} línea(s) consultadas)")

        with st.expander("🐞 Ver respuesta cruda de la IA (debug)"):
            st.caption(f"Modelo usado: {st.session_state.get('ventas_model_used', '—')}")
            st.code(st.session_state.get("ventas_raw_response", ""), language="json")

        # Las opciones deben incluir también los valores ya presentes en la tabla
        # (ej. un "sin_match" con el texto que leyó la IA), si no Streamlit rechaza
        # el desplegable por tener un valor fuera de la lista.
        prod_options_ventas = sorted(set(qb_df[prod_col].dropna().astype(str)) | set(st.session_state["res_df"]["Product/service"].astype(str))) if prod_col else []
        sku_options_ventas = sorted((set(clean_val(v) for v in qb_df[sku_col].dropna()) | set(st.session_state["res_df"]["SKU"].astype(str))) - {""}) if sku_col else []

        edited_df = st.data_editor(
            st.session_state["res_df"],
            column_config={
                "Product/service": st.column_config.SelectboxColumn("Product/service", options=prod_options_ventas, width="large"),
                "SKU": st.column_config.SelectboxColumn("SKU", options=sku_options_ventas),
                "Rate": st.column_config.NumberColumn("Rate ($)", format="$%.2f", min_value=0.0),
                "Estado": st.column_config.TextColumn("Estado", disabled=True),
            },
            num_rows="dynamic",
            width="stretch", hide_index=True,
            key="ventas_editor",
        )
        edited_df = sync_edited_rows(edited_df, "ventas_editor", "res_df", qb_df, prod_col, sku_col, sales_desc_col)

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
        prod_options_compras = sorted(set(qb_df[prod_col].dropna().astype(str)) | set(st.session_state["res_compras"]["Product/service"].astype(str))) if prod_col else []
        sku_options_compras = sorted((set(clean_val(v) for v in qb_df[sku_col].dropna()) | set(st.session_state["res_compras"]["SKU"].astype(str))) - {""}) if sku_col else []

        edited_compras_df = st.data_editor(
            st.session_state["res_compras"],
            column_config={
                "Product/service": st.column_config.SelectboxColumn("Product/service", options=prod_options_compras, width="large"),
                "SKU": st.column_config.SelectboxColumn("SKU", options=sku_options_compras),
                "Cost": st.column_config.NumberColumn("Cost ($)", format="$%.2f", min_value=0.0),
                "Estado": st.column_config.TextColumn("Estado", disabled=True),
            },
            num_rows="dynamic",
            width="stretch", hide_index=True,
            key="compras_editor",
        )
        edited_compras_df = sync_edited_rows(edited_compras_df, "compras_editor", "res_compras", qb_df, prod_col, sku_col, purchase_desc_col, fallback_desc_col=sales_desc_col)

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

st.divider()
st.subheader("🧪 Módulo de Pruebas — Registrar Venta ya Pagada")
st.caption(
    "Crea ventas de prueba (Invoice + Payment, ya cobradas) directo del catálogo automático de QuickBooks, "
    "sin pasar por la IA — sirve para sembrar historial real de precios en el Sandbox y así poder probar "
    "el precio automático por cliente/producto."
)

test_catalog_df = st.session_state.get("qb_catalog_df")

if not qb_connected:
    st.info("🔌 Conecta QuickBooks (barra lateral) para usar este módulo.")
elif test_catalog_df is None or test_catalog_df.empty:
    st.info("📥 Usa el botón '🔄 Traer catálogo de QuickBooks' en la barra lateral primero (este módulo usa siempre el catálogo automático, sin importar qué tengas elegido arriba).")
else:
    test_nombres_cliente = [c["DisplayName"] for c in st.session_state.get("qb_customers", [])]
    col_cli, col_fecha = st.columns([2, 1])
    with col_cli:
        test_cliente_elegido = st.selectbox("Cliente", test_nombres_cliente, key="test_cliente_sel")
    with col_fecha:
        test_fecha = st.date_input("Fecha de la venta", value=datetime.now(), key="test_fecha_sel")
    test_customer_id = next(
        (c["Id"] for c in st.session_state.get("qb_customers", []) if c["DisplayName"] == test_cliente_elegido),
        None,
    )

    test_producto_opciones = [f"{row['Product/Service']}  |  SKU: {row['SKU']}" for _, row in test_catalog_df.iterrows()]
    col_prod, col_qty, col_rate, col_add = st.columns([3, 1, 1, 1])
    with col_prod:
        test_producto_elegido = st.selectbox("Producto (catálogo QB)", test_producto_opciones, key="test_producto_sel")
    with col_qty:
        test_qty = st.number_input("Cantidad", min_value=1, value=1, key="test_qty_sel")
    with col_rate:
        test_rate = st.number_input("Precio ($)", min_value=0.0, value=0.0, step=0.01, key="test_rate_sel")
    with col_add:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕ Agregar"):
            idx = test_producto_opciones.index(test_producto_elegido)
            row = test_catalog_df.iloc[idx]
            st.session_state.setdefault("test_sale_lines", []).append({
                "Product/service": row["Product/Service"],
                "SKU": row["SKU"],
                "Qty": test_qty,
                "Rate": test_rate,
            })

    test_lines = st.session_state.get("test_sale_lines", [])
    if test_lines:
        st.dataframe(pd.DataFrame(test_lines), hide_index=True, width="stretch")
        col_clear, col_submit = st.columns([1, 2])
        with col_clear:
            if st.button("🧹 Vaciar líneas"):
                st.session_state["test_sale_lines"] = []
                st.rerun()
        with col_submit:
            if st.button("💰 Crear Invoice Pagado en QuickBooks", type="primary"):
                try:
                    with st.spinner("Creando Invoice + Payment en QuickBooks..."):
                        invoice, payment = qb_client.create_invoice_paid(
                            test_cliente_elegido,
                            pd.DataFrame(test_lines),
                            customer_id=test_customer_id,
                            txn_date=test_fecha.strftime("%Y-%m-%d"),
                        )
                    st.success(
                        f"✅ Invoice #{invoice.get('DocNumber', invoice.get('Id'))} creado y pagado "
                        f"(Payment #{payment.get('DocNumber', payment.get('Id'))}) para {test_cliente_elegido} "
                        f"el {test_fecha.strftime('%Y-%m-%d')}."
                    )
                    st.session_state["test_sale_lines"] = []
                except Exception as e:
                    st.error(f"❌ No se pudo crear el Invoice/Payment: {e}")

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
