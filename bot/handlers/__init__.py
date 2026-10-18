from aiogram import Router

from . import admin, fallback, payments, sections


def get_routers() -> list[Router]:
    # Порядок важен: fallback должен быть последним, он ловит всё остальное
    return [admin.router, payments.router, sections.router, fallback.router]
