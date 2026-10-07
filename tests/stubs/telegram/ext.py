class ApplicationHandlerStop(Exception):
    pass


class ContextTypes:
    DEFAULT_TYPE = object


class _Any:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return _Any()

    def __call__(self, *a, **k):
        return _Any()

    def __and__(self, other):
        return self

    def __invert__(self):
        return self


Application = _Any()
CommandHandler = MessageHandler = TypeHandler = _Any
filters = _Any()
