# NexusERP · Control de Asistencia (Google Apps Script)

App web de marcaje de asistencia (Kiosco) y supervisión (Torre de Control) para varias empresas.
Funciona dentro de Google Apps Script y guarda todo en un Google Sheet, así que toda la compañía
ve los mismos datos.

---

## 1. Instalación (para quien la publica)

1. Cree un **Google Sheet nuevo** con la cuenta de la empresa (ej. "Control de Asistencia").
2. En el Sheet: **Extensiones → Apps Script**.
3. En el editor:
   - Abra `Código.gs`, borre todo y pegue el contenido de **`Codigo.gs`** de esta carpeta.
   - Clic en **＋ → HTML**, nómbrelo exactamente **`index`** (sin `.html`) y pegue todo **`index.html`**.
     Es un archivo grande (≈230 KB, casi todo en una línea); pegarlo tarda unos segundos.
   - Guarde (Ctrl+S).
4. **Implementar → Nueva implementación → ⚙ → Aplicación web**:
   - Descripción: `v1`
   - **Ejecutar como: Yo** (la cuenta dueña del Sheet)
   - **Quién tiene acceso:**
     - Con Google Workspace: **"Cualquier usuario de <su dominio>"** (solo correos de la empresa). ← recomendado
     - Con Gmail personal: "Cualquier usuario con una cuenta de Google" (cualquiera con el link y una cuenta Google podría entrar).
5. **Implementar**, autorice los permisos (Avanzado → Ir a … → Permitir) y copie la **URL de la aplicación web**
   (`https://script.google.com/macros/s/…/exec`). Esa es la URL que usan todos.
6. Primer ingreso a la Torre de Control: un supervisor + clave **`admin123`** → la app obliga a cambiarla.

> Los empleados **no** necesitan permiso sobre el Sheet: la app escribe como el dueño.

### Actualizar la app después de un cambio
Pegue el nuevo `index.html` y/o `Codigo.gs` → **Implementar → Gestionar implementaciones → ✏ (editar) →
Versión: Nueva versión → Implementar**. Así la URL no cambia.

### Si olvidan la contraseña de la Torre
En el editor de Apps Script, elija la función `resetTowerPassword` y pulse **Ejecutar**. Vuelve a `admin123`.

---

## 2. Qué guarda y dónde

| Hoja | Contenido |
|---|---|
| **Marcajes** | Una fila por marcaje (entrada, almuerzo, regreso, salida, salida forzada). Es el reporte principal; se puede filtrar, hacer tablas dinámicas, etc. Incluye el correo Google de quien marcó (solo si es del mismo dominio). |
| **Empleados / Empresas / Estaciones / Estado Actual** | Vistas legibles. Se (re)escriben al pulsar **Sincronizar** en Torre → Configuración. |
| **_Config** (oculta) | Datos internos de la app en JSON (empresas, empleados, estaciones, estado de cada empleado). **No editar a mano.** |

La contraseña de la Torre se guarda como hash (SHA-256 con sal) en las *Propiedades del script*, nunca en el Sheet ni en la página.

---

## 3. Cómo está hecho (para quien siga el desarrollo)

```
control-asistencia-apps-script/
├── Codigo.gs                       ← backend (Apps Script). Se pega tal cual.
├── index.html                      ← GENERADO por build.sh. Se pega en el archivo HTML "index".
├── src/App.tsx                     ← código fuente de la interfaz (React + Tailwind + lucide-react)
├── src/App_original_localStorage.tsx ← versión original (antes de Apps Script), solo referencia
├── src/input.css, tailwind.config.js, build-html.js, build.sh, package.json
```

**No edite `index.html` a mano.** Edite `src/App.tsx` y regenere:

```bash
cd control-asistencia-apps-script
bash build.sh        # requiere Node 18+; instala dependencias la primera vez
```

### Arquitectura

