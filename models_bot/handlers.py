"""Обработка апдейтов: команды, кнопки, ввод своей роли и чат с тестовой моделью.

Бот работает только в личных сообщениях: в группе он молчит, чтобы не отвечать
на каждое сообщение участников.
"""

import asyncio
import logging

from models_bot import answers, ui
from models_bot.catalog import CUSTOM_ROLE, MODELS, ROLE_MAX_LENGTH, ROLES, Model
from models_bot.store import DETAILS, LANGS, Prefs, Store, Turn, next_value
from models_bot.tg import Api, TelegramError

log = logging.getLogger(__name__)


class Bot:
    def __init__(self, api: Api, store: Store) -> None:
        self.api = api
        self.store = store

    # Отправка

    async def send(self, chat_id: int, text: str, markup: dict | None = None) -> None:
        await self.api(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            link_preview_options={"is_disabled": True},
        )

    async def show(self, query: dict, text: str, markup: dict) -> None:
        """Перерисовать экран в сообщении с кнопкой, а если его не править — прислать заново."""
        message = query.get("message") or {}
        if message.get("date"):  # у недоступного (слишком старого) сообщения date = 0
            try:
                await self.api(
                    "editMessageText",
                    chat_id=message["chat"]["id"],
                    message_id=message["message_id"],
                    text=text,
                    parse_mode="HTML",
                    reply_markup=markup,
                    link_preview_options={"is_disabled": True},
                )
                return
            except TelegramError as e:
                if "message is not modified" in e.description:
                    return  # повторное нажатие на ту же кнопку
                log.info("Экран не отредактирован, присылаю заново: %s", e)
        await self.send(query["from"]["id"], text, markup)

    async def toast(self, query: dict, text: str = "", alert: bool = False) -> None:
        try:
            await self.api(
                "answerCallbackQuery", callback_query_id=query["id"], text=text, show_alert=alert
            )
        except TelegramError as e:
            log.info("Ответ на кнопку не ушёл: %s", e)  # кнопку нажали слишком давно

    # Вход

    async def handle(self, update: dict) -> None:
        if query := update.get("callback_query"):
            await self.on_button(query)
        elif message := update.get("message"):
            await self.on_message(message)

    async def on_message(self, message: dict) -> None:
        user = message.get("from")
        if not user or message["chat"]["type"] != "private":
            return
        uid = user["id"]
        self.store.touch(uid)
        prefs = self.store.get(uid)
        text = message.get("text")
        if text and text.startswith("/"):
            if prefs.awaiting:
                prefs = self.store.update(uid, awaiting="")  # команда отменяет ввод роли
            command = text.split()[0][1:].split("@")[0].lower()
            await self.command(command, uid, user, prefs)
        elif prefs.awaiting == "role":
            await self.role_input(uid, text)
        elif text == ui.NEW_DIALOG:
            await self.new_dialog(uid, prefs)
        elif text == ui.END_CHAT:
            await self.end_chat(uid, user, prefs)
        elif not text:
            await self.send(uid, ui.ONLY_TEXT)
        elif prefs.chat:
            await self.chat(uid, prefs, text)
        else:
            await self.send(uid, ui.no_chat_text(), ui.no_chat_keyboard(prefs))

    async def command(self, command: str, uid: int, user: dict, prefs: Prefs) -> None:
        if command == "start":
            await self.send(uid, ui.welcome(user.get("first_name"), prefs), ui.main_keyboard())
        elif command == "models":
            await self.send(uid, ui.models_text(), ui.models_keyboard())
        elif command == "other":
            await self.send(uid, ui.other_text(), ui.other_keyboard())
        elif command == "chat":
            await self.start_chat(uid, MODELS[prefs.model], prefs)
        elif command == "new":
            await self.new_dialog(uid, prefs)
        elif command == "stop":
            await self.end_chat(uid, user, prefs)
        elif command == "settings":
            await self.send(
                uid,
                ui.settings_text(prefs, self.store.used_today(uid)),
                ui.settings_keyboard(prefs),
            )
        elif command == "help":
            await self.send(uid, ui.HELP)
        else:
            await self.send(uid, ui.welcome(user.get("first_name"), prefs), ui.main_keyboard())

    async def on_button(self, query: dict) -> None:
        message = query.get("message") or {}
        if message.get("chat", {}).get("type", "private") != "private":
            await self.toast(query)
            return
        user = query["from"]
        uid = user["id"]
        self.store.touch(uid)
        prefs = self.store.get(uid)
        if prefs.awaiting:
            prefs = self.store.update(uid, awaiting="")  # любая кнопка отменяет ввод роли
        kind, _, arg = (query.get("data") or "").partition(":")
        if kind == "nav" and arg in SCREENS:
            await self.toast(query, SCREENS[arg])
            await self.screen(query, arg, prefs)
        elif kind in ("card", "use", "chat"):
            model_id, _, src = arg.partition(":")
            model = MODELS.get(model_id)
            if model is None:
                await self.toast(query, "Этой модели больше нет", alert=True)
                await self.screen(query, "models", prefs)
            elif kind == "card":
                await self.toast(query, model.label)
                await self.show(
                    query, ui.model_card(model, prefs), ui.card_keyboard(model, prefs, src)
                )
            elif kind == "use":
                await self.use_model(query, model, prefs, src)
            else:
                await self.toast(query, f"▸ Чат с {model.name}")
                await self.start_chat(uid, model, prefs)
        elif kind == "tgl" and arg in ui.TOGGLES:
            await self.toggle(query, arg, prefs)
        elif kind == "role" and (arg in ROLES or arg == CUSTOM_ROLE):
            await self.set_role(query, arg)
        elif kind == "dlg":  # кнопка под ответами из прошлой версии бота
            await self.toast(query)
            await self.new_dialog(uid, prefs)
        else:
            await self.toast(query, ui.STALE_BUTTON)
            await self.screen(query, "main", prefs)

    # Экраны

    async def screen(self, query: dict, name: str, prefs: Prefs) -> None:
        uid = query["from"]["id"]
        if name == "main":
            text, markup = ui.welcome(query["from"].get("first_name"), prefs), ui.main_keyboard()
        elif name == "models":
            text, markup = ui.models_text(), ui.models_keyboard()
        elif name == "other":
            text, markup = ui.other_text(), ui.other_keyboard()
        elif name == "settings":
            text = ui.settings_text(prefs, self.store.used_today(uid))
            markup = ui.settings_keyboard(prefs)
        elif name == "picker":
            text, markup = ui.picker_text(), ui.picker_keyboard(prefs)
        elif name == "role":
            text, markup = ui.role_text(prefs), ui.role_keyboard(prefs)
        else:
            text, markup = ui.notify_text(), ui.notify_keyboard(prefs)
        await self.show(query, text, markup)

    # Настройки

    async def use_model(self, query: dict, model: Model, prefs: Prefs, src: str) -> None:
        uid = query["from"]["id"]
        if prefs.model == model.id and src != "picker":
            await self.toast(query, f"✓ {model.name} уже основная")
            return
        prefs = self.store.update(uid, model=model.id)
        await self.toast(query, f"✓ Основная модель · {model.name}")
        if src == "picker":
            await self.screen(query, "settings", prefs)
        else:
            await self.show(query, ui.model_card(model, prefs), ui.card_keyboard(model, prefs, src))

    async def toggle(self, query: dict, key: str, prefs: Prefs) -> None:
        if key == "lang":
            value = next_value(LANGS, prefs.lang)
        elif key == "detail":
            value = next_value(DETAILS, prefs.detail)
        else:
            value = not getattr(prefs, key)
        prefs = self.store.update(query["from"]["id"], **{key: value})
        await self.toast(query, ui.toggle_note(key, prefs))
        await self.screen(query, "notify" if key.startswith("notify_") else "settings", prefs)

    async def set_role(self, query: dict, key: str) -> None:
        uid = query["from"]["id"]
        if key == CUSTOM_ROLE:
            self.store.update(uid, awaiting="role")
            await self.toast(query, "✎ Своя роль")
            await self.show(query, ui.role_input_text(), ui.role_input_keyboard())
            return
        prefs = self.store.update(uid, role=key)
        await self.toast(query, f"✓ Роль · {prefs.role_title}")
        await self.screen(query, "role", prefs)

    async def role_input(self, uid: int, text: str | None) -> None:
        if not text:
            await self.send(uid, ui.ROLE_ONLY_TEXT, ui.role_input_keyboard())
            return
        text = text.strip()
        if len(text) > ROLE_MAX_LENGTH:
            await self.send(uid, ui.role_too_long(len(text)), ui.role_input_keyboard())
            return
        prefs = self.store.update(uid, role=CUSTOM_ROLE, role_text=text, awaiting="")
        await self.send(uid, ui.role_text(prefs, note=ui.ROLE_SAVED), ui.role_keyboard(prefs))

    # Чат

    async def start_chat(self, uid: int, model: Model, prefs: Prefs) -> None:
        previous = MODELS.get(prefs.chat)
        prefs = self.store.update(uid, chat=model.id)
        self.store.forget(uid)
        await self.send(uid, ui.chat_started(model, prefs, previous), ui.chat_keyboard(model))

    async def new_dialog(self, uid: int, prefs: Prefs) -> None:
        if not prefs.chat:
            await self.send(uid, ui.NO_CHAT_TO_CLOSE, ui.REMOVE_KEYBOARD)
            return
        self.store.forget(uid)
        await self.send(uid, ui.new_dialog_done(MODELS[prefs.chat]))

    async def end_chat(self, uid: int, user: dict, prefs: Prefs) -> None:
        if not prefs.chat:
            await self.send(uid, ui.NO_CHAT_TO_CLOSE, ui.REMOVE_KEYBOARD)
            return
        model = MODELS[prefs.chat]
        prefs = self.store.update(uid, chat="")
        self.store.forget(uid)
        await self.send(uid, ui.chat_ended(model), ui.REMOVE_KEYBOARD)
        await self.send(uid, ui.welcome(user.get("first_name"), prefs), ui.main_keyboard())

    async def chat(self, uid: int, prefs: Prefs, text: str) -> None:
        if self.store.used_today(uid) >= answers.TEST_DAILY_LIMIT:
            await self.send(uid, answers.limit_reached())
            return
        model = MODELS[prefs.chat]
        await self.api("sendChatAction", chat_id=uid, action="typing")
        await asyncio.sleep(answers.TYPING_SECONDS)
        history = self.store.history(uid) if prefs.memory else []
        reply = answers.compose(model, prefs, text, history)
        before, after = self.store.spend(uid, reply.total)
        if prefs.memory:
            self.store.remember(
                uid,
                Turn("user", text, reply.question_tokens),
                Turn("bot", answers.plain_text(reply.html), reply.output_tokens),
            )
        html = reply.html
        if prefs.show_usage:
            html += "\n\n" + answers.usage_line(reply, after)
        await self.send(uid, html)
        if prefs.notify_limit:
            for notice in answers.limit_notices(before, after):
                await self.send(uid, notice)


# Экран → всплывающая подпись при переходе
SCREENS = {
    "main": "← Главное меню",
    "models": ui.MODELS_TITLE,
    "other": "→ Остальные модели",
    "settings": ui.SETTINGS,
    "picker": "⛭ Основная модель",
    "role": "⛭ Роль",
    "notify": "⛭ Уведомления",
}
