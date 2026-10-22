"""Общие ошибки агента."""


class AgentError(Exception):
    """Базовая ошибка модуля, которую API превращает в ответ клиенту."""


class ClientNotFound(AgentError):
    """В справочнике нет такого SYNTHETIC_U."""


class BudgetExceeded(AgentError):
    """Исчерпан лимит шагов, токенов, вызовов или времени."""


class ToolDenied(AgentError):
    """Вызов инструмента вне белого списка или с недопустимым правом."""


class SchemaRejected(AgentError):
    """Ответ модели не прошёл JSON Schema."""
