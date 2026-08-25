class DomainError(Exception):
    """Service-layer errors; mapped to HTTP responses by the handler in main.py.

    Keeps policy ("this Seymour is already listed") in the services, where the
    worker can trigger it too, rather than in a router that only HTTP can reach.
    """


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class PermissionDeniedError(DomainError):
    pass


class InvalidInputError(DomainError):
    pass
