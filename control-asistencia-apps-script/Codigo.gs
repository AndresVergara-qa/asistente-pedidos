/**
 * NexusERP · Control de Asistencia — Backend en Google Apps Script
 *
 * Este script debe estar VINCULADO a un Google Sheet
 * (en el Sheet: Extensiones > Apps Script).
 *
 * Hojas que crea automáticamente:
 *   - Marcajes       → cada entrada / almuerzo / salida (una fila por marcaje)
 *   - Empleados, Empresas, Estaciones, Estado Actual → vistas legibles (botón "Sincronizar")
 *   - _Config        → datos internos de la app (no editar a mano)
 */

const TZ = 'America/New_York'; // Hora oficial Miami
const MAX_LOGS_TO_CLIENT = 3000; // marcajes más recientes que se envían a la pantalla
const DEFAULT_PASSWORD = 'admin123';
const CHUNK = 45000; // una celda admite 50.000 caracteres

const SHEET_LOGS = 'Marcajes';
const SHEET_CONFIG = '_Config';
const LOG_HEADERS = [
  'ID', 'Fecha', 'Hora', 'ID Colaborador', 'Colaborador', 'ID Empresa', 'Empresa',
  'Departamento', 'Acción', 'Evento', 'Estación', 'Nota', 'Usuario Google', 'Fecha ISO'
];

// ---------------------------------------------------------------------------
// Página web
// ---------------------------------------------------------------------------
function doGet() {
  const boot = getState();
  const json = JSON.stringify(boot).replace(/</g, '\\u003c');
  const html = HtmlService.createHtmlOutputFromFile('index')
    .getContent()
    .replace('__ERP_BOOT__', function () { return json; });
  return HtmlService.createHtmlOutput(html)
    .setTitle('NexusERP · Asistencia')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

// ---------------------------------------------------------------------------
// Funciones llamadas desde la página (google.script.run)
// ---------------------------------------------------------------------------
function getState() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const state = {
    sheetUrl: ss.getUrl(),
    otpUsed: hasCustomPassword_(),
    logs: readLogs_(),
    status: readJson_('status') || {}
  };
  ['companies', 'employees', 'stations'].forEach(function (k) {
    const v = readJson_(k);
    if (v) state[k] = v;
  });
  return state;
}

function verifyTowerPassword(pwd) {
  return { ok: checkPassword_(pwd), otpUsed: hasCustomPassword_() };
}

function changeTowerPassword(currentPwd, newPwd) {
  if (!checkPassword_(currentPwd)) return { ok: false, message: 'La sesión expiró. Vuelva a ingresar a la Torre.' };
  newPwd = String(newPwd || '').trim();
  if (newPwd.length < 4) return { ok: false, message: 'Mínimo 4 caracteres.' };
  if (newPwd === DEFAULT_PASSWORD) return { ok: false, message: 'No puede reutilizar ' + DEFAULT_PASSWORD + '.' };
  const salt = Utilities.getUuid();
  PropertiesService.getScriptProperties().setProperties({
    TOWER_PWD_SALT: salt,
    TOWER_PWD_HASH: hash_(salt + newPwd)
  });
  return { ok: true };
}

/** Guarda empresas / empleados / estaciones. Requiere contraseña de Torre (salvo la primera carga). */
function saveConfig(pwd, key, json) {
  if (['companies', 'employees', 'stations'].indexOf(key) === -1) throw new Error('Clave no permitida: ' + key);
  const value = JSON.parse(json);
  if (!Array.isArray(value)) throw new Error('Formato inválido');
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const firstLoad = readJson_(key) === null;
    if (!firstLoad && !checkPassword_(pwd)) throw new Error('Debe ingresar a la Torre de Control para modificar datos.');
    writeJson_(key, value);
  } finally {
    lock.releaseLock();
  }
  return { ok: true };
}

