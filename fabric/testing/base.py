"""
This module contains helpers/fixtures to assist in testing Fabric-driven code.

It is not intended for production use, and pulls in some test-oriented
dependencies as needed. You can install an 'extra' variant of Fabric to get
these dependencies if you aren't already using them for your own testing
purposes: ``pip install fabric[testing]``.

.. note::
    If you're using pytest for your test suite, you may be interested in
    grabbing ``fabric[pytest]`` instead, which encompasses the dependencies of
    both this module and the `fabric.testing.fixtures` module, which contains
    pytest fixtures.

.. versionadded:: 2.1
"""
import os
from itertools import chain, repeat
from io import BytesIO
from unittest.mock import Mock, PropertyMock, call, patch, ANY
from deprecated.sphinx import deprecated
from deprecated.classic import deprecated as deprecated_no_docstring

class Command:
    """
    Data record specifying params of a command execution to mock/expect.

    :param str cmd:
        Command string to expect. If not given, no expectations about the
        command executed will be set up. Default: ``None``.

    :param bytes out: Data yielded as remote stdout. Default: ``b""``.

    :param bytes err: Data yielded as remote stderr. Default: ``b""``.

    :param int exit: Remote exit code. Default: ``0``.

    :param int waits:
        Number of calls to the channel's ``exit_status_ready`` that should
        return ``False`` before it then returns ``True``. Default: ``0``
        (``exit_status_ready`` will return ``True`` immediately).

    .. versionadded:: 2.1
    """

    def __init__(self, cmd=None, out=b'', err=b'', in_=None, exit=0, waits=0):
        self.cmd = cmd
        self.out = out
        self.err = err
        self.in_ = in_
        self.exit = exit
        self.waits = waits

    def __repr__(self):
        return '<{} cmd={!r}>'.format(self.__class__.__name__, self.cmd)

    def expect_execution(self, channel):
        """
        Assert that the ``channel`` was used to run this command.

        .. versionadded:: 2.7
        """
        if self.cmd is not None:
            channel.exec_command.assert_called_with(self.cmd)
        if self.in_ is not None:
            channel.sendall.assert_called_with(self.in_)

class ShellCommand(Command):
    """
    A pseudo-command that expects an interactive shell to be executed.

    .. versionadded:: 2.7
    """

class MockChannel(Mock):
    """
    Mock subclass that tracks state for its ``recv(_stderr)?`` methods.

    Turns out abusing function closures inside MockRemote to track this state
    only worked for 1 command per session!

    .. versionadded:: 2.1
    """

    def __init__(self, *args, **kwargs):
        object.__setattr__(self, '__stdout', kwargs.pop('stdout'))
        object.__setattr__(self, '__stderr', kwargs.pop('stderr'))
        object.__setattr__(self, '_stdin', BytesIO())
        super().__init__(*args, **kwargs)

class Session:
    """
    A mock remote session of a single connection and 1 or more command execs.

    Allows quick configuration of expected remote state, and also helps
    generate the necessary test mocks used by `MockRemote` itself. Only useful
    when handed into `MockRemote`.

    The parameters ``cmd``, ``out``, ``err``, ``exit`` and ``waits`` are all
    shorthand for the same constructor arguments for a single anonymous
    `.Command`; see `.Command` for details.

    To give fully explicit `.Command` objects, use the ``commands`` parameter.

    :param str user:
    :param str host:
    :param int port:
        Sets up expectations that a connection will be generated to the given
        user, host and/or port. If ``None`` (default), no expectations are
        generated / any value is accepted.

    :param commands:
        Iterable of `.Command` objects, used when mocking nontrivial sessions
        involving >1 command execution per host. Default: ``None``.

        .. note::
            Giving ``cmd``, ``out`` etc alongside explicit ``commands`` is not
            allowed and will result in an error.

    :param bool enable_sftp: Whether to enable basic SFTP mocking support.

    :param transfers:
        None if no transfers to expect; otherwise, should be a list of dicts of
        the form ``{"method": "get|put", **kwargs}`` where ``**kwargs`` are the
        kwargs expected in the relevant `~paramiko.sftp_client.SFTPClient`
        method. (eg: ``{"method": "put", "localpath": "/some/path"}``)

    .. versionadded:: 2.1
    .. versionchanged:: 3.2
        Added the ``enable_sftp`` and ``transfers`` parameters.
    """

    def __init__(self, host=None, user=None, port=None, commands=None, cmd=None, out=None, in_=None, err=None, exit=None, waits=None, enable_sftp=False, transfers=None):
        params = cmd or out or err or exit or waits
        if commands and params:
            raise ValueError("You can't give both 'commands' and individual Command parameters!")
        self.guard_only = not (commands or cmd or transfers)
        self.host = host
        self.user = user
        self.port = port
        self.commands = commands
        if params:
            kwargs = {}
            if cmd is not None:
                kwargs['cmd'] = cmd
            if out is not None:
                kwargs['out'] = out
            if err is not None:
                kwargs['err'] = err
            if in_ is not None:
                kwargs['in_'] = in_
            if exit is not None:
                kwargs['exit'] = exit
            if waits is not None:
                kwargs['waits'] = waits
            self.commands = [Command(**kwargs)]
        if not self.commands:
            self.commands = [Command()]
        self._enable_sftp = enable_sftp
        self.transfers = transfers

    def generate_mocks(self):
        """
        Mocks `~paramiko.client.SSHClient` and `~paramiko.channel.Channel`.

        Specifically, the client will expect itself to be connected to
        ``self.host`` (if given), the channels will be associated with the
        client's `~paramiko.transport.Transport`, and the channels will
        expect/provide command-execution behavior as specified on the
        `.Command` objects supplied to this `.Session`.

        The client is then attached as ``self.client`` and the channels as
        ``self.channels``.

        :returns:
            ``None`` - this is mostly a "deferred setup" method and callers
            will just reference the above attributes (and call more methods) as
            needed.

        .. versionadded:: 2.1
        """
        self.client = Mock()
        self.channels = []
        
        for command in self.commands:
            channel = MockChannel(stdout=command.out, stderr=command.err)
            channel.recv_exit_status.return_value = command.exit
            channel.exit_status_ready.side_effect = chain(repeat(False, command.waits), repeat(True))
            self.channels.append(channel)

        self.client.get_transport().open_session.side_effect = self.channels

        if self.host:
            self.client.connect.assert_called_with(self.host, username=self.user, port=self.port)

        if self._enable_sftp:
            self.sftp_client = Mock()
            self.client.open_sftp.return_value = self.sftp_client

    def stop(self):
        """
        Stop any internal per-session mocks.

        .. versionadded:: 3.2
        """
        if hasattr(self, 'client'):
            self.client.close()
        if hasattr(self, 'sftp_client'):
            self.sftp_client.close()

