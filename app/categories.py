CATEGORIES: list[str] = [
    "Продукты",
    "Кафе/рестораны",
    "Транспорт",
    "Авто",
    "Жильё/коммуналка",
    "Дом",
    "Здоровье",
    "Красота",
    "Одежда",
    "Техника",
    "Алкоголь/табак",
    "Развлечения",
    "Связь/интернет",
    "Кошечка",
    "Прочее",
]

DEFAULT_CATEGORY = "Прочее"

# Emoji and CSS colour token (``--c-<name>`` in base.html) for each category.
CATEGORY_STYLE: dict[str, tuple[str, str]] = {
    "Продукты": ("🛒", "food"),
    "Кафе/рестораны": ("🍽️", "cafe"),
    "Транспорт": ("🚕", "transport"),
    "Авто": ("🚗", "auto"),
    "Жильё/коммуналка": ("🏠", "home"),
    "Дом": ("🧽", "house"),
    "Здоровье": ("💊", "health"),
    "Красота": ("💅", "beauty"),
    "Одежда": ("👕", "clothes"),
    "Техника": ("🎮", "tech"),
    "Алкоголь/табак": ("🍷", "alco"),
    "Развлечения": ("🎬", "fun"),
    "Связь/интернет": ("📶", "net"),
    "Кошечка": ("🐈‍⬛", "cat"),
    "Прочее": ("📦", "other"),
}

# Shared disambiguation notes, used both for receipt extraction and for
# categorising a single manually entered expense.
CATEGORY_GUIDANCE = (
    "Category disambiguation notes: "
    "'Алкоголь/табак' is any alcoholic drink (beer, wine, spirits, etc.) and any tobacco "
    "or nicotine product (cigarettes, heated tobacco sticks like TEREA/HEETS for IQOS, "
    "vapes), even if bought in a grocery store — it never goes into 'Продукты'. "
    "'Дом' is household supplies and small homeware: cleaning products, sponges, "
    "gloves, paper towels, air fresheners, dishes, glasses, trays, food containers — "
    "as opposed to 'Жильё/коммуналка', which is rent, bills and home services. "
    "'Техника' covers electronics and gaming: computer/console hardware and accessories, "
    "software, and video games. "
    "'Красота' covers cosmetics and personal grooming services: makeup/skincare products, "
    "manicure, pedicure, hairdresser/barber — as opposed to 'Здоровье', which is for "
    "medicine, medical services, health-related purchases and hygiene products "
    "(sanitary pads, wet wipes, hand sanitizer). "
    "'Авто' is for car-related expenses: fuel, parking, maintenance, car parts — as opposed "
    "to 'Транспорт', which is for public/shared transport (taxi, bus, metro, etc.). "
    "'Кошечка' is everything for the household cat: cat food and treats, litter, "
    "toys, scratching posts, vet visits and pet medicine — it takes priority over "
    "'Продукты' and 'Здоровье' (e.g. cat food bought at a supermarket is 'Кошечка')."
)


# Categories that were renamed: old stored or model-supplied names still map.
RENAMED_CATEGORIES = {"Алкоголь": "Алкоголь/табак"}


def normalize_category(value: str | None) -> str:
    """Map a model/user-supplied category string onto the fixed list."""
    value = (value or "").strip()
    value = RENAMED_CATEGORIES.get(value, value)
    return value if value in CATEGORIES else DEFAULT_CATEGORY
