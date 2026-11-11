"""Рисование для панели: один интерфейс, два «движка» — Tk Canvas (программа) и PIL (проверка раскладки
без экрана). Координаты — пиксели (float). Размер шрифта — в пикселях."""
FONT = "Arial"


class TkGfx:
    """Рисует на tk.Canvas; все элементы слоя получают тег, чтобы слой можно было перерисовать целиком."""

    def __init__(self, canvas):
        self.cv = canvas
        self.tag = ""
        self._fonts = {}

    def begin(self, tag):
        self.tag = tag
        self.cv.delete(tag)

    def end(self):
        self.tag = ""

    def _font(self, size, bold):
        key = (int(round(size)), bool(bold))
        f = self._fonts.get(key)
        if f is None:
            import tkinter.font as _tkfont
            f = self._fonts[key] = _tkfont.Font(family=FONT, size=-max(6, key[0]), weight="bold" if bold else "normal")
        return f

    def rect(self, x0, y0, x1, y1, fill="", outline="", width=1):
        self.cv.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=width, tags=self.tag)

    def rrect(self, x0, y0, x1, y1, r, fill="", outline="", width=1):
        r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        self.cv.create_polygon(pts, smooth=True, fill=fill, outline=outline, width=width, tags=self.tag)

    def line(self, x0, y0, x1, y1, color, width=1, dash=None):
        self.cv.create_line(x0, y0, x1, y1, fill=color, width=width, dash=dash, tags=self.tag)

    def oval(self, x0, y0, x1, y1, fill="", outline="", width=1):
        self.cv.create_oval(x0, y0, x1, y1, fill=fill, outline=outline, width=width, tags=self.tag)

    def text(self, x, y, s, size, color, bold=True, anchor="center"):
        self.cv.create_text(x, y, text=s, fill=color, font=self._font(size, bold), anchor=anchor, tags=self.tag)

    def measure(self, s, size, bold=True):
        return self._font(size, bold).measure(s)


class PilGfx:
    """Рисует в PIL-картинку (для проверки раскладки на машине без Tk). Сглаживание — рендер ×2."""

    K = 2

    def __init__(self, w, h, bg="#0b0e12", font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 font_path_n="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        from PIL import Image, ImageDraw
        self.W, self.H = w, h
        self.img = Image.new("RGB", (w * self.K, h * self.K), bg)
        self.d = ImageDraw.Draw(self.img)
        self.fp, self.fpn, self._fonts = font_path, font_path_n, {}

    def begin(self, tag):
        pass

    def end(self):
        pass

    def _font(self, size, bold):
        from PIL import ImageFont
        key = (int(round(size * self.K)), bold)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(self.fp if bold else self.fpn, max(6, key[0]))
        return self._fonts[key]

    def _p(self, *v):
        return [c * self.K for c in v]

    def rect(self, x0, y0, x1, y1, fill="", outline="", width=1):
        self.d.rectangle(self._p(x0, y0, x1, y1), fill=fill or None, outline=outline or None, width=int(width * self.K))

    def rrect(self, x0, y0, x1, y1, r, fill="", outline="", width=1):
        self.d.rounded_rectangle(self._p(x0, y0, x1, y1), radius=r * self.K, fill=fill or None,
                                 outline=outline or None, width=int(width * self.K))

    def line(self, x0, y0, x1, y1, color, width=1, dash=None):
        if not dash:
            self.d.line(self._p(x0, y0, x1, y1), fill=color, width=max(1, int(width * self.K)))
            return
        import math
        L = math.hypot(x1 - x0, y1 - y0)
        if L == 0:
            return
        ux, uy, pos, on = (x1 - x0) / L, (y1 - y0) / L, 0.0, True
        while pos < L:
            seg = min(dash[0 if on else 1], L - pos)
            if on:
                self.d.line(self._p(x0 + ux * pos, y0 + uy * pos, x0 + ux * (pos + seg), y0 + uy * (pos + seg)),
                            fill=color, width=max(1, int(width * self.K)))
            pos, on = pos + seg, not on

    def oval(self, x0, y0, x1, y1, fill="", outline="", width=1):
        self.d.ellipse(self._p(x0, y0, x1, y1), fill=fill or None, outline=outline or None, width=int(width * self.K))

    def text(self, x, y, s, size, color, bold=True, anchor="center"):
        a = {"center": "mm", "w": "lm", "e": "rm", "n": "mt", "s": "ms", "nw": "lt", "ne": "rt"}[anchor]
        self.d.text((x * self.K, y * self.K), s, font=self._font(size, bold), fill=color, anchor=a)

    def measure(self, s, size, bold=True):
        return self._font(size, bold).getlength(s) / self.K

    def save(self, path):
        self.img.resize((self.W, self.H)).save(path)