```
Navegador (index.html, React)                     Apps Script (Codigo.gs)            Google Sheet
──────────────────────────────                     ───────────────────────            ────────────
doGet() inyecta el estado inicial en  ◀────────── doGet() → getState()   ◀───────── _Config, Marcajes
  window.__ERP__ (placeholder __ERP_BOOT__)
Kiosco: Confirmar registro ─ google.script.run ─▶ registerMark(entry)    ─────────▶ appendRow en Marcajes
                                                   (valida estado, pone hora       + estado en _Config
                                                    Miami del servidor, LockService)
Torre: login ───────────────────────────────────▶ verifyTowerPassword(pwd)
Torre: editar empresas/empleados/estaciones ────▶ saveConfig(pwd, key, json)  ──▶ _Config
Torre: Forzar salida ───────────────────────────▶ registerMark(entry, true, pwd)
Torre: Sincronizar ─────────────────────────────▶ fullSync(pwd)            ─────▶ hojas legibles
Cada 20 s ──────────────────────────────────────▶ getState()   (refresca marcajes y estado)
```

Puntos clave del código:

- **`src/App.tsx`**: helper `gas(fn, ...args)` envuelve `google.script.run` en una Promise.
  `BOOT = window.__ERP__` trae el estado inicial. `savedRef` guarda el último JSON enviado/recibido
  de cada colección para no re-guardar lo que vino del servidor. `applyServerState()` aplica el polling.
- **El servidor es la autoridad** para el estado de cada empleado (PRESENTE / ALMUERZO / FINALIZADO / AUSENTE)
  y para la hora del marcaje. El cliente valida antes para dar respuesta rápida, pero si el servidor
  rechaza (ej. otro kiosco ya marcó), se muestra el mensaje del servidor.
- **Seguridad**: toda función que modifica configuración o fuerza salidas exige la contraseña de Torre
  (`checkPassword_`). La única excepción es `saveConfig` cuando esa colección todavía no existe
  (primera carga, siembra los datos de ejemplo).
- Datos grandes: `writeJson_` parte el JSON en trozos de 45.000 caracteres por celda.
- Se envían a la pantalla los últimos `MAX_LOGS_TO_CLIENT` (3000) marcajes; el histórico completo queda en el Sheet.

### Limitaciones conocidas / ideas para seguir
- **Exportar CSV** puede no funcionar dentro de Apps Script (el iframe de Google puede bloquear descargas). El reporte real es la hoja *Marcajes*.
- Los datos de ejemplo (empresas y empleados ficticios) se cargan la primera vez; edítelos o bórrelos desde la Torre.
- El estado de un empleado no se reinicia solo cada día (igual que la versión original): quien olvidó marcar salida queda "PRESENTE" hasta que la Torre use **Forzar salida**. Idea: un activador diario (`ScriptApp.newTrigger`) que cierre jornadas abiertas a medianoche.
- Ideas: reporte de horas trabajadas por día/semana en una hoja aparte; PIN por empleado en el kiosco; foto o geolocalización.
- Límites de Apps Script (cuenta Workspace): ~30 ejecuciones simultáneas y 6 min por ejecución; suficiente para cientos de marcajes por hora.

---

## 4. Contexto para seguir con otra IA (Gemini, ChatGPT, Claude…)

Copie y pegue este bloque junto con los archivos `Codigo.gs` y `src/App.tsx`:

```
Estoy trabajando en una app de control de asistencia ("NexusERP") hecha con React 18 + Tailwind 3 +
lucide-react, compilada con esbuild a un único index.html que se sirve con HtmlService de Google Apps Script.
El backend es Codigo.gs (Apps Script vinculado a un Google Sheet).

Reglas del proyecto:
- La UI está en src/App.tsx. Para comunicarse con el servidor usa gas('nombreFuncion', ...args),
  que envuelve google.script.run en una Promise. No usar fetch ni localStorage para datos.
- El estado inicial llega en window.__ERP__ (doGet reemplaza el texto __ERP_BOOT__ en index.html).
- Datos: hoja "Marcajes" (una fila por marcaje, columnas en LOG_HEADERS) y hoja oculta "_Config"
  con JSON por clave: companies, employees, stations, status.
- El servidor es la autoridad: registerMark() valida la transición de estado con LockService y pone
  la hora oficial (America/New_York). Funciones que modifican datos exigen la contraseña de la Torre
  (checkPassword_; hash SHA-256 con sal en ScriptProperties).
- Para compilar: bash build.sh (genera index.html). No editar index.html a mano.
- Para publicar: pegar Codigo.gs y index.html en el proyecto Apps Script e implementar una
  nueva versión de la aplicación web (Ejecutar como: Yo).

Lo que quiero hacer ahora: <describa el cambio>
```
