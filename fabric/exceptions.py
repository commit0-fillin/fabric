class NothingToDo(Exception):
    pass

class GroupException(Exception):
    """
    Lightweight exception wrapper for `.GroupResult` when one contains errors.

    .. versionadded:: 2.0
    """

    def __init__(self, result):
        super().__init__("One or more errors occurred during group execution")
        self.result = result

class InvalidV1Env(Exception):
    """
    Raised when attempting to import a Fabric 1 ``env`` which is missing data.
    """
    pass
