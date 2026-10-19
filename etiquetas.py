"""
Etiquetas de pallet para la mercancía que RECIBE el almacén.

Cada pallet que llega recibe un ID único (CD-AAMMDD-NN, consecutivo por día),
una etiqueta 4x6 con código de barras (Code 128 del ID), el proveedor y lo que
trae (productos y cajas). Pensado para usarse desde el celular:

- Los productos se agregan escaneando la caja (pistola Bluetooth, que "escribe"
  el código en el campo de escaneo) o con una foto del código de barras.
- El código de la caja se busca contra el SKU de QuickBooks. Si no coincide, se
  elige el producto una vez y el código queda guardado (pestaña "codigos_cajas").
- Cada pallet queda en la pestaña "pallets" del mismo Google Sheet de la app.

Con ?vista=etiquetas en la URL la app muestra solo esta pantalla (para el
personal del almacén), sin barra lateral ni las otras pestañas.
"""
import io
import json
import os
import re
from datetime import datetime

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

import qb_client
from gsheets_utils import get_gsheets_connection

PALLETS_WORKSHEET = "pallets"
CODIGOS_WORKSHEET = "codigos_cajas"
PALLET_COLUMNS = ["id", "fecha", "dia", "proveedor", "po", "ubicacion", "pallet_n", "pallets_lote",
                  "contenido", "items_json", "total_cajas", "anulado"]
CODIGO_COLUMNS = ["codigo", "producto", "sku", "fecha"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_REG = os.path.join(BASE_DIR, "assets", "fonts", "DejaVuSans.ttf")
FONT_BOLD = os.path.join(BASE_DIR, "assets", "fonts", "DejaVuSans-Bold.ttf")

DPI = 203  # resolución típica de impresoras térmicas 4x6
LABEL_W, LABEL_H = 4 * DPI, 6 * DPI


# =========================================================
# DATOS: QuickBooks
# =========================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_inventory_items():
    """Todos los items Inventory activos de QuickBooks (paginado: QBO devuelve
    máximo 1000 por consulta y el catálogo tiene más de 3,000)."""
    items, start = [], 1
    while True:
        page = qb_client._query(
            "SELECT Id, Name, Sku, QtyOnHand, Type FROM Item "
            f"WHERE Active = true STARTPOSITION {start} MAXRESULTS 1000"
        ).get("Item", [])
        items.extend(page)
        if len(page) < 1000:
            break
        start += 1000
    rows = [{
        "producto": it.get("Name", ""),
        "sku": (it.get("Sku") or "").strip(),
        "qoh": float(it.get("QtyOnHand") or 0),
    } for it in items if it.get("Type") == "Inventory"]
    return pd.DataFrame(rows, columns=["producto", "sku", "qoh"])


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_vendor_names():
    return [v["DisplayName"] for v in qb_client.list_vendors()]


# =========================================================
# DATOS: Google Sheets
# =========================================================
def _read_ws(worksheet, columns):
    try:
        df = get_gsheets_connection().read(worksheet=worksheet, ttl=0)
        if df is None or df.empty:
            return pd.DataFrame(columns=columns)
        df = df.dropna(how="all")
        for c in columns:
            if c not in df.columns:
                df[c] = ""
        return df[columns].fillna("")
    except Exception:
        return pd.DataFrame(columns=columns)


def _write_ws(worksheet, df):
    conn = get_gsheets_connection()
    try:
        conn.update(worksheet=worksheet, data=df)
    except Exception:
        # Primera vez: la pestaña no existe todavía en el Google Sheet.
        conn.create(worksheet=worksheet, data=df)


def _as_text(df, cols):
    """Antepone la comilla que obliga a Sheets a guardar el valor como texto."""
    df = df.copy()
    for c in cols:
        df[c] = df[c].map(lambda v: f"'{_text(v)}" if _text(v) else "")
    return df


def _text(v):
    """Sheets devuelve números largos como float ('7591058001017.0')."""
    s = str(v).strip()
    if s.startswith("'"):
        s = s[1:]
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def load_pallets_fresh():
    df = _read_ws(PALLETS_WORKSHEET, PALLET_COLUMNS)
    for c in ["id", "dia", "po", "ubicacion", "proveedor"]:
        df[c] = df[c].map(_text)
    return df


# La pestaña se dibuja en cada recarga de la app (Streamlit corre todas las
# pestañas), así que las lecturas a Sheets se cachean un minuto y se limpian
# al escribir.
@st.cache_data(ttl=60, show_spinner=False)
def load_pallets():
    return load_pallets_fresh()


@st.cache_data(ttl=60, show_spinner=False)
def load_codigos():
    df = _read_ws(CODIGOS_WORKSHEET, CODIGO_COLUMNS)
    return {_text(r["codigo"]): {"producto": str(r["producto"]), "sku": _text(r["sku"])}
            for _, r in df.iterrows() if _text(r["codigo"])}


def save_codigo(codigo, producto, sku):
    df = _read_ws(CODIGOS_WORKSHEET, CODIGO_COLUMNS)
    df = df[df["codigo"].map(_text) != codigo]
    # La comilla fuerza texto en Sheets (si no, los códigos largos pierden dígitos).
    nueva = pd.DataFrame([{"codigo": f"'{codigo}", "producto": producto, "sku": f"'{sku}" if sku else "",
                           "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")}])
    _write_ws(CODIGOS_WORKSHEET, pd.concat([df, nueva], ignore_index=True))
    load_codigos.clear()


