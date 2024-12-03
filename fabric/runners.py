import signal
import threading
from invoke import Runner, pty_size, Result as InvokeResult

class Remote(Runner):
    """
    Run a shell command over an SSH connection.

    This class subclasses `invoke.runners.Runner`; please see its documentation
    for most public API details.

    .. note::
        `.Remote`'s ``__init__`` method expects a `.Connection` (or subclass)
        instance for its ``context`` argument.

    .. versionadded:: 2.0
    """

    def __init__(self, *args, **kwargs):
        """
        Thin wrapper for superclass' ``__init__``; please see it for details.

        Additional keyword arguments defined here are listed below.

        :param bool inline_env:
            Whether to 'inline' shell env vars as prefixed parameters, instead
            of trying to submit them via `.Channel.update_environment`.
            Default: ``True``.

        .. versionchanged:: 2.3
            Added the ``inline_env`` parameter.
        .. versionchanged:: 3.0
            Changed the default value of ``inline_env`` from ``False`` to
            ``True``.
        """
        self.inline_env = kwargs.pop('inline_env', None)
        super().__init__(*args, **kwargs)

    def handle_window_change(self, signum, frame):
        """
        Respond to a `signal.SIGWINCH` (as a standard signal handler).

        Sends a window resize command via Paramiko channel method.
        """
        if self.context.client and self.context.client.get_transport():
            channel = self.context.client.get_transport().open_session()
            if channel:
                width, height = pty_size()
                channel.resize_pty(width=width, height=height)

class RemoteShell(Remote):
    pass

class Result(InvokeResult):
    """
    An `invoke.runners.Result` exposing which `.Connection` was run against.

    Exposes all attributes from its superclass, then adds a ``.connection``,
    which is simply a reference to the `.Connection` whose method yielded this
    result.

    .. versionadded:: 2.0
    """

    def __init__(self, **kwargs):
        connection = kwargs.pop('connection')
        super().__init__(**kwargs)
        self.connection = connection
