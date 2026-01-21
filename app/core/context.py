import contextvars

_tenant_id: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_id", default="-")
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def set_tenant_id(tenant_id: str) -> None:
    _tenant_id.set(tenant_id)


def get_tenant_id() -> str:
    return _tenant_id.get()


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str:
    return _request_id.get()


def clear_context() -> None:
    _tenant_id.set("-")
    _request_id.set("-")
