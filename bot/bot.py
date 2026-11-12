import asyncio
import json
import logging
import random
import re
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, WebAppInfo)

import db
from config import (ADDRESS_RE, ADMIN_IDS, BOT_TOKEN, CURRENCIES, FEE_PERCENT,
                    FEE_RUB_PERCENT, FIAT, QUOTE_TTL_MIN, RUB_MAX, SBP_BANK,
                    SBP_NAME, SBP_PHONE, SUPPORT_USERNAME, WEBAPP_URL)
from rates import get_rate, get_usd_prices

logging.basicConfig(level=logging.INFO)
router = Router()

STATUS_RU = {
    "new": "🕐 Ожидает оплаты", "paid": "🔎 Проверяется",
    "done": "✅ Выполнена", "rejected": "❌ Отклонена", "cancelled": "🚫 Отменена",
}


class Exchange(StatesGroup):
    amount = State()
    address = State()
    confirm = State()
    receipt = State()   # ждём скриншот чека СБП


# ---------- клавиатуры ----------

def _main_kb() -> ReplyKeyboardMarkup:
    # Mini App открывается именно с обычной (reply) кнопки — только так работает tg.sendData
    if WEBAPP_URL:
        sep = "&" if "?" in WEBAPP_URL else "?"
        exchange_btn = KeyboardButton(
            text="💱 Обменять", web_app=WebAppInfo(
                url=f"{WEBAPP_URL}{sep}fee={FEE_PERCENT}&feerub={FEE_RUB_PERCENT}"
                    f"&rubmax={RUB_MAX:g}"))
    else:
        exchange_btn = KeyboardButton(text="💱 Обменять")
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [exchange_btn, KeyboardButton(text="📈 Курсы")],
        [KeyboardButton(text="📋 Мои заявки"), KeyboardButton(text="🆘 Поддержка")],
    ])


MAIN_KB = _main_kb()


def currency_kb(prefix: str, exclude: str | None = None) -> InlineKeyboardMarkup:
    # Рубли можно только отдать: в списке «что получаете» их нет
    names = {"RUB": "🇷🇺 RUB (СБП)"}
    btns = [InlineKeyboardButton(text=names.get(t, t), callback_data=f"{prefix}:{t}")
            for t in CURRENCIES
            if t != exclude and not (prefix == "to" and t in FIAT)]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fmt(x: float) -> str:
    return f"{x:,.8f}".rstrip("0").rstrip(".").replace(",", " ")


def money(x: float, cur: str) -> str:
    if cur == "RUB":
        return f"{x:,.2f}".replace(",", " ") + " ₽"
    return f"{fmt(x)} {cur}"


def check_amount(frm: str, amount: float) -> str | None:
    """Текст ошибки или None, если сумма подходит."""
    if not (amount >= CURRENCIES[frm][2]):
        return f"Минимальная сумма — {money(CURRENCIES[frm][2], frm)}"
    if frm == "RUB" and amount > RUB_MAX:
        return f"Максимальная сумма — {money(RUB_MAX, frm)}"
    return None


# ---------- общие команды ----------

@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        f"Привет, {m.from_user.first_name}! 👋\n\n"
        "Здесь можно обменять криптовалюту или купить её за рубли по СБП.\n"
        f"Комиссия: {FEE_PERCENT}% за крипто-обмен, {FEE_RUB_PERCENT}% за покупку за рубли "
        "(уже включена в курс).",
        reply_markup=MAIN_KB)


@router.message(F.text == "📈 Курсы")
async def show_rates(m: Message):
    try:
        p = await get_usd_prices()
    except Exception:
        return await m.answer("Не удалось получить курсы, попробуйте позже.")
    rub = p.get("RUB")
    lines = [f"<b>{t}</b> — ${fmt(v)}" + (f" · {money(v / rub, 'RUB')}" if rub else "")
             for t, v in p.items() if t not in FIAT]
    await m.answer("📈 Рыночные курсы:\n\n" + "\n".join(lines))


@router.message(F.text == "🆘 Поддержка")
async def support(m: Message):
    await m.answer(f"По всем вопросам: @{SUPPORT_USERNAME}")


@router.message(F.text == "📋 Мои заявки")
async def my_orders(m: Message):
    rows = await db.user_orders(m.from_user.id)
    if not rows:
        return await m.answer("У вас пока нет заявок.")
    text = "\n\n".join(
        f"<b>#{r['id']}</b> {money(r['amount_in'], r['frm'])} → "
        f"{money(r['amount_out'], r['to_cur'])}\n{STATUS_RU[r['status']]}"
        for r in rows)
    await m.answer(text)


