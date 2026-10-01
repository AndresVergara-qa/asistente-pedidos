"""
Arma el Forecast de Compras: cruza los Estimates abiertos (demanda) contra
Inventory (QtyOnHand de QuickBooks) y lo que está en tránsito (transito.py)
para decidir qué pedir.

Sigue el formato acordado en el procedimiento: una columna por estimate,
Total QTY Estimates, In Transit, Inventory, Total Inventory, Forecast y
To Be Ordered (este último siempre en blanco — es decisión manual, el
procedimiento pide no llenarlo automáticamente).

El Excel se genera con fórmulas reales (SUM, restas) en vez de solo el
número ya calculado, para que se pueda auditar de dónde sale cada total.
"""
import io
import re

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import transito

FALTANTE_RE = re.compile(r"Cant:\s*([\d.]+)")


def build_line_items(selected_estimates, qb_df):
    """Aplana las líneas de los estimates seleccionados, cruzando contra el
    catálogo (por Item Id) para traer el SKU/Descripción reales.
    Devuelve (detalle_df, pendientes_df): detalle_df tiene una fila por
    línea de estimate (Estimate, Product/Service, SKU, Description, Qty);
    pendientes_df son líneas sin match de catálogo (quedaron como texto
    libre al crear el estimado) — el procedimiento pide confirmarlas a mano
    antes de asignarles cantidad, no inventarlas."""
    catalog_by_id = {}
    for _, row in qb_df.iterrows():
        item_id = str(row.get("Item Id", "")).strip()
        if item_id:
            catalog_by_id[item_id] = row

    filas = []
    pendientes = []
    for est in selected_estimates:
        etiqueta = f"Estimate {est['doc_number']} {est['customer']}".strip()
        for ln in est["lines"]:
            if ln.get("sin_catalogar"):
                m = FALTANTE_RE.search(ln["product_name"])
                pendientes.append({
                    "Estimate": etiqueta,
                    "Descripción cruda": ln["product_name"],
                    "Qty detectada": float(m.group(1)) if m else None,
                })
                continue

            cat_row = catalog_by_id.get(str(ln["item_id"]))
            if cat_row is None:
                pendientes.append({
                    "Estimate": etiqueta,
                    "Descripción cruda": ln["product_name"],
                    "Qty detectada": ln["qty"],
                })
                continue

            filas.append({
                "Estimate": etiqueta,
                "Product/Service": cat_row.get("Product/Service", ""),
                "SKU": cat_row.get("SKU", ""),
                "Description": cat_row.get("Sales Description", "") or cat_row.get("Purchase Description", ""),
                "Qty": ln["qty"],
            })

    detalle_df = pd.DataFrame(filas)
    pendientes_df = pd.DataFrame(pendientes)
    return detalle_df, pendientes_df


def build_forecast_table(detalle_df, qb_df):
    """Pivotea el detalle a una fila por SKU/presentación (una columna por
    estimate) y agrega Inventory, In Transit, Total Inventory y Forecast.
    Devuelve (forecast_df, estimate_cols)."""
    if detalle_df.empty:
        return pd.DataFrame(), []

    pivot = detalle_df.pivot_table(
        index=["Product/Service", "SKU", "Description"],
        columns="Estimate",
        values="Qty",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()

    estimate_cols = [c for c in pivot.columns if c not in ("Product/Service", "SKU", "Description")]
    pivot["Total QTY Estimates"] = pivot[estimate_cols].sum(axis=1)

    inv_by_sku = {}
    for _, row in qb_df.iterrows():
        sku = str(row.get("SKU", "")).strip().upper()
        if sku:
            inv_by_sku[sku] = row.get("Qty On Hand", 0) or 0
    sku_upper = pivot["SKU"].astype(str).str.strip().str.upper()
    pivot["Inventory"] = sku_upper.map(inv_by_sku).fillna(0)

    transito_by_sku = transito.get_pending_qty_by_sku()
    pivot["In Transit"] = sku_upper.map(transito_by_sku).fillna(0)

    pivot["Total Inventory"] = pivot["In Transit"] + pivot["Inventory"]
    pivot["Forecast"] = pivot["Total Inventory"] - pivot["Total QTY Estimates"]
    pivot["To Be Ordered"] = ""

    pivot = pivot.sort_values("SKU")
    ordered_cols = ["Product/Service", "SKU", "Description"] + estimate_cols + [
        "Total QTY Estimates", "In Transit", "Inventory", "Total Inventory", "Forecast", "To Be Ordered",
    ]
    return pivot[ordered_cols].reset_index(drop=True), estimate_cols


def export_excel(forecast_df, estimate_cols, pendientes_df):
    """Genera el Excel con fórmulas reales para Total QTY Estimates, Total
    Inventory y Forecast, SKU guardado como texto (no perder ceros a la
    izquierda), y una hoja aparte con las líneas sin catalogar."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Forecast"

    headers = list(forecast_df.columns)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    n_est = len(estimate_cols)
    col_total_est = 4 + n_est
    col_in_transit = col_total_est + 1
    col_inventory = col_total_est + 2
    col_total_inv = col_total_est + 3
    col_forecast = col_total_est + 4

    for r, (_, row) in enumerate(forecast_df.iterrows(), start=2):
        for c, col in enumerate(headers, start=1):
            if col == "SKU":
                cell = ws.cell(row=r, column=c, value=str(row[col]))
                cell.number_format = "@"
            elif col == "Total QTY Estimates":
                first = get_column_letter(4)
                last = get_column_letter(3 + n_est)
                ws.cell(row=r, column=c, value=f"=SUM({first}{r}:{last}{r})")
            elif col == "Total Inventory":
                ws.cell(row=r, column=c, value=f"={get_column_letter(col_in_transit)}{r}+{get_column_letter(col_inventory)}{r}")
            elif col == "Forecast":
                ws.cell(row=r, column=c, value=f"={get_column_letter(col_total_inv)}{r}-{get_column_letter(col_total_est)}{r}")
            else:
                ws.cell(row=r, column=c, value=row[col])

    for c in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 18

    if not pendientes_df.empty:
        ws2 = wb.create_sheet("Revisar - sin catálogo")
        ws2.append(list(pendientes_df.columns))
        for cell in ws2[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="FFF3CD")
        for _, row in pendientes_df.iterrows():
            ws2.append(list(row))
        for c in range(1, len(pendientes_df.columns) + 1):
            ws2.column_dimensions[get_column_letter(c)].width = 40

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
