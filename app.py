import os
import json
import re
from datetime import datetime
import pandas as pd
import streamlit as st
import google.generativeai as genai
from PIL import Image
from streamlit_gsheets import GSheetsConnection

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(
    page_title="Convenient Distributor | Asistente de Pedidos y Compras", 
    page_icon="📦", 
    layout="wide"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Obtiene la clave directamente desde Secrets (Oculto del front)
DEFAULT_API_KEY = st.secrets.get("GEMINI_API_KEY", "")

# --- ENCABEZADO ---
st.markdown("<h2 style='text-align: center; color: #7B2CBF; font-weight: bold;'>Convenient Distributor</h2>", unsafe_allow_html=True)
st.title("🧾 Asistente Integral (Ventas y Compras)")
st.markdown("Mapeo inteligente de pedidos y facturas de proveedores con **Inteligencia Artificial**.")

# --- CONEXIÓN A GOOGLE SHEETS ---
@st.cache_resource
def get_gsheets_connection():
    return st.connection("gsheets", type=GSheetsConnection)

def load_price_memory():
    """Carga los precios guardados desde Google Sheets."""
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
    """Actualiza y guarda los precios nuevos en Google Sheets."""
    memory_df = load_price_memory()
    hoy = datetime.now().strftime("%Y-%m-%d")

    for _, row in df_editado.iterrows():
        sku = str(row.get("SKU", "")).strip()
        producto = str(row.get("Product/service", "")).strip()
        try:
            rate = float(row.get("Rate", 0.0))
        except (ValueError, TypeError):
            rate = 0.0

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

# --- FUNCIONES DE APOYO ---
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

# --- BARRA LATERAL (DATOS Y ARCHIVOS GLOBALES) ---
st.sidebar.header("Datos Globales")
cliente_actual = st.sidebar.text_input(
    "Nombre del Cliente (Para Ventas)",
    value="Cliente General",
    help="COLOCAR NOMBRE EXACTO DEL CLIENTE EN QUICKBOOKS"
)

st.sidebar.header("Archivos de Referencia")
qb_file = st.sidebar.file_uploader(
    "Catálogo de QuickBooks", 
    type=["xlsx", "xls", "csv"], 
    key="qb",
    help="SUBIR INVENTARIO COMPLETO DE QUICKBOOKS (Se usará para Ventas y Compras)"
)

qb_df = None
if qb_file:
    try:
        qb_df = load_dataframe(qb_file)
        st.sidebar.success(f"✅ Catálogo: {len(qb_df)} productos.")
    except Exception as e:
        st.sidebar.error(f"Error cargando Catálogo: {e}")

# Detección automática de tabla_medidas
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

# ==========================================
# CREACIÓN DE PESTAÑAS PRINCIPALES
# ==========================================
tab_ventas, tab_compras = st.tabs(["🛒 Procesar Pedidos (Ventas)", "📦 Ingreso de Facturas (Compras)"])

# ==========================================
# PESTAÑA 1: VENTAS (CÓDIGO DE VENTAS)
# ==========================================
with tab_ventas:
    st.markdown("### Ingresa el detalle del pedido")
    subtab1, subtab2 = st.tabs(["📷 Subir Imagen", "📝 Pegar Texto"])

    with subtab1:
        uploaded_image = st.file_uploader("Sube la captura del pedido", type=["png", "jpg", "jpeg"])

    with subtab2:
        pasted_text = st.text_area("Pega aquí el mensaje del pedido de WhatsApp", height=150)

    # --- PROCESAMIENTO VENTAS ---
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

                # Preparar catálogos
                cols_upper = {str(c).strip().upper(): c for c in qb_df.columns}
                prod_col = next((cols_upper[k] for k in ["PRODUCT/SERVICE", "NAME", "PRODUCT/SERVICE NAME", "PRODUCT"] if k in cols_upper), qb_df.columns[0])
                sku_col = next((cols_upper[k] for k in ["SKU", "ITEM SKU"] if k in cols_upper), None)
                desc_col = next((cols_upper[k] for k in ["DESCRIPTION", "SALES DESCRIPTION", "DESCRP", "MEMO/DESCRIPTION"] if k in cols_upper), None)

                cols_qb = [c for c in [prod_col, sku_col, desc_col] if c is not None]
                catalog_csv = qb_df[cols_qb].dropna(subset=[prod_col]).to_csv(index=False)

                measures_csv = ""
                if measures_df is not None:
                    m_cols = [c for c in measures_df.columns if any(k in str(c).upper() for k in ["PROD", "PREST", "BOX", "PALLET", "MEDIDA"])]
                    measures_csv = measures_df[m_cols].dropna(how="all").to_csv(index=False) if m_cols else measures_df.to_csv(index=False)

                prompt = f"""
                Eres un experto en logística y facturación en QuickBooks para 'Convenient Distributor'.
                OBJETIVO: Extrae productos, calcula cantidades (Qty) y extrae el precio unitario (Rate) basándote en la información proporcionada (imagen o texto), el catálogo y la tabla de medidas.

                REGLAS DE PRODUCTOS:
                1. JACK DANIEL'S VARIETY PACK: Mapea a "JACK DANIEL'S" (CANS VARIETY PACK 2X12 OZ).
                2. CRUSH: Grape = lata 12oz ("Crush Grape"). Strawberry = lata 12oz ("Crush STRAWBERRY 12pk x2").
                3. COCA-COLA ORIGINAL: Mapea a "COCA COLA 12PK".
                4. PRIME ICE POP: Mapea a "Prime Ice Pop 12/16.9 OZ BTL".

                INSTRUCCIÓN DE PRECIO (RATE):
                Busca en el pedido si el producto tiene un precio unitario asignado. Si aparece un precio, extráelo en formato numérico (ej. 15.99). Si no aparece, coloca 0.0.

                CATÁLOGO QUICKBOOKS:
                {catalog_csv}

                TABLA DE MEDIDAS (Para conversiones de pallets/cajas):
                {measures_csv}

                FORMATO DE SALIDA (JSON ESTRICTO. SOLO DEVUELVE EL ARREGLO JSON):
                [
                  {{
                    "texto_original": "texto detectado",
                    "producto_qb": "Nombre Exacto en QB",
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
                
                response = None
                ultimo_error = ""

                with st.spinner("🤖 Conectando con la Inteligencia Artificial..."):
                    candidate_models = []
                    try:
                        for m in genai.list_models():
                            if 'generateContent' in m.supported_generation_methods:
                                candidate_models.append(m.name)
                    except Exception:
                        candidate_models = ["gemini-1.5-flash", "gemini-2.5-flash"]

                    for nombre_modelo in candidate_models:
                        try:
                            model = genai.GenerativeModel(nombre_modelo)
                            response = model.generate_content(ai_input)
                            if response and response.text:
                                break
                        except Exception as err:
                            ultimo_error = str(err)
                            continue

                    if response is None:
                        st.error(f"❌ Error de conexión: {ultimo_error}")
                        st.stop()

                    raw_text = response.text.strip()
                    raw_text = re.sub(r"^```(?:json)?", "", raw_text, flags=re.IGNORECASE).strip()
                    raw_text = re.sub(r"```$", "", raw_text).strip()
                    items = json.loads(raw_text)

                with st.spinner("💰 Consultando Google Sheets y precios históricos..."):
                    price_mem = load_price_memory()
                    client_mem = price_mem[price_mem["Cliente"].astype(str).str.strip().str.upper() == cliente_actual.strip().upper()]

                    sku_prices = {}
                    prod_prices = {}
                    for _, r in client_mem.iterrows():
                        p_str = str(r.get("Precio", "0")).strip()
                        try:
                            val_p = float(p_str)
                        except ValueError:
                            val_p = 0.0

                        s_key = str(r.get("SKU", "")).strip()
                        p_key = str(r.get("Producto", "")).strip().upper()

                        if s_key: sku_prices[s_key] = val_p
                        if p_key: prod_prices[p_key] = val_p

                    results = []
                    for item in items:
                        p_name = item.get("producto_qb", "").strip()
                        extracted_rate = float(item.get("rate", 0.0))
                        
                        match_row = qb_df[qb_df[prod_col].astype(str).str.strip().str.upper() == p_name.upper()]
                        if match_row.empty:
                            match_row = qb_df[qb_df[prod_col].astype(str).str.strip().str.upper().str.contains(p_name.upper(), regex=False, na=False)]

                        actual_pname = match_row.iloc[0][prod_col] if not match_row.empty else p_name
                        sku_val = clean_val(match_row.iloc[0][sku_col]) if not match_row.empty and sku_col else ""
                        desc_val = clean_val(match_row.iloc[0][desc_col]) if not match_row.empty and desc_col else ""

                        recalled_rate = sku_prices.get(sku_val, 0.0)
                        if recalled_rate == 0.0:
                            recalled_rate = prod_prices.get(actual_pname.upper(), 0.0)

                        final_rate = recalled_rate if recalled_rate > 0.0 else extracted_rate

                        results.append({
                            "Product/service": actual_pname,
                            "SKU": sku_val,
                            "Description": desc_val,
                            "Qty": item.get("qty", 1),
                            "Rate": final_rate,
                        })

                    st.session_state["res_df"] = pd.DataFrame(results)

            except Exception as err:
                st.error(f"❌ DETALLE DEL ERROR:\n\n`{type(err).__name__}: {err}`")

    # --- PANTALLA DE RESULTADOS VENTAS ---
    if "res_df" in st.session_state:
        st.divider()
        st.subheader(f"🔍 Verificación del Pedido — Cliente: **{cliente_actual}**")
        st.info("💡 **Revisión:** Modifica los precios (Rate) si es necesario y haz clic en **Guardar Precios** para almacenarlos en Google Sheets.")

        edited_df = st.data_editor(
            st.session_state["res_df"],
            column_config={
                "Rate": st.column_config.NumberColumn("Rate ($)", format="$%.2f", min_value=0.0)
            },
            width="stretch",
            hide_index=True,
        )

        if st.button("💾 Aprender y Guardar Precios"):
            save_price_memory(cliente_actual.strip(), edited_df)

        st.divider()
        st.subheader("📋 LISTO PARA QUICKBOOKS")
        st.caption("Haz clic en el icono de copiar (arriba a la derecha de la caja) y pégalo directamente en tu factura de QuickBooks.")
        tsv_lines = []
        for _, row in edited_df.iterrows():
            p = str(row["Product/service"]).replace("\t", " ").strip()
            s = str(row["SKU"]).replace("\t", " ").strip() if row["SKU"] else ""
            d = str(row["Description"]).replace("\t", " ").strip() if row["Description"] else ""
            q = str(row["Qty"])
            r = str(row["Rate"])
            tsv_lines.append(f"\t{p}\t{s}\t{d}\t{q}\t{r}")

        clean_tsv_text = "\n".join(tsv_lines)
        st.code(clean_tsv_text, language="text")

# ==========================================
# PESTAÑA 2: COMPRAS (CORREGIDA)
# ==========================================
with tab_compras:
    st.markdown("### Extraer datos de Facturas de Proveedores (Bills)")
    st.write("Sube la imagen de la factura. La IA extraerá los datos, cruzará la información con el **Catálogo de QuickBooks** (subido en la barra lateral) y dejará los datos listos para copiar y pegar.")
    
    uploaded_bill = st.file_uploader("Sube la factura del proveedor", type=["png", "jpg", "jpeg"], key="bill_uploader")
    
    if st.button("⚡ Analizar Factura de Compra", type="primary"):
        if not DEFAULT_API_KEY:
            st.error("⚠️ Falta configurar la clave GEMINI_API_KEY en los Secrets.")
        elif qb_df is None:
            st.error("⚠️ Debes cargar el Catálogo de QuickBooks en la barra lateral izquierda para que la IA sepa con qué productos hacer el cruce.")
        elif not uploaded_bill:
            st.error("⚠️ Debes subir una imagen de la factura del proveedor.")
        else:
            try:
                genai.configure(api_key=DEFAULT_API_KEY.strip())
                
                # Preparamos el catálogo para que la IA lo lea
                cols_upper = {str(c).strip().upper(): c for c in qb_df.columns}
                prod_col = next((cols_upper[k] for k in ["PRODUCT/SERVICE", "NAME", "PRODUCT/SERVICE NAME", "PRODUCT"] if k in cols_upper), qb_df.columns[0])
                sku_col = next((cols_upper[k] for k in ["SKU", "ITEM SKU"] if k in cols_upper), None)
                
                cols_qb_compras = [c for c in [prod_col, sku_col] if c is not None]
                catalog_compras_csv = qb_df[cols_qb_compras].dropna(subset=[prod_col]).to_csv(index=False)
                
                prompt_compras = f"""
                Eres un experto analizando facturas de compras (Bills) de proveedores.
                
                TAREAS:
                1. Analiza la imagen de la factura adjunta. Extrae las líneas de los productos reales.
                2. IGNORA estrictamente: Taxes, Cupones, Cobros por envíos (Freight), depósitos y Pallets (Ej. CHEP PALLETS).
                3. Por cada línea válida, identifica: Cantidad, Costo Unitario, Costo Total y la Descripción original del proveedor.
                4. Usa el UPC, SKU o la descripción de la factura para encontrar la mejor coincidencia exacta dentro de nuestro Catálogo de QuickBooks.
                
                CATÁLOGO DE QUICKBOOKS:
                {catalog_compras_csv}
                
                FORMATO DE SALIDA (JSON ESTRICTO):
                [
                    {{
                        "Product/service": "Nombre exacto encontrado en el catálogo",
                        "Original_Description": "Lo que dice la factura",
                        "Qty": 10,
                        "Unit Cost": 15.50,
                        "Total Amount": 155.00
                    }}
                ]
                Solo devuelve el JSON puro, sin comillas triples invertidas ni texto adicional.
                """
                
                image_parts = [Image.open(uploaded_bill)]
                
                with st.spinner("📦 Analizando factura y cruzando con inventario..."):
                    # Detección dinámica del modelo para evitar errores de versión API (404)
                    candidate_models_compras = []
                    try:
                        for m in genai.list_models():
                            if 'generateContent' in m.supported_generation_methods:
                                candidate_models_compras.append(m.name)
                    except Exception:
                        candidate_models_compras = ["gemini-1.5-flash", "gemini-2.5-flash"]

                    response_compras = None
                    ultimo_error_compra = ""
                    for nombre_modelo in candidate_models_compras:
                        try:
                            model = genai.GenerativeModel(nombre_modelo)
                            response_compras = model.generate_content([prompt_compras] + image_parts)
                            if response_compras and response_compras.text:
                                break
                        except Exception as err:
                            ultimo_error_compra = str(err)
                            continue

                    if response_compras is None:
                        st.error(f"❌ Error al procesar la factura: {ultimo_error_compra}")
                        st.stop()

                    raw_text_compras = response_compras.text.strip()
                    raw_text_compras = re.sub(r"^```(?:json)?", "", raw_text_compras, flags=re.IGNORECASE).strip()
                    raw_text_compras = re.sub(r"```$", "", raw_text_compras).strip()
                    
                    datos_compras = json.loads(raw_text_compras)
                    df_compras = pd.DataFrame(datos_compras)
                    st.session_state["res_compras"] = df_compras

            except Exception as e:
                st.error(f"❌ Error al procesar la factura: {e}")

    # Mostrar resultados de compras
    if "res_compras" in st.session_state:
        st.divider()
        st.success("¡Factura procesada con éxito!")
        
        col1, col2 = st.columns([1, 2])
        with col1:
            st.image(uploaded_bill, caption="Factura Original", use_container_width=True)
            
        with col2:
            st.write("### Tabla Lista para QuickBooks (Bills)")
            st.caption("Haz clic en el ícono de copiar en la esquina superior derecha de la tabla.")
            st.dataframe(st.session_state["res_compras"], use_container_width=True)

# --- PIE DE PÁGINA ---
st.markdown("<br><br>", unsafe_allow_html=True)
st.divider()
with st.expander("❓ ¿Qué es esto y cómo funciona?"):
    st.markdown("""
    Esta aplicación es un **ayudante de facturación creado para Convenient Distributor**. 
    
    * **🛒 Ventas:** Mapea pedidos de WhatsApp usando el catálogo de QuickBooks y nuestra Memoria Inteligente de Google Sheets.
    * **📦 Compras:** Lee imágenes de Bills de proveedores, filtra información basura y cruza los productos con QuickBooks de forma automática.
    """)
