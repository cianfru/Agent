/* Pick a chip colour per logo: logos that already fill their circle need none; light artwork sits on a dark chip,
   dark artwork on a light one. Writes chips.json, which build.py embeds next to the logos. */
const { chromium } = require("playwright"); const fs = require("fs"), path = require("path");
const dir = path.join(__dirname, "coins");
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  const out = {};
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith(".svg"))) {
    const uri = "data:image/svg+xml;base64," + fs.readFileSync(path.join(dir, f)).toString("base64");
    out[f.replace(".svg", "")] = await p.evaluate(async (src) => {
      const img = new Image(); img.src = src; await img.decode();
      const c = document.createElement("canvas"); c.width = c.height = 64; const g = c.getContext("2d"); g.drawImage(img, 0, 0, 64, 64);
      const d = g.getImageData(0, 0, 64, 64).data; let inCircle = 0, covered = 0, lum = 0, n = 0;
      for (let y = 0; y < 64; y++) for (let x = 0; x < 64; x++) {
        if (Math.hypot(x - 31.5, y - 31.5) > 31) continue; inCircle++;
        const i = (y * 64 + x) * 4, a = d[i + 3] / 255; if (a > 0.5) { covered++; lum += (0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]) / 255; n++; }
      }
      const cover = covered / inCircle, L = n ? lum / n : 0;
      return { cover: +cover.toFixed(2), lum: +L.toFixed(2), chip: cover > 0.9 ? "transparent" : L > 0.55 ? "#1d1f3f" : "#f4f2fb" };
    }, uri);
  }
  fs.writeFileSync(path.join(__dirname, "chips.json"), JSON.stringify(out, null, 1));
  console.log(Object.entries(out).map(([k, v]) => `${k} ${v.cover} ${v.lum} ${v.chip}`).join("\n"));
  await b.close();
})();
