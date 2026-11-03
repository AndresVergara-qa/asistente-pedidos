// Lector de códigos en segundo plano (Web Worker): así leer cada cuadro no traba el video de la cámara.
// Recibe el cuadro del video (ImageBitmap), lo recorta aquí y lo lee con zxing-wasm.
importScripts("vendor/zxing-wasm-reader.js");
const listo = ZXingWASM.prepareZXingModule({
  overrides: { locateFile: (path, prefix) => path.endsWith(".wasm") ? new URL("vendor/" + path, self.location.href).href : prefix + path },
  fireImmediately: true,
});
let leer = {}, cv = null, ctx = null;

function recortar(bmp, banda) {
  if (typeof OffscreenCanvas === "undefined") return null;
  const vw = bmp.width, vh = bmp.height;
  if (!cv) { cv = new OffscreenCanvas(1, 1); ctx = cv.getContext("2d", { willReadFrequently: true }); }
  if (!ctx) return null;
  if (banda) {
    // La franja del centro, donde está la línea roja, a resolución completa.
    const bh = Math.round(vh * 0.4); cv.width = vw; cv.height = bh;
    ctx.drawImage(bmp, 0, Math.round((vh - bh) / 2), vw, bh, 0, 0, vw, bh);
  } else {
    // El cuadro entero, reducido a 1280 de ancho como máximo.
    const k = Math.min(1, 1280 / vw); cv.width = Math.round(vw * k); cv.height = Math.round(vh * k);
    ctx.drawImage(bmp, 0, 0, cv.width, cv.height);
  }
  return ctx.getImageData(0, 0, cv.width, cv.height);
}

onmessage = async (e) => {
  const { id, img, bmp, banda, opciones } = e.data;
  if (opciones) { leer = opciones; return; }
  try {
    let data = img;
    if (bmp) {
      data = recortar(bmp, banda); bmp.close();
      if (!data) { postMessage({ id, text: null, sinCanvas: true }); return; }
    }
    await listo;
    const r = await ZXingWASM.readBarcodes(data, leer);
    const ok = r.find((x) => x.text);
    postMessage({ id, text: ok ? ok.text : null });
  } catch (err) {
    postMessage({ id, text: null, error: String(err) });
  }
};
