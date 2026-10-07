"""Минимальная заглушка python-telegram-bot для тестов без сети."""


class ReplyKeyboardMarkup:
    def __init__(self, keyboard, resize_keyboard=False):
        self.keyboard = keyboard

    def buttons(self):
        return [b for row in self.keyboard for b in row]


class Update:
    pass
