/** @OnlyCurrentDoc */
// Con la línea de arriba, Google pide permiso solo sobre este Google Sheet, no sobre todos.

/**
 * Etiquetas de pallet: la "base de datos" de la página docs/index.html vive en
 * el mismo Google Sheet que usa asistente-pedidos, en tres pestañas:
 *   - pallets              una fila por pallet (mismas columnas que la pestaña de Streamlit)
 *   - codigos_cajas        códigos de barras aprendidos -> producto
 *   - productos_etiquetas  lista de productos (se sube desde la página, en Ajustes)
 *   - codigos_internet     nombres que dieron las bases públicas de códigos (para no buscar dos veces)
 *
 * Cómo instalarlo (una sola vez):
 *   1. Abre el Google Sheet > Extensiones > Apps Script, borra lo que haya y pega este archivo.
 *   2. Cambia CLAVE (abajo) por la clave que van a usar los del almacén y guarda.
 *   3. Implementar > Nueva implementación > tipo "Aplicación web".
 *      Ejecutar como: Yo. Quién tiene acceso: Cualquier persona. Autoriza los permisos
 *      (incluye "conectarse a un servicio externo": es para buscar los códigos que no están en el inventario).
 *   4. Copia la URL que termina en /exec: es la que se pega en la página.
 * Si después cambias este código: Implementar > Gestionar implementaciones > editar > Nueva versión.
 */
const CLAVE = 'cambia-esta-clave';

const HOJA_PALLETS = 'pallets';
const HOJA_CODIGOS = 'codigos_cajas';
const HOJA_PRODUCTOS = 'productos_etiquetas';
const COLS_PALLETS = ['id', 'fecha', 'dia', 'proveedor', 'po', 'ubicacion', 'pallet_n', 'pallets_lote',
  'contenido', 'items_json', 'total_cajas', 'anulado', 'recibido_por'];
const HOJA_INTERNET = 'codigos_internet';
const COLS_CODIGOS = ['codigo', 'producto', 'sku', 'fecha', 'fuente'];
const COLS_INTERNET = ['codigo', 'nombre', 'fuente', 'fecha'];
const COLS_PRODUCTOS = ['producto', 'sku', 'qoh', 'actualizado'];
// Columnas que Sheets convertiría en número (y perdería ceros o dígitos): se guardan como texto.
const COMO_TEXTO = ['dia', 'po', 'codigo', 'sku'];

function doGet() {
  return responder_(() => ({ mensaje: 'Etiquetas de pallet en línea' }));
}

function doPost(e) {
  return responder_(() => {
    const req = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (String(req.clave || '') !== CLAVE) throw new Error('CLAVE');
    switch (req.accion) {
      case 'inicio': return inicio_();
      case 'pallets': return { pallets: pallets_() };
      case 'crear': return crear_(req.pallet || {}, req.copias);
      case 'crearLote': return crearLote_(req);
      case 'codigo': return guardarCodigo_(req.codigo, req.producto, req.sku, req.fuente);
      case 'buscarUpc': return buscarUpc_(req.codigo);
      case 'anular': return anular_(req.id);
      case 'productos': return subirProductos_(req.productos || []);
      default: throw new Error('Acción desconocida: ' + req.accion);
    }
  });
}

