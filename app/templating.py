from datetime import date, timedelta

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.categories import CATEGORY_STYLE, DEFAULT_CATEGORY
from app.config import BASE_DIR
from app.stats import MONTHS_GENITIVE

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

RUBLE_ALIASES = {"RUB", "RUR", "РУБ", "РУБ.", "Р", "Р.", "₽"}
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _group_thousands(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def money(value: float | None, currency: str | None = None) -> Markup:
    """``1890`` → ``1 890 ₽``, ``2315.4`` → ``2 315,40 ₽`` (kopecks dimmed)."""
    if value is None:
        return Markup("—")
    symbol = "₽" if not currency or currency.strip().upper() in RUBLE_ALIASES else currency
    sign = "−" if value < 0 else ""
    cents = round(abs(value) * 100)
    whole, frac = divmod(cents, 100)
    text = sign + _group_thousands(whole)
    if frac:
        text += Markup('<span class="kop">,{:02d}</span>').format(frac)
    return Markup("{} {}").format(Markup(text), symbol)


def plain_amount(value: float) -> str:
    """Amount for an editable field: ``940``, ``755,40``, ``-30``."""
    if round(value, 2) == int(value):
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def compact_number(value: float) -> str:
    """Axis ticks: ``0``, ``750``, ``2,5 тыс``, ``20 тыс``."""
    if abs(value) < 1000:
        return f"{value:g}"
    return f"{value / 1000:g}".replace(".", ",") + "\u00a0тыс"


def short_date(value: date) -> str:
    """``пт, 26 сен``"""
    return f"{WEEKDAYS_SHORT[value.weekday()]}, {value.day} {MONTHS_GENITIVE[value.month - 1][:3]}"


def human_date(value: date | None) -> str:
    if value is None:
        return "—"
    today = date.today()
    if value == today:
        return "Сегодня"
    if value == today - timedelta(days=1):
        return "Вчера"
    return short_date(value)


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def cat_emoji(category: str) -> str:
    return CATEGORY_STYLE.get(category, CATEGORY_STYLE[DEFAULT_CATEGORY])[0]


def cat_color(category: str) -> str:
    return f"var(--c-{CATEGORY_STYLE.get(category, CATEGORY_STYLE[DEFAULT_CATEGORY])[1]})"


templates.env.filters.update(
    money=money,
    plain_amount=plain_amount,
    compact_number=compact_number,
    short_date=short_date,
    human_date=human_date,
    cat_emoji=cat_emoji,
    cat_color=cat_color,
)
templates.env.globals.update(plural=plural)
