"""Обработка апдейтов: сообщения и чат с моделью, кнопки, меню, настройки, оплата,
приглашения по ссылке /start ref_<id>.

Вы всегда в чате: сообщение уходит текущей модели, а если чата ещё нет, он создаётся
сам. Клавиатура внизу — «+ Новый чат», «☰ Мои чаты», «⁂ Модель», «⛭ Меню».

Бот работает только в личных сообщениях: в группе он молчит, чтобы не отвечать
на каждое сообщение участников. Методы вызываются из рабочих потоков tg.Serial,
по одному потоку на пользователя за раз.
"""

import time

from models_bot import answers, log, ui
from models_bot.catalog import (
    CUSTOM_ROLE,
    MODELS,
    NO_ROLE,
    PACKS,
    REF_BONUS,
    REF_PREFIX,
    ROLE_MAX_LENGTH,
    STYLES,
    TOOLS,
    Model,
)
from models_bot.store import (
    DETAILS,
    LANGS,
    TITLE_LENGTH,
    Chat,
    Prefs,
    Referral,
    Store,
    Turn,
    next_value,
)
from models_bot.tg import NETWORK_ERRORS, Api, TelegramError

KEYBOARD_VERSION = 2  # клавиатура внизу; у кого старее — один раз пришлём новую


def _int(text: str) -> int:
    return int(text) if text.isdigit() else 0


