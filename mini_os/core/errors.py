from __future__ import annotations


class MiniOSError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class UsageError(MiniOSError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 2)


class PermissionDenied(MiniOSError):
    def __init__(self, target: str) -> None:
        super().__init__(f"{target}: permission denied", 13)


class ExistsErrorOS(MiniOSError):
    def __init__(self, target: str) -> None:
        super().__init__(f"{target}: file exists", 17)


class NoSpace(MiniOSError):
    def __init__(self, target: str = "disk") -> None:
        super().__init__(f"{target}: no space left on device", 28)


class NotEmpty(MiniOSError):
    def __init__(self, target: str) -> None:
        super().__init__(f"{target}: directory not empty", 39)
