CATEGORIES: list[str] = [
    "Продукты",
    "Кафе/рестораны",
    "Транспорт",
    "Авто",
    "Жильё/коммуналка",
    "Здоровье",
    "Красота",
    "Одежда",
    "Техника",
    "Алкоголь",
    "Развлечения",
    "Связь/интернет",
    "Прочее",
]

DEFAULT_CATEGORY = "Прочее"

# Shared disambiguation notes, used both for receipt extraction and for
# categorising a single manually entered expense.
CATEGORY_GUIDANCE = (
    "Category disambiguation notes: "
    "'Алкоголь' is any alcoholic drink (beer, wine, spirits, etc.), even if bought in a "
    "grocery store — it never goes into 'Продукты'. "
    "'Техника' covers electronics and gaming: computer/console hardware and accessories, "
    "software, and video games. "
    "'Красота' covers cosmetics and personal grooming services: makeup/skincare products, "
    "manicure, pedicure, hairdresser/barber — as opposed to 'Здоровье', which is for "
    "medicine, medical services, and health-related purchases. "
    "'Авто' is for car-related expenses: fuel, parking, maintenance, car parts — as opposed "
    "to 'Транспорт', which is for public/shared transport (taxi, bus, metro, etc.)."
)


def normalize_category(value: str | None) -> str:
    """Map a model/user-supplied category string onto the fixed list."""
    if value and value.strip() in CATEGORIES:
        return value.strip()
    return DEFAULT_CATEGORY
