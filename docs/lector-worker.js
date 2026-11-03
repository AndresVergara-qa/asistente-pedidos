// Lector de códigos en segundo plano (Web Worker): así leer cada cuadro no traba el video de la cámara.
// Recibe el cuadro del video (ImageBitmap) y la zona a leer, la recorta aquí y la lee con zxing-wasm.
importScripts("vendor/zxing-wasm-reader.js");
const listo = ZXingWASM.prepareZXingModule({
  overrides: { locateFile: (path, prefix) => path.endsWith(".wasm") ? new URL("vendor/" + path, self.location.href).href : prefix + path },
  fireImmediately: true,
});
let leer = {}, cv = null, ctx = null;

function recortar(bmp, z, escala) {
  if (typeof OffscreenCanvas === "undefined") return null;
  if (!cv) { cv = new OffscreenCanvas(1, 1); ctx = cv.getContext("2d", { willReadFrequently: true }); }
  if (!ctx) return null;
  // z = [x, y, ancho, alto] de la zona a leer (la página dice cuál). "escala": el cuadro entero se reduce
  // a 1280 de ancho como máximo; las franjas se leen a resolución completa.
  const [x, y, w, h] = z || [0, 0, bmp.width, bmp.height];
  const k = escala ? Math.min(1, 1280 / w) : 1;
  cv.width = Math.round(w * k); cv.height = Math.round(h * k);
  ctx.drawImage(bmp, x, y, w, h, 0, 0, cv.width, cv.height);
  return ctx.getImageData(0, 0, cv.width, cv.height);
}

// En algunos Android el cuadro del video llega transparente al segundo plano: se avisa para leer de otra forma.
function vacio(img) {
  const d = img.data, paso = Math.max(4, Math.floor(d.length / 4 / 200) * 4);
  for (let i = 3; i < d.length; i += paso) if (d[i] !== 0) return false;
  return true;
}

onmessage = async (e) => {
  const { id, img, bmp, zona, escala, opciones } = e.data;
  if (opciones) { leer = opciones; return; }
  try {
    let data = img;
    if (bmp) {
      data = recortar(bmp, zona, escala); bmp.close();
      if (!data) { postMessage({ id, text: null, sinCanvas: true }); return; }
      if (vacio(data)) { postMessage({ id, text: null, vacio: true }); return; }
    }
    await listo;
    const r = await ZXingWASM.readBarcodes(data, leer);
    const ok = r.find((x) => x.text);
    postMessage({ id, text: ok ? ok.text : null });
  } catch (err) {
    postMessage({ id, text: null, error: String(err) });
  }
};