@router.callback_query(F.data == "cancel")
async def cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Обмен отменён.")


# ---------- сценарий обмена ----------

@router.message(F.text == "💱 Обменять")
async def ex_start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Что отдаёте?", reply_markup=currency_kb("from"))


@router.callback_query(F.data.startswith("from:"))
async def ex_from(c: CallbackQuery, state: FSMContext):
    frm = c.data.split(":")[1]
    await state.update_data(frm=frm)
    if frm == "RUB" and not SBP_PHONE:
        await state.clear()
        return await c.message.edit_text("Приём рублей временно недоступен.")
    await c.message.edit_text(f"Отдаёте: <b>{frm}</b>\nЧто получаете?",
                              reply_markup=currency_kb("to", exclude=frm))


@router.callback_query(F.data.startswith("to:"))
async def ex_to(c: CallbackQuery, state: FSMContext):
    to = c.data.split(":")[1]
    data = await state.update_data(to=to)
    frm = data["frm"]
    try:
        rate = await get_rate(frm, to)
    except Exception:
        await state.clear()
        return await c.message.edit_text("Не удалось получить курс, попробуйте позже.")
    await state.set_state(Exchange.amount)
    await c.message.edit_text(
        f"<b>{frm} → {to}</b>\nКурс: 1 {frm} = {fmt(rate)} {to}\n\n"
        f"Введите сумму в {frm} (минимум {money(CURRENCIES[frm][2], frm)}"
        + (f", максимум {money(RUB_MAX, frm)}" if frm == "RUB" else "") + "):")


@router.message(Exchange.amount)
async def ex_amount(m: Message, state: FSMContext):
    data = await state.get_data()
    frm, to = data["frm"], data["to"]
    try:
        amount = float(m.text.replace(",", ".").replace(" ", ""))
        assert amount > 0
    except (ValueError, AssertionError, AttributeError):
        return await m.answer("Введите число, например 0.05")
    if err := check_amount(frm, amount):
        return await m.answer(err)

    rate = await get_rate(frm, to)
    out = amount * rate
    await state.update_data(amount=amount, rate=rate, out=out)
    await state.set_state(Exchange.address)
    await m.answer(
        f"Вы отдаёте: <b>{money(amount, frm)}</b>\n"
        f"Вы получите: <b>{fmt(out)} {to}</b>\n\n"
        f"Отправьте адрес кошелька {to} ({CURRENCIES[to][1]}) для получения:")


def valid_address(cur: str, addr: str) -> bool:
    return bool(re.fullmatch(ADDRESS_RE.get(cur, r"\S{20,}"), addr))


@router.message(Exchange.address)
async def ex_address(m: Message, state: FSMContext):
    addr = (m.text or "").strip()
    data = await state.get_data()
    if not valid_address(data["to"], addr):
        return await m.answer(
            f"Похоже, это не адрес {data['to']} в сети {CURRENCIES[data['to']][1]}. "
            "Проверьте и отправьте ещё раз.")
    await state.update_data(addr=addr)
    await show_confirm(m, state)


# ---------- заявка из Mini App ----------

@router.message(F.web_app_data)
async def from_webapp(m: Message, state: FSMContext):
    await state.clear()
    try:
        d = json.loads(m.web_app_data.data)
        frm, to = str(d["from"]), str(d["to"])
        amount = float(d["amount"])
        addr = str(d["address"]).strip()
    except (ValueError, KeyError, TypeError):
        return await m.answer("Не удалось прочитать заявку из приложения.", reply_markup=MAIN_KB)

    if frm not in CURRENCIES or to not in CURRENCIES or frm == to or to in FIAT:
        return await m.answer("Такая пара не поддерживается.", reply_markup=MAIN_KB)
    if frm == "RUB" and not SBP_PHONE:
        return await m.answer("Приём рублей временно недоступен.", reply_markup=MAIN_KB)
    if err := check_amount(frm, amount):
        return await m.answer(err, reply_markup=MAIN_KB)
    if not valid_address(to, addr):
        return await m.answer(f"Неверный адрес {to} ({CURRENCIES[to][1]}).", reply_markup=MAIN_KB)

    # Курс всегда считаем на сервере: цифрам из приложения не доверяем
    try:
        rate = await get_rate(frm, to)
    except Exception:
        return await m.answer("Не удалось получить курс, попробуйте позже.", reply_markup=MAIN_KB)
    await state.update_data(frm=frm, to=to, amount=amount, rate=rate,
                            out=amount * rate, addr=addr)
    await show_confirm(m, state)