class MockRemote:
    """
    Class representing mocked remote SSH/SFTP state.

    It supports stop/start style patching (useful for doctests) but then wraps
    that in a more convenient/common contextmanager pattern (useful in most
    other situations). The latter is also leveraged by the
    `fabric.testing.fixtures` module, recommended if you're using pytest.

    Note that the `expect` and `expect_sessions` methods automatically call
    `start`, so you won't normally need to do so by hand.

    By default, a single anonymous/internal `Session` is created, for
    convenience (eg mocking out SSH functionality as a safety measure). Users
    requiring detailed remote session expectations can call methods like
    `expect` or `expect_sessions`, which wipe that anonymous Session & set up a
    new one instead.

    .. versionadded:: 2.1
    .. versionchanged:: 3.2
        Added the ``enable_sftp`` init kwarg to enable mocking both SSH and
        SFTP at the same time.
    .. versionchanged:: 3.2
        Added contextmanager semantics to the class, so you don't have to
        remember to call `safety`/`stop`.
    """

    def __init__(self, enable_sftp=False):
        self._enable_sftp = enable_sftp
        self.expect_sessions(Session(enable_sftp=enable_sftp))

    def expect(self, *args, **kwargs):
        """
        Convenience method for creating & 'expect'ing a single `Session`.

        Returns the single `MockChannel` yielded by that Session.

        .. versionadded:: 2.1
        """
        session = Session(*args, **kwargs, enable_sftp=self._enable_sftp)
        channels = self.expect_sessions(session)
        return channels[0] if channels else None

    def expect_sessions(self, *sessions):
        """
        Sets the mocked remote environment to expect the given ``sessions``.

        Returns a list of `MockChannel` objects, one per input `Session`.

        .. versionadded:: 2.1
        """
        self.sessions = sessions
        self.stop()
        return self.start()

    def start(self):
        """
        Start patching SSHClient with the stored sessions, returning channels.

        .. versionadded:: 2.1
        """
        self.patcher = patch('fabric.connection.SSHClient', spec=True)
        MockSSHClient = self.patcher.start()
        self.clients = []
        channels = []

        for session in self.sessions:
            client = MockSSHClient.return_value
            session.generate_mocks()
            client.get_transport = session.client.get_transport
            client.open_sftp = session.client.open_sftp
            self.clients.append(client)
            channels.extend(session.channels)

        return channels

    def stop(self):
        """
        Stop patching SSHClient.

        .. versionadded:: 2.1
        """
        if hasattr(self, 'patcher'):
            self.patcher.stop()
        for session in getattr(self, 'sessions', []):
            session.stop()

    @deprecated(version='3.2', reason='This method has been renamed to `safety` & will be removed in 4.0')
    def sanity(self):
        """
        Run post-execution sanity checks (usually 'was X called' tests.)

        .. versionadded:: 2.1
        """
        self.safety()

    def safety(self):
        """
        Run post-execution safety checks (eg ensuring expected calls happened).

        .. versionadded:: 3.2
        """
        for session, client in zip(self.sessions, self.clients):
            if not session.guard_only:
                client.connect.assert_called_once()
            for channel, command in zip(session.channels, session.commands):
                command.expect_execution(channel)
            if session._enable_sftp and session.transfers:
                for transfer in session.transfers:
                    method = getattr(session.sftp_client, transfer['method'])
                    method.assert_called_once_with(**{k: v for k, v in transfer.items() if k != 'method'})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.safety()
        finally:
            self.stop()

@deprecated(version='3.2', reason='This class has been merged with `MockRemote` which can now handle SFTP mocking too. Please switch to it!')
class MockSFTP:
    """
    Class managing mocked SFTP remote state.

    Used in start/stop fashion in eg doctests; wrapped in the SFTP fixtures in
    conftest.py for main use.

    .. versionadded:: 2.1
    """

    def __init__(self, autostart=True):
        if autostart:
            self.start()
