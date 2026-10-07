"""ИИ-анализ закрытого дня через Claude API.

Без ключа ANTHROPIC_API_KEY или при ошибке сети возвращает None —
тогда бот показывает короткий анализ по правилам (accounting.rule_insights).
"""

import json
import logging
import urllib.error
import urllib.request

from config import AI_MODEL, ANTHROPIC_API_KEY, CURRENCY


logger = logging.getLogger("coffee_manager.ai")

API_URL = "https://api.anthropic.com/v1/messages"
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def build_prompt(context):
    f = context["finances"]
    lines = [
        f"День: {context['day']:%d.%m.%Y}, {WEEKDAYS[context['weekday']]}.",
        f"Продано напитков и еды: {f['sold']} шт., выручка {f['revenue']:.0f} {CURRENCY}.",
        f"Себестоимость проданного: {f['cost']:.0f}, маржа {f['margin']:.0f}%.",
        f"Угощения: {f['treat_qty']} шт. на {f['treat_cost']:.0f}; брак: {f['waste_qty']} шт. на {f['waste_cost']:.0f}.",
        f"Разовые расходы: {f['expenses']:.0f}; доля постоянных расходов на день: {f['fixed']:.0f}; налог (оценка): {f['tax']:.0f}.",
        f"Чистая прибыль дня: {f['net']:.0f} {CURRENCY}.",
    ]

    if context["avg_revenue"] is not None:
        lines.append(
            f"Средняя выручка за прошлые закрытые дни: {context['avg_revenue']:.0f}, "
            f"среднее число продаж: {context['avg_sold']:.0f}."
        )

    if context["breakeven"]:
        cups, day_costs, per_cup = context["breakeven"]
        lines.append(
            f"Точка безубыточности: {cups} шт. в день (расходы дня {day_costs:.0f}, прибыль с чашки {per_cup:.0f})."
        )

    lines.append("Продажи по позициям (шт., выручка, себестоимость):")
    for name, quantity, revenue, cost in context["products"]:
        lines.append(f"- {name}: {quantity}, {revenue:.0f}, {cost:.0f}")

    lines.append("Цена и себестоимость позиций меню:")
    for _, name, price, cost, _ in context["product_costs"]:
        lines.append(f"- {name}: цена {price:.0f}, себестоимость {cost:.0f}")

    if context["low_stock"]:
        stock = ", ".join(f"{n} {s:g} {u}" for n, u, s, _ in context["low_stock"])
        lines.append(f"Заканчивается на складе: {stock}.")

    return (
        "Ты финансовый помощник владельца небольшой кофейни. Вот итоги дня:\n\n"
        + "\n".join(lines)
        + "\n\nНапиши анализ дня для владельца: 3–4 коротких пункта, каждый начинай с «• ». "
        "Только конкретика по этим цифрам: что хорошо, что плохо, и одно действие на завтра "
        "с ожидаемым эффектом в рублях, если его можно оценить. Без вступлений, приветствий "
        "и общих советов. Не выдумывай данных, которых нет. Пиши по-русски, простыми словами."
    )


def analyze_day(context):
    if not ANTHROPIC_API_KEY:
        return None

    body = json.dumps(
        {
            "model": AI_MODEL,
            "max_tokens": 400,
            "messages": [{"role": "user", "content": build_prompt(context)}],
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        logger.exception("ИИ-анализ недоступен")
        return None

    text = "".join(
        block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
    ).strip()

    return text or None
