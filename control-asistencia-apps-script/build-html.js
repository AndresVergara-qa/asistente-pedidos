// Une CSS + JS compilados en index.html para HtmlService de Apps Script.
const fs = require('fs');
const css = fs.readFileSync('build/app.css', 'utf8');
const js = fs.readFileSync('build/app.js', 'utf8').replace(/<\/script/gi, '<\\/script');
const html = `<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<base target="_top">
<style>body{background:#020617;color:#e2e8f0;margin:0}${css}</style>
</head>
<body>
<div id="root"></div>
<script>window.__ERP__ = __ERP_BOOT__;</script>
<script>${js}</script>
</body>
</html>
`;
fs.writeFileSync('index.html', html);
