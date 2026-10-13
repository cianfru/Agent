import pathlib
d = pathlib.Path(__file__).parent
page = (d / "page.html").read_text()
js = "\n".join((d / f).read_text() for f in ("art.js", "chart.js", "shell.js", "apps.js", "world.js", "tour.js"))
page = page.replace("/*CSS*/", (d / "style.css").read_text()).replace("/*DATA*/", (d / "sample.json").read_text()).replace("/*JS*/", js)
(d / "outpost.html").write_text(page)
print(len(page) // 1024, "KB")
