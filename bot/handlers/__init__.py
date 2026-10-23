from aiogram import Router

from . import admin, fallback, payments, sections, support


def get_routers() -> list[Router]:
    # Порядок важен: fallback должен быть последним, он ловит всё остальное.
    # support — сразу после admin: ответ админа реплаем на обращение может начинаться с «/»
    # (например, «/profile — там ваш ключ»), и его не должны перехватить команды разделов.
    # Админские команды идут раньше поддержки намеренно: реплай «/givekey 42 ключ» на
    # обращение должен выполниться, а не уйти клиенту вместе с ключом провайдера
    return [admin.router, support.router, payments.router, sections.router, fallback.router]