/** Registra un marcaje. La hora la pone el servidor (hora Miami). */
function registerMark(entry, forced, pwd) {
  if (forced && !checkPassword_(pwd)) return { ok: false, message: 'Debe ingresar a la Torre de Control.' };
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const status = readJson_('status') || {};
    const empId = String(entry.empId || '');
    if (!empId) return { ok: false, message: 'Colaborador inválido.', status: status };

    const employees = readJson_('employees');
    if (employees) {
      const emp = employees.filter(function (e) { return e.id === empId; })[0];
      if (!emp) return { ok: false, message: 'El colaborador ya no existe.', status: status };
      if (emp.active === false) return { ok: false, message: 'El colaborador está inactivo.', status: status };
    }

    const current = (status[empId] && status[empId].state) || 'AUSENTE';
    const action = entry.action;
    let next;
    if (forced) {
      next = 'FINALIZADO';
    } else if (action === 'CHECK_IN') {
      if (current === 'PRESENTE' || current === 'ALMUERZO') return { ok: false, message: 'Ya registra entrada activa.', status: status };
      next = 'PRESENTE';
    } else if (action === 'LUNCH_START') {
      if (current !== 'PRESENTE') return { ok: false, message: 'Debe marcar entrada primero.', status: status };
      next = 'ALMUERZO';
    } else if (action === 'LUNCH_END') {
      if (current !== 'ALMUERZO') return { ok: false, message: 'No está en almuerzo.', status: status };
      next = 'PRESENTE';
    } else if (action === 'CHECK_OUT') {
      if (current === 'AUSENTE' || current === 'FINALIZADO') return { ok: false, message: 'No hay jornada activa.', status: status };
      next = 'FINALIZADO';
    } else {
      return { ok: false, message: 'Acción desconocida.', status: status };
    }

    const now = new Date();
    const clean = {
      id: 'LOG-' + Utilities.getUuid().slice(0, 8).toUpperCase(),
      date: miamiDate_(now),
      timestamp: Utilities.formatDate(now, TZ, 'HH:mm:ss'),
      empId: empId,
      empName: String(entry.empName || ''),
      companyId: String(entry.companyId || ''),
      companyName: String(entry.companyName || ''),
      dept: String(entry.dept || ''),
      action: forced ? 'CHECK_OUT' : action,
      actionLabel: String(entry.actionLabel || ''),
      station: String(entry.station || ''),
      note: String(entry.note || 'Sin observaciones').slice(0, 500),
      user: safeEmail_(),
      iso: Utilities.formatDate(now, TZ, "yyyy-MM-dd'T'HH:mm:ss")
    };

    logsSheet_().appendRow([
      clean.id, clean.date, clean.timestamp, clean.empId, clean.empName, clean.companyId, clean.companyName,
      clean.dept, clean.action, clean.actionLabel, clean.station, clean.note, clean.user, clean.iso
    ]);
    status[empId] = { state: next, lastAction: clean.timestamp };
    writeJson_('status', status);
    return { ok: true, entry: clean, status: status };
  } finally {
    lock.releaseLock();
  }
}

function renameEmployeeStatus(pwd, oldId, newId) {
  if (!checkPassword_(pwd)) throw new Error('No autorizado');
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const status = readJson_('status') || {};
    if (status[oldId]) {
      status[newId] = status[oldId];
      delete status[oldId];
      writeJson_('status', status);
    }
  } finally {
    lock.releaseLock();
  }
  return { ok: true };
}

/** Escribe hojas legibles (Empleados, Empresas, Estaciones, Estado Actual) y devuelve el estado. */
function fullSync(pwd) {
  if (!checkPassword_(pwd)) throw new Error('No autorizado');
  const companies = readJson_('companies') || [];
  const employees = readJson_('employees') || [];
  const stations = readJson_('stations') || [];
  const status = readJson_('status') || {};
  const compName = {};
  companies.forEach(function (c) { compName[c.id] = c.name; });

  writeTable_('Empresas', ['ID', 'Empresa', 'Activa'],
    companies.map(function (c) { return [c.id, c.name, c.active !== false ? 'Sí' : 'No']; }));
  writeTable_('Estaciones', ['ID', 'Estación', 'Activa'],
    stations.map(function (s) { return [s.id, s.name, s.active !== false ? 'Sí' : 'No']; }));
  writeTable_('Empleados', ['ID', 'Nombre', 'Empresa', 'Departamento', 'Cargo', 'Supervisor', 'Activo', 'Motivo inactivo'],
    employees.map(function (e) {
      return [e.id, e.name, compName[e.companyId] || e.companyId, e.department, e.role,
        e.isSupervisor ? 'Sí' : 'No', e.active !== false ? 'Sí' : 'No', e.inactiveReason || ''];
    }));
  writeTable_('Estado Actual', ['ID', 'Nombre', 'Empresa', 'Estado', 'Último marcaje'],
    employees.map(function (e) {
      const st = status[e.id] || {};
      return [e.id, e.name, compName[e.companyId] || e.companyId, st.state || 'AUSENTE', st.lastAction || ''];
    }));
  return getState();
}

