"""Обработка апдейтов: команды, кнопки, чаты, ввод своей роли и названия чата.

Бот работает только в личных сообщениях: в группе он молчит, чтобы не отвечать
на каждое сообщение участников. Методы вызываются из рабочих потоков tg.Serial,
по одному потоку на пользователя за раз.
"""

import time

from models_bot import answers, log, ui
from models_bot.catalog import CUSTOM_ROLE, MODELS, ROLE_MAX_LENGTH, ROLES, Model
from models_bot.store import (
    DETAILS,
    LANGS,
    TITLE_LENGTH,
    Chat,
    Prefs,
    Store,
    Turn,
    next_value,
)
from models_bot.tg import Api, TelegramError


def _int(text: str) -> int:
    return int(text) if text.isdigit() else 0


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

    # Чаты пользователя

    def own_chat(self, uid: int, chat_id: int) -> Chat | None:
        chat = self.store.chat(chat_id) if chat_id else None
        return chat if chat and chat.user_id == uid else None  # чужой чат — как удалённый

    def open_chat_of(self, uid: int, prefs: Prefs) -> tuple[Chat | None, Model | None]:
        """Открытый чат и его модель. Если чат удалён или его модель убрали — чат закрыт."""
        chat = self.own_chat(uid, prefs.active)
        model = MODELS.get(chat.model) if chat else None
        if prefs.active and model is None:
            self.store.update(uid, active=0)
            return None, None
        return chat, model

    def last_question(self, chat: Chat) -> str | None:
        return next(
            (t.text for t in reversed(self.store.history(chat.id)) if t.author == "user"), None
        )

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
        if prefs.awaiting and ((text or "").startswith("/") or text in ui.CHAT_BUTTONS):
            # Команда или кнопка чата отменяют ввод роли или названия, а не становятся им
            prefs = self.store.update(uid, awaiting="")
        if text and text.startswith("/"):
            command = text.split()[0][1:].split("@")[0].lower()
            self.command(command, uid, user, prefs)
        elif text == ui.CHATS:
            self.send_chats(uid, prefs)
        elif text == ui.NEW_DIALOG:
            self.new_dialog(uid, prefs)
        elif text == ui.END_CHAT:
            self.end_chat(uid, user, prefs)
        elif prefs.awaiting == "role":
            self.role_input(uid, text)
        elif prefs.awaiting.startswith("rename:"):
            self.rename_input(uid, _int(prefs.awaiting.partition(":")[2]), text)
        elif not text:
            self.send(uid, ui.ONLY_TEXT)
        else:
            chat, model = self.open_chat_of(uid, prefs)
            if chat:
                self.chat(uid, prefs, chat, model, text)
            else:
                chats = self.store.chats(uid)
                last = chats[0] if chats else None
                self.send(uid, ui.no_chat_text(), ui.no_chat_keyboard(prefs, last))

    def command(self, command: str, uid: int, user: dict, prefs: Prefs) -> None:
        if command == "models":
            self.send(uid, ui.models_text(), ui.models_keyboard())
        elif command == "other":
            self.send(uid, ui.other_text(), ui.other_keyboard())
        elif command == "chats":
            self.send_chats(uid, prefs)
        elif command == "chat":
            self.chat_with(uid, MODELS[prefs.model], prefs)
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
            chat, _ = self.open_chat_of(uid, prefs)
            self.send(uid, ui.welcome(user.get("first_name"), prefs, chat), ui.main_keyboard())

    def on_button(self, query: dict) -> None:
        message = query.get("message") or {}
        if message.get("chat", {}).get("type", "private") != "private":
            self.toast(query)
            return
        uid = query["from"]["id"]
        prefs = self.visitor(uid)
        if prefs.awaiting:
            # Любая кнопка отменяет ввод роли или названия
            prefs = self.store.update(uid, awaiting="")
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
                self.show_card(query, model, prefs, src)
            elif kind == "use":
                self.use_model(query, model, prefs, src)
            else:
                self.chat_button(query, model, prefs, src)
        elif kind == "chats":
            page, _, mode = arg.partition(":")
            self.toast(query)
            self.show_chats(query, prefs, _int(page), mode == "e")
        elif kind == "newchat":
            self.toast(query, f"▸ Новый чат с {MODELS[prefs.model].name}")
            self.start_chat(uid, MODELS[prefs.model], prefs)
        elif kind in ("open", "info", "ren", "del", "delok"):
            chat_id, _, src = arg.partition(":")
            chat = self.own_chat(uid, _int(chat_id))
            if chat is None:
                self.toast(query, ui.CHAT_GONE, alert=True)
                self.show_chats(query, prefs, 0, kind != "open")
            else:
                self.chat_action(query, kind, chat, prefs, src)
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
            chat, _ = self.open_chat_of(uid, prefs)
            text = ui.welcome(query["from"].get("first_name"), prefs, chat)
            markup = ui.main_keyboard()
        elif name == "models":
            text, markup = ui.models_text(), ui.models_keyboard()
        elif name == "other":
            text, markup = ui.other_text(), ui.other_keyboard()
        elif name == "chats":
            self.show_chats(query, prefs, 0, False)
            return
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

    def show_card(self, query: dict, model: Model, prefs: Prefs, src: str) -> None:
        chat, _ = self.open_chat_of(query["from"]["id"], prefs)
        open_model = chat.model if chat else ""
        self.show(
            query,
            ui.model_card(model, prefs, open_model),
            ui.card_keyboard(model, prefs, src, open_model),
        )

    def chats_screen(self, uid: int, prefs: Prefs, page: int, edit: bool) -> tuple[str, dict]:
        chats = self.store.chats(uid)
        active, _ = self.open_chat_of(uid, prefs)
        return (
            ui.chats_text(chats, active, edit),
            ui.chats_keyboard(chats, active.id if active else 0, page, edit, prefs),
        )

    def show_chats(self, query: dict, prefs: Prefs, page: int, edit: bool) -> None:
        self.show(query, *self.chats_screen(query["from"]["id"], prefs, page, edit))

    def send_chats(self, uid: int, prefs: Prefs) -> None:
        self.send(uid, *self.chats_screen(uid, prefs, 0, False))

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
            self.show_card(query, model, prefs, src)

    def toggle(self, query: dict, key: str, prefs: Prefs) -> None:
        if key == "lang":
            value = next_value(LANGS, prefs.lang)
        elif key == "detail":
            value = next_value(DETAILS, prefs.detail)
        else:
            value = not getattr(prefs, key)
        prefs = self.store.update(query["from"]["id"], **{key: value})
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

    # Список чатов: перейти, переименовать, удалить

    def chat_action(self, query: dict, kind: str, chat: Chat, prefs: Prefs, src: str) -> None:
        """src у «open»: l — нажато в списке, i — в карточке чата, пусто — под подсказкой."""
        uid = query["from"]["id"]
        if kind == "open":
            model = MODELS.get(chat.model)
            if model is None:
                self.toast(query, "Модель этого чата больше недоступна", alert=True)
                return
            self.toast(query, f"▸ {ui.chat_name(chat)}")
            prefs = self.store.update(uid, active=chat.id)
            last = self.last_question(chat)
            if src == "l":  # в списке галочка переезжает на этот чат
                self.show_chats(query, prefs, 0, False)
            elif src == "i":
                self.show(
                    query,
                    ui.chat_info_text(chat, chat.id, last),
                    ui.chat_info_keyboard(chat, chat.id),
                )
            self.send(uid, ui.chat_opened(chat, model, last, prefs), ui.chat_keyboard(model))
        elif kind == "info":
            self.toast(query)
            self.show(
                query,
                ui.chat_info_text(chat, prefs.active, self.last_question(chat)),
                ui.chat_info_keyboard(chat, prefs.active),
            )
        elif kind == "ren":
            self.store.update(uid, awaiting=f"rename:{chat.id}")
            self.toast(query, "✎ Переименовать")
            self.show(query, ui.rename_text(chat), ui.rename_keyboard(chat))
        elif kind == "del":
            self.toast(query)
            self.show(query, ui.delete_text(chat), ui.delete_keyboard(chat))
        else:  # delok — удаление подтверждено
            self.store.delete_chat(chat.id)
            self.toast(query, "✕ Чат удалён")
            if prefs.active == chat.id:
                prefs = self.store.update(uid, active=0)
                self.show_chats(query, prefs, 0, True)
                # Клавиатура внизу была от этого чата — убираем её
                self.send(uid, ui.chat_deleted(chat), ui.REMOVE_KEYBOARD)
            else:
                self.show_chats(query, prefs, 0, True)

    def rename_input(self, uid: int, chat_id: int, text: str | None) -> None:
        chat = self.own_chat(uid, chat_id)
        if chat is None:
            self.store.update(uid, awaiting="")
            self.send(uid, ui.CHAT_GONE)
            return
        if not text:
            self.send(uid, ui.TITLE_ONLY_TEXT, ui.rename_keyboard(chat))
            return
        title = " ".join(text.split())
        if len(title) > TITLE_LENGTH:
            self.send(uid, ui.title_too_long(len(title)), ui.rename_keyboard(chat))
            return
        self.store.rename_chat(chat.id, title)
        prefs = self.store.update(uid, awaiting="")
        chat = chat._replace(title=title)
        self.send(
            uid,
            f"{ui.TITLE_SAVED}\n\n"
            + ui.chat_info_text(chat, prefs.active, self.last_question(chat)),
            ui.chat_info_keyboard(chat, prefs.active),
        )

    # Чат

    def chat_button(self, query: dict, model: Model, prefs: Prefs, src: str) -> None:
        uid = query["from"]["id"]
        self.toast(query, f"▸ Чат с {model.name}")
        if src not in ("models", "other"):
            self.chat_with(uid, model, prefs)  # кнопка под подсказкой «чат не открыт»
            return
        # Кнопка в карточке начинает новый чат, даже если с этой моделью уже есть открытый.
        # Карточку обновляем: на ней видно, что чат открыт
        prefs, started = self.new_chat(uid, model, prefs)
        self.show_card(query, model, prefs, src)
        self.send(uid, started, ui.chat_keyboard(model))

    def chat_with(self, uid: int, model: Model, prefs: Prefs) -> None:
        """/chat и «▸ Новый чат с …» под подсказкой: открытый чат с той же моделью
        не дублируем — просто напоминаем, что он открыт."""
        chat, open_model = self.open_chat_of(uid, prefs)
        if chat and open_model.id == model.id:
            self.send(uid, ui.chat_already_open(chat, model), ui.chat_keyboard(model))
        else:
            self.start_chat(uid, model, prefs)

    def start_chat(self, uid: int, model: Model, prefs: Prefs) -> None:
        _, started = self.new_chat(uid, model, prefs)
        self.send(uid, started, ui.chat_keyboard(model))

    def new_chat(self, uid: int, model: Model, prefs: Prefs) -> tuple[Prefs, str]:
        """Новый чат становится открытым; прошлый остаётся в списке, если в нём что-то было.
        Возвращает настройки и текст сообщения о начале чата."""
        previous, _ = self.open_chat_of(uid, prefs)
        if previous and previous.messages == 0:
            self.store.delete_chat(previous.id)  # пустой чат хранить незачем
            previous = None
        chat, dropped = self.store.new_chat(uid, model.id)
        prefs = self.store.update(uid, active=chat.id)
        return prefs, ui.chat_started(model, prefs, previous, dropped)

    def new_dialog(self, uid: int, prefs: Prefs) -> None:
        """«↺ Новый диалог»: ещё один чат с той же моделью, прошлый сохраняется."""
        chat, model = self.open_chat_of(uid, prefs)
        if chat is None:
            self.send(uid, ui.NO_CHAT_TO_CLOSE, ui.REMOVE_KEYBOARD)
        elif chat.messages == 0:
            self.send(uid, ui.chat_already_open(chat, model), ui.chat_keyboard(model))
        else:
            self.start_chat(uid, model, prefs)

    def end_chat(self, uid: int, user: dict, prefs: Prefs) -> None:
        chat, _ = self.open_chat_of(uid, prefs)
        if chat is None:
            self.send(uid, ui.NO_CHAT_TO_CLOSE, ui.REMOVE_KEYBOARD)
            return
        prefs = self.store.update(uid, active=0)
        kept = chat.messages > 0
        if not kept:
            self.store.delete_chat(chat.id)
        self.send(uid, ui.chat_ended(chat, kept), ui.REMOVE_KEYBOARD)
        self.send(uid, ui.welcome(user.get("first_name"), prefs), ui.main_keyboard())

    def chat(self, uid: int, prefs: Prefs, chat: Chat, model: Model, text: str) -> None:
        used = self.store.used_today(uid)
        if used >= answers.TEST_DAILY_LIMIT:
            self.send(uid, answers.limit_reached())
            return
        self.api("sendChatAction", chat_id=uid, action="typing")
        time.sleep(answers.TYPING_SECONDS)
        history = self.store.history(chat.id) if prefs.memory else []
        reply = answers.compose(model, prefs, text, history)
        html = reply.html
        if prefs.show_usage:
            html += "\n\n" + answers.usage_line(reply, used + reply.total)
        # Клавиатура чата — под каждым ответом: если её скрыли, она вернётся
        self.send(uid, html, ui.chat_keyboard(model))
        # Токены и история записываются, только когда ответ дошёл
        before, after = self.store.spend(uid, reply.total)
        self.store.note_question(chat.id, text)
        if prefs.memory:
            self.store.remember(
                chat.id,
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
    "chats": ui.MY_CHATS,
    "settings": ui.SETTINGS,
    "picker": "⛭ Основная модель",
    "role": "⛭ Роль",
    "notify": "⛭ Уведомления",
}
