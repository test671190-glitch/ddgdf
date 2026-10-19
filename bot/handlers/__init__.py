from aiogram import Router

from . import admin, fallback, payments, sections, support


def get_routers() -> list[Router]:
    # Порядок важен: fallback должен быть последним, он ловит всё остальное
    return [admin.router, payments.router, sections.router, support.router, fallback.router]
