import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.categories import CATEGORY_STYLE, DEFAULT_CATEGORY
from app.config import BASE_DIR, get_settings
from app.stats import MONTHS_GENITIVE

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

# All spending is in euros; a receipt in another currency shows its own code.
CURRENCY_SYMBOL = "€"
CURRENCY_ALIASES = {"EUR", "EURO", "EUROS", "€"}
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _group_thousands(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def currency_label(currency: str | None) -> str:
    """``€`` for euros (or unknown), otherwise the receipt's own code: ``USD``."""
    if not currency or currency.strip().upper() in CURRENCY_ALIASES:
        return CURRENCY_SYMBOL
    return currency.strip()


def money(value: float | None, currency: str | None = None) -> Markup:
    """``1890`` → ``1 890 €``, ``2315.4`` → ``2 315,40 €`` (cents dimmed)."""
    if value is None:
        return Markup("—")
    symbol = currency_label(currency)
    sign = "−" if value < 0 else ""
    cents = round(abs(value) * 100)
    whole, frac = divmod(cents, 100)
    text = sign + _group_thousands(whole)
    if frac:
        text += Markup('<span class="cents">,{:02d}</span>').format(frac)
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


def usd(value: float | None) -> str:
    """Claude costs are billed in dollars and are often fractions of a cent."""
    if value is None:
        return "—"
    if value == 0:
        return "$0"
    if abs(value) < 1:
        # Up to 4 decimals, trailing zeros dropped (but keep cents): $0.011, $0.42, $0.0031.
        text = f"{value:.4f}".rstrip("0")
        if len(text.split(".")[1]) < 2:
            text = f"{value:.2f}"
        return f"${text}"
    return f"${value:,.2f}"


def compact_tokens(value: int) -> str:
    """``900``, ``8,9 тыс``, ``53 тыс``: token counts at a glance."""
    if value < 1000:
        return str(value)
    thousands = value / 1000
    text = f"{thousands:.0f}" if thousands >= 10 else f"{thousands:.1f}".replace(".", ",")
    return text + "\u00a0тыс"


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


def human_date_time(value: datetime) -> str:
    """A stored UTC timestamp in the configured timezone: ``Сегодня 14:05``."""
    local = value.replace(tzinfo=value.tzinfo or UTC).astimezone(ZoneInfo(get_settings().timezone))
    return f"{human_date(local.date())} {local:%H:%M}"


def simple_markdown(text: str) -> Markup:
    """The little formatting Claude's answers use: paragraphs, ``- `` bullets, ``**bold**``."""
    def inline(line: str) -> str:
        escaped = str(Markup.escape(line))
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)

    html, bullets = [], []
    for raw in text.splitlines() + [""]:
        line = raw.strip()
        if line.startswith(("- ", "• ", "* ")):
            bullets.append(f"<li>{inline(line[2:])}</li>")
            continue
        if bullets:
            html.append("<ul>" + "".join(bullets) + "</ul>")
            bullets = []
        if line:
            html.append(f"<p>{inline(line)}</p>")
    return Markup("".join(html))


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


# The household cat is black, and the 🐈‍⬛ emoji looks different on every phone,
# so this category gets a drawn icon wherever it can (not inside <option>).
# The outline token keeps the black silhouette visible in dark mode.
BLACK_CAT_SVG = Markup(
    '<svg class="cat-icon" viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true">'
    '<path fill="#1b1b1d" stroke="var(--cat-outline)" stroke-width=".8" stroke-linejoin="round" '
    'd="M4.5 3 9.3 7.1Q12 6.4 14.7 7.1L19.5 3 19.6 9.6Q20.5 11.5 20 13.8 19.3 20.5 12 20.8 '
    '4.7 20.5 4 13.8 3.5 11.5 4.4 9.6Z"/>'
    '<ellipse cx="8.8" cy="13" rx="1.9" ry="2.1" fill="#f2c94c"/>'
    '<ellipse cx="15.2" cy="13" rx="1.9" ry="2.1" fill="#f2c94c"/>'
    '<ellipse cx="8.8" cy="13.1" rx=".55" ry="1.6" fill="#1b1b1d"/>'
    '<ellipse cx="15.2" cy="13.1" rx=".55" ry="1.6" fill="#1b1b1d"/>'
    '<path d="M11.2 16.2h1.6l-.8.9z" fill="#e7a1ac"/>'
    '<path d="M9.4 17 5.2 16.4M9.4 17.6 5.4 18.4M14.6 17l4.2-.6M14.6 17.6l4 .8" '
    'stroke="#8a8a8e" stroke-width=".45" stroke-linecap="round"/>'
    "</svg>"
)
CUSTOM_ICONS = {"Кошечка": BLACK_CAT_SVG}


def cat_icon(category: str) -> Markup:
    """Icon for visual spots: a drawn SVG where we have one, else the emoji."""
    return CUSTOM_ICONS.get(category) or Markup.escape(cat_emoji(category))


def cat_emoji(category: str) -> str:
    return CATEGORY_STYLE.get(category, CATEGORY_STYLE[DEFAULT_CATEGORY])[0]


def cat_color(category: str) -> str:
    return f"var(--c-{CATEGORY_STYLE.get(category, CATEGORY_STYLE[DEFAULT_CATEGORY])[1]})"


templates.env.filters.update(
    money=money,
    currency_label=currency_label,
    plain_amount=plain_amount,
    compact_number=compact_number,
    usd=usd,
    compact_tokens=compact_tokens,
    short_date=short_date,
    human_date=human_date,
    human_date_time=human_date_time,
    cat_emoji=cat_emoji,
    cat_icon=cat_icon,
    cat_color=cat_color,
    simple_markdown=simple_markdown,
)
templates.env.globals.update(plural=plural, currency_symbol=CURRENCY_SYMBOL)