class Bot:
    def __init__(self, api: Api, store: Store, username: str = "") -> None:
        self.api = api
        self.store = store
        self.username = username  # имя бота без @: для ссылки-приглашения

    # Отправка

    def send(self, chat_id: int, text: str, markup: dict | None = None) -> int:
        """Отправить сообщение. Возвращает его id."""
        sent = self.api(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            link_preview_options={"is_disabled": True},
        )
        return sent["message_id"]

    def edit(self, chat_id: int, message_id: int, text: str, markup: dict | None = None) -> None:
        self.api(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
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
                self.edit(message["chat"]["id"], message["message_id"], text, markup)
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

    def stream(self, uid: int, html: str, markup: dict, message_id: int | None = None) -> float:
        """Ответ «печатается»: первая часть уходит сразу, дальше — правки того же сообщения,
        а последняя правка ставит разметку и кнопки. message_id — писать в это сообщение.
        Короткий ответ — в две части, длинный — в STREAM_STEPS. Возвращает время первой части."""
        plain = answers.plain_text(html)
        steps = answers.STREAM_STEPS if len(plain) > answers.SHORT_ANSWER else 2
        first = 0.0
        for step in range(1, steps):
            cut = plain.rfind(" ", 0, len(plain) * step // steps)
            if cut <= 0:
                continue
            part = answers.escape(plain[:cut].rstrip()) + ui.CURSOR
            try:
                if message_id is None:
                    message_id = self.send(uid, part)  # первая часть — без задержки
                else:
                    time.sleep(answers.STREAM_DELAY)
                    self.edit(uid, message_id, part)
                first = first or time.monotonic()
            except TelegramError as e:
                log.info("Промежуточная часть ответа не ушла: %s", e)
        if message_id is None:
            self.send(uid, html, markup)
            return first or time.monotonic()
        time.sleep(answers.STREAM_DELAY)
        try:
            self.edit(uid, message_id, html, markup)
        except TelegramError as e:
            if "message is not modified" not in e.description:
                log.info("Ответ не дописан в сообщение, присылаю заново: %s", e)
                self.send(uid, html, markup)
        return first or time.monotonic()

    # Текущий чат

    def own_chat(self, uid: int, chat_id: int) -> Chat | None:
        chat = self.store.chat(chat_id) if chat_id else None
        return chat if chat and chat.user_id == uid else None  # чужой чат — как удалённый

    def current(self, uid: int, prefs: Prefs) -> tuple[Chat | None, Model]:
        """Текущий чат (или None, если его ещё нет) и модель, которая отвечает."""
        chat = self.own_chat(uid, prefs.active)
        model = MODELS.get(chat.model) if chat else None
        if chat and model is None:
            chat = None  # модель чата убрали из каталога — следующее сообщение начнёт новый
        return chat, model or MODELS[prefs.model]

    def last_question(self, chat: Chat) -> str | None:
        return next(
            (t.text for t in reversed(self.store.history(chat.id)) if t.author == "user"), None
        )

    def keyboard(self, uid: int, prefs: Prefs) -> dict:
        _, model = self.current(uid, prefs)
        return ui.keyboard(model)

    # Вход

    def handle(self, update: dict) -> None:
        if query := update.get("callback_query"):
            self.on_button(query)
        elif message := update.get("message"):
            self.on_message(message)
        elif checkout := update.get("pre_checkout_query"):
            self.pre_checkout(checkout)

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
        inviter = self.inviter(uid, message.get("text"))  # до visitor: он запомнит uid
        prefs = self.visitor(uid)
        bonus = self.store.add_referral(inviter, uid, REF_BONUS) if inviter else None
        if payment := message.get("successful_payment"):
            self.on_payment(uid, payment)  # оплату начисляем и заблокированному
            return
        if prefs.banned_until > time.time():
            self.send(uid, ui.banned(prefs.banned_until))
            return
        attachment = answers.attachment_of(message)
        text = message.get("text") or (message.get("caption") if attachment else None)
        command = (
            text.split()[0][1:].split("@")[0].lower()
            if text and text.startswith("/") and not attachment
            else None
        )
        buttons = ui.KEYBOARD_BUTTONS + ui.OLD_BUTTONS
        if prefs.awaiting and (command is not None or text in buttons):
            # Команда или кнопка внизу отменяют ввод роли или названия, а не становятся им
            prefs = self.store.update(uid, awaiting="")
        prefs = self.refresh_keyboard(uid, prefs, command)
        if command is not None:
            self.command(command, uid, user, prefs)
        elif text in buttons:
            self.keyboard_button(text, uid, prefs)
        elif prefs.awaiting.startswith("tool:"):
            self.tool_input(uid, prefs, text, attachment)
        elif prefs.awaiting == "role":
            self.role_input(uid, None if attachment else text)
        elif prefs.awaiting.startswith("rename:"):
            chat_id = _int(prefs.awaiting.partition(":")[2])
            self.rename_input(uid, chat_id, None if attachment else text)
        elif not text and not attachment:
            self.send(uid, ui.ONLY_TEXT)
        else:
            self.ask(uid, prefs, text or "", attachment)
        if bonus is not None:  # после приветствия: сообщение пригласившему его не задержит
            self.tell_inviter(inviter, user, bonus)

    def refresh_keyboard(self, uid: int, prefs: Prefs, command: str | None) -> Prefs:
        """После обновления бота у прежних пользователей на экране старая клавиатура:
        один раз присылаем новую с пояснением. /start пришлёт её и так."""
        if prefs.kb >= KEYBOARD_VERSION:
            return prefs
        prefs = self.store.update(uid, kb=KEYBOARD_VERSION)
        if command != "start" and self.store.chats(uid):
            self.send(uid, ui.KEYBOARD_UPDATED, self.keyboard(uid, prefs))
        return prefs

    def keyboard_button(self, text: str, uid: int, prefs: Prefs) -> None:
        if text in (ui.NEW_CHAT, ui.OLD_NEW_DIALOG):
            self.new_chat(uid, prefs)
        elif text in (ui.MY_CHATS, ui.OLD_CHATS):
            self.send_chats(uid, prefs)
        elif text == ui.MODEL:
            self.send_picker(uid, prefs)
        elif text == ui.MENU:
            self.send_menu(uid, prefs)
        else:  # «■ Завершить чат» со старой клавиатуры
            self.send(uid, ui.NO_CLOSING, self.keyboard(uid, prefs))

    def command(self, command: str, uid: int, user: dict, prefs: Prefs) -> None:
        if command in ("new", "chat"):
            self.new_chat(uid, prefs)
        elif command == "chats":
            self.send_chats(uid, prefs)
        elif command == "model":
            self.send_picker(uid, prefs)
        elif command == "menu":
            self.send_menu(uid, prefs)
        elif command == "tools":
            _, model = self.current(uid, prefs)
            self.send(uid, ui.tools_text(model), ui.tools_keyboard())
        elif command == "models":
            self.send(uid, ui.models_text(), ui.models_keyboard())
        elif command == "other":
            self.send(uid, ui.other_text(), ui.other_keyboard())
        elif command == "settings":
            text = ui.settings_text(prefs, self.store.used_today(uid))
            self.send(uid, text, ui.settings_keyboard(prefs))
        elif command == "balance":
            self.send(
                uid, ui.balance_text(prefs, self.store.used_today(uid)), ui.balance_keyboard()
            )
        elif command in ("invite", "ref"):
            self.send(uid, *self.invite_screen(uid))
        elif command == "help":
            self.send(uid, ui.HELP)
        elif command == "paysupport":
            self.send(uid, ui.PAY_SUPPORT)
        elif command == "stop":
            self.send(uid, ui.NO_CLOSING, self.keyboard(uid, prefs))
        else:  # /start и неизвестные команды: сразу меню
            # Клавиатуру внизу и меню с кнопками в одно сообщение не уложить: сначала строка
            # с клавиатурой (её могли потерять, очистив чат), потом меню
            self.send(uid, ui.welcome(user.get("first_name")), self.keyboard(uid, prefs))
            self.send_menu(uid, prefs)

    def on_button(self, query: dict) -> None:
        message = query.get("message") or {}
        if message.get("chat", {}).get("type", "private") != "private":
            self.toast(query)
            return
        uid = query["from"]["id"]
        prefs = self.visitor(uid)
        if prefs.banned_until > time.time():
            self.toast(query, ui.banned(prefs.banned_until), alert=True)
            return
        if prefs.awaiting:
            # Любая кнопка отменяет ввод роли или названия
            prefs = self.store.update(uid, awaiting="")
        kind, _, arg = (query.get("data") or "").partition(":")
        if kind == "nav" and arg in SCREENS:
            self.toast(query, SCREENS[arg])
            self.screen(query, arg, prefs)
        elif kind in ("card", "pick", "chat"):
            model_id, _, src = arg.partition(":")
            model = MODELS.get(model_id)
            if model is None:
                self.toast(query, "Этой модели больше нет", alert=True)
                self.screen(query, "models", prefs)
            elif kind == "card":
                self.toast(query, model.label)
                self.show_card(query, model, prefs, src)
            elif kind == "pick":
                self.pick_model(query, model, prefs, src)
            else:
                self.toast(query, f"{ui.NEW_CHAT} · {model.name}")
                self.new_chat(uid, prefs, model)
                if src in ("models", "other"):
                    self.show_card(query, model, self.store.get(uid), src)
        elif kind == "use":  # «Сделать основной» из прошлой версии — теперь смена модели
            model_id, _, src = arg.partition(":")
            if model := MODELS.get(model_id):
                self.pick_model(query, model, prefs, "p")
            else:
                self.toast(query, ui.STALE_BUTTON)
        elif kind == "chats":
            page, _, mode = arg.partition(":")
            self.toast(query)
            self.show_chats(query, prefs, _int(page), mode == "e")
        elif kind == "newchat":
            self.toast(query, ui.NEW_CHAT)
            self.new_chat(uid, prefs)
        elif kind in ("open", "info", "ren", "del", "delok"):
            chat_id, _, src = arg.partition(":")
            chat = self.own_chat(uid, _int(chat_id))
            if chat is None:
                self.toast(query, ui.CHAT_GONE, alert=True)
                self.show_chats(query, prefs, 0, kind != "open")
            else:
                self.chat_action(query, kind, chat, prefs, src)
        elif kind in ("ans", "ansm"):
            reply_id, _, action = arg.partition(":")
            answer = self.store.reply(_int(reply_id))
            if answer is None or answer.user_id != uid:
                self.toast(query, "Этот ответ уже не изменить", alert=True)
            else:
                self.answer_action(query, answer, kind, action, prefs)
        elif kind == "buy" and arg in PACKS:
            self.toast(query)
            self.api("sendInvoice", chat_id=uid, **ui.invoice(PACKS[arg]))
        elif kind == "tgl" and arg in ui.TOGGLES:
            self.toggle(query, arg, prefs)
        elif kind == "role":
            self.set_role(query, arg)
        elif kind == "tool" and arg in TOOLS:
            self.choose_tool(query, arg)
        elif kind == "style" and arg in STYLES:
            self.store.update(uid, awaiting=f"tool:styles:{arg}")
            self.toast(query, STYLES[arg].capitalize())
            self.show(query, ui.tool_ask_text("styles", arg), ui.tool_ask_keyboard())
        elif kind == "dlg":  # «Новый диалог» под ответами самой первой версии
            self.toast(query)
            self.new_chat(uid, prefs)
        else:
            self.toast(query, ui.STALE_BUTTON)
            self.screen(query, "main", prefs)

    # Экраны

    def screen(self, query: dict, name: str, prefs: Prefs) -> None:
        uid = query["from"]["id"]
        chat, model = self.current(uid, prefs)
        if name == "main":
            text, markup = ui.menu_text(model, chat, prefs), ui.menu_keyboard()
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
        elif name == "balance":
            text = ui.balance_text(prefs, self.store.used_today(uid))
            markup = ui.balance_keyboard()
        elif name == "picker":
            text, markup = ui.picker_text(model), ui.picker_keyboard(model.id)
        elif name == "role":
            text, markup = ui.role_text(prefs), ui.role_keyboard(prefs)
        elif name == "tools":
            text, markup = ui.tools_text(model), ui.tools_keyboard()
        elif name == "invite":
            text, markup = self.invite_screen(uid)
        else:
            text, markup = ui.notify_text(), ui.notify_keyboard(prefs)
        self.show(query, text, markup)

    def send_menu(self, uid: int, prefs: Prefs) -> None:
        chat, model = self.current(uid, prefs)
        self.send(uid, ui.menu_text(model, chat, prefs), ui.menu_keyboard())

    def send_picker(self, uid: int, prefs: Prefs) -> None:
        _, model = self.current(uid, prefs)
        self.send(uid, ui.picker_text(model), ui.picker_keyboard(model.id))

    def show_card(self, query: dict, model: Model, prefs: Prefs, src: str) -> None:
        _, current = self.current(query["from"]["id"], prefs)
        self.show(query, ui.model_card(model, current.id), ui.card_keyboard(model, src, current.id))

    def chats_screen(self, uid: int, prefs: Prefs, page: int, edit: bool) -> tuple[str, dict]:
        chats = [c for c in self.store.chats(uid) if c.messages or c.id == prefs.active]
        active, _ = self.current(uid, prefs)
        return (
            ui.chats_text(chats, active, edit),
            ui.chats_keyboard(chats, active.id if active else 0, page, edit),
        )

    def show_chats(self, query: dict, prefs: Prefs, page: int, edit: bool) -> None:
        self.show(query, *self.chats_screen(query["from"]["id"], prefs, page, edit))

    def send_chats(self, uid: int, prefs: Prefs) -> None:
        self.send(uid, *self.chats_screen(uid, prefs, 0, False))

    # Модель и чаты

    def pick_model(self, query: dict, model: Model, prefs: Prefs, src: str) -> None:
        """Сменить модель текущего чата: переписка остаётся, дальше отвечает новая модель."""
        uid = query["from"]["id"]
        chat, current = self.current(uid, prefs)
        if current.id == model.id:
            self.toast(query, f"✓ Уже отвечает {model.name}")
            return
        if chat:
            self.store.set_chat_model(chat.id, model.id)
        prefs = self.store.update(uid, model=model.id)
        self.toast(query, f"✓ Теперь отвечает {model.name}")
        if src == "p":
            self.show(query, ui.picker_text(model), ui.picker_keyboard(model.id))
        else:
            self.show_card(query, model, prefs, src)
        # Новая клавиатура: в поле ввода — новая модель
        self.send(uid, ui.model_switched(model, chat), ui.keyboard(model))

    def new_chat(self, uid: int, prefs: Prefs, model: Model | None = None) -> None:
        """Начать новый чат; прошлый остаётся в «Мои чаты». model — с другой моделью."""
        chat, current = self.current(uid, prefs)
        model = model or current
        if chat and chat.messages == 0:
            # Текущий чат и так пустой — новый не плодим, только меняем модель, если просили
            if chat.model != model.id:
                self.store.set_chat_model(chat.id, model.id)
            self.store.update(uid, model=model.id)
            self.send(uid, ui.already_new(model), ui.keyboard(model))
            return
        new, dropped = self.store.new_chat(uid, model.id)
        self.store.update(uid, active=new.id, model=model.id)
        self.send(uid, ui.new_chat_text(model, chat, dropped), ui.keyboard(model))

    def chat_action(self, query: dict, kind: str, chat: Chat, prefs: Prefs, src: str) -> None:
        """src у «open»: l — нажато в списке, i — в карточке чата."""
        uid = query["from"]["id"]
        if kind == "open":
            model = MODELS.get(chat.model)
            if model is None:
                self.toast(query, "Модель этого чата больше недоступна", alert=True)
                return
            self.toast(query, f"▸ {ui.chat_name(chat)}")
            previous, _ = self.current(uid, prefs)
            if previous and previous.messages == 0 and previous.id != chat.id:
                self.store.delete_chat(previous.id)  # пустой чат хранить незачем
            prefs = self.store.update(uid, active=chat.id, model=chat.model)
            last = self.last_question(chat)
            if src == "l":  # в списке галочка переезжает на этот чат
                self.show_chats(query, prefs, 0, False)
            elif src == "i":
                self.show(
                    query,
                    ui.chat_info_text(chat, chat.id, last),
                    ui.chat_info_keyboard(chat, chat.id),
                )
            self.send(uid, ui.chat_opened(chat, model, last, prefs), ui.keyboard(model))
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
                self.send(uid, ui.chat_deleted(chat), self.keyboard(uid, prefs))
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

    # Настройки

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
        """Роль — только своя: написать, изменить или сбросить."""
        uid = query["from"]["id"]
        if key == CUSTOM_ROLE:
            self.store.update(uid, awaiting="role")
            self.toast(query, "✎ Своя роль")
            self.show(query, ui.role_input_text(), ui.role_input_keyboard())
            return
        if key == "reset":
            prefs = self.store.update(uid, role=NO_ROLE, role_text="")
            self.toast(query, ui.ROLE_RESET)
        else:  # кнопка готовой роли из прошлой версии
            prefs = self.store.get(uid)
            self.toast(query, ui.STALE_BUTTON)
        self.screen(query, "role", prefs)

    # Инструменты

    def choose_tool(self, query: dict, key: str) -> None:
        tool = TOOLS[key]
        self.toast(query, f"{tool.mark} {tool.title}")
        if key == "styles":  # сначала стиль, потом фото
            self.show(query, ui.styles_text(), ui.styles_keyboard())
            return
        self.store.update(query["from"]["id"], awaiting=f"tool:{key}")
        self.show(query, ui.tool_ask_text(key), ui.tool_ask_keyboard())

    def tool_input(
        self, uid: int, prefs: Prefs, text: str | None, attachment: answers.Attachment | None
    ) -> None:
        """Сообщение для выбранного инструмента: он отвечает в текущем чате один раз."""
        _, key, style = (prefs.awaiting.split(":") + [""])[:3]
        needs = TOOLS[key].needs
        if needs == "photo":
            fits = attachment is not None and attachment.kind == "image"
        elif needs == "text":
            fits = bool(text) and attachment is None
        else:  # текст или документ
            fits = bool(text) or (attachment is not None and attachment.kind == "file")
        if not fits:
            self.send(uid, ui.tool_needs(key), ui.tool_ask_keyboard())
            return
        prefs = self.store.update(uid, awaiting="")
        self.ask(uid, prefs, text or "", attachment, tool=f"{key}:{style}" if style else key)

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

    # Ответ модели

    def out_of_tokens(self, uid: int, prefs: Prefs) -> bool:
        """Нет ни бесплатных токенов на сегодня, ни купленных: отвечать нечем."""
        if answers.can_answer(self.store.used_today(uid), prefs.balance):
            return False
        self.send(uid, answers.limit_reached(), ui.buy_keyboard())
        return True

    def finish(self, prefs: Prefs, reply: answers.Reply, html: str, used: int) -> str:
        """Ответ готов: добавить строку расхода. Возвращает HTML для отправки."""
        if prefs.show_usage:
            balance = max(0, prefs.balance - answers.paid_part(used, used + reply.total))
            html += "\n\n" + answers.usage_line(reply, used + reply.total, balance)
        return html

    def pay(self, uid: int, prefs: Prefs, total: int) -> None:
        """Списать токены за доставленный ответ: сначала бесплатные, сверх них — баланс."""
        before, after = self.store.spend(uid, total)
        paid = answers.paid_part(before, after)
        balance = self.store.charge(uid, paid) if paid else prefs.balance
        if prefs.notify_limit:
            for notice in answers.limit_notices(before, after, balance, paid):
                markup = None if balance else ui.buy_keyboard()
                self.send(uid, notice, markup)

    def ask(
        self,
        uid: int,
        prefs: Prefs,
        text: str,
        attachment: answers.Attachment | None,
        tool: str | None = None,
    ) -> None:
        """Сообщение модели в текущий чат. Чата нет — он начинается сам.
        tool — инструмент, которым ответить («brainstorm», «styles:anime»)."""
        started_at = time.monotonic()
        chat, model = self.current(uid, prefs)
        if attachment and attachment.kind not in model.inputs:
            self.send(uid, ui.cant_read(model, attachment.kind))
            return
        used = self.store.used_today(uid)
        if not answers.can_answer(used, prefs.balance):
            self.send(uid, answers.limit_reached(), ui.buy_keyboard())
            return
        started = chat is None
        if started:
            chat, _ = self.store.new_chat(uid, model.id)
            prefs = self.store.update(uid, active=chat.id)
        history = self.store.history(chat.id) if prefs.memory else []
        reply = answers.compose(model, prefs, text, history, attachment=attachment, tool=tool)
        packed = answers.pack_attachment(attachment)
        reply_id = self.store.add_reply(uid, chat.id, text, model.id, packed, tool)
        html = self.finish(prefs, reply, reply.html, used)
        if started:
            html = f"<i>{ui.NEW_CHAT}</i>\n" + html
        first = self.stream(uid, html, ui.answer_keyboard(reply_id))
        log.info(
            "Ответ %s: первая часть через %.2f с, весь — через %.2f с",
            model.id,
            first - started_at,
            time.monotonic() - started_at,
        )
        # Токены и история записываются, только когда ответ дошёл
        self.pay(uid, prefs, reply.total)
        label = f"[{TOOLS[tool.partition(':')[0]].title}] " if tool else ""
        about = f"[{attachment.about}] " if attachment else ""
        asked = f"{label}{about}{text}".strip()
        self.store.note_question(chat.id, asked)
        if prefs.memory:
            self.store.remember(
                chat.id,
                uid,
                Turn("user", asked, reply.question_tokens),
                Turn("bot", answers.plain_text(reply.html), reply.output_tokens),
            )

    # Кнопки под ответом

    def answer_action(self, query: dict, answer, kind: str, action: str, prefs: Prefs) -> None:
        uid = query["from"]["id"]
        message = query.get("message") or {}
        if kind == "ans" and action in ("m", "back"):
            self.toast(query)
            markup = (
                ui.other_model_keyboard(answer.id, answer.model)
                if action == "m"
                else ui.answer_keyboard(answer.id)
            )
            if message.get("date"):
                try:
                    self.api(
                        "editMessageReplyMarkup",
                        chat_id=uid,
                        message_id=message["message_id"],
                        reply_markup=markup,
                    )
                except TelegramError as e:
                    log.info("Кнопки под ответом не обновились: %s", e)
            return
        model_id = action if kind == "ansm" else answer.model
        model = MODELS.get(model_id)
        if model is None or (kind == "ans" and action not in ("r", "s")):
            self.toast(query, "Этой модели больше нет", alert=True)
            return
        attachment = answers.unpack_attachment(answer.attachment)
        if attachment and attachment.kind not in model.inputs:
            self.toast(query, f"{model.name} не понимает {ui.INPUTS[attachment.kind]}", alert=True)
            return
        if not answers.can_answer(self.store.used_today(uid), prefs.balance):
            self.toast(query, "Токены на сегодня закончились", alert=True)
            self.send(uid, answers.limit_reached(), ui.buy_keyboard())
            return
        self.toast(query, {"r": ui.REGENERATE, "s": ui.SHORTER}.get(action, f"⇄ {model.name}"))
        chat = self.own_chat(uid, answer.chat_id)
        latest = chat is not None and self.store.last_reply_id(chat.id) == answer.id
        history = self.store.history(chat.id) if chat and prefs.memory else []
        if latest and history:
            history = history[:-2]  # этот вопрос и прошлый ответ на него — не контекст
        attempt = answer.attempt + 1 if action == "r" else answer.attempt
        reply = answers.compose(
            model,
            prefs,
            answer.question,
            history,
            attempt=attempt,
            brief=action == "s",
            attachment=attachment,
            tool=answer.tool,
        )
        used = self.store.used_today(uid)
        html = self.finish(prefs, reply, reply.html, used)
        message_id = message["message_id"] if message.get("date") else None
        self.stream(uid, html, ui.answer_keyboard(answer.id), message_id)
        self.pay(uid, prefs, reply.total)
        self.store.update_reply(answer.id, attempt=attempt, model=model.id)
        if latest and prefs.memory:
            self.store.replace_last_answer(
                chat.id, answers.plain_text(reply.html), reply.output_tokens
            )

    # Приглашения

    def invite_screen(self, uid: int) -> tuple[str, dict]:
        if not self.username:
            self.username = self.api("getMe")["username"]
        link = ui.ref_link(self.username, uid)
        return ui.invite_text(link, *self.store.referrals(uid)), ui.invite_keyboard(link)

    def inviter(self, uid: int, text: str | None) -> int:
        """Кто пригласил: /start ref_<id> от того, кого бот ещё не видел. 0 — не засчитано:
        уже был в боте, пригласил сам себя или такого пригласившего нет."""
        parts = (text or "").split()
        if (
            len(parts) != 2
            or parts[0].split("@")[0].lower() != "/start"
            or not parts[1].startswith(REF_PREFIX)
        ):
            return 0
        inviter = _int(parts[1][len(REF_PREFIX) :])
        if inviter == uid or not self.store.exists(inviter) or self.store.exists(uid):
            return 0
        return inviter

    def tell_inviter(self, inviter: int, invitee: dict, result: Referral) -> None:
        """Бонус уже на балансе пригласившего (или он только что заблокирован за накрутку):
        сообщить ему, если он не заблокировал бота. Пока блокировка идёт — молчим."""
        log.info(
            "Приглашение: %s пришёл по ссылке %s, +%s%s",
            invitee["id"],
            inviter,
            result.credited,
            " — накрутка, блокировка" if result.banned else "",
        )
        prefs = self.store.get(inviter)
        if prefs.blocked or not (result.credited or result.banned):
            return
        text = (
            ui.referral_banned(prefs.banned_until)
            if result.banned
            else ui.referral_joined(invitee.get("first_name"), result.credited, result.balance)
        )
        try:
            self.send(inviter, text)
        except TelegramError as e:
            if e.code == 403:  # бота заблокировали: бонус начислен, сообщение не нужно
                self.store.update(inviter, blocked=True)
            log.info("Сообщение о приглашении не дошло до %s: %s", inviter, e)
        except NETWORK_ERRORS as e:
            log.info("Сообщение о приглашении не дошло до %s: %r", inviter, e)

    # Оплата Stars

    def pre_checkout(self, checkout: dict) -> None:
        """Telegram спрашивает, можно ли провести оплату: ответить нужно за 10 секунд."""
        kind, _, key = (checkout.get("invoice_payload") or "").partition(":")
        pack = PACKS.get(key) if kind == "pack" else None
        ok = (
            pack is not None
            and checkout.get("currency") == "XTR"
            and checkout.get("total_amount") == pack.stars
        )
        self.api(
            "answerPreCheckoutQuery",
            pre_checkout_query_id=checkout["id"],
            ok=ok,
            error_message=None if ok else "Этот счёт устарел, откройте «◈ Мой баланс» заново.",
        )

    def on_payment(self, uid: int, payment: dict) -> None:
        kind, _, key = (payment.get("invoice_payload") or "").partition(":")
        pack = PACKS.get(key) if kind == "pack" else None
        charge_id = payment.get("telegram_payment_charge_id", "")
        if pack is None or payment.get("currency") != "XTR":
            log.error("Оплата %s от %s с неизвестным счётом", charge_id, uid)
            self.send(uid, ui.PAY_SUPPORT)
            return
        balance = self.store.credit(
            charge_id, uid, pack.key, payment.get("total_amount", 0), pack.tokens
        )
        if balance is None:
            log.warning("Оплата %s уже начислена", charge_id)
            return
        log.info("Оплата %s: %s Stars от %s", charge_id, payment.get("total_amount"), uid)
        self.send(uid, ui.paid(pack.tokens, balance))


# Экран → всплывающая подпись при переходе
SCREENS = {
    "main": "← Меню",
    "models": ui.MODELS_TITLE,
    "other": "→ Остальные модели",
    "chats": ui.MY_CHATS,
    "settings": ui.SETTINGS,
    "picker": ui.MODEL,
    "role": "⛭ Роль",
    "notify": "⛭ Уведомления",
    "balance": ui.BALANCE,
    "tools": ui.TOOLS_TITLE,
    "invite": ui.INVITE,
}