# =========================================================
# CÓDIGOS DE BARRAS
# =========================================================
def digits(s):
    return re.sub(r"\D", "", str(s))


def upc_key(d):
    """Los 11 dígitos 'de producto' de un UPC, sin importar cómo venga:
    UPC-A (12), sin el 0 inicial (11), EAN-13 con 0 inicial, o el GTIN-14 de
    la caja (indicador + 0 + UPC sin dígito verificador + verificador nuevo)."""
    if not d or len(d) < 8:
        return None
    if len(d) <= 12:
        return d.zfill(12)[:11]
    if len(d) == 13:
        return d[1:12] if d[0] == "0" else None
    if len(d) == 14:
        return d[2:13]
    return None


def build_sku_index(items_df):
    idx = {}
    for _, r in items_df.iterrows():
        sku = r["sku"].replace(" ", "")
        if sku.isdigit() and 11 <= len(sku) <= 14:
            k = upc_key(sku)
            if k:
                idx.setdefault(k, []).append(r)
    return idx


def lookup_code(code, codigos, sku_index):
    d = digits(code)
    if d in codigos:
        return codigos[d]["producto"], codigos[d]["sku"]
    hits = sku_index.get(upc_key(d) or "", [])
    if hits:
        best = max(hits, key=lambda r: r["qoh"])
        return best["producto"], best["sku"]
    return None


def decode_photo(file_bytes):
    """Lee el código de barras de una foto. Devuelve el texto o None."""
    import zxingcpp
    img = Image.open(io.BytesIO(file_bytes))
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("L")
    for max_side in (1600, 1000, 2400):
        scale = min(1.0, max_side / max(img.size))
        im = img.resize((int(img.width * scale), int(img.height * scale))) if scale < 1 else img
        for angle in (0, 90):
            res = zxingcpp.read_barcodes(im.rotate(angle, expand=True) if angle else im)
            if res:
                return res[0].text
    return None


# =========================================================
# ETIQUETA (imagen 4x6 a 203 dpi) Y PDF
# =========================================================
def _font(size, bold=False):
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    except Exception:
        return ImageFont.load_default()


def _fit(draw, text, max_w, size, bold=True, min_size=14):
    while size > min_size:
        f = _font(size, bold)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(min_size, bold)


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for w in str(text).split():
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) > max_w and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _barcode_img(value, height):
    import barcode
    from barcode.writer import ImageWriter
    img = barcode.Code128(value, writer=ImageWriter()).render(writer_options={
        "module_width": 0.38, "module_height": 15.0, "quiet_zone": 1.0,
        "write_text": False, "dpi": DPI, "background": "white", "foreground": "black",
    })
    return img.resize((img.width, height))