async def show_confirm(m: Message, state: FSMContext):
    data = await state.get_data()
    addr = data["addr"]
    await state.set_state(Exchange.confirm)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Создать заявку", callback_data="confirm"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel"),
    ]])
    await m.answer(
        "Проверьте заявку:\n\n"
        f"Отдаёте: <b>{money(data['amount'], data['frm'])}</b>\n"
        f"Получаете: <b>{fmt(data['out'])} {data['to']}</b>\n"
        f"Адрес получения: <code>{addr}</code>\n\n"
        f"Курс фиксируется на {QUOTE_TTL_MIN} минут после создания заявки.",
        reply_markup=kb)


@router.callback_query(Exchange.confirm, F.data == "confirm")
async def ex_confirm(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    frm = d["frm"]
    amount, out = d["amount"], d["out"]

    if frm == "RUB":
        if not SBP_PHONE:
            return await c.message.edit_text("Приём рублей временно недоступен.")
        # Случайные копейки делают сумму уникальной: по ней оператор находит перевод
        amount = round(int(amount) + random.randint(1, 99) / 100, 2)
        out = amount * d["rate"]
    elif not CURRENCIES[frm][3]:
        return await c.message.edit_text("Приём этой монеты временно недоступен.")

    order_id = await db.create_order(
        c.from_user.id, c.from_user.username, frm, d["to"], amount,
        out, d["rate"], d["addr"], QUOTE_TTL_MIN)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💸 Я оплатил", callback_data=f"paid:{order_id}"),
        InlineKeyboardButton(text="🚫 Отменить", callback_data=f"ucancel:{order_id}"),
    ]])
    if frm == "RUB":
        pay = (f"Переведите ровно <b>{money(amount, 'RUB')}</b> по СБП:\n\n"
               f"📱 Телефон: <code>{SBP_PHONE}</code>\n"
               f"🏦 Банк: {SBP_BANK}\n"
               f"👤 Получатель: {SBP_NAME}\n\n"
               "❗️ Сумма должна совпадать до копейки, иначе платёж не найдётся.\n"
               "Комментарий к переводу не пишите.\n\n"
               f"Вы получите: <b>{money(out, d['to'])}</b>")
    else:
        pay = (f"Переведите <b>{money(amount, frm)}</b> (сеть {CURRENCIES[frm][1]}) на адрес:\n"
               f"<code>{CURRENCIES[frm][3]}</code>")
    await c.message.edit_text(
        f"Заявка <b>#{order_id}</b> создана.\n\n{pay}\n\n"
        f"⏳ На оплату {QUOTE_TTL_MIN} минут. После перевода нажмите «Я оплатил».",
        reply_markup=kb)


@router.callback_query(F.data.startswith("ucancel:"))
async def user_cancel(c: CallbackQuery):
    oid = int(c.data.split(":")[1])
    o = await db.get_order(oid)
    if not o or o["user_id"] != c.from_user.id or o["status"] != "new":
        return await c.answer("Эту заявку уже нельзя отменить", show_alert=True)
    await db.set_status(oid, "cancelled")
    await c.message.edit_text(f"Заявка #{oid} отменена.")


@router.callback_query(F.data.startswith("paid:"))
async def user_paid(c: CallbackQuery, bot: Bot, state: FSMContext):
    oid = int(c.data.split(":")[1])
    o = await db.get_order(oid)
    if not o or o["user_id"] != c.from_user.id or o["status"] != "new":
        return await c.answer("Заявка не найдена или уже обработана", show_alert=True)
    if time.time() > o["expires_at"]:
        await db.set_status(oid, "cancelled")
        return await c.message.edit_text(
            f"⌛ Время на оплату заявки #{oid} истекло. Если вы уже перевели средства — "
            f"напишите в поддержку @{SUPPORT_USERNAME}.")

    if o["frm"] in FIAT:
        # Для рублей просим чек: так оператору проще найти перевод
        await state.set_state(Exchange.receipt)
        await state.update_data(order_id=oid)
        return await c.message.edit_text(
            f"Заявка <b>#{oid}</b>: пришлите скриншот или PDF чека о переводе по СБП.")

    await db.set_status(oid, "paid")
    await c.message.edit_text(
        f"Спасибо! Заявка <b>#{oid}</b> передана оператору на проверку. "
        "Обычно это занимает 10–30 минут.")
    await notify_admins(bot, o)