function responder_(fn) {
  let out;
  try {
    out = Object.assign({ ok: true }, fn());
  } catch (err) {
    out = { ok: false, error: String((err && err.message) || err) };
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

// ---------- lectura y escritura de pestañas ----------
function hoja_(nombre, cols) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(nombre);
  if (!sh) sh = ss.insertSheet(nombre);
  if (sh.getLastRow() === 0) sh.getRange(1, 1, 1, cols.length).setValues([cols]);
  return sh;
}

// Encabezados de la pestaña; si falta alguna columna nuestra, se agrega al final.
function encabezado_(sh, cols) {
  const lastCol = Math.max(sh.getLastColumn(), 1);
  const head = sh.getRange(1, 1, 1, lastCol).getDisplayValues()[0].map(h => String(h).trim());
  const faltan = cols.filter(c => head.indexOf(c) < 0);
  if (faltan.length) {
    sh.getRange(1, head.filter(h => h !== '').length + 1, 1, faltan.length).setValues([faltan]);
    return encabezado_(sh, cols);
  }
  return head;
}

function leer_(nombre, cols) {
  const sh = hoja_(nombre, cols);
  const head = encabezado_(sh, cols);
  if (sh.getLastRow() < 2) return [];
  const vals = sh.getRange(2, 1, sh.getLastRow() - 1, head.length).getDisplayValues();
  return vals.filter(r => r.some(v => v !== '')).map((r, k) => {
    const o = { _fila: k + 2 };
    cols.forEach(c => { o[c] = String(r[head.indexOf(c)]).replace(/^'/, '').trim(); });
    return o;
  });
}

function fila_(head, obj) {
  return head.map(h => {
    if (!(h in obj)) return '';
    const v = obj[h];
    if (v === null || v === undefined) return '';
    return COMO_TEXTO.indexOf(h) >= 0 && String(v) !== '' ? "'" + v : v;
  });
}

function agregar_(nombre, cols, objs) {
  if (!objs.length) return;
  const sh = hoja_(nombre, cols);
  const head = encabezado_(sh, cols);
  sh.getRange(sh.getLastRow() + 1, 1, objs.length, head.length).setValues(objs.map(o => fila_(head, o)));
}

function ahora_() {
  const tz = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  const d = new Date();
  return { dia: Utilities.formatDate(d, tz, 'yyMMdd'), fecha: Utilities.formatDate(d, tz, 'yyyy-MM-dd HH:mm') };
}

function conLlave_(fn) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try { return fn(); } finally { lock.releaseLock(); }
}

// ---------- acciones ----------
function inicio_() {
  const prod = leer_(HOJA_PRODUCTOS, COLS_PRODUCTOS).filter(p => p.producto);
  const codigos = {};
  leer_(HOJA_CODIGOS, COLS_CODIGOS).forEach(c => { if (c.codigo) codigos[c.codigo] = { producto: c.producto, sku: c.sku }; });
  return {
    productos: prod.map(p => [p.producto, p.sku, Number(String(p.qoh).replace(/,/g, '')) || 0]),
    actualizado: prod.length ? prod[0].actualizado : '',
    codigos: codigos,
    pallets: pallets_(),
  };
}

function pallets_() {
  return leer_(HOJA_PALLETS, COLS_PALLETS)
    .filter(p => p.id)
    .sort((a, b) => (a.fecha < b.fecha ? 1 : a.fecha > b.fecha ? -1 : (a.id < b.id ? 1 : -1)))
    .slice(0, 1000)
    .map(p => {
      let items = [];
      try { items = JSON.parse(p.items_json || '[]'); } catch (e) { items = []; }
      return {
        id: p.id, fecha: p.fecha, dia: p.dia, proveedor: p.proveedor, po: p.po, ubicacion: p.ubicacion,
        recibido_por: p.recibido_por || '', pallet_n: Number(p.pallet_n) || 1, pallets_lote: Number(p.pallets_lote) || 1, items: items,
        total_cajas: Number(String(p.total_cajas).replace(/,/g, '')) || 0,
        anulado: String(p.anulado).toUpperCase() === 'TRUE',
      };
    });
}

// El mismo producto en dos renglones se suma en uno solo.
function limpiarItems_(lista) {
  const items = [];
  (lista || []).forEach(it => {
    const producto = String(it.producto || '').trim();
    const cajas = Number(it.cajas) || 0;
    if (!producto || cajas <= 0) return;
    const igual = items.filter(x => x.producto === producto)[0];
    if (igual) igual.cajas += cajas;
    else items.push({ producto: producto, sku: String(it.sku || ''), cajas: cajas });
  });
  return items;
}

// contenidos: una lista de items por pallet. Todos van en el mismo lote (pallet 1 de N, 2 de N...).
function guardarLote_(p, contenidos) {
  const proveedor = String(p.proveedor || '').trim();
  if (!proveedor) throw new Error('Falta el proveedor.');
  if (!contenidos.length || contenidos.some(c => !c.length)) throw new Error('Falta lo que trae el pallet.');
  if (contenidos.length > 40) throw new Error('Son más de 40 pallets: guárdalos en dos partes.');
  return conLlave_(() => {
    const t = ahora_();
    // Consecutivo del día: se lee la pestaña dentro de la llave, así dos teléfonos no sacan el mismo ID.
    const usados = leer_(HOJA_PALLETS, COLS_PALLETS)
      .map(r => /^CD-(\d{6})-(\d+)$/.exec(r.id))
      .filter(m => m && m[1] === t.dia)
      .map(m => parseInt(m[2], 10));
    const desde = (usados.length ? Math.max.apply(null, usados) : 0) + 1;
    const n = contenidos.length;
    const nuevos = contenidos.map((items, k) => ({
      id: 'CD-' + t.dia + '-' + String(desde + k).padStart(2, '0'), fecha: t.fecha, dia: t.dia,
      proveedor: proveedor, po: String(p.po || '').trim(), ubicacion: String(p.ubicacion || '').trim(),
      recibido_por: String(p.recibido_por || '').trim(), pallet_n: k + 1, pallets_lote: n, contenido: items.map(i => i.cajas + ' ' + i.producto).join('; '),
      items_json: JSON.stringify(items), total_cajas: items.reduce((a, i) => a + i.cajas, 0), anulado: '',
      _items: items,
    }));
    agregar_(HOJA_PALLETS, COLS_PALLETS, nuevos);
    return {
      pallets: nuevos.map(x => ({
        id: x.id, fecha: x.fecha, dia: x.dia, proveedor: x.proveedor, po: x.po, ubicacion: x.ubicacion,
        recibido_por: x.recibido_por, pallet_n: x.pallet_n, pallets_lote: x.pallets_lote, items: x._items, total_cajas: x.total_cajas, anulado: false,
      })),
    };
  });
}

// Pallets iguales: el mismo contenido repetido en "copias" pallets.
function crear_(p, copias) {
  const n = Math.max(1, Math.min(40, parseInt(copias, 10) || 1));
  const items = limpiarItems_(p.items);
  const contenidos = [];
  for (let k = 0; k < n; k++) contenidos.push(items);
  return guardarLote_(p, items.length ? contenidos : [[]]);
}

// Pallets distintos que llegaron juntos: cada uno con lo suyo.
function crearLote_(req) {
  const contenidos = (req.pallets || []).map(x => limpiarItems_(x && x.items)).filter(c => c.length);
  return guardarLote_(req, contenidos);
}

function guardarCodigo_(codigo, producto, sku, fuente) {
  // Solo dígitos si es numérico; si trae letras (Code 128, como los X00… de Amazon), letras y números.
  codigo = String(codigo || '').trim().toUpperCase();
  codigo = /[A-Z]/.test(codigo) ? codigo.replace(/[^A-Z0-9]/g, '') : codigo.replace(/\D/g, '');
  producto = String(producto || '').trim();
  if (!codigo || !producto) throw new Error('Falta el código o el producto.');
  return conLlave_(() => {
    const sh = hoja_(HOJA_CODIGOS, COLS_CODIGOS);
    const head = encabezado_(sh, COLS_CODIGOS);
    const obj = { codigo: codigo, producto: producto, sku: String(sku || ''), fecha: ahora_().fecha, fuente: String(fuente || 'almacén') };
    const ya = leer_(HOJA_CODIGOS, COLS_CODIGOS).filter(c => c.codigo === codigo)[0];
    if (ya) sh.getRange(ya._fila, 1, 1, head.length).setValues([fila_(head, obj)]);
    else agregar_(HOJA_CODIGOS, COLS_CODIGOS, [obj]);
    return {};
  });
}

// ---------- códigos que no están en el inventario ----------
// Busca el nombre del producto en bases públicas y gratis (Open Food Facts y UPCitemdb). Lo que se
// encuentra queda en la pestaña codigos_internet; lo que no, se recuerda unas horas para no repetir.
function buscarUpc_(codigo) {
  const d = String(codigo || '').replace(/\D/g, '');
  if (d.length < 8 || d.length > 14) return { nombre: '' };
  const ya = leer_(HOJA_INTERNET, COLS_INTERNET).filter(r => r.codigo === d)[0];
  if (ya) return { nombre: ya.nombre, fuente: ya.fuente };
  const cache = CacheService.getScriptCache();
  if (cache.get('upc0_' + d)) return { nombre: '' };
  let r = null;
  for (const c of variantesUpc_(d)) {
    r = openFoodFacts_(c) || upcItemDb_(c);
    if (r) break;
  }
  if (!r) { cache.put('upc0_' + d, '1', 6 * 3600); return { nombre: '' }; }
  conLlave_(() => agregar_(HOJA_INTERNET, COLS_INTERNET, [{ codigo: d, nombre: r.nombre, fuente: r.fuente, fecha: ahora_().fecha }]));
  return r;
}

// El mismo código escrito de las formas en que lo guardan las bases: UPC-A de 12, EAN-13, y para el
// GTIN-14 de una caja, el código de adentro (sin el primer dígito y con su dígito de control).
function variantesUpc_(d) {
  const out = [];
  const add = x => { if (x && out.indexOf(x) < 0) out.push(x); };
  add(d);
  if (d.length === 14 && d[0] === '0') add(d.slice(1));
  const sin0 = d.replace(/^0+/, '');
  if (sin0.length <= 12) { add(sin0.padStart(12, '0')); add(sin0.padStart(13, '0')); }
  if (d.length === 14) { const cuerpo = d.slice(1, 13); add(cuerpo + digitoControl_(cuerpo)); }
  return out.slice(0, 4);
}

function digitoControl_(sinDigito) {
  let t = 0;
  for (let i = sinDigito.length - 1, k = 3; i >= 0; i--, k = k === 3 ? 1 : 3) t += Number(sinDigito[i]) * k;
  return String((10 - t % 10) % 10);
}

function pedir_(url) {
  try {
    const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: { 'User-Agent': 'EtiquetasPallet/1.0' } });
    if (res.getResponseCode() !== 200) return null;
    return JSON.parse(res.getContentText());
  } catch (err) { return null; }
}

function openFoodFacts_(c) {
  const j = pedir_('https://world.openfoodfacts.org/api/v2/product/' + c + '.json?fields=product_name,brands,quantity');
  const p = j && j.status === 1 && j.product;
  if (!p || !p.product_name) return null;
  const marca = String(p.brands || '').split(',')[0].trim();
  let nombre = String(p.product_name).trim();
  if (marca && nombre.toUpperCase().indexOf(marca.toUpperCase()) < 0) nombre = marca + ' ' + nombre;
  if (p.quantity) nombre += ' ' + String(p.quantity).trim();
  return { nombre: nombre.replace(/\s+/g, ' ').slice(0, 120), fuente: 'Open Food Facts' };
}

function upcItemDb_(c) {
  const j = pedir_('https://api.upcitemdb.com/prod/trial/lookup?upc=' + c);
  const it = j && j.items && j.items[0];
  if (!it || !it.title) return null;
  return { nombre: String(it.title).replace(/\s+/g, ' ').trim().slice(0, 120), fuente: 'UPCitemdb' };
}

function anular_(id) {
  return conLlave_(() => {
    const sh = hoja_(HOJA_PALLETS, COLS_PALLETS);
    const head = encabezado_(sh, COLS_PALLETS);
    const p = leer_(HOJA_PALLETS, COLS_PALLETS).filter(r => r.id === id)[0];
    if (!p) throw new Error('No encontré el pallet ' + id + '.');
    sh.getRange(p._fila, head.indexOf('anulado') + 1).setValue('TRUE');
    return {};
  });
}

// productos: [[nombre, sku, cantidad], ...] tal como salen del inventario de QuickBooks.
function subirProductos_(productos) {
  const limpios = productos
    .map(p => [String(p[0] || '').trim(), String(p[1] || '').trim(), Number(p[2]) || 0])
    .filter(p => p[0]);
  if (!limpios.length) throw new Error('El archivo no trae productos.');
  return conLlave_(() => {
    const sh = hoja_(HOJA_PRODUCTOS, COLS_PRODUCTOS);
    const fecha = ahora_().fecha;
    sh.clearContents();
    const filas = [COLS_PRODUCTOS].concat(limpios.map(p => [p[0], p[1] ? "'" + p[1] : '', p[2], fecha]));
    sh.getRange(1, 1, filas.length, COLS_PRODUCTOS.length).setValues(filas);
    return { productos: limpios.length, actualizado: fecha };
  });
}