// ---------------------------------------------------------------------------
// Utilidades internas
// ---------------------------------------------------------------------------
function logsSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET_LOGS);
  if (!sh) {
    sh = ss.insertSheet(SHEET_LOGS, 0);
    sh.appendRow(LOG_HEADERS);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, LOG_HEADERS.length).setFontWeight('bold');
    sh.getRange('B:C').setNumberFormat('@'); // fecha y hora como texto
  }
  return sh;
}

function readLogs_() {
  const sh = logsSheet_();
  const last = sh.getLastRow();
  if (last < 2) return [];
  const n = Math.min(MAX_LOGS_TO_CLIENT, last - 1);
  const rows = sh.getRange(last - n + 1, 1, n, LOG_HEADERS.length).getDisplayValues();
  return rows.reverse().map(function (r) {
    return {
      id: r[0], date: r[1], timestamp: r[2], empId: r[3], empName: r[4], companyId: r[5],
      companyName: r[6], dept: r[7], action: r[8], actionLabel: r[9], station: r[10], note: r[11],
      user: r[12], iso: r[13]
    };
  });
}

function configSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET_CONFIG);
  if (!sh) {
    sh = ss.insertSheet(SHEET_CONFIG);
    sh.getRange('A1').setValue('Datos internos de la app de asistencia. No editar.');
    sh.hideSheet();
  }
  return sh;
}

function findConfigRow_(sh, key) {
  const last = sh.getLastRow();
  if (last < 2) return -1;
  const keys = sh.getRange(2, 1, last - 1, 1).getValues();
  for (let i = 0; i < keys.length; i++) if (keys[i][0] === key) return i + 2;
  return -1;
}

function readJson_(key) {
  const sh = configSheet_();
  const row = findConfigRow_(sh, key);
  if (row === -1) return null;
  const width = sh.getLastColumn();
  if (width < 2) return null;
  const text = sh.getRange(row, 2, 1, width - 1).getValues()[0].join('');
  if (!text) return null;
  try { return JSON.parse(text); } catch (e) { return null; }
}

function writeJson_(key, value) {
  const sh = configSheet_();
  const text = JSON.stringify(value);
  const parts = [];
  for (let i = 0; i < text.length; i += CHUNK) parts.push(text.slice(i, i + CHUNK));
  if (!parts.length) parts.push('');
  let row = findConfigRow_(sh, key);
  if (row === -1) row = Math.max(sh.getLastRow() + 1, 2);
  const width = Math.max(sh.getLastColumn(), parts.length + 1);
  const values = [key].concat(parts);
  while (values.length < width) values.push('');
  sh.getRange(row, 1, 1, width).setNumberFormat('@').setValues([values]);
}

function writeTable_(name, headers, rows) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clearContents();
  sh.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
  if (rows.length) sh.getRange(2, 1, rows.length, headers.length).setValues(rows);
  sh.setFrozenRows(1);
}

function hasCustomPassword_() {
  return !!PropertiesService.getScriptProperties().getProperty('TOWER_PWD_HASH');
}

function checkPassword_(pwd) {
  pwd = String(pwd || '');
  if (!pwd) return false;
  const props = PropertiesService.getScriptProperties();
  const h = props.getProperty('TOWER_PWD_HASH');
  if (!h) return pwd === DEFAULT_PASSWORD;
  return hash_(props.getProperty('TOWER_PWD_SALT') + pwd) === h;
}

function hash_(text) {
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8);
  return Utilities.base64Encode(bytes);
}

function miamiDate_(d) {
  const dias = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];
  const meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sept', 'oct', 'nov', 'dic'];
  const dow = Number(Utilities.formatDate(d, TZ, 'u')) % 7; // 1=lun ... 7=dom
  const day = Number(Utilities.formatDate(d, TZ, 'd'));
  const month = Number(Utilities.formatDate(d, TZ, 'M')) - 1;
  const year = Utilities.formatDate(d, TZ, 'yyyy');
  return dias[dow] + ', ' + day + ' ' + meses[month] + ' ' + year;
}

function safeEmail_() {
  try { return Session.getActiveUser().getEmail() || ''; } catch (e) { return ''; }
}

/** Úselo desde el editor si olvida la contraseña: vuelve a admin123. */
function resetTowerPassword() {
  PropertiesService.getScriptProperties().deleteProperty('TOWER_PWD_HASH');
  PropertiesService.getScriptProperties().deleteProperty('TOWER_PWD_SALT');
}
