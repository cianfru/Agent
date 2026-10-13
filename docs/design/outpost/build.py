import pathlib
d = pathlib.Path(__file__).parent
page = (d / "page.html").read_text()
js = "\n".join((d / f).read_text() for f in ("art.js", "chart.js", "shell.js", "apps.js", "world.js", "tour.js"))
import base64, json
chips = json.loads((d / "chips.json").read_text())
icons = {f.stem: {"src": "data:image/svg+xml;base64," + base64.b64encode(f.read_bytes()).decode(), "chip": chips[f.stem]["chip"]}
         for f in sorted((d / "coins").glob("*.svg"))}
page = page.replace("/*ICONS*/", json.dumps(icons, separators=(",", ":")))
page = page.replace("/*CSS*/", (d / "style.css").read_text()).replace("/*DATA*/", (d / "sample.json").read_text()).replace("/*JS*/", js)
(d / "outpost.html").write_text(page)
print(len(page) // 1024, "KB")
