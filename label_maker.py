# -*- coding: utf-8 -*-
"""
Label Maker 3 — диспетчерские наклейки на панель щита и на сами аппараты.

Данные: TAG1 (строка 1) + DESC1..DESC3 (строки 2–4) + ширина.
Ширина: число модулей (ключ словаря), имя блока ACADE (ключ словаря) или миллиметры.

Принцип: одна функция раскладки (layout) считает всю геометрию в мм.
PDF (reportlab) и предпросмотр (Pillow) рисуют один и тот же результат
одним и тем же TTF-шрифтом, поэтому превью = печать.

Файлы рядом со скриптом:
  ISOCPEUR.ttf   — шрифт (желательно; иначе ищется системный)
  widths.json    — словарь ширин (создаётся автоматически)
  settings.json  — настройки (создаётся автоматически)

Два комплекта из одной таблицы: наклейки на панель и наклейки на аппараты
(дублируют панельные — панель сняли, надписи остались). PDF у каждого свой.

Служебные значения в колонке TAG1:
  ---  → перевод на новый ряд (например, следующая DIN-рейка)
  ===  → новая страница
"""

import json
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd
from PIL import Image, ImageDraw, ImageFont, ImageTk
from reportlab.lib.pagesizes import A4, A3, landscape, portrait
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

VERSION = "3.0"
MM_TO_PT = 72 / 25.4
FIELDS = ("tag", "d1", "d2", "d3", "width", "dwidth", "fill")
FIELD_TITLES = ("TAG1 (стр. 1)", "DESC1 (стр. 2)", "DESC2 (стр. 3)", "DESC3 (стр. 4)", "Ширина",
                "Ширина на аппарат", "Заливка тега")
# Заливка полосы тега — светлые цвета, чёрный текст на них читается и на ч/б принтере не сливается
FILL_COLORS = {
    "жёлтый": "#FFF176", "зелёный": "#AED581", "голубой": "#81D4FA", "розовый": "#F48FB1",
    "оранжевый": "#FFB74D", "сиреневый": "#B39DDB", "серый": "#BDBDBD",
}
DEV_NONE_WORDS = ("нет", "-", "—", "0", "none")
DEV_TAG_WORDS = ("тег", "только тег", "tag")
ROW_BREAK = "---"
PAGE_BREAK = "==="


def app_dir():
    if getattr(sys, "frozen", False):          # собранный exe (PyInstaller)
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = app_dir()
WIDTHS_FILE = os.path.join(APP_DIR, "widths.json")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")

# ---------------------------------------------------------------- словарь ширин

DEFAULT_WIDTHS = {
    "numbers": {"1": 18, "2": 36, "3": 54, "4": 71, "5": 88, "6": 105, "7": 123, "8": 141},
    # имена (тип аппарата, имя блока) каждый добавляет свои: «Словарь ширин» → «Добавить»
    "names": {},
}

# Кириллица и латиница, которые выглядят одинаково (Р/P, А/A ...) —
# при сравнении имён считаются одной буквой.
_LOOKALIKE = str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY")


def name_key(s):
    return " ".join(str(s).upper().translate(_LOOKALIKE).split())


# Какая наклейка нужна на сам аппарат (задаётся в словаре для типа аппарата)
DEV_KINDS = {"auto": "авто", "single": "1 модуль", "tag": "только тег", "none": "нет"}
DEV_KINDS_BACK = {v: k for k, v in DEV_KINDS.items()}
MODULE_MM = 17.5   # DIN 43880 — для пересчёта миллиметров в число модулей


