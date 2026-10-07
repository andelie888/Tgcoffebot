"""Проверка Coffee Manager 2.0 без сети: логика учёта и сценарии бота.

Запуск: python3 tests/run_tests.py
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile

from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["DB_FILE"] = str(TMP / "test.db")
os.environ["ADMIN_IDS"] = "111"
os.environ["ANTHROPIC_API_KEY"] = ""
sys.path[:0] = [str(ROOT / "tests" / "stubs"), str(ROOT)]

import accounting as acc  # noqa: E402
import bot  # noqa: E402
import config  # noqa: E402
import setup_shop  # noqa: E402

from database import create_tables, migrate_database  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

FAILS = []


def check(condition, label):
    print(("  ✅ " if condition else "  ❌ ") + label)
    if not condition:
        FAILS.append(label)


# ----- фейковый Telegram -----


class Message:
    def __init__(self, text, sink):
        self.text = text
        self.sink = sink

    async def reply_text(self, text, reply_markup=None):
        self.sink.append((text, reply_markup))


class User:
    id = 111


class FakeUpdate(bot.Update):
    def __init__(self, text, sink):
        self.message = Message(text, sink)
        self.effective_user = User()


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)

    async def send_document(self, chat_id, document, filename, caption=None, disable_notification=False):
        self.sent.append(("document", chat_id, filename, len(document.read())))


class FakeJobs:
    def __init__(self):
        self.once = []

    def run_once(self, callback, when):
        self.once.append((callback, when))


class Context:
    def __init__(self):
        self.user_data = {}
        self.bot = FakeBot()
        self.job_queue = FakeJobs()


CTX = Context()
bot.BACKUP_DIR = TMP / "backups"  # тесты не трогают настоящие бэкапы
NOW = [datetime(2026, 10, 7, 21, 0, tzinfo=ZoneInfo("Europe/Samara"))]
bot.now_local = lambda: NOW[0]


def say(text):
    sink = []
    asyncio.run(bot.menu_button(FakeUpdate(text, sink), CTX))
    return sink


def last_text(sink):
    return sink[-1][0] if sink else ""


def all_text(sink):
    return "\n---\n".join(text for text, _ in sink)


def buttons(sink):
    markup = sink[-1][1] if sink else None
    return markup.buttons() if markup else []


def db(sql, params=()):
    with sqlite3.connect(config.DB_NAME) as c:
        return c.execute(sql, params).fetchall()


def stock(name):
    return db("SELECT stock FROM ingredients WHERE name = ?", (name,))[0][0]


# ----- подготовка: пример кофейни -----

print("\n1. Загрузка примера кофейни")
create_tables()
migrate_database()
data = json.loads((ROOT / "shop_data.example.json").read_text(encoding="utf-8"))
check(data.get("fixed_costs") and data.get("tax_mode"), "пример содержит постоянные расходы и налог")
data.pop("fixed_costs")
data.pop("tax_mode")
report = setup_shop.fill(data)
check(len(report) == 8, f"позиций в меню 8 (получилось {len(report)})")

products = acc.get_products()
pid = {name: p for p, name, *_ in products}

# ----- разбор ввода -----

print("\n2. Разбор сообщения с продажами")
items, unknown = acc.parse_items("Латте 0.4 - 10\nРаф 0,3 — 5\nЭспрессо 3, Круассан 2шт", products)
check(items == {pid["Латте 0.4"]: 10, pid["Раф 0.3"]: 5, pid["Эспрессо"]: 3, pid["Круассан"]: 2}, f"разобрано {items}")
check(unknown == [], "нет непонятых строк")

items, unknown = acc.parse_items(acc.day_template(products), products)
check(items == {} and unknown == [], "пустой шаблон ничего не записывает")

items, unknown = acc.parse_items("латте 0.4 л 7\nКапучино 0.3 - 4", products)
check(items == {pid["Латте 0.4"]: 7}, "«0.4 л» понимается")
check(unknown == ["Капучино 0.3 - 4"], "неизвестный товар попадает в непонятые")

items, _ = acc.parse_items("Латте 0.4 - 0", products)
check(items == {}, "ноль пропускается")

items, unknown = acc.parse_items("Раф 0.4 x3; Эспрессо: 1", products)
check(items == {pid["Раф 0.4"]: 3, pid["Эспрессо"]: 1}, "разделители x и : понимаются")

items, unknown = acc.parse_items("Латте 4", products)
check(items == {} and unknown == ["Латте 4"], "«Латте 4» без объёма неоднозначно → спросить")

# ----- закрытие дня через бота -----

print("\n3. Закрытие дня в боте")
coffee0, milk0, cup3_0, cup4_0 = stock("Кофе в зёрнах"), stock("Молоко"), stock("Стакан 0.3"), stock("Стакан 0.4")

out = say("✅ Закрыть день")
check("Закрытие дня · 07.10" in last_text(out), "открылось закрытие дня за 07.10")
check(bot.BTN_TEMPLATE in buttons(out), "есть кнопка шаблона")

out = say(bot.BTN_TEMPLATE)
check("Латте 0.3 - " in last_text(out), "шаблон содержит позиции меню")

out = say("Латте 0.4 - 10\nРаф 0.3 - 5\nЭспрессо 3\nКруассан 2\nМокко 1")
check("Мокко 1" in last_text(out), "бот переспрашивает про «Мокко 1»")

out = say("Капучино нет")
check("Не понял" in last_text(out), "мусор снова переспрашивается")

out = say("Латте 0.3 - 4")
text = last_text(out)
expected_revenue = 270 * 10 + 280 * 5 + 150 * 3 + 180 * 2 + 230 * 4
check("Итого: 24 шт." in text, "итого 24 шт. (запомнены прошлые строки)")
check(bot.money(expected_revenue) in text, f"выручка {bot.money(expected_revenue)}")

out = say(bot.BTN_CONFIRM_SALES)
check("бесплатные" in last_text(out), "спрашивает про бесплатные")

out = say(bot.BTN_HAS_FREE)
out = say("Латте 0.3 - 2")
check(bot.BTN_REASON_TREAT in buttons(out), "спрашивает причину")
out = say(bot.BTN_REASON_TREAT)
check("Ещё были" in last_text(out), "показывает записанные бесплатные")
out = say(bot.BTN_MORE_FREE)
out = say("Эспрессо 1")
out = say(bot.BTN_REASON_WASTE)
out = say(bot.BTN_NO_FREE)
report_text = all_text(out)
check("День 07.10 закрыт" in report_text, "день закрыт")
check("Продано: 24 шт." in report_text, "в отчёте 24 шт.")
check("Угощения: 2 шт." in report_text and "Брак: 1 шт." in report_text, "угощения и брак в отчёте")
check("Анализ дня" in report_text, "есть анализ дня (по правилам, без ключа)")
check(acc.is_day_closed(date(2026, 10, 7)), "день отмечен закрытым")

latte4 = {"Кофе в зёрнах": 0.018, "Молоко": 0.30}
expected_coffee = coffee0 - (0.018 * (10 + 5 + 3 + 4 + 2 + 1))
expected_milk = milk0 - (0.30 * 10 + 0.2 * 5 + 0.22 * (4 + 2))
check(abs(stock("Кофе в зёрнах") - expected_coffee) < 1e-9, "кофе списан по рецептам, включая бесплатные")
check(abs(stock("Молоко") - expected_milk) < 1e-9, "молоко списано по рецептам")
check(stock("Стакан 0.3") == cup3_0 - 6 - 5, "стаканы 0.3 списаны (4+2 латте, 5 раф)")

free_rows = db("SELECT kind, SUM(quantity), SUM(unit_price) FROM sales WHERE kind != 'sale' GROUP BY kind")
check(dict((k, q) for k, q, _ in free_rows) == {"treat": 2, "waste": 1}, "бесплатные записаны отдельно")
check(all(p == 0 for _, _, p in free_rows), "у бесплатных цена 0 — выручку не завышают")

print("\n4. Повторное закрытие того же дня заменяет цифры")
say("✅ Закрыть день")
say("Латте 0.4 - 1")
say(bot.BTN_CONFIRM_SALES)
say(bot.BTN_NO_FREE)
f = acc.get_finances(date(2026, 10, 7), date(2026, 10, 7))
check(f["sold"] == 1 and f["revenue"] == 270, "осталась одна продажа")
check(f["treat_qty"] == 0, "старые угощения убраны")
check(abs(stock("Кофе в зёрнах") - (coffee0 - 0.018)) < 1e-9, "склад восстановлен и списан заново")

print("\n4б. Склад в минусе не «растёт» при повторном закрытии")
with sqlite3.connect(config.DB_NAME) as c:
    c.execute("UPDATE ingredients SET stock = 0.1 WHERE name = 'Молоко'")
d = date(2026, 10, 6)
acc.close_day(d, {pid["Латте 0.4"]: 5})
after_first = stock("Молоко")
acc.close_day(d, {pid["Латте 0.4"]: 5})
check(abs(after_first - (0.1 - 1.5)) < 1e-9, "молоко ушло в минус ровно на расход")
check(abs(stock("Молоко") - after_first) < 1e-9, "повторное закрытие не меняет склад")
acc.close_day(d, {})
check(abs(stock("Молоко") - 0.1) < 1e-9, "отмена продаж дня возвращает склад точно")
with sqlite3.connect(config.DB_NAME) as c:
    c.execute("DELETE FROM day_closures WHERE day = '2026-10-06'")
    c.execute("UPDATE ingredients SET stock = ? WHERE name = 'Молоко'", (milk0 - 0.30 * 1,))

# ----- себестоимость из рецепта -----

print("\n5. Себестоимость считается из рецепта и цен закупки")
costs = {name: cost for _, name, _, cost, _ in acc.get_product_costs()}
check(abs(costs["Латте 0.4"] - (0.018 * 1800 + 0.30 * 90 + 7 + 3)) < 0.01, f"латте 0.4 = {costs['Латте 0.4']}")
check(costs["Круассан"] == 70, "без рецепта — ручная себестоимость")

out = say("🛒 Закупка")
out = say("🛒 Молоко")
out = say("10")
out = say("1200")
check("подорожал на 33%" in last_text(out), "бот сообщает, что молоко подорожало")
costs = {name: cost for _, name, _, cost, _ in acc.get_product_costs()}
check(abs(costs["Латте 0.4"] - (0.018 * 1800 + 0.30 * 120 + 7 + 3)) < 0.01, "себестоимость латте пересчиталась")

# ----- постоянные расходы и налог -----

print("\n6. Постоянные расходы и налог")
say("⚙️ Настройки")
say(bot.BTN_FIXED_COSTS)
say(bot.BTN_ADD_FIXED)
say("Аренда")
out = say("31000")
check("Аренда: 31 000" in all_text(out), "аренда добавлена")
check("1 000 ₽ в день" in all_text(out), "показывает 1 000 ₽ в день (октябрь 31 день)")
say(bot.BTN_ADD_FIXED)
say("Зарплата")
say("62000")

say(bot.BTN_TAX)
out = say("🏛 УСН 6% (доходы)")
check("УСН 6%" in all_text(out), "выбран УСН 6%")

f = acc.get_finances(date(2026, 10, 7), date(2026, 10, 7))
check(abs(f["fixed"] - 3000) < 0.01, f"доля постоянных за день 3000 (получилось {f['fixed']:.2f})")
check(abs(f["tax"] - 270 * 0.06) < 0.01, "налог 6% от выручки")
check(abs(f["net"] - (270 - f["cost"] - 3000 - 270 * 0.06)) < 0.01, "чистая прибыль учитывает всё")

out = say(bot.BTN_FIXED_COSTS)
out = say("🏠 Аренда")
out = say(bot.BTN_CHANGE_AMOUNT)
out = say("40000")
check("Аренда: 40 000" in all_text(out), "сумма аренды изменена")

say(bot.BTN_TAX)
say("🏛 Патент")
out = say("36500")
check("100 ₽ в день" in all_text(out), "патент 36 500 в год = 100 в день")
check(abs(acc.get_finances(date(2026, 10, 7), date(2026, 10, 7))["tax"] - 100) < 0.01, "налог по патенту за день 100")
say(bot.BTN_TAX)
say("🏛 УСН 6% (доходы)")

# ----- безубыточность -----

print("\n7. Точка безубыточности и утреннее сообщение")
cups, day_costs, per_cup = acc.breakeven(date(2026, 10, 8))
check(cups > 0, f"нужно продать {cups} шт. (расходы дня {day_costs:.0f}, прибыль с чашки {per_cup:.0f})")
morning = bot.build_morning_text(date(2026, 10, 8))
check("Чтобы выйти в ноль" in morning, "утром пишет план безубыточности")

# ----- инвентаризация и потери -----

print("\n8. Инвентаризация и неучтённые потери")
milk_before = stock("Молоко")
say(bot.BTN_REPORTS)
say(bot.BTN_STOCK)
say(bot.BTN_STOCKTAKE)
say("📝 Молоко")
out = say(str(round(milk_before - 2, 3)))
check("Неучтённая потеря" in all_text(out), "показывает потерю при недостаче")
out = say(bot.BTN_LOSSES)
check("Неучтённые потери по инвентаризации" in last_text(out) and "Молоко" in last_text(out), "потери видны в отчёте")

# ----- отчёты -----

print("\n9. Отчёты")
out = say("📅 Сегодня")
check("Чистая прибыль" in last_text(out) and "Постоянные расходы" in last_text(out), "финансы за сегодня")
out = say("📅 Вчера")
check("Финансы · Вчера" in last_text(out), "финансы за вчера")
out = say("📅 Этот месяц")
check("Этот месяц" in last_text(out), "финансы за месяц")
out = say(bot.BTN_REPORT_PRODUCTS)
check("Прибыль с одной порции" in last_text(out), "отчёт по товарам")
out = say(bot.BTN_HISTORY)
check("Молоко" in last_text(out), "история закупок")

# ----- напоминания -----

print("\n10. Напоминания")
NOW[0] = datetime(2026, 10, 8, 22, 0, tzinfo=ZoneInfo("Europe/Samara"))
CTX.bot.sent.clear()
asyncio.run(bot.evening_reminder(CTX))
check(CTX.bot.sent and "Пора закрыть день 08.10" in CTX.bot.sent[0], "вечером напоминает закрыть день")
check(len(CTX.job_queue.once) == 1, "запланирован повтор через час")
NOW[0] = datetime(2026, 10, 8, 23, 0, tzinfo=ZoneInfo("Europe/Samara"))
asyncio.run(bot.repeat_reminder(CTX))
check(len(CTX.bot.sent) == 2, "повтор приходит, если день не закрыт")

NOW[0] = datetime(2026, 10, 9, 0, 30, tzinfo=ZoneInfo("Europe/Samara"))
out = say("✅ Закрыть день")
check("Закрытие дня · 08.10" in last_text(out), "после полуночи закрывается вчерашний день")
say("Эспрессо 5")
say(bot.BTN_CONFIRM_SALES)
say(bot.BTN_NO_FREE)
CTX.bot.sent.clear()
NOW[0] = datetime(2026, 10, 9, 0, 40, tzinfo=ZoneInfo("Europe/Samara"))
asyncio.run(bot.repeat_reminder(CTX))
check(not CTX.bot.sent, "после закрытия повтор не приходит")

NOW[0] = datetime(2026, 10, 12, 9, 0, tzinfo=ZoneInfo("Europe/Samara"))
morning = bot.build_morning_text(date(2026, 10, 12))
check("Понедельник" in morning, "по понедельникам напоминает об инвентаризации")
check("Вчерашний день (11.10) не закрыт" in morning, "утром напоминает о незакрытом вчерашнем дне")
out = say("✅ Закрыть день")
check(bot.BTN_CLOSE_YESTERDAY in buttons(out), "предлагает закрыть вчерашний день")
out = say(bot.BTN_CLOSE_YESTERDAY)
check("Закрытие дня · 11.10" in last_text(out), "переключился на 11.10")
say(bot.BTN_CANCEL)

month = bot.build_month_text(date(2026, 11, 1))
check("Итоги месяца" in month and "Выручка" in month, "1-го числа — итоги месяца")

# ----- меню и проверки -----

print("\n11. Меню и названия")
check(bot.main_menu().buttons() == [bot.BTN_CLOSE_DAY, bot.BTN_REPORTS, bot.BTN_PURCHASE, bot.BTN_SETTINGS], "главное меню из 4 кнопок")
check(bot.validate_product_name("Чай 2") is not None, "«Чай 2» запрещено")
check(bot.validate_product_name("Флэт уайт 0.3") is None, "«Флэт уайт 0.3» можно")
check(bot.validate_product_name("латте 0.4") is not None, "дубликат без учёта регистра запрещён")
out = say("что-то непонятное")
check("Не понял" in last_text(out), "непонятный текст → подсказка")

# ----- рецепты и ингредиенты в боте -----

print("\n12а. Новый ингредиент и рецепт прямо в боте")
check(acc.parse_amount("18 г", "кг") == 0.018 and acc.parse_amount("200 мл", "л") == 0.2, "«18 г» и «200 мл» переводятся")
check(acc.parse_amount("1", "шт") == 1 and acc.parse_amount("5 кг", "л") is None, "неверная единица не принимается")

NOW[0] = datetime(2026, 10, 12, 15, 0, tzinfo=ZoneInfo("Europe/Samara"))
say(bot.BTN_REPORTS)
say(bot.BTN_STOCK)
say(bot.BTN_NEW_INGREDIENT)
say("Сироп карамель")
say("л")
say("700")
say("1.5")
out = say("0.3")
check("Сироп карамель" in all_text(out), "ингредиент добавлен и виден на складе")

say(bot.BTN_SETTINGS)
say(bot.BTN_MENU_MANAGEMENT)
say(bot.BTN_ADD_PRODUCT)
say("Карамельный латте 0.4")
say("330")
say("0")
say("✏️ Карамельный латте 0.4")
out = say(bot.BTN_RECIPE)
check("Рецепта пока нет" in last_text(out), "у нового товара нет рецепта")
for ing, amount in [("Кофе в зёрнах", "18 г"), ("Молоко", "280 мл"), ("Сироп карамель", "30 мл"), ("Стакан 0.4", "1"), ("Крышка", "1")]:
    say(bot.BTN_RECIPE_ADD)
    say(f"🥄 {ing}")
    out = say(amount)
check("Себестоимость порции" in last_text(out), "рецепт собран")
costs = {name: cost for _, name, _, cost, _ in acc.get_product_costs()}
milk_price = db("SELECT purchase_price FROM ingredients WHERE name='Молоко'")[0][0]
expected = 0.018 * 1800 + 0.28 * milk_price + 0.03 * 700 + 7 + 3
check(abs(costs["Карамельный латте 0.4"] - expected) < 0.01, f"себестоимость по рецепту {costs['Карамельный латте 0.4']}")
say(bot.BTN_RECIPE_ADD)
say("🥄 Молоко")
say("0.3")
check(len(acc.get_recipe(pid_new := [p for p, n, *_ in acc.get_products() if n == "Карамельный латте 0.4"][0])) == 5, "повторный ингредиент заменяет количество, а не дублирует")
syrup_before = stock("Сироп карамель")
say(bot.BTN_MAIN)
say("✅ Закрыть день")
say("Карамельный латте 0.4 - 10")
say(bot.BTN_CONFIRM_SALES)
say(bot.BTN_NO_FREE)
check(abs(stock("Сироп карамель") - (syrup_before - 0.3)) < 1e-9, "новый сироп списывается при закрытии дня")

print("\n12б. Ночной бэкап приходит в Telegram")
bot.BACKUP_CHAT_ID = 111
CTX.bot.sent.clear()
asyncio.run(bot.backup_job(CTX))
check(CTX.bot.sent and CTX.bot.sent[0][0] == "document" and CTX.bot.sent[0][3] > 0, "файл базы отправлен")

# ----- миграция настоящей базы -----

print("\n12. Миграция текущей базы с Mac")
real = ROOT / "real_copy.db"
if real.exists():
    migrated = TMP / "real.db"
    shutil.copy(real, migrated)
    with sqlite3.connect(migrated) as c:
        before = c.execute("SELECT COUNT(*), SUM(quantity * unit_price) FROM sales").fetchone()
    config.DB_NAME = acc.DB_NAME = str(migrated)
    import database

    database.DB_NAME = str(migrated)
    create_tables()
    migrate_database()
    with sqlite3.connect(migrated) as c:
        after = c.execute("SELECT COUNT(*), SUM(quantity * unit_price) FROM sales WHERE kind = 'sale'").fetchone()
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check(before == after, f"старые продажи на месте: {after[0]} шт. записей")
    check({"day_closures", "fixed_costs", "shop_settings"} <= tables, "новые таблицы созданы")
    f = acc.get_finances(date(1900, 1, 1), date(2026, 10, 7))
    check(abs(f["revenue"] - before[1]) < 0.01, "финансы считаются на старой базе")
else:
    print("  (нет копии базы — пропуск)")

print()
if FAILS:
    print(f"❌ Ошибок: {len(FAILS)}")
    for item in FAILS:
        print("   -", item)
    sys.exit(1)

print("✅ Все проверки пройдены")
