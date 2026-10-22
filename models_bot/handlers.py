"""Обработка апдейтов: команды, кнопки, ввод своей роли и чат с тестовой моделью.

Бот работает только в личных сообщениях: в группе он молчит, чтобы не отвечать
на каждое сообщение участников. Методы вызываются из рабочих потоков tg.Serial,
по одному потоку на пользователя за раз.
"""

import time

from models_bot import answers, log, ui
from models_bot.catalog import CUSTOM_ROLE, MODELS, ROLE_MAX_LENGTH, ROLES, Model
from models_bot.store import DETAILS, LANGS, Prefs, Store, Turn, next_value
from models_bot.tg import Api, TelegramError


class Bot:
    def __init__(self, api: Api, store: Store) -> None:
        self.api = api
        self.store = store

    # Отправка

    def send(self, chat_id: int, text: str, markup: dict | None = None) -> None:
        self.api(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            link_preview_options={"is_disabled": True},
        )

    def show(self, query: dict, text: str, markup: dict) -> None:
        """Перерисовать экран в сообщении с кнопкой, а если его не править — прислать заново."""
        message = query.get("message") or {}
        if message.get("date"):  # у недоступного (слишком старого) сообщения date = 0
            try:
                self.api(
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
        self.send(query["from"]["id"], text, markup)

    def toast(self, query: dict, text: str = "", alert: bool = False) -> None:
        try:
            self.api(
                "answerCallbackQuery", callback_query_id=query["id"], text=text, show_alert=alert
            )
        except TelegramError as e:
            log.info("Ответ на кнопку не ушёл: %s", e)  # кнопку нажали слишком давно

    # Вход

    def handle(self, update: dict) -> None:
        if query := update.get("callback_query"):
            self.on_button(query)
        elif message := update.get("message"):
            self.on_message(message)

    def visitor(self, uid: int) -> Prefs:
        """Настройки того, кто написал или нажал кнопку."""
        self.store.touch(uid)
        prefs = self.store.get(uid)
        if prefs.blocked:
            prefs = self.store.update(uid, blocked=False)  # раз пишет — снова доступен
        return prefs

    def on_message(self, message: dict) -> None:
        user = message.get("from")
        if not user or message["chat"]["type"] != "private":
            return
        uid = user["id"]
        prefs = self.visitor(uid)
        text = message.get("text")
        if prefs.awaiting and (
            (text or "").startswith("/") or text in (ui.NEW_DIALOG, ui.END_CHAT)
        ):
            # Команда или кнопка чата отменяют ввод своей роли, а не становятся ею
            prefs = self.store.update(uid, awaiting="")
        if text and text.startswith("/"):
            command = text.split()[0][1:].split("@")[0].lower()
            self.command(command, uid, user, prefs)
        elif text == ui.NEW_DIALOG:
            self.new_dialog(uid, prefs)
        elif text == ui.END_CHAT:
            self.end_chat(uid, user, prefs)
        elif prefs.awaiting == "role":
            self.role_input(uid, text)
        elif not text:
            self.send(uid, ui.ONLY_TEXT)
        elif prefs.chat:
            self.chat(uid, prefs, text)
        else:
            self.send(uid, ui.no_chat_text(), ui.no_chat_keyboard(prefs))

    def command(self, command: str, uid: int, user: dict, prefs: Prefs) -> None:
        if command == "models":
            self.send(uid, ui.models_text(), ui.models_keyboard())
        elif command == "other":
            self.send(uid, ui.other_text(), ui.other_keyboard())
        elif command == "chat":
            self.open_chat(uid, MODELS[prefs.model], prefs)
        elif command == "new":
            self.new_dialog(uid, prefs)
        elif command == "stop":
            self.end_chat(uid, user, prefs)
        elif command == "settings":
            text = ui.settings_text(prefs, self.store.used_today(uid))
            self.send(uid, text, ui.settings_keyboard(prefs))
        elif command == "help":
            self.send(uid, ui.HELP)
        else:  # /start и неизвестные команды
            self.send(uid, ui.welcome(user.get("first_name"), prefs), ui.main_keyboard())

    def on_button(self, query: dict) -> None:
        message = query.get("message") or {}
        if message.get("chat", {}).get("type", "private") != "private":
            self.toast(query)
            return
        uid = query["from"]["id"]
        prefs = self.visitor(uid)
        if prefs.awaiting:
            prefs = self.store.update(uid, awaiting="")  # любая кнопка отменяет ввод роли
        kind, _, arg = (query.get("data") or "").partition(":")
        if kind == "nav" and arg in SCREENS:
            self.toast(query, SCREENS[arg])
            self.screen(query, arg, prefs)
        elif kind in ("card", "use", "chat"):
            model_id, _, src = arg.partition(":")
            model = MODELS.get(model_id)
            if model is None:
                self.toast(query, "Этой модели больше нет", alert=True)
                self.screen(query, "models", prefs)
            elif kind == "card":
                self.toast(query, model.label)
                self.show(query, ui.model_card(model, prefs), ui.card_keyboard(model, prefs, src))
            elif kind == "use":
                self.use_model(query, model, prefs, src)
            else:
                self.chat_button(query, model, prefs, src)
        elif kind == "tgl" and arg in ui.TOGGLES:
            self.toggle(query, arg, prefs)
        elif kind == "role" and (arg in ROLES or arg == CUSTOM_ROLE):
            self.set_role(query, arg)
        elif kind == "dlg":  # кнопка под ответами из прошлой версии бота
            self.toast(query)
            self.new_dialog(uid, prefs)
        else:
            self.toast(query, ui.STALE_BUTTON)
            self.screen(query, "main", prefs)

    # Экраны

    def screen(self, query: dict, name: str, prefs: Prefs) -> None:
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
        self.show(query, text, markup)

    # Настройки

    def use_model(self, query: dict, model: Model, prefs: Prefs, src: str) -> None:
        uid = query["from"]["id"]
        if prefs.model == model.id and src != "picker":
            self.toast(query, f"✓ {model.name} уже основная")
            return
        prefs = self.store.update(uid, model=model.id)
        self.toast(query, f"✓ Основная модель · {model.name}")
        if src == "picker":
            self.screen(query, "settings", prefs)
        else:
            self.show(query, ui.model_card(model, prefs), ui.card_keyboard(model, prefs, src))

    def toggle(self, query: dict, key: str, prefs: Prefs) -> None:
        uid = query["from"]["id"]
        if key == "lang":
            value = next_value(LANGS, prefs.lang)
        elif key == "detail":
            value = next_value(DETAILS, prefs.detail)
        else:
            value = not getattr(prefs, key)
        prefs = self.store.update(uid, **{key: value})
        if key == "memory" and not value:
            self.store.forget(uid)  # память выключена — переписку не храним
        self.toast(query, ui.toggle_note(key, prefs))
        self.screen(query, "notify" if key.startswith("notify_") else "settings", prefs)

    def set_role(self, query: dict, key: str) -> None:
        uid = query["from"]["id"]
        if key == CUSTOM_ROLE:
            self.store.update(uid, awaiting="role")
            self.toast(query, "✎ Своя роль")
            self.show(query, ui.role_input_text(), ui.role_input_keyboard())
            return
        prefs = self.store.update(uid, role=key)
        self.toast(query, f"✓ Роль · {prefs.role_title}")
        self.screen(query, "role", prefs)

    def role_input(self, uid: int, text: str | None) -> None:
        if not text:
            self.send(uid, ui.ROLE_ONLY_TEXT, ui.role_input_keyboard())
            return
        text = text.strip()
        if len(text) > ROLE_MAX_LENGTH:
            self.send(uid, ui.role_too_long(len(text)), ui.role_input_keyboard())
            return
        prefs = self.store.update(uid, role=CUSTOM_ROLE, role_text=text, awaiting="")
        self.send(uid, ui.role_text(prefs, note=ui.ROLE_SAVED), ui.role_keyboard(prefs))

    # Чат

    def chat_button(self, query: dict, model: Model, prefs: Prefs, src: str) -> None:
        uid = query["from"]["id"]
        self.toast(query, f"▸ Чат с {model.name}")
        if src not in ("models", "other"):
            self.open_chat(uid, model, prefs)  # кнопка под подсказкой «чат не начат»
            return
        # Кнопка в карточке начинает чат заново, даже если он уже открыт.
        # Карточку обновляем: на ней видно, что чат открыт
        previous = MODELS.get(prefs.chat)
        prefs = self.start_chat(uid, model)
        self.show(query, ui.model_card(model, prefs), ui.card_keyboard(model, prefs, src))
        self.send(uid, ui.chat_started(model, prefs, previous), ui.chat_keyboard(model))

    def open_chat(self, uid: int, model: Model, prefs: Prefs) -> None:
        """/chat и «▸ Чат с …» под подсказкой: уже открытый чат с той же моделью не сбрасываем."""
        if prefs.chat == model.id:
            self.send(uid, ui.chat_already_open(model), ui.chat_keyboard(model))
            return
        previous = MODELS.get(prefs.chat)
        prefs = self.start_chat(uid, model)
        self.send(uid, ui.chat_started(model, prefs, previous), ui.chat_keyboard(model))

    def start_chat(self, uid: int, model: Model) -> Prefs:
        prefs = self.store.update(uid, chat=model.id)
        self.store.forget(uid)  # новый чат — с чистой историей
        return prefs

    def new_dialog(self, uid: int, prefs: Prefs) -> None:
        if not prefs.chat:
            self.send(uid, ui.NO_CHAT_TO_CLOSE, ui.REMOVE_KEYBOARD)
            return
        self.store.forget(uid)
        model = MODELS[prefs.chat]
        self.send(uid, ui.new_dialog_done(model), ui.chat_keyboard(model))

    def end_chat(self, uid: int, user: dict, prefs: Prefs) -> None:
        if not prefs.chat:
            self.send(uid, ui.NO_CHAT_TO_CLOSE, ui.REMOVE_KEYBOARD)
            return
        model = MODELS[prefs.chat]
        prefs = self.store.update(uid, chat="")
        self.store.forget(uid)
        self.send(uid, ui.chat_ended(model), ui.REMOVE_KEYBOARD)
        self.send(uid, ui.welcome(user.get("first_name"), prefs), ui.main_keyboard())

    def chat(self, uid: int, prefs: Prefs, text: str) -> None:
        used = self.store.used_today(uid)
        if used >= answers.TEST_DAILY_LIMIT:
            self.send(uid, answers.limit_reached())
            return
        model = MODELS[prefs.chat]
        self.api("sendChatAction", chat_id=uid, action="typing")
        time.sleep(answers.TYPING_SECONDS)
        history = self.store.history(uid) if prefs.memory else []
        reply = answers.compose(model, prefs, text, history)
        html = reply.html
        if prefs.show_usage:
            html += "\n\n" + answers.usage_line(reply, used + reply.total)
        # Клавиатура чата — под каждым ответом: если её скрыли, она вернётся
        self.send(uid, html, ui.chat_keyboard(model))
        # Токены и история записываются, только когда ответ дошёл
        before, after = self.store.spend(uid, reply.total)
        if prefs.memory:
            self.store.remember(
                uid,
                Turn("user", text, reply.question_tokens),
                Turn("bot", answers.plain_text(reply.html), reply.output_tokens),
            )
        if prefs.notify_limit:
            for notice in answers.limit_notices(before, after):
                self.send(uid, notice)


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