def render_label(p):
    """p: dict con id, fecha (datetime o str), proveedor, po, ubicacion,
    pallet_n, pallets_lote, items [{producto, cajas}], total_cajas, anulado."""
    img = Image.new("RGB", (LABEL_W, LABEL_H), "white")
    d = ImageDraw.Draw(img)
    M, W, H = 24, LABEL_W, LABEL_H
    lab = _font(22, True)

    def box(x, y, w, h, lw=4):
        d.rectangle([x, y, x + w, y + h], outline="black", width=lw)

    fecha = p["fecha"] if isinstance(p["fecha"], datetime) else pd.to_datetime(p["fecha"])
    # Fila 1: recibido / pallet n de N
    box(M, M, W - 2 * M, 110)
    d.text((M + 16, M + 12), "RECIBIDO", font=lab, fill="black")
    d.text((M + 16, M + 42), fecha.strftime("%m/%d/%y"), font=_font(46, True), fill="black")
    if int(p.get("pallets_lote") or 1) > 1:
        t = f"{int(p['pallet_n'])} DE {int(p['pallets_lote'])}"
        f = _font(46, True)
        d.text((W - M - 16 - d.textlength("PALLET", font=lab), M + 12), "PALLET", font=lab, fill="black")
        d.text((W - M - 16 - d.textlength(t, font=f), M + 42), t, font=f, fill="black")
    else:
        t = "CONVENIENT DISTRIBUTOR"
        d.text((W - M - 16 - d.textlength(t, font=lab), M + 12), t, font=lab, fill="black")
        t2 = "Medley, FL"
        d.text((W - M - 16 - d.textlength(t2, font=_font(22)), M + 46), t2, font=_font(22), fill="black")

    # Fila 2: proveedor
    y = M + 110
    box(M, y, W - 2 * M, 150)
    d.text((M + 16, y + 12), "PROVEEDOR", font=lab, fill="black")
    prov = str(p["proveedor"]).upper()
    f = _fit(d, prov, W - 2 * M - 32, 72)
    d.text((M + 16, y + 50), prov, font=f, fill="black")

    # Fila 3: PO / ubicación
    y += 150
    half = (W - 2 * M) // 2
    box(M, y, half, 96)
    box(M + half, y, half, 96)
    d.text((M + 16, y + 10), "PO / FACTURA #", font=lab, fill="black")
    d.text((M + 16, y + 40), str(p.get("po") or "—"), font=_fit(d, str(p.get("po") or "—"), half - 32, 42), fill="black")
    d.text((M + half + 16, y + 10), "UBICACIÓN", font=lab, fill="black")
    d.text((M + half + 16, y + 40), str(p.get("ubicacion") or "—"), font=_fit(d, str(p.get("ubicacion") or "—"), half - 32, 42), fill="black")

    # Fila 4: ID + código de barras
    y += 96
    box(M, y, W - 2 * M, 230)
    d.text((M + 16, y + 10), "ID DEL PALLET", font=lab, fill="black")
    bc = _barcode_img(p["id"], 110)
    if bc.width > W - 2 * M - 40:
        bc = bc.resize((W - 2 * M - 40, 110))
    img.paste(bc, ((W - bc.width) // 2, y + 44))
    f = _font(50, True)
    d.text(((W - d.textlength(p["id"], font=f)) // 2, y + 160), p["id"], font=f, fill="black")

    # Contenido
    y += 230
    foot_h = 86
    c_h = H - M - foot_h - y
    box(M, y, W - 2 * M, c_h)
    d.rectangle([M, y, W - M, y + 40], fill="black")
    d.text((M + 16, y + 8), "CAJAS", font=lab, fill="white")
    d.text((M + 150, y + 8), "PRODUCTO", font=lab, fill="white")
    items = p["items"]
    for fs in range(30, 17, -2):
        f = _font(fs, False)
        rows = [(it["cajas"], _wrap(d, str(it["producto"]).upper(), f, W - 2 * M - 170)) for it in items]
        need = sum(len(r[1]) * (fs + 8) + 10 for r in rows)
        if need <= c_h - 54:
            break
    ry = y + 50
    for cajas, lines in rows:
        if ry > y + c_h - fs:
            break
        q = f"{cajas:g}" if isinstance(cajas, float) else str(cajas)
        d.text((M + 16, ry), q, font=_font(fs + 2, True), fill="black")
        for i, t in enumerate(lines):
            if ry + i * (fs + 8) <= y + c_h - fs:
                d.text((M + 150, ry + i * (fs + 8)), t, font=f, fill="black")
        ry += len(lines) * (fs + 8) + 10
        d.line([M + 8, ry - 6, W - M - 8, ry - 6], fill="black", width=1)

    # Pie: total
    y = H - M - foot_h
    box(M, y, W - 2 * M, foot_h)
    d.text((M + 16, y + 32), "TOTAL CAJAS", font=lab, fill="black")
    tot = f"{float(p['total_cajas']):,.0f}" if float(p["total_cajas"]).is_integer() else f"{float(p['total_cajas']):,.1f}"
    f = _font(56, True)
    d.text((W - M - 16 - d.textlength(tot, font=f), y + 12), tot, font=f, fill="black")

    if str(p.get("anulado", "")).upper() in ("TRUE", "SI", "SÍ", "1"):
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(over)
        f = _font(140, True)
        tw = od.textlength("ANULADO", font=f)
        od.text(((W - tw) // 2, H // 2 - 80), "ANULADO", font=f, fill=(200, 0, 0, 190))
        img = Image.alpha_composite(img.convert("RGBA"), over.rotate(30, center=(W // 2, H // 2))).convert("RGB")
    return img


def labels_pdf(images, paper="4x6"):
    if paper == "carta":
        pages = []
        for im in images:
            page = Image.new("RGB", (int(8.5 * DPI), 11 * DPI), "white")
            h = int(10.2 * DPI)
            w = int(h * 4 / 6)
            page.paste(im.resize((w, h)), ((page.width - w) // 2, int(0.4 * DPI)))
            pages.append(page)
    else:
        pages = images
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=DPI)
    return buf.getvalue()


# =========================================================
# IDs
# =========================================================
def _seq(pid):
    m = re.match(r"^CD-\d{6}-(\d+)$", str(pid))
    return int(m.group(1)) if m else 0


def next_ids(pallets_df, dia, n):
    usados = pallets_df.loc[pallets_df["dia"] == dia, "id"].map(_seq)
    start = (usados.max() if len(usados) else 0) + 1
    return [f"CD-{dia}-{start + k:02d}" for k in range(n)]


def row_to_label(r):
    try:
        items = json.loads(r["items_json"]) if r["items_json"] else []
    except Exception:
        items = []
    return {
        "id": r["id"], "fecha": r["fecha"], "proveedor": r["proveedor"], "po": r["po"],
        "ubicacion": r["ubicacion"], "pallet_n": r["pallet_n"] or 1, "pallets_lote": r["pallets_lote"] or 1,
        "items": items, "total_cajas": r["total_cajas"] or 0, "anulado": r["anulado"],
    }


# =========================================================
# PANTALLA
# =========================================================
def _ss():
    s = st.session_state
    s.setdefault("etq_lines", [])         # [{producto, sku, cajas}]
    s.setdefault("etq_pending", None)     # código escaneado que no se conoce
    s.setdefault("etq_msg", None)         # (tipo, texto)
    s.setdefault("etq_last_photo", None)
    s.setdefault("etq_result", None)      # lista de dicts de etiquetas recién generadas
    s.setdefault("etq_ver", 0)            # cambia cuando el código modifica las cajas (refresca los inputs)
    return s


def _add_line(producto, sku, cajas=1):
    s = st.session_state
    s["etq_ver"] = s.get("etq_ver", 0) + 1
    for ln in s["etq_lines"]:
        if ln["producto"] == producto:
            ln["cajas"] += cajas
            return ln["cajas"]
    s["etq_lines"].append({"producto": producto, "sku": sku, "cajas": cajas})
    return cajas


def _handle_code(code, codigos, sku_index):
    s = st.session_state
    code = str(code or "").strip()
    if not code:
        return
    if re.match(r"^CD-\d{6}-", code, re.I):
        s["etq_buscar"] = code.upper()
        s["etq_msg"] = ("info", f"Ese es el ID de un pallet ({code.upper()}). Búscalo abajo en Registro.")
        return
    d = digits(code)
    if len(d) < 6:
        s["etq_msg"] = ("warning", f"El código {code} no parece de una caja. Intenta otra vez.")
        return
    hit = lookup_code(d, codigos, sku_index)
    if hit:
        n = _add_line(hit[0], hit[1])
        s["etq_pending"] = None
        s["etq_msg"] = ("success", f"+1 {hit[0]} (van {n:g})")
    else:
        s["etq_pending"] = d
        s["etq_msg"] = ("warning", f"El código {d} todavía no está guardado. Elige el producto abajo una sola vez y queda aprendido.")


def render(standalone=False):
    s = _ss()
    st.markdown("### 🏷️ Etiqueta de pallet (recepción)")

    connected = qb_client.is_configured() and qb_client.is_connected()
    if connected:
        try:
            with st.spinner("Cargando productos de QuickBooks..."):
                items_df = fetch_inventory_items()
            vendors = fetch_vendor_names()
        except Exception as e:
            st.error(f"No pude traer los productos de QuickBooks: {e}")
            items_df, vendors = pd.DataFrame(columns=["producto", "sku", "qoh"]), []
    else:
        st.warning("QuickBooks no está conectado: no hay lista de productos. Pide que lo conecten desde la app principal.")
        items_df, vendors = pd.DataFrame(columns=["producto", "sku", "qoh"]), []

    codigos = load_codigos()
    sku_index = build_sku_index(items_df)
    pallets_df = load_pallets()

    # Productos ordenados: primero los que tienen inventario, y se suman los aprendidos por código.
    prods = items_df.sort_values(["qoh", "producto"], ascending=[False, True])
    opciones = [f"{r.producto}  ·  {r.qoh:,.0f} en QBO" if r.qoh > 0 else r.producto for r in prods.itertuples()]
    opcion_a_prod = dict(zip(opciones, zip(prods["producto"], prods["sku"])))
    for c in codigos.values():
        if c["producto"] not in set(prods["producto"]):
            opcion_a_prod.setdefault(c["producto"], (c["producto"], c["sku"]))
            if c["producto"] not in opciones:
                opciones.append(c["producto"])

    # 1. Proveedor
    st.markdown("**1 · Proveedor**")
    recientes = [p for p in pallets_df.sort_values("fecha", ascending=False)["proveedor"].tolist() if p]
    orden = list(dict.fromkeys(recientes + vendors))
    prov_sel = st.selectbox("Proveedor", ["Elige o busca…"] + orden + ["Otro (escribirlo)"], key="etq_prov_sel",
                            label_visibility="collapsed")
    proveedor = ""
    if prov_sel == "Otro (escribirlo)":
        proveedor = st.text_input("Nombre del proveedor", key="etq_prov_txt").strip()
    elif prov_sel != "Elige o busca…":
        proveedor = prov_sel

    # 2. Qué trae
    st.markdown("**2 · Qué trae el pallet**")
    foto = st.file_uploader("📷 Tomar foto del código de la caja", type=["jpg", "jpeg", "png", "webp"], key="etq_foto",
                            help="En el celular abre la cámara. Toma la foto de cerca y con buena luz.")
    if foto is not None and s["etq_last_photo"] != foto.file_id:
        s["etq_last_photo"] = foto.file_id
        code = decode_photo(foto.getvalue())
        if code:
            _handle_code(code, codigos, sku_index)
        else:
            s["etq_msg"] = ("warning", "No pude leer el código en la foto. Acércate más, con buena luz y el código derecho.")

    def _on_scan():
        _handle_code(st.session_state.get("etq_scan", ""), codigos, sku_index)
        st.session_state["etq_scan"] = ""
    st.text_input("🔫 Pistola o código a mano (Enter para agregar)", key="etq_scan", on_change=_on_scan,
                  placeholder="Escanea con la pistola o escribe el código")

    if s["etq_msg"]:
        tipo, txt = s["etq_msg"]
        getattr(st, tipo)(txt)

    if s["etq_pending"]:
        c1, c2 = st.columns([3, 1])
        with c1:
            elegido = st.selectbox(f"¿Qué producto es el código {s['etq_pending']}?", ["Elige…"] + opciones,
                                   key="etq_pending_sel")
        with c2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Guardar código", disabled=elegido == "Elige…"):
                prod, sku = opcion_a_prod[elegido]
                try:
                    save_codigo(s["etq_pending"], prod, sku)
                    s["etq_msg"] = ("success", f"Listo: el código {s['etq_pending']} queda guardado como {prod}.")
                except Exception as e:
                    s["etq_msg"] = ("warning", f"Agregué el producto, pero no pude guardar el código en Sheets: {e}")
                _add_line(prod, sku)
                s["etq_pending"] = None
                st.rerun()

    with st.expander("➕ Agregar producto a mano (sin escanear)"):
        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            manual = st.selectbox("Producto", ["Busca el producto…"] + opciones, key="etq_manual_sel")
        with c2:
            manual_cj = st.number_input("Cajas", min_value=0.0, value=1.0, step=1.0, key="etq_manual_cj")
        with c3:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Agregar", disabled=manual == "Busca el producto…", key="etq_manual_btn"):
                prod, sku = opcion_a_prod[manual]
                _add_line(prod, sku, manual_cj)
                st.rerun()

    if s["etq_lines"]:
        for i, ln in enumerate(list(s["etq_lines"])):
            c1, c2, c3 = st.columns([1.2, 4, 0.8])
            with c1:
                ln["cajas"] = st.number_input("Cajas", min_value=0.0, value=float(ln["cajas"]), step=1.0,
                                              key=f"etq_cj_{s['etq_ver']}_{i}", label_visibility="collapsed")
            with c2:
                st.markdown(f"**{ln['producto']}**")
            with c3:
                if st.button("✕", key=f"etq_del_{s['etq_ver']}_{i}"):
                    s["etq_lines"].pop(i)
                    s["etq_ver"] += 1
                    st.rerun()
        total = sum(ln["cajas"] for ln in s["etq_lines"])
        st.markdown(f"**Total cajas: {total:,.0f}**")
    else:
        st.caption("Todavía no hay productos. Escanea una caja o agrégalo a mano.")

    # 3. Opcional
    st.markdown("**3 · Opcional**")
    c1, c2, c3 = st.columns(3)
    with c1:
        po = st.text_input("PO o factura #", key="etq_po", placeholder="Ej. 1177")
    with c2:
        ubic = st.text_input("Ubicación", key="etq_ubic", placeholder="Ej. B02")
    with c3:
        lote = st.number_input("Pallets iguales", min_value=1, max_value=40, value=1, step=1, key="etq_lote",
                               help="Si llegan varios pallets con lo mismo, cada uno recibe su propio ID.")

    if st.button("🏷️ Generar etiqueta", type="primary", width="stretch"):
        items = [{"producto": ln["producto"], "sku": ln["sku"], "cajas": ln["cajas"]} for ln in s["etq_lines"] if ln["cajas"] > 0]
        if not proveedor:
            st.error("Falta el proveedor.")
        elif not items:
            st.error("Falta lo que trae el pallet: escanea o agrega al menos un producto con cajas.")
        else:
            ahora = datetime.now()
            dia = ahora.strftime("%y%m%d")
            fresh = load_pallets_fresh()  # releer justo antes, por si alguien generó otro pallet
            ids = next_ids(fresh, dia, int(lote))
            total = sum(it["cajas"] for it in items)
            contenido = "; ".join(f"{it['cajas']:g} {it['producto']}" for it in items)
            nuevos = [{
                "id": pid, "fecha": ahora.strftime("%Y-%m-%d %H:%M"), "dia": dia, "proveedor": proveedor,
                "po": po, "ubicacion": ubic, "pallet_n": k + 1, "pallets_lote": int(lote),
                "contenido": contenido, "items_json": json.dumps(items, ensure_ascii=False),
                "total_cajas": total, "anulado": "",
            } for k, pid in enumerate(ids)]
            try:
                _write_ws(PALLETS_WORKSHEET, _as_text(pd.concat([fresh, pd.DataFrame(nuevos)], ignore_index=True)[PALLET_COLUMNS], ["dia", "po"]))
                load_pallets.clear()
                s["etq_result"] = [{**n, "items": items} for n in nuevos]
                s["etq_lines"] = []
                s["etq_msg"] = None
                s["etq_pending"] = None
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo guardar en Google Sheets: {e}")

    if s["etq_result"]:
        res = s["etq_result"]
        st.success(f"Listo: {res[0]['id']}" + (f" a {res[-1]['id']}" if len(res) > 1 else "") + " guardado en el registro.")
        _show_labels(res, key="nuevo")

    # Registro
    st.divider()
    st.markdown("### 📋 Registro de pallets")
    buscar = st.text_input("Escanea la etiqueta del pallet o busca por ID, proveedor o producto", key="etq_buscar")
    reg = pallets_df.sort_values("fecha", ascending=False)
    if buscar:
        b = buscar.strip().lower()
        reg = reg[reg.apply(lambda r: b in f"{r['id']} {r['proveedor']} {r['po']} {r['contenido']}".lower(), axis=1)]
    if reg.empty:
        st.caption("No hay pallets que mostrar todavía." if pallets_df.empty else "Nada coincide con la búsqueda.")
    else:
        vista = reg[["id", "fecha", "proveedor", "po", "contenido", "total_cajas", "anulado"]].head(200)
        st.dataframe(vista, hide_index=True, width="stretch")
        elegido = st.selectbox("Ver o reimprimir", ["Elige un pallet…"] + reg["id"].head(200).tolist(), key="etq_reg_sel",
                               index=1 if len(reg) == 1 else 0)
        if elegido != "Elige un pallet…":
            r = reg[reg["id"] == elegido].iloc[0]
            _show_labels([row_to_label(r)], key="reg")
            if str(r["anulado"]).upper() != "TRUE":
                if st.checkbox(f"Quiero anular {elegido} (el ID no se vuelve a usar)", key="etq_anular_chk"):
                    if st.button("Anular", key="etq_anular_btn"):
                        full = load_pallets_fresh()
                        full.loc[full["id"] == elegido, "anulado"] = "TRUE"
                        _write_ws(PALLETS_WORKSHEET, _as_text(full[PALLET_COLUMNS], ["dia", "po"]))
                        load_pallets.clear()
                        st.rerun()


def _show_labels(lista, key):
    labels = [{**p, "items": p.get("items") or []} for p in lista]
    imgs = [render_label(p) for p in labels]
    if len(imgs) > 1:
        n = st.slider("Ver etiqueta", 1, len(imgs), 1, key=f"etq_slider_{key}")
    else:
        n = 1
    st.image(imgs[n - 1], width=320)
    papel = st.radio("Papel", ["Etiqueta 4x6", "Hoja carta"], horizontal=True, key=f"etq_papel_{key}")
    pdf = labels_pdf(imgs, "carta" if papel == "Hoja carta" else "4x6")
    nombre = f"Etiqueta_{labels[0]['id']}.pdf" if len(labels) == 1 else f"Etiquetas_{labels[0]['id']}_a_{_seq(labels[-1]['id'])}.pdf"
    st.download_button("⬇️ Descargar PDF para imprimir", pdf, file_name=nombre, mime="application/pdf",
                       width="stretch", key=f"etq_pdf_{key}")