class WidthMap:
    def __init__(self):
        self.numbers = {}   # int -> mm
        self.names = {}     # исходное имя -> mm
        self.dev = {}       # исходное имя -> auto | single | tag | none
        self.load()

    def load(self):
        data = DEFAULT_WIDTHS
        if os.path.exists(WIDTHS_FILE):
            try:
                with open(WIDTHS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                messagebox.showwarning("widths.json", f"Не прочитан, взяты значения по умолчанию:\n{e}")
        self.numbers = {int(k): float(v) for k, v in data.get("numbers", {}).items()}
        self.names, self.dev = {}, {}
        for k, v in data.get("names", {}).items():
            if isinstance(v, dict):          # {"mm": 54, "dev": "tag"}
                self.names[str(k)] = float(v.get("mm", 18))
                if v.get("dev", "auto") in DEV_KINDS:
                    self.dev[str(k)] = v["dev"]
            else:                            # старый формат: просто число
                self.names[str(k)] = float(v)
        if not os.path.exists(WIDTHS_FILE):
            self.save()

    def save(self):
        """Возвращает True при успехе."""
        data = {
            "numbers": {str(k): v for k, v in sorted(self.numbers.items())},
            "names": {k: ({"mm": v, "dev": self.dev[k]} if self.dev.get(k, "auto") != "auto" else v)
                      for k, v in sorted(self.names.items())},
        }
        try:
            with open(WIDTHS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            messagebox.showerror("widths.json", f"Не удалось сохранить словарь ширин:\n{e}")
            return False

    def find_key(self, s):
        k = name_key(s)
        for name in self.names:
            if name_key(name) == k:
                return name
        return None

    def find_name(self, s):
        key = self.find_key(s)
        return None if key is None else self.names[key]

    def resolve_ex(self, value, default_mm):
        """-> (ширина мм, вид, тип аппаратной наклейки, число модулей)"""
        mm, kind = self.resolve(value, default_mm)
        dev = "auto"
        if kind == "name":
            dev = self.dev.get(self.find_key(str(value).strip()), "auto")
        if kind == "number":
            n = int(float(str(value).replace(",", ".")))
        else:
            n = max(1, int(round(mm / MODULE_MM)))
        return mm, kind, dev, n

    def resolve(self, value, default_mm):
        """-> (ширина мм, вид): number | name | mm | empty | unknown"""
        s = str(value).strip()
        if not s:
            return default_mm, "empty"
        mm = self.find_name(s)
        if mm is not None:
            return mm, "name"
        try:
            f = float(s.lower().replace("мм", "").replace("mm", "").replace(",", ".").strip())
        except ValueError:
            return default_mm, "unknown"
        if f.is_integer() and int(f) in self.numbers:
            return self.numbers[int(f)], "number"
        if f > 0:
            return f, "mm"
        return default_mm, "unknown"


# ---------------------------------------------------------------- настройки

DEFAULT_SETTINGS = {
    "page_format": "A4",
    "orientation": "альбомная",
    "margin_mm": 5.0,
    "h_gap_mm": 0.0,
    "v_gap_mm": 0.0,
    "label_height_mm": 25.0,
    "header_height_mm": 7.0,      # полоса тега сверху, под ней черта
    "desc_first_mm": 3.0,          # центр 1-й строки описания ниже черты
    "desc_pitch_mm": 5.0,          # шаг строк описания
    "font_tag_mm": 4.0,
    "font_desc_mm": 3.0,
    "font_min_mm": 1.5,
    "fit_mode": "уменьшить",       # уменьшить — меньше кегль, сжать — уже по ширине
    "text_pad_mm": 0.7,            # поле слева/справа от текста
    "line_width_mm": 0.25,
    "default_width_mm": 18.0,
    "sheet_title": "",             # подпись листа справа внизу, к ней добавится -N
    "font_path": "",
    "preview_px_per_mm": 3.0,
    # --- наклейки на аппараты (высота букв — как в AutoCAD, по заглавной)
    "dev_mode": "многомодульные",  # многомодульные — по ширине аппарата; одномодульные — на один полюс
    "dev_widths": "13.7; 31.7; 48.8; 67.2",   # ширина на 1, 2, 3, 4 модуля; дальше — с тем же шагом
    "dev_height_mm": 12.0,
    "dev_header_mm": 4.0,
    "dev_tag_cap_mm": 2.5,
    "dev_desc_cap_mm": 1.7,
    "dev_desc_first_mm": 1.6,      # центр 1-й строки описания ниже черты
    "dev_desc_pitch_mm": 2.4,
    "dev_min_cap_mm": 1.0,
    "dev_pad_mm": 0.4,
    "dev_tag_w_mm": 14.0,          # «только тег» (цокольные реле), замер по месту
    "dev_tag_h_mm": 6.0,
    "last_mapping": {},
}


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s.update(json.load(f))
        except Exception:
            pass
    old = {"landscape": "альбомная", "portrait": "книжная", "shrink": "уменьшить", "squeeze": "сжать"}
    for k in ("orientation", "fit_mode"):
        s[k] = old.get(s[k], s[k])
    # прежняя заглушка 10×5 мм для «только тег» -> замеренные 14×6
    if s.get("dev_tag_w_mm") == 10.0 and s.get("dev_tag_h_mm") == 5.0:
        s["dev_tag_w_mm"], s["dev_tag_h_mm"] = 14.0, 6.0
    return s


def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        messagebox.showerror("settings.json", f"Не удалось сохранить настройки:\n{e}")


# ---------------------------------------------------------------- шрифт

FONT_CANDIDATES = [
    os.path.join(APP_DIR, "ISOCPEUR.ttf"),
    os.path.join(APP_DIR, "isocpeur.ttf"),
    r"C:\Windows\Fonts\isocpeur.ttf",
    r"C:\Windows\Fonts\ISOCPEUR.TTF",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


class Font:
    """Один TTF-файл для PDF и для превью."""
    NAME = "LabelFont"

    def __init__(self, preferred=""):
        self.path = None
        self.cyrillic = False
        self.cap = 0.7
        self._pil_cache = {}
        for p in ([preferred] if preferred else []) + FONT_CANDIDATES:
            if p and os.path.exists(p):
                try:
                    tt = TTFont(self.NAME, p)
                except Exception:
                    continue
                pdfmetrics.registerFont(tt)
                self.path = p
                self.cyrillic = all(ord(ch) in tt.face.charToGlyph for ch in "АБВЖЩЯабвжщя")
                cap = getattr(tt.face, "capHeight", 0) or 0
                self.cap = cap / 1000.0 if 300 < cap < 1000 else 0.7
                break

    def width_mm(self, text, size_mm):
        return pdfmetrics.stringWidth(text, self.NAME, size_mm * MM_TO_PT) / MM_TO_PT

    def pil(self, size_px):
        size_px = max(1, int(round(size_px)))
        f = self._pil_cache.get(size_px)
        if f is None:
            f = ImageFont.truetype(self.path, size_px)
            self._pil_cache[size_px] = f
        return f


# ---------------------------------------------------------------- раскладка

def page_size_mm(st):
    base = A3 if st["page_format"] == "A3" else A4
    w, h = landscape(base) if st["orientation"] in ("landscape", "альбомная") else portrait(base)
    return w / MM_TO_PT, h / MM_TO_PT


def fit_text(font, text, size_mm, avail_mm, st, min_mm=None):
    """-> (кегль мм, горизонтальный масштаб, переполнение)"""
    min_mm = st["font_min_mm"] if min_mm is None else min_mm
    w = font.width_mm(text, size_mm)
    if w <= avail_mm or w == 0:
        return size_mm, 1.0, False
    if st["fit_mode"] in ("squeeze", "сжать"):
        k = avail_mm / w
        if k >= 0.5:
            return size_mm, k, False
        return size_mm, 0.5, True
    new = size_mm * avail_mm / w
    if new >= min_mm:
        return new, 1.0, False
    return min_mm, 1.0, True


def parse_dev_widths(s):
    """'13.7; 31.7; 48.8; 67.2' -> [13.7, 31.7, 48.8, 67.2]; None, если записано с ошибкой."""
    vals = []
    for part in str(s).replace(",", ".").replace(";", " ").split():
        try:
            v = float(part)
        except ValueError:
            return None
        if v <= 0:
            return None
        vals.append(v)
    return vals or None


def dev_widths_table(st):
    vals = parse_dev_widths(st.get("dev_widths", "")) or [13.7]
    return {i + 1: v for i, v in enumerate(vals)}


def dev_width(n, st):
    """Ширина аппаратной наклейки на n модулей: из таблицы, дальше — с её средним шагом."""
    tbl = dev_widths_table(st)
    if n in tbl:
        return tbl[n]
    lo, hi = min(tbl), max(tbl)
    step = (tbl[hi] - tbl[lo]) / (hi - lo) if hi > lo else MODULE_MM
    return tbl[hi] + (n - hi) * step


def parse_fill(value):
    """'жёлтый' | 'Жёлтый' | 'желтый' | '#FFF176' -> (r, g, b) 0..1, None (пусто) или False (не понято)."""
    s = str(value).strip()
    if not s:
        return None
    key = s.lower().replace("ё", "е")
    for name, hx in FILL_COLORS.items():
        if name.replace("ё", "е") == key:
            s = hx
            break
    h = s.lstrip("#")
    if len(h) == 6:
        try:
            return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        except ValueError:
            pass
    return False


def label_spec(lab, widths, font, st, kind):
    """
    Геометрия одной наклейки (мм) или None, если наклейка не нужна.
    -> dict(w, h, header (None — без черты), pad, min, lines=[(текст, кегль, центр от верха)]), замечание
    """
    tag = str(lab.get("tag", "")).strip()
    desc = [str(lab.get(k, "")).strip() for k in ("d1", "d2", "d3")]
    mm, wkind, dev, n = widths.resolve_ex(lab.get("width", ""), st["default_width_mm"])
    note = f"ширина «{lab.get('width')}» не найдена — взято {mm:g} мм" if wkind == "unknown" else None

    if kind == "panel":
        hh = st["header_height_mm"]
        lines = [(tag, st["font_tag_mm"], hh / 2)]
        lines += [(d, st["font_desc_mm"], hh + st["desc_first_mm"] + k * st["desc_pitch_mm"])
                  for k, d in enumerate(desc)]
        return dict(w=mm, h=st["label_height_mm"], header=hh, pad=st["text_pad_mm"],
                    min=st["font_min_mm"], lines=lines), note

    # --- на аппарат: высоты букв заданы по заглавной (как в AutoCAD) -> кегль = высота / доля заглавной
    em = lambda cap: cap / font.cap
    forced_w = None
    ov = str(lab.get("dwidth", "")).strip()
    if ov:                                  # ручное значение в строке важнее словаря и режима
        low = ov.lower()
        if low in DEV_NONE_WORDS:
            dev = "none"
        elif low in DEV_TAG_WORDS:
            dev = "tag"
        else:
            try:
                f = float(low.replace("мм", "").replace("mm", "").replace(",", ".").strip())
            except ValueError:
                f = 0
            if f <= 0:
                note = f"ширина на аппарат «{ov}» не распознана — взято по словарю"
            else:
                dev = "auto"
                forced_w = dev_width(int(f), st) if f.is_integer() and int(f) in dev_widths_table(st) else f
    if dev == "none":
        return None, None
    if dev == "tag":
        h = st["dev_tag_h_mm"]
        return dict(w=st["dev_tag_w_mm"], h=h, header=None, pad=st["dev_pad_mm"],
                    min=em(st["dev_min_cap_mm"]), lines=[(tag, em(st["dev_tag_cap_mm"]), h / 2)]), None
    single = forced_w is None and (dev == "single" or st.get("dev_mode") == "одномодульные")
    hh = st["dev_header_mm"]
    lines = [(tag, em(st["dev_tag_cap_mm"]), hh / 2)]
    lines += [(d, em(st["dev_desc_cap_mm"]), hh + st["dev_desc_first_mm"] + k * st["dev_desc_pitch_mm"])
              for k, d in enumerate(desc)]
    w = forced_w if forced_w is not None else dev_width(1 if single else n, st)
    return dict(w=w, h=st["dev_height_mm"], header=hh,
                pad=st["dev_pad_mm"], min=em(st["dev_min_cap_mm"]), lines=lines), note


def layout(labels, widths, font, st, kind="panel"):
    """
    labels: список dict с ключами FIELDS; kind: panel — на панель, device — на аппараты.
    -> (pages, issues)
    pages: список страниц; страница — список наклеек
       {idx, x, y, w, h, line_y, texts:[(text, cx, baseline, size_mm, hscale)]}  (мм, ось Y вниз)
    issues: {индекс строки: текст замечания}
    """
    pw, ph = page_size_mm(st)
    m = st["margin_mm"]
    bottom_reserve = 6.0 if st.get("sheet_title") else 0.0

    pages, issues = [[]], {}
    x, y, row_h = m, m, 0.0

    def new_row():
        nonlocal x, y, row_h
        x = m
        y += row_h + st["v_gap_mm"]
        row_h = 0.0

    def new_page():
        nonlocal x, y, row_h
        pages.append([])
        x, y, row_h = m, m, 0.0

    for idx, lab in enumerate(labels):
        tag = str(lab.get("tag", "")).strip()
        if tag == ROW_BREAK:
            if x > m:
                new_row()
            continue
        if tag == PAGE_BREAK:
            if pages[-1]:
                new_page()
            continue

        spec, note = label_spec(lab, widths, font, st, kind)
        if spec is None:
            continue
        if note:
            issues[idx] = note
        w, lh = spec["w"], spec["h"]
        if w > pw - 2 * m:
            issues[idx] = f"наклейка {w:g} мм шире страницы"

        if x > m and x + w > pw - m + 1e-6:
            new_row()
        if pages[-1] and y + lh > ph - m - bottom_reserve + 1e-6:
            new_page()

        texts = []
        avail = max(1.0, w - 2 * spec["pad"])
        cx = x + w / 2
        for text, size, cy in spec["lines"]:
            if not text:
                continue
            size, hs, over = fit_text(font, text, size, avail, st, spec["min"])
            if over:
                issues[idx] = f"«{text}» не влезает в {w:g} мм даже минимальным кеглем"
            texts.append((text, cx, y + cy + size * font.cap / 2, size, hs))

        line_y = None if spec["header"] is None else y + spec["header"]
        fill = parse_fill(lab.get("fill", ""))
        if fill is False:
            issues[idx] = f"цвет заливки «{lab.get('fill')}» не распознан"
            fill = None
        fill_h = (spec["header"] if spec["header"] is not None else lh) if fill else 0
        pages[-1].append(dict(idx=idx, x=x, y=y, w=w, h=lh, line_y=line_y, texts=texts,
                              fill=fill, fill_h=fill_h))
        x += w + st["h_gap_mm"]
        row_h = max(row_h, lh)

    if not pages[-1] and len(pages) > 1:
        pages.pop()
    return pages, issues


# ---------------------------------------------------------------- вывод

def render_pdf(path, pages, font, st, title=None):
    pw, ph = page_size_mm(st)
    c = rl_canvas.Canvas(path, pagesize=(pw * MM_TO_PT, ph * MM_TO_PT))
    c.setTitle(title or st.get("sheet_title") or "Наклейки")
    c.setAuthor("Label Maker")
    c.setCreator(f"Label Maker {VERSION} — github.com/allexx51-dev/label-maker")
    P = lambda v: v * MM_TO_PT          # мм -> pt
    Y = lambda v: (ph - v) * MM_TO_PT   # мм от верха -> pt от низа
    for n, page in enumerate(pages, 1):
        c.setLineWidth(P(st["line_width_mm"]))
        for it in page:
            if it.get("fill"):
                c.setFillColorRGB(*it["fill"])
                c.rect(P(it["x"]), Y(it["y"] + it["fill_h"]), P(it["w"]), P(it["fill_h"]), stroke=0, fill=1)
                c.setFillColorRGB(0, 0, 0)
            c.rect(P(it["x"]), Y(it["y"] + it["h"]), P(it["w"]), P(it["h"]))
            if it["line_y"] is not None:
                c.line(P(it["x"]), Y(it["line_y"]), P(it["x"] + it["w"]), Y(it["line_y"]))
            for text, cx, base, size, hs in it["texts"]:
                t = c.beginText()
                t.setFont(Font.NAME, P(size))
                t.setHorizScale(hs * 100)
                tw = font.width_mm(text, size) * hs
                t.setTextOrigin(P(cx - tw / 2), Y(base))
                t.textOut(text)
                c.drawText(t)
        if st.get("sheet_title"):
            c.setFont(Font.NAME, P(4))
            c.drawRightString(P(pw - st["margin_mm"]), P(st["margin_mm"]), f"{st['sheet_title']}-{n}")
        c.showPage()
    c.save()


def render_page_image(page, page_no, font, st, ppm, mark=None):
    """Растр страницы тем же шрифтом и той же геометрией, что и PDF."""
    pw, ph = page_size_mm(st)
    img = Image.new("RGB", (int(pw * ppm) + 1, int(ph * ppm) + 1), "white")
    d = ImageDraw.Draw(img)
    lw = max(1, int(round(st["line_width_mm"] * ppm)))
    m = st["margin_mm"] * ppm
    d.rectangle([m, m, pw * ppm - m, ph * ppm - m], outline=(215, 215, 215))
    for it in page:
        x0, y0 = it["x"] * ppm, it["y"] * ppm
        x1, y1 = (it["x"] + it["w"]) * ppm, (it["y"] + it["h"]) * ppm
        if mark is not None and it["idx"] == mark:
            d.rectangle([x0, y0, x1, y1], fill=(255, 245, 200))
        if it.get("fill"):
            d.rectangle([x0, y0, x1, y0 + it["fill_h"] * ppm], fill=tuple(int(round(v * 255)) for v in it["fill"]))
        d.rectangle([x0, y0, x1, y1], outline="black", width=lw)
        if it["line_y"] is not None:
            d.line([x0, it["line_y"] * ppm, x1, it["line_y"] * ppm], fill="black", width=lw)
        for text, cx, base, size, hs in it["texts"]:
            f = font.pil(size * ppm)
            if hs >= 0.999:
                d.text((cx * ppm, base * ppm), text, font=f, fill="black", anchor="ms")
            else:
                l, t, r, b = f.getbbox(text, anchor="ls")
                tmp = Image.new("L", (r - l + 2, b - t + 2), 0)
                ImageDraw.Draw(tmp).text((-l + 1, -t + 1), text, font=f, fill=255, anchor="ls")
                nw = max(1, int(round(tmp.width * hs)))
                tmp = tmp.resize((nw, tmp.height), Image.LANCZOS)
                px = int(round(cx * ppm - nw / 2))
                py = int(round(base * ppm + t - 1))
                img.paste((0, 0, 0), (px, py), tmp)
    if st.get("sheet_title"):
        f = font.pil(4 * ppm)
        d.text(((pw - st["margin_mm"]) * ppm, (ph - st["margin_mm"]) * ppm),
               f"{st['sheet_title']}-{page_no}", font=f, fill="black", anchor="rs")
    return img


# ---------------------------------------------------------------- чтение таблиц

HEADER_GUESS = {
    "tag": ["TAG1", "TAG", "ТЕГ", "ТЭГ", "ПОЗ", "ОБОЗНАЧ"],
    "d1": ["DESC1", "ОПИСАНИЕ1", "ОПИС1", "DESCRIPTION1"],
    "d2": ["DESC2", "ОПИСАНИЕ2", "ОПИС2", "DESCRIPTION2"],
    "d3": ["DESC3", "ОПИСАНИЕ3", "ОПИС3", "DESCRIPTION3"],
    "width": ["WIDTH", "ШИРИНА", "BLOCKNAME", "BLOCK", "БЛОК", "ИМЯБЛОКА", "МОДУЛ"],
    "dwidth": ["DEVWIDTH", "ШИРИНААППАРАТ", "НААППАРАТ", "АППАРАТ"],
    "fill": ["FILL", "ЗАЛИВКА", "ЦВЕТ", "COLOR"],
}


def norm_header(s):
    return "".join(ch for ch in str(s).upper() if ch.isalnum())


def cell_str(v):
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_table(path):
    """-> DataFrame без заголовка, всё строками."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".txt"):
        for enc in ("utf-8-sig", "cp1251"):
            try:
                df = pd.read_csv(path, header=None, sep=None, engine="python", encoding=enc, dtype=str)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("не удалось определить кодировку CSV")
    else:
        df = pd.read_excel(path, header=None, dtype=object)
    df = df.map(cell_str) if hasattr(df, "map") else df.applymap(cell_str)
    # убрать только пустые колонки СПРАВА: пустая колонка в середине
    # (например, незаполненный DESC3) должна сохранить позиции остальных
    filled = [c for c in range(df.shape[1]) if (df.iloc[:, c] != "").any()]
    df = df.iloc[:, :(filled[-1] + 1) if filled else 0]
    df = df.loc[(df != "").any(axis=1)].reset_index(drop=True)   # пустые строки
    df.columns = range(df.shape[1])
    return df


def guess_header_row(df):
    for r in range(min(15, len(df))):
        cells = [norm_header(v) for v in df.iloc[r]]
        hits = sum(1 for c in cells for keys in HEADER_GUESS.values() if any(k in c for k in keys) and c)
        if hits >= 2:
            return r
    return None


def guess_mapping(headers):
    mp = {}
    normed = [norm_header(h) for h in headers]
    for field in ("dwidth",) + tuple(f for f in FIELDS if f != "dwidth"):
        for key in HEADER_GUESS[field]:
            for i, h in enumerate(normed):
                if h and key in h and i not in mp.values():
                    mp[field] = i
                    break
            if field in mp:
                break
    return mp


# ---------------------------------------------------------------- GUI

class MappingDialog(tk.Toplevel):
    """Выбор: какая колонка таблицы что означает."""

    def __init__(self, master, df, settings):
        super().__init__(master)
        self.title("Колонки таблицы")
        self.transient(master)
        self.grab_set()
        self.df = df
        self.result = None
        hr = guess_header_row(df)

        tk.Label(self, text="Строка заголовка (0 — заголовка нет):").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.hr_var = tk.IntVar(value=(hr + 1) if hr is not None else 0)
        sp = tk.Spinbox(self, from_=0, to=min(30, len(df)), width=5, textvariable=self.hr_var,
                        command=self.on_header_change)
        sp.grid(row=0, column=1, sticky="w")
        sp.bind("<KeyRelease>", lambda e: self.on_header_change())

        self.combos = {}
        for i, (f, title) in enumerate(zip(FIELDS, FIELD_TITLES), start=1):
            tk.Label(self, text=title + ":").grid(row=i, column=0, sticky="w", padx=8, pady=2)
            cb = ttk.Combobox(self, state="readonly", width=40)
            cb.grid(row=i, column=1, padx=8, pady=2, sticky="we")
            self.combos[f] = cb

        self.sample = tk.Text(self, height=8, width=90, wrap="none", font=("Consolas", 9))
        self.sample.grid(row=7, column=0, columnspan=2, padx=8, pady=6)

        bf = tk.Frame(self)
        bf.grid(row=8, column=0, columnspan=2, pady=6)
        tk.Button(bf, text="Загрузить", width=12, command=self.ok).pack(side=tk.LEFT, padx=4)
        tk.Button(bf, text="Отмена", width=12, command=self.destroy).pack(side=tk.LEFT, padx=4)

        self.last = settings.get("last_mapping", {})
        self.on_header_change()
        self.wait_window()

    def options(self):
        hr = self.hr_var.get()
        opts = ["(нет)"]
        for c in self.df.columns:
            letter = chr(ord("A") + c) if c < 26 else str(c + 1)
            head = self.df.iat[hr - 1, c] if hr > 0 else ""
            opts.append(f"{letter}: {head}" if head else letter)
        return opts

    def on_header_change(self):
        try:
            hr = int(self.hr_var.get())
        except (tk.TclError, ValueError):
            return
        opts = self.options()
        if hr > 0:
            headers = list(self.df.iloc[hr - 1])
            mp = guess_mapping(headers)
            key = "|".join(norm_header(h) for h in headers)
            if key in self.last:
                mp = {k: v for k, v in self.last[key].items() if v < len(headers)}
        else:
            mp = {f: i for i, f in enumerate(FIELDS) if i < self.df.shape[1]}
        for f, cb in self.combos.items():
            cb["values"] = opts
            cb.current(mp[f] + 1 if f in mp else 0)
        self.sample.delete("1.0", tk.END)
        for r in range(hr, min(hr + 8, len(self.df))):
            self.sample.insert(tk.END, " | ".join(self.df.iloc[r]) + "\n")

    def ok(self):
        hr = int(self.hr_var.get())
        mp = {f: cb.current() - 1 for f, cb in self.combos.items() if cb.current() > 0}
        if hr > 0:
            key = "|".join(norm_header(h) for h in self.df.iloc[hr - 1])
            self.last[key] = mp
        rows = []
        for r in range(hr, len(self.df)):
            lab = {f: (self.df.iat[r, mp[f]] if f in mp else "") for f in FIELDS}
            if any(lab.values()):
                rows.append(lab)
        self.result = rows
        self.destroy()


SETTINGS_FORM = [
    ("Лист", [
        ("page_format", "Формат листа", ("A4", "A3")),
        ("orientation", "Ориентация", ("альбомная", "книжная")),
        ("margin_mm", "Поля листа, мм", None),
        ("h_gap_mm", "Зазор между наклейками по горизонтали, мм", None),
        ("v_gap_mm", "Зазор между рядами, мм", None),
        ("fit_mode", "Если не влезает", ("уменьшить", "сжать")),
        ("line_width_mm", "Толщина линий, мм", None),
        ("default_width_mm", "Ширина по умолчанию, мм", None),
        ("sheet_title", "Подпись листа (пусто — нет)", "str"),
    ]),
    ("На панель", [
        ("label_height_mm", "Высота наклейки, мм", None),
        ("header_height_mm", "Высота полосы тега (до черты), мм", None),
        ("desc_first_mm", "Центр 1-й строки описания ниже черты, мм", None),
        ("desc_pitch_mm", "Шаг строк описания, мм", None),
        ("font_tag_mm", "Кегль тега, мм", None),
        ("font_desc_mm", "Кегль описания, мм", None),
        ("font_min_mm", "Минимальный кегль, мм", None),
        ("text_pad_mm", "Поле текста слева/справа, мм", None),
    ]),
    ("На аппарат", [
        ("dev_mode", "Режим", ("многомодульные", "одномодульные")),
        ("dev_widths", "Ширина на 1, 2, 3… модуля, мм", "str"),
        ("dev_height_mm", "Высота наклейки, мм", None),
        ("dev_header_mm", "Высота полосы тега (до черты), мм", None),
        ("dev_tag_cap_mm", "Высота букв тега, мм (как в AutoCAD)", None),
        ("dev_desc_cap_mm", "Высота букв описания, мм", None),
        ("dev_desc_first_mm", "Центр 1-й строки описания ниже черты, мм", None),
        ("dev_desc_pitch_mm", "Шаг строк описания, мм", None),
        ("dev_min_cap_mm", "Минимальная высота букв, мм", None),
        ("dev_pad_mm", "Поле текста слева/справа, мм", None),
        ("dev_tag_w_mm", "«Только тег»: ширина, мм", None),
        ("dev_tag_h_mm", "«Только тег»: высота, мм", None),
    ]),
]


class LabelApp:
    def __init__(self, root):
        self.root = root
        root.title(f"Label Maker {VERSION} — диспетчерские наклейки")
        root.geometry("1100x640")
        self.st = load_settings()
        self.widths = WidthMap()
        self.font = Font(self.st.get("font_path", ""))
        self.labels = []
        self.issues = {}
        self.dev_issues = {}
        self.edit_entry = None
        self.preview_win = None
        self.preview_kind = "panel"

        self.build_ui()
        self.check_font()
        self.refresh()

    # ---------- интерфейс
    def build_ui(self):
        rows = [
            [   # данные
                [("Открыть Excel/CSV", self.load_table), ("Сохранить Excel", self.save_table)],
                [("Добавить", self.add_row), ("Удалить", self.delete_rows), ("▲", lambda: self.move(-1)),
                 ("▼", lambda: self.move(1)), ("Новый ряд", lambda: self.insert_marker(ROW_BREAK)),
                 ("Новая страница", lambda: self.insert_marker(PAGE_BREAK))],
            ],
            [   # оформление и вывод
                [("Заливка…", self.fill_menu), ("Словарь ширин", self.edit_widths),
                 ("Настройки", self.edit_settings), ("Шрифт…", self.choose_font)],
                [("Предпросмотр", self.preview), ("PDF на панель", lambda: self.export_pdf("panel")),
                 ("PDF на аппараты", lambda: self.export_pdf("device"))],
            ],
        ]
        for groups in rows:
            bar = tk.Frame(self.root)
            bar.pack(fill=tk.X, padx=4, pady=(4, 0))
            for g in groups:
                fr = tk.Frame(bar)
                fr.pack(side=tk.LEFT, padx=6)
                for text, cmd in g:
                    tk.Button(fr, text=text, command=cmd).pack(side=tk.LEFT, padx=1)

        frame = tk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True, padx=4)
        cols = ("n", "tag", "d1", "d2", "d3", "width", "mm", "dwidth", "fill")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        heads = ("№",) + FIELD_TITLES[:5] + ("панель, мм", "аппарат, мм", "заливка")
        widths = (40, 95, 170, 165, 150, 185, 85, 95, 85)
        narrow = ("n", "mm", "dwidth", "fill")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center" if c in narrow else "w", stretch=c not in narrow)
        vs = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Delete>", lambda e: self.delete_rows())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_issue())

        colors = {"number": "#cceeff", "name": "#ccffcc", "mm": "#e8e8e8", "empty": "#ffffcc",
                  "unknown": "#ff9191", "marker": "#d0d0f0", "issue": "#ffc080"}
        for t, c in colors.items():
            self.tree.tag_configure(t, background=c)

        self.status = tk.Label(self.root, anchor="w", relief=tk.SUNKEN)
        self.status.pack(fill=tk.X, side=tk.BOTTOM)
        legend = tk.Frame(self.root)
        legend.pack(fill=tk.X, side=tk.BOTTOM, padx=4)
        for t, txt in (("number", "число модулей"), ("name", "имя блока"), ("mm", "миллиметры"),
                       ("empty", "пусто → по умолчанию"), ("unknown", "нет в словаре"),
                       ("issue", "текст не влезает"), ("marker", "разрыв ряда/страницы")):
            tk.Label(legend, text=txt, bg=colors[t], padx=6).pack(side=tk.LEFT, padx=2, pady=2)
        tk.Label(legend, text="* — вручную", padx=6).pack(side=tk.LEFT, padx=2)

    def check_font(self):
        if not self.font.path:
            messagebox.showerror("Шрифт", "Не найден ни один TTF-шрифт. Положите ISOCPEUR.ttf рядом с программой.")
        elif not self.font.cyrillic:
            messagebox.showwarning("Шрифт", f"В шрифте {os.path.basename(self.font.path)} нет кириллицы —\n"
                                            "русские надписи не напечатаются. Выберите другой шрифт.")

    # ---------- таблица
    def relayout(self, kind="panel"):
        if not self.font.path:
            return [], {}
        if kind == "device":
            pages, self.dev_issues = layout(self.labels, self.widths, self.font, self.st, "device")
            return pages, self.dev_issues
        pages, self.issues = layout(self.labels, self.widths, self.font, self.st)
        return pages, self.issues

    def device_cell(self, lab):
        """Текст колонки «аппарат, мм»: ширина, «тег» или «—»."""
        spec, _ = label_spec(lab, self.widths, self.font, self.st, "device")
        mark = " *" if str(lab.get("dwidth", "")).strip() else ""
        if spec is None:
            return "—" + mark
        return ("тег" if spec["header"] is None else f"{spec['w']:g}") + mark

    def refresh(self, keep_sel=None):
        self.relayout("device")
        pages, issues = self.relayout()
        self.tree.delete(*self.tree.get_children())
        for i, lab in enumerate(self.labels):
            tag_txt = str(lab["tag"]).strip()
            if tag_txt in (ROW_BREAK, PAGE_BREAK):
                tag, mm, dmm = "marker", "", ""
            else:
                w, kind = self.widths.resolve(lab["width"], self.st["default_width_mm"])
                tag, mm = kind, f"{w:g}"
                dmm = self.device_cell(lab)
                if i in issues or i in self.dev_issues:
                    tag = "issue" if kind != "unknown" else "unknown"
            self.tree.insert("", tk.END, iid=str(i),
                             values=(i + 1,) + tuple(lab[f] for f in FIELDS[:5]) + (mm, dmm, lab.get("fill", "")),
                             tags=(tag,))
        if keep_sel:
            sel = [str(i) for i in keep_sel if 0 <= i < len(self.labels)]
            self.tree.selection_set(sel)
            if sel:
                self.tree.see(sel[0])
        n = sum(len(p) for p in pages)
        self.status.config(text=f"Строк: {len(self.labels)}   наклеек: {n}   листов: {len(pages) if n else 0}   "
                                f"замечаний: панель {len(issues)}, аппараты {len(self.dev_issues)}   "
                                f"шрифт: {os.path.basename(self.font.path or '—')}")
        self.update_preview()

    def show_issue(self):
        sel = self.selected()
        if sel and (sel[0] in self.issues or sel[0] in self.dev_issues):
            txt = self.issues.get(sel[0]) or "на аппарат: " + self.dev_issues[sel[0]]
            self.status.config(text=f"Строка {sel[0] + 1}: {txt}")
        self.update_preview()

    def selected(self):
        return sorted(int(i) for i in self.tree.selection())

    EDIT_ORDER = ("tag", "d1", "d2", "d3", "width", "dwidth", "fill")

    def on_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        cols = self.tree["columns"]
        col = cols[int(self.tree.identify_column(event.x)[1:]) - 1]
        item = self.tree.identify_row(event.y)
        if not item or col not in self.EDIT_ORDER:
            return
        # после двойного щелчка таблица забирает фокус обратно — открываем поле, когда щелчок отработает
        self.root.after(30, self.start_edit, int(item), col)

    def start_edit(self, row, field):
        self.finish_edit(save=False)
        item = str(row)
        self.tree.see(item)
        self.tree.update_idletasks()
        bbox = self.tree.bbox(item, field)
        if not bbox:
            return
        if field == "width":
            values = [str(k) for k in sorted(self.widths.numbers)] + sorted(self.widths.names)
        elif field == "dwidth":
            values = ["", *[str(k) for k in sorted(dev_widths_table(self.st))], "тег", "нет"]
        elif field == "fill":
            values = ["", *FILL_COLORS]
        else:
            values = None
        if values is not None:
            e = ttk.Combobox(self.tree, values=values)
            e.bind("<<ComboboxSelected>>", lambda ev: self.finish_edit())
        else:
            e = tk.Entry(self.tree)
        e.place(x=bbox[0], y=bbox[1], width=max(bbox[2], 160 if values is not None else 0), height=bbox[3])
        e.insert(0, self.labels[row].get(field, ""))
        e.select_range(0, tk.END)
        e.focus_force()
        self.edit_entry = (e, row, field)
        e.bind("<Return>", lambda ev: self.finish_edit(move=1))
        e.bind("<Tab>", lambda ev: (self.finish_edit(next_col=True), "break")[1])
        e.bind("<Escape>", lambda ev: self.finish_edit(save=False))
        for seq in ("<Control-a>", "<Control-A>", "<Control-Cyrillic_ef>", "<Control-Cyrillic_EF>"):
            try:
                e.bind(seq, lambda ev: (e.select_range(0, tk.END), e.icursor(tk.END), "break")[2])
            except tk.TclError:      # нет такой клавиши в этой сборке Tk
                pass
        e.bind("<FocusOut>", lambda ev: self.root.after(80, self._focus_out_check, e))

    def _focus_out_check(self, e):
        if not (self.edit_entry and self.edit_entry[0] is e):
            return
        # Путь виджета с фокусом берём у самого Tk: окно раскрытого списка Combobox
        # tkinter не знает, и focus_get() на нём падает — из-за этого поле закрывалось.
        try:
            path = str(self.root.tk.call("focus"))
        except tk.TclError:
            path = ""
        if path.startswith(str(e)):          # фокус в самом поле или в его раскрытом списке
            return
        self.finish_edit()

    def finish_edit(self, save=True, move=0, next_col=False):
        if not self.edit_entry:
            return
        e, row, field = self.edit_entry
        self.edit_entry = None
        val = e.get().strip()
        e.destroy()
        if save and val != str(self.labels[row].get(field, "")).strip():
            self.labels[row][field] = val
            self.refresh(keep_sel=[row])
        self.tree.focus_set()
        order = self.EDIT_ORDER
        if next_col and order.index(field) + 1 < len(order):
            self.start_edit(row, order[order.index(field) + 1])
        elif move and row + move < len(self.labels):
            self.tree.selection_set(str(row + move))

    def fill_menu(self):
        """Меню цветов у курсора: цвет ставится каждой выделенной строке."""
        sel = [i for i in self.selected() if str(self.labels[i]["tag"]).strip() not in (ROW_BREAK, PAGE_BREAK)]
        if not sel:
            messagebox.showinfo("Заливка", "Выделите строки (Ctrl/Shift + щелчок), затем выберите цвет.")
            return
        m = tk.Menu(self.root, tearoff=0)
        for name, hx in FILL_COLORS.items():
            m.add_command(label=f"  {name}", background=hx, activebackground=hx,
                          command=lambda n=name: self.set_fill(sel, n))
        m.add_separator()
        m.add_command(label="  без заливки", command=lambda: self.set_fill(sel, ""))
        m.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def set_fill(self, rows, value):
        for i in rows:
            self.labels[i]["fill"] = value
        self.refresh(keep_sel=rows)

    def add_row(self):
        sel = self.selected()
        pos = sel[-1] + 1 if sel else len(self.labels)
        self.labels.insert(pos, {f: "" for f in FIELDS})
        self.refresh(keep_sel=[pos])
        self.start_edit(pos, 0)

    def insert_marker(self, marker):
        sel = self.selected()
        pos = sel[0] if sel else len(self.labels)
        lab = {f: "" for f in FIELDS}
        lab["tag"] = marker
        self.labels.insert(pos, lab)
        self.refresh(keep_sel=[pos])

    def delete_rows(self):
        sel = self.selected()
        for i in reversed(sel):
            del self.labels[i]
        self.refresh(keep_sel=[min(sel[0], len(self.labels) - 1)] if sel and self.labels else None)

    def move(self, d):
        sel = self.selected()
        if not sel or sel[0] + d < 0 or sel[-1] + d >= len(self.labels):
            return
        order = sel if d < 0 else list(reversed(sel))
        for i in order:
            self.labels[i], self.labels[i + d] = self.labels[i + d], self.labels[i]
        self.refresh(keep_sel=[i + d for i in sel])

    # ---------- файлы
    def load_table(self):
        path = filedialog.askopenfilename(filetypes=[("Таблицы", "*.xlsx *.xls *.csv *.txt"), ("Все", "*.*")])
        if not path:
            return
        try:
            df = read_table(path)
        except ImportError as e:
            messagebox.showerror("Ошибка", f"{e}\nДля .xls нужен пакет xlrd: pip install xlrd")
            return
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл:\n{e}")
            return
        if df.empty:
            messagebox.showwarning("Пусто", "В файле нет данных.")
            return
        dlg = MappingDialog(self.root, df, self.st)
        if dlg.result is None:
            return
        self.st["last_mapping"] = dlg.last
        save_settings(self.st)
        if self.labels and messagebox.askyesno("Загрузка", "Добавить к текущему списку?\n«Нет» — заменить."):
            self.labels.extend(dlg.result)
        else:
            self.labels = dlg.result
        self.root.title(f"Label Maker {VERSION} — {os.path.basename(path)}")
        self.refresh()

    def save_table(self):
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        df = pd.DataFrame(self.labels, columns=list(FIELDS))
        df.columns = ["TAG1", "DESC1", "DESC2", "DESC3", "WIDTH", "DEV_WIDTH", "FILL"]
        try:
            df.to_excel(path, index=False)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}")

    def export_pdf(self, kind="panel"):
        pages, issues = self.relayout(kind)
        what = "на аппараты" if kind == "device" else "на панель"
        if not any(pages):
            messagebox.showinfo("PDF", "Нет наклеек.")
            return
        if issues and not messagebox.askyesno(
                "Замечания", f"Есть замечания ({len(issues)}), например:\n"
                             f"строка {min(issues) + 1}: {issues[min(issues)]}\n\nВсё равно сохранить?"):
            return
        path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
                                            title=f"PDF {what}")
        if not path:
            return
        try:
            render_pdf(path, pages, self.font, self.st, f"Наклейки {what}")
        except PermissionError:
            messagebox.showerror("Ошибка", "Файл занят — закройте его в просмотрщике PDF.")
            return
        except Exception as e:
            messagebox.showerror("Ошибка", f"PDF не сохранён:\n{e}")
            return
        messagebox.showinfo("Готово", f"PDF {what} сохранён: {path}\nЛистов: {len(pages)}. Печать — масштаб 100%.")

    def choose_font(self):
        path = filedialog.askopenfilename(filetypes=[("TrueType", "*.ttf *.otf")])
        if not path:
            return
        f = Font(path)
        if f.path != path:
            messagebox.showerror("Шрифт", "Этот файл не читается как TTF.")
            return
        self.font = f
        self.st["font_path"] = path
        save_settings(self.st)
        self.check_font()
        self.refresh()

    # ---------- словарь ширин
    def edit_widths(self):
        top = tk.Toplevel(self.root)
        top.title("Словарь ширин")
        top.geometry("620x480")
        tv = ttk.Treeview(top, columns=("k", "v", "d"), show="headings")
        tv.heading("k", text="Ключ (число модулей или тип аппарата)")
        tv.heading("v", text="Ширина, мм")
        tv.heading("d", text="На аппарат")
        tv.column("v", width=90, anchor="center", stretch=False)
        tv.column("d", width=100, anchor="center", stretch=False)
        tv.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        def fill():
            tv.delete(*tv.get_children())
            for k, v in sorted(self.widths.numbers.items()):
                tv.insert("", tk.END, iid=f"n:{k}", values=(k, f"{v:g}", DEV_KINDS["auto"]))
            for k, v in sorted(self.widths.names.items()):
                tv.insert("", tk.END, iid=f"s:{k}",
                          values=(k, f"{v:g}", DEV_KINDS[self.widths.dev.get(k, "auto")]))

        def commit():
            self.widths.save()
            fill()
            self.refresh(keep_sel=self.selected())

        def entry_dialog(title, key="", mm="", dev="auto", key_locked=False):
            """-> (ключ, мм, тип) или None"""
            d = tk.Toplevel(top)
            d.title(title)
            d.transient(top)
            d.grab_set()
            res = {}
            kv, mv, dv = tk.StringVar(value=key), tk.StringVar(value=mm), tk.StringVar(value=DEV_KINDS[dev])
            tk.Label(d, text="Число модулей или тип аппарата:").grid(row=0, column=0, sticky="w", padx=8, pady=3)
            ke = tk.Entry(d, textvariable=kv, width=32, state="readonly" if key_locked else "normal")
            ke.grid(row=0, column=1, padx=8, pady=3)
            tk.Label(d, text="Ширина на панели, мм:").grid(row=1, column=0, sticky="w", padx=8, pady=3)
            me = tk.Entry(d, textvariable=mv, width=12)
            me.grid(row=1, column=1, sticky="w", padx=8, pady=3)
            tk.Label(d, text="Наклейка на аппарат:").grid(row=2, column=0, sticky="w", padx=8, pady=3)
            dc = ttk.Combobox(d, textvariable=dv, values=list(DEV_KINDS.values()), state="readonly", width=14)
            dc.grid(row=2, column=1, sticky="w", padx=8, pady=3)
            tk.Label(d, text="авто — по режиму в настройках; 1 модуль — всегда на один полюс;\n"
                             "только тег — маленькая, для цокольных реле; нет — места на аппарате нет.\n"
                             "Для чисел модулей тип всегда «авто».",
                     fg="gray", justify="left").grid(row=3, column=0, columnspan=2, sticky="w", padx=8)

            def ok():
                k = kv.get().strip()
                try:
                    v = float(mv.get().replace(",", "."))
                except ValueError:
                    messagebox.showerror("Ошибка", "Ширина — число", parent=d)
                    return
                if not k or v <= 0:
                    messagebox.showerror("Ошибка", "Нужны ключ и ширина больше нуля", parent=d)
                    return
                res["r"] = (k, v, DEV_KINDS_BACK[dv.get()])
                d.destroy()

            bf2 = tk.Frame(d)
            bf2.grid(row=4, column=0, columnspan=2, pady=6)
            tk.Button(bf2, text="OK", width=10, command=ok).pack(side=tk.LEFT, padx=4)
            tk.Button(bf2, text="Отмена", width=10, command=d.destroy).pack(side=tk.LEFT, padx=4)
            (me if key_locked else ke).focus_set()
            d.bind("<Return>", lambda e: ok())
            d.wait_window()
            return res.get("r")

        def store(key, mm, dev):
            if key.isdigit():
                self.widths.numbers[int(key)] = mm
            else:
                self.widths.names[key] = mm
                if dev == "auto":
                    self.widths.dev.pop(key, None)
                else:
                    self.widths.dev[key] = dev

        def add():
            r = entry_dialog("Новая запись")
            if r:
                store(*r)
                commit()

        def edit(_=None):
            sel = tv.selection()
            if not sel:
                return
            kind, key = sel[0].split(":", 1)
            if kind == "n":
                r = entry_dialog("Изменить", key, f"{self.widths.numbers[int(key)]:g}", key_locked=True)
            else:
                r = entry_dialog("Изменить", key, f"{self.widths.names[key]:g}",
                                 self.widths.dev.get(key, "auto"), key_locked=True)
            if r:
                store(*r)
                commit()

        def delete():
            sel = tv.selection()
            if not sel or not messagebox.askyesno("Удалить", f"Удалить записей: {len(sel)}?", parent=top):
                return
            for iid in sel:
                kind, key = iid.split(":", 1)
                if kind == "n":
                    self.widths.numbers.pop(int(key), None)
                else:
                    self.widths.names.pop(key, None)
                    self.widths.dev.pop(key, None)
            commit()

        tv.bind("<Double-1>", edit)
        bf = tk.Frame(top)
        bf.pack(fill=tk.X, pady=4)
        for t_, c in (("Добавить", add), ("Изменить", edit), ("Удалить", delete)):
            tk.Button(bf, text=t_, command=c, width=12).pack(side=tk.LEFT, padx=4)
        tk.Label(bf, text=os.path.basename(WIDTHS_FILE), fg="gray").pack(side=tk.RIGHT, padx=4)
        fill()

    # ---------- настройки
    def edit_settings(self):
        top = tk.Toplevel(self.root)
        top.title("Настройки")
        top.transient(self.root)
        nb = ttk.Notebook(top)
        nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        vars_ = {}
        for tab_title, items in SETTINGS_FORM:
            tab = tk.Frame(nb)
            nb.add(tab, text=tab_title)
            for r, (key, title, kind) in enumerate(items):
                tk.Label(tab, text=title + ":").grid(row=r, column=0, sticky="w", padx=8, pady=2)
                v = tk.StringVar(value=str(self.st[key]))
                if isinstance(kind, tuple):
                    w = ttk.Combobox(tab, textvariable=v, values=kind, state="readonly", width=16)
                else:
                    w = tk.Entry(tab, textvariable=v, width=16 if kind is None else 30)
                w.grid(row=r, column=1, sticky="w", padx=8, pady=2)
                vars_[key] = (v, kind)

        def apply(close):
            new = dict(self.st)
            for key, (v, kind) in vars_.items():
                if kind is None:
                    try:
                        new[key] = float(v.get().replace(",", "."))
                    except ValueError:
                        messagebox.showerror("Ошибка", f"Не число: {key}", parent=top)
                        return
                else:
                    new[key] = v.get()
            if new["header_height_mm"] >= new["label_height_mm"] or new["dev_header_mm"] >= new["dev_height_mm"]:
                messagebox.showerror("Ошибка", "Полоса тега выше самой наклейки.", parent=top)
                return
            if parse_dev_widths(new["dev_widths"]) is None:
                messagebox.showerror("Ошибка", "Ширины на аппарат: числа через «;», например 13.7; 31.7",
                                     parent=top)
                return
            self.st = new
            save_settings(self.st)
            self.refresh(keep_sel=self.selected())
            if close:
                top.destroy()

        def reset():
            for key, (v, kind) in vars_.items():
                v.set(str(DEFAULT_SETTINGS[key]))

        bf = tk.Frame(top)
        bf.pack(pady=8)
        tk.Button(bf, text="Применить", command=lambda: apply(False), width=11).pack(side=tk.LEFT, padx=3)
        tk.Button(bf, text="OK", command=lambda: apply(True), width=11).pack(side=tk.LEFT, padx=3)
        tk.Button(bf, text="По умолчанию", command=reset, width=11).pack(side=tk.LEFT, padx=3)

    # ---------- предпросмотр
    def preview(self):
        if self.preview_win and self.preview_win.winfo_exists():
            self.preview_win.lift()
            return
        top = tk.Toplevel(self.root)
        top.title("Предпросмотр (1:1 с PDF)")
        top.geometry("1000x760")
        self.preview_win = top
        bar = tk.Frame(top)
        bar.pack(fill=tk.X)

        def zoom(k):
            self.st["preview_px_per_mm"] = min(12.0, max(1.0, self.st["preview_px_per_mm"] * k))
            self.update_preview()

        tk.Button(bar, text="−", width=3, command=lambda: zoom(1 / 1.25)).pack(side=tk.LEFT, padx=2, pady=2)
        tk.Button(bar, text="+", width=3, command=lambda: zoom(1.25)).pack(side=tk.LEFT, padx=2)
        self.zoom_label = tk.Label(bar)
        self.zoom_label.pack(side=tk.LEFT, padx=6)
        tk.Label(bar, text="выделенная строка подсвечена", fg="gray").pack(side=tk.RIGHT, padx=6)
        kind_var = tk.StringVar(value="на панель" if self.preview_kind == "panel" else "на аппараты")
        cb = ttk.Combobox(bar, textvariable=kind_var, values=("на панель", "на аппараты"),
                          state="readonly", width=12)
        cb.pack(side=tk.LEFT, padx=8)

        def set_kind(_=None):
            self.preview_kind = "device" if kind_var.get() == "на аппараты" else "panel"
            self.update_preview()
        cb.bind("<<ComboboxSelected>>", set_kind)

        fr = tk.Frame(top)
        fr.pack(fill=tk.BOTH, expand=True)
        cv = tk.Canvas(fr, bg="#808080")
        vs = ttk.Scrollbar(fr, orient="vertical", command=cv.yview)
        hs = ttk.Scrollbar(top, orient="horizontal", command=cv.xview)
        cv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        cv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hs.pack(fill=tk.X)
        cv.bind("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120), "units"))
        cv.bind("<Button-4>", lambda e: cv.yview_scroll(-3, "units"))
        cv.bind("<Button-5>", lambda e: cv.yview_scroll(3, "units"))
        self.preview_canvas = cv
        self.update_preview()

    def update_preview(self):
        if not (self.preview_win and self.preview_win.winfo_exists()) or not self.font.path:
            return
        cv = self.preview_canvas
        ppm = self.st["preview_px_per_mm"]
        self.zoom_label.config(text=f"{ppm * 25.4 / 96 * 100:.0f}% (при 96 dpi)")
        pages, _ = layout(self.labels, self.widths, self.font, self.st, self.preview_kind)
        sel = self.selected()
        mark = sel[0] if sel else None
        cv.delete("all")
        self._preview_imgs = []
        y = 10
        for n, page in enumerate(pages, 1):
            img = ImageTk.PhotoImage(render_page_image(page, n, self.font, self.st, ppm, mark))
            self._preview_imgs.append(img)
            cv.create_image(10, y, image=img, anchor="nw")
            y += img.height() + 14
        cv.configure(scrollregion=(0, 0, (self._preview_imgs[0].width() + 20) if self._preview_imgs else 0, y))


if __name__ == "__main__":
    root = tk.Tk()
    LabelApp(root)
    root.mainloop()
