# Инструменты генерации: .pptx и графики

Читай при генерации файлов. Нет пакета → `pip install`; при невозможности — отдай раскадровку/таблицу + спецификацию.

## Презентации — python-pptx

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

prs = Presentation()
prs.slide_width = Inches(13.333)   # 16:9
prs.slide_height = Inches(7.5)

ACCENT = RGBColor(0x2E, 0x5C, 0xFF)   # из фирменного стиля ТЗ
DARK   = RGBColor(0x1A, 0x1A, 0x1A)

# Титул
slide = prs.slides.add_slide(prs.slide_layouts[6])  # пустой макет — полный контроль
tb = slide.shapes.add_textbox(Inches(0.8), Inches(2.5), Inches(11.7), Inches(2))
p = tb.text_frame.paragraphs[0]
p.text = "Заголовок презентации"
p.font.size = Pt(44); p.font.bold = True; p.font.color.rgb = DARK

# Контентный слайд: заголовок-вывод + буллеты
slide = prs.slides.add_slide(prs.slide_layouts[6])
h = slide.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(1))
hp = h.text_frame.paragraphs[0]
hp.text = "Выручка выросла на 40% за квартал"   # вывод, не тема
hp.font.size = Pt(32); hp.font.bold = True; hp.font.color.rgb = ACCENT

body = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.7), Inches(5))
tf = body.text_frame; tf.word_wrap = True
for i, line in enumerate(["Тезис один", "Тезис два", "Тезис три"]):
    par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    par.text = "• " + line
    par.font.size = Pt(22)

# Вставка графика-картинки от data-visualizer
slide.shapes.add_picture("charts/revenue.png", Inches(7), Inches(2), width=Inches(5.5))

prs.save("10 — Claude/Рабочее пространство/office/presentation.pptx")
```

Принципы в коде: пустой макет (`slide_layouts[6]`) для полного контроля сетки; единые координаты заголовков на всех слайдах; кегль ≥ 18 pt; ≤ 2 шрифтов; акцентный цвет только на главное.

## Графики — matplotlib

```python
import matplotlib.pyplot as plt
import matplotlib as mpl

mpl.rcParams.update({
    "font.family": "DejaVu Sans",      # есть кириллица
    "font.size": 12,
    "axes.spines.top": False,           # убрать лишние рамки
    "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.3,
    "figure.dpi": 150,
})

fig, ax = plt.subplots(figsize=(8, 5))
ax.bar(categories, values, color="#2E5CFF")
ax.set_ylim(bottom=0)                    # ось Y от нуля — честность
ax.set_title("Заголовок-вывод", fontsize=14, fontweight="bold")
ax.set_ylabel("Единицы измерения")
fig.text(0.99, 0.01, "Источник: ... · 2026-06-11", ha="right", fontsize=8, color="gray")
fig.tight_layout()
fig.savefig("10 — Claude/Рабочее пространство/office/charts/chart.png", bbox_inches="tight")
fig.savefig("10 — Claude/Рабочее пространство/office/charts/chart.svg", bbox_inches="tight")  # масштабируемо
```

Кириллица: используй `DejaVu Sans` (встроен в matplotlib) или установленный Times New Roman. Проверь, что подписи не обрезаны (`bbox_inches="tight"`).

## Графики — plotly (интерактив для веба/презентаций)

```python
import plotly.express as px
fig = px.line(df, x="date", y="value", title="Динамика показателя")
fig.update_layout(template="simple_white", font=dict(size=14))
fig.write_html("10 — Claude/Рабочее пространство/office/charts/chart.html")
fig.write_image("10 — Claude/Рабочее пространство/office/charts/chart.png")   # требует kaleido
```

## Вывод документов (.docx / .pdf)

- **.docx** — `python-docx` (поля, шрифт, стили по ГОСТ из `gost-standards`).
- **Markdown → .docx/.pdf** — `pandoc` с reference-docx для оформления.
- Кириллица в PDF: используй шрифт с поддержкой (Times New Roman / DejaVu), иначе пустые квадраты.

## Фолбэк
Пакет недоступен и pip не сработал → не блокируйся: отдай (1) детальную раскадровку слайдов (текст + layout каждого) или (2) данные таблицей + спецификацию графика, с пометкой «требуется ручная сборка / доустановка пакета».
