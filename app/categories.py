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


def normalize_category(value: str | None) -> str:
    """Map a model/user-supplied category string onto the fixed list."""
    if value and value.strip() in CATEGORIES:
        return value.strip()
    return DEFAULT_CATEGORY