@router.message(Exchange.receipt, F.photo | F.document)
async def got_receipt(m: Message, bot: Bot, state: FSMContext):
    oid = (await state.get_data())["order_id"]
    await state.clear()
    o = await db.get_order(oid)
    if not o or o["status"] != "new":
        return await m.answer("Заявка не найдена или уже обработана.")
    await db.set_status(oid, "paid")
    await m.answer(
        f"Спасибо! Заявка <b>#{oid}</b> с чеком передана оператору. "
        "Обычно проверка занимает 10–30 минут.", reply_markup=MAIN_KB)
    await notify_admins(bot, o, receipt=m)


@router.message(Exchange.receipt)
async def receipt_wrong(m: Message):
    await m.answer("Нужен скриншот (фото) или PDF чека. Если что-то не так — "
                   f"напишите @{SUPPORT_USERNAME}.")


async def notify_admins(bot: Bot, o, receipt: Message | None = None):
    oid = o["id"]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выполнено", callback_data=f"adone:{oid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"areject:{oid}"),
    ]])
    user = f"@{o['username']}" if o["username"] else f"id {o['user_id']}"
    if o["frm"] in FIAT:
        incoming = (f"Получить: <b>{money(o['amount_in'], o['frm'])}</b> по СБП "
                    f"на {SBP_PHONE} ({SBP_BANK})")
    else:
        incoming = (f"Получить: <b>{money(o['amount_in'], o['frm'])}</b> на "
                    f"<code>{CURRENCIES[o['frm']][3]}</code>")
    text = (f"🔔 Оплачена заявка <b>#{oid}</b> от {user}\n\n{incoming}\n"
            f"Отправить: <b>{money(o['amount_out'], o['to_cur'])}</b> "
            f"({CURRENCIES[o['to_cur']][1]}) на\n<code>{o['payout_addr']}</code>")
    for admin in ADMIN_IDS:
        try:
            if receipt and receipt.photo:
                await bot.send_photo(admin, receipt.photo[-1].file_id, caption=text, reply_markup=kb)
            elif receipt and receipt.document:
                await bot.send_document(admin, receipt.document.file_id, caption=text, reply_markup=kb)
            else:
                await bot.send_message(admin, text, reply_markup=kb)
        except Exception as e:
            logging.warning("Не удалось уведомить админа %s: %s", admin, e)


# ---------- админка ----------

@router.callback_query(F.data.startswith(("adone:", "areject:")))
async def admin_action(c: CallbackQuery, bot: Bot):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Нет доступа", show_alert=True)
    action, oid = c.data.split(":")
    oid = int(oid)
    o = await db.get_order(oid)
    if not o or o["status"] != "paid":
        return await c.answer("Заявка уже обработана", show_alert=True)

    if action == "adone":
        await db.set_status(oid, "done")
        await bot.send_message(
            o["user_id"],
            f"✅ Заявка #{oid} выполнена! {money(o['amount_out'], o['to_cur'])} "
            "отправлены на ваш кошелёк.")
        mark = "✅ ВЫПОЛНЕНО"
    else:
        await db.set_status(oid, "rejected")
        await bot.send_message(
            o["user_id"],
            f"❌ Заявка #{oid} отклонена: оплата не найдена. "
            f"Если вы уверены, что перевели средства, напишите @{SUPPORT_USERNAME}.")
        mark = "❌ ОТКЛОНЕНО"
    new_text = c.message.html_text + f"\n\n<b>{mark}</b> ({c.from_user.full_name})"
    if c.message.photo or c.message.document:
        await c.message.edit_caption(caption=new_text)
    else:
        await c.message.edit_text(new_text)


@router.message(Command("stats"))
async def admin_stats(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return
    s = await db.stats()
    lines = [f"{STATUS_RU.get(k, k)}: {v}" for k, v in s.items()] or ["Заявок нет"]
    await m.answer("📊 Статистика:\n\n" + "\n".join(lines))


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Укажите BOT_TOKEN в файле .env")
    await db.init()
    from aiogram.client.default import DefaultBotProperties
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
