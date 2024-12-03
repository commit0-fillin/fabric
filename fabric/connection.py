from contextlib import contextmanager
from io import StringIO
from threading import Event
import socket
from decorator import decorator
from invoke import Context
from invoke.exceptions import ThreadException
from paramiko.agent import AgentRequestHandler
from paramiko.client import SSHClient, AutoAddPolicy
from paramiko.config import SSHConfig
from paramiko.proxy import ProxyCommand
from .config import Config
from .exceptions import InvalidV1Env
from .transfer import Transfer
from .tunnels import TunnelManager, Tunnel

class Connection(Context):
    """
    A connection to an SSH daemon, with methods for commands and file transfer.

    **Basics**

    This class inherits from Invoke's `~invoke.context.Context`, as it is a
    context within which commands, tasks etc can operate. It also encapsulates
    a Paramiko `~paramiko.client.SSHClient` instance, performing useful high
    level operations with that `~paramiko.client.SSHClient` and
    `~paramiko.channel.Channel` instances generated from it.

    .. _connect_kwargs:

    .. note::
        Many SSH specific options -- such as specifying private keys and
        passphrases, timeouts, disabling SSH agents, etc -- are handled
        directly by Paramiko and should be specified via the
        :ref:`connect_kwargs argument <connect_kwargs-arg>` of the constructor.

    **Lifecycle**

    `.Connection` has a basic "`create <__init__>`, `connect/open <open>`, `do
    work <run>`, `disconnect/close <close>`" lifecycle:

    - `Instantiation <__init__>` imprints the object with its connection
      parameters (but does **not** actually initiate the network connection).

        - An alternate constructor exists for users :ref:`upgrading piecemeal
          from Fabric 1 <from-v1>`: `from_v1`

    - Methods like `run`, `get` etc automatically trigger a call to
      `open` if the connection is not active; users may of course call `open`
      manually if desired.
    - It's best to explicitly close your connections when done using them. This
      can be accomplished by manually calling `close`, or by using the object
      as a contextmanager::

          with Connection('host') as c:
             c.run('command')
             c.put('file')

      .. warning::
          While Fabric (and Paramiko) attempt to register connections for
          automatic garbage collection, it's not currently safe to rely on that
          feature, as it can lead to end-of-process hangs and similar behavior.

    .. note::
        This class rebinds `invoke.context.Context.run` to `.local` so both
        remote and local command execution can coexist.

    **Configuration**

    Most `.Connection` parameters honor :doc:`Invoke-style configuration
    </concepts/configuration>` as well as any applicable :ref:`SSH config file
    directives <connection-ssh-config>`. For example, to end up with a
    connection to ``admin@myhost``, one could:

    - Use any built-in config mechanism, such as ``/etc/fabric.yml``,
      ``~/.fabric.json``, collection-driven configuration, env vars, etc,
      stating ``user: admin`` (or ``{"user": "admin"}``, depending on config
      format.) Then ``Connection('myhost')`` would implicitly have a ``user``
      of ``admin``.
    - Use an SSH config file containing ``User admin`` within any applicable
      ``Host`` header (``Host myhost``, ``Host *``, etc.) Again,
      ``Connection('myhost')`` will default to an ``admin`` user.
    - Leverage host-parameter shorthand (described in `.Config.__init__`), i.e.
      ``Connection('admin@myhost')``.
    - Give the parameter directly: ``Connection('myhost', user='admin')``.

    The same applies to agent forwarding, gateways, and so forth.

    .. versionadded:: 2.0
    """
    host = None
    original_host = None
    user = None
    port = None
    ssh_config = None
    gateway = None
    forward_agent = None
    connect_timeout = None
    connect_kwargs = None
    client = None
    transport = None
    _sftp = None
    _agent_handler = None

    @classmethod
    def from_v1(cls, env, **kwargs):
        """
        Alternate constructor which uses Fabric 1's ``env`` dict for settings.

        All keyword arguments besides ``env`` are passed unmolested into the
        primary constructor.

        .. warning::
            Because your own config overrides will win over data from ``env``,
            make sure you only set values you *intend* to change from your v1
            environment!

        For details on exactly which ``env`` vars are imported and what they
        become in the new API, please see :ref:`v1-env-var-imports`.

        :param env:
            An explicit Fabric 1 ``env`` dict (technically, any
            ``fabric.utils._AttributeDict`` instance should work) to pull
            configuration from.

        .. versionadded:: 2.4
        """
        if not isinstance(env, dict):
            raise InvalidV1Env("The 'env' parameter must be a dictionary")

        config = Config.from_v1(env)
        return cls(config=config, **kwargs)

    def __init__(self, host, user=None, port=None, config=None, gateway=None, forward_agent=None, connect_timeout=None, connect_kwargs=None, inline_ssh_env=None):
        """
        Set up a new object representing a server connection.

        :param str host:
            the hostname (or IP address) of this connection.

            May include shorthand for the ``user`` and/or ``port`` parameters,
            of the form ``user@host``, ``host:port``, or ``user@host:port``.

            .. note::
                Due to ambiguity, IPv6 host addresses are incompatible with the
                ``host:port`` shorthand (though ``user@host`` will still work
                OK). In other words, the presence of >1 ``:`` character will
                prevent any attempt to derive a shorthand port number; use the
                explicit ``port`` parameter instead.

            .. note::
                If ``host`` matches a ``Host`` clause in loaded SSH config
                data, and that ``Host`` clause contains a ``Hostname``
                directive, the resulting `.Connection` object will behave as if
                ``host`` is equal to that ``Hostname`` value.

                In all cases, the original value of ``host`` is preserved as
                the ``original_host`` attribute.

                Thus, given SSH config like so::

                    Host myalias
                        Hostname realhostname

                a call like ``Connection(host='myalias')`` will result in an
                object whose ``host`` attribute is ``realhostname``, and whose
                ``original_host`` attribute is ``myalias``.

        :param str user:
            the login user for the remote connection. Defaults to
            ``config.user``.

        :param int port:
            the remote port. Defaults to ``config.port``.

        :param config:
            configuration settings to use when executing methods on this
            `.Connection` (e.g. default SSH port and so forth).

            Should be a `.Config` or an `invoke.config.Config`
            (which will be turned into a `.Config`).

            Default is an anonymous `.Config` object.

        :param gateway:
            An object to use as a proxy or gateway for this connection.

            This parameter accepts one of the following:

            - another `.Connection` (for a ``ProxyJump`` style gateway);
            - a shell command string (for a ``ProxyCommand`` style style
              gateway).

            Default: ``None``, meaning no gatewaying will occur (unless
            otherwise configured; if one wants to override a configured gateway
            at runtime, specify ``gateway=False``.)

            .. seealso:: :ref:`ssh-gateways`

        :param bool forward_agent:
            Whether to enable SSH agent forwarding.

            Default: ``config.forward_agent``.

        :param int connect_timeout:
            Connection timeout, in seconds.

            Default: ``config.timeouts.connect``.


        :param dict connect_kwargs:

            .. _connect_kwargs-arg:

            Keyword arguments handed verbatim to
            `SSHClient.connect <paramiko.client.SSHClient.connect>` (when
            `.open` is called).

            `.Connection` tries not to grow additional settings/kwargs of its
            own unless it is adding value of some kind; thus,
            ``connect_kwargs`` is currently the right place to hand in paramiko
            connection parameters such as ``pkey`` or ``key_filename``. For
            example::

                c = Connection(
                    host="hostname",
                    user="admin",
                    connect_kwargs={
                        "key_filename": "/home/myuser/.ssh/private.key",
                    },
                )

            Default: ``config.connect_kwargs``.

        :param bool inline_ssh_env:
            Whether to send environment variables "inline" as prefixes in front
            of command strings (``export VARNAME=value && mycommand here``;
            this is the default behavior), or submit them through the SSH
            protocol itself.

            In Fabric 2.x this defaulted to ``False`` (try using the protocol
            behavior), but in 3.x it changed to ``True`` due to the simple fact
            that most remote servers are deployed with a restricted
            ``AcceptEnv`` setting, making use of the protocol approach
            non-viable.

            The actual default value is the value of the ``inline_ssh_env``
            :ref:`configuration value <default-values>` (which, as above,
            currently defaults to ``True``).

            .. warning::
                This functionality does **not** currently perform any shell
                escaping on your behalf! Be careful when using nontrivial
                values, and note that you can put in your own quoting,
                backslashing etc if desired.

                Consider using a different approach (such as actual
                remote shell scripts) if you run into too many issues here.

            .. note::
                When serializing into prefixed ``FOO=bar`` format, we apply the
                builtin `sorted` function to the env dictionary's keys, to
                remove what would otherwise be ambiguous/arbitrary ordering.

            .. note::
                This setting has no bearing on *local* shell commands; it only
                affects remote commands, and thus, methods like `.run` and
                `.sudo`.

        :raises ValueError:
            if user or port values are given via both ``host`` shorthand *and*
            their own arguments. (We `refuse the temptation to guess`_).

        .. _refuse the temptation to guess:
            http://zen-of-python.info/
            in-the-face-of-ambiguity-refuse-the-temptation-to-guess.html#12

        .. versionchanged:: 2.3
            Added the ``inline_ssh_env`` parameter.

        .. versionchanged:: 3.0
            ``inline_ssh_env`` still defaults to the config value, but said
            config value has now changed and defaults to ``True``, not
            ``False``.
        """
        super().__init__(config=config)
        if config is None:
            config = Config()
        elif not isinstance(config, Config):
            config = config.clone(into=Config)
        self._set(_config=config)
        shorthand = self.derive_shorthand(host)
        host = shorthand['host']
        err = 'You supplied the {} via both shorthand and kwarg! Please pick one.'
        if shorthand['user'] is not None:
            if user is not None:
                raise ValueError(err.format('user'))
            user = shorthand['user']
        if shorthand['port'] is not None:
            if port is not None:
                raise ValueError(err.format('port'))
            port = shorthand['port']
        self.ssh_config = self.config.base_ssh_config.lookup(host)
        self.original_host = host
        self.host = host
        if 'hostname' in self.ssh_config:
            self.host = self.ssh_config['hostname']
        self.user = user or self.ssh_config.get('user', self.config.user)
        self.port = port or int(self.ssh_config.get('port', self.config.port))
        self.gateway = gateway if gateway is not None else self.get_gateway()
        if forward_agent is None:
            forward_agent = self.config.forward_agent
            if 'forwardagent' in self.ssh_config:
                map_ = {'yes': True, 'no': False}
                forward_agent = map_[self.ssh_config['forwardagent']]
        self.forward_agent = forward_agent
        if connect_timeout is None:
            connect_timeout = self.ssh_config.get('connecttimeout', self.config.timeouts.connect)
        if connect_timeout is not None:
            connect_timeout = int(connect_timeout)
        self.connect_timeout = connect_timeout
        self.connect_kwargs = self.resolve_connect_kwargs(connect_kwargs)
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        self.client = client
        self.transport = None
        if inline_ssh_env is None:
            inline_ssh_env = self.config.inline_ssh_env
        self.inline_ssh_env = inline_ssh_env

    def __repr__(self):
        bits = [('host', self.host)]
        if self.user != self.config.user:
            bits.append(('user', self.user))
        if self.port != self.config.port:
            bits.append(('port', self.port))
        if self.gateway:
            val = 'proxyjump'
            if isinstance(self.gateway, str):
                val = 'proxycommand'
            bits.append(('gw', val))
        return '<Connection {}>'.format(' '.join(('{}={}'.format(*x) for x in bits)))

    def __eq__(self, other):
        if not isinstance(other, Connection):
            return False
        return self._identity() == other._identity()

    def __lt__(self, other):
        return self._identity() < other._identity()

    def __hash__(self):
        return hash(self._identity())

    @property
    def is_connected(self):
        """
        Whether or not this connection is actually open.

        .. versionadded:: 2.0
        """
        return self.transport is not None and self.transport.is_active()

    def open(self):
        """
        Initiate an SSH connection to the host/port this object is bound to.

        This may include activating the configured gateway connection, if one
        is set.

        Also saves a handle to the now-set Transport object for easier access.

        Various connect-time settings (and/or their corresponding :ref:`SSH
        config options <ssh-config>`) are utilized here in the call to
        `SSHClient.connect <paramiko.client.SSHClient.connect>`. (For details,
        see :doc:`the configuration docs </concepts/configuration>`.)

        :returns:
            The result of the internal call to `.SSHClient.connect`, if
            performing an initial connection; ``None`` otherwise.

        .. versionadded:: 2.0
        .. versionchanged:: 3.1
            Now returns the inner Paramiko connect call's return value instead
            of always returning the implicit ``None``.
        """
        if self.is_connected:
            return None

        if self.gateway:
            sock = self.open_gateway()
        else:
            sock = None

        connect_kwargs = self.connect_kwargs.copy()
        connect_kwargs.update({
            'hostname': self.host,
            'port': self.port,
            'username': self.user,
            'timeout': self.connect_timeout,
            'sock': sock,
        })

        result = self.client.connect(**connect_kwargs)
        self.transport = self.client.get_transport()
        return result

    def open_gateway(self):
        """
        Obtain a socket-like object from `gateway`.

        :returns:
            A ``direct-tcpip`` `paramiko.channel.Channel`, if `gateway` was a
            `.Connection`; or a `~paramiko.proxy.ProxyCommand`, if `gateway`
            was a string.

        .. versionadded:: 2.0
        """
        if isinstance(self.gateway, Connection):
            self.gateway.open()
            return self.gateway.transport.open_channel(
                'direct-tcpip',
                (self.host, self.port),
                ('', 0)
            )
        elif isinstance(self.gateway, str):
            return ProxyCommand(self.gateway)
        else:
            raise ValueError("Unsupported gateway type")

    def close(self):
        """
        Terminate the network connection to the remote end, if open.

        If any SFTP sessions are open, they will also be closed.

        If no connection or SFTP session is open, this method does nothing.

        .. versionadded:: 2.0
        .. versionchanged:: 3.0
            Now closes SFTP sessions too (2.x required manually doing so).
        """
        if self._sftp:
            self._sftp.close()
            self._sftp = None

        if self.transport:
            self.transport.close()
            self.transport = None

        if self.client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @opens
    def run(self, command, **kwargs):
        """
        Execute a shell command on the remote end of this connection.

        This method wraps an SSH-capable implementation of
        `invoke.runners.Runner.run`; see its documentation for details.

        .. warning::
            There are a few spots where Fabric departs from Invoke's default
            settings/behaviors; they are documented under
            `.Config.global_defaults`.

        .. versionadded:: 2.0
        """
        runner = Remote(context=self, **kwargs)
        return runner.run(command)

    @opens
    def sudo(self, command, **kwargs):
        """
        Execute a shell command, via ``sudo``, on the remote end.

        This method is identical to `invoke.context.Context.sudo` in every way,
        except in that -- like `run` -- it honors per-host/per-connection
        configuration overrides in addition to the generic/global ones. Thus,
        for example, per-host sudo passwords may be configured.

        .. versionadded:: 2.0
        """
        runner = Remote(context=self, **kwargs)
        return runner.sudo(command)

    @opens
    def shell(self, **kwargs):
        """
        Run an interactive login shell on the remote end, as with ``ssh``.

        This method is intended strictly for use cases where you can't know
        what remote shell to invoke, or are connecting to a non-POSIX-server
        environment such as a network appliance or other custom SSH server.
        Nearly every other use case, including interactively-focused ones, will
        be better served by using `run` plus an explicit remote shell command
        (eg ``bash``).

        `shell` has the following differences in behavior from `run`:

        - It still returns a `~invoke.runners.Result` instance, but the object
          will have a less useful set of attributes than with `run` or `local`:

            - ``command`` will be ``None``, as there is no such input argument.
            - ``stdout`` will contain a full record of the session, including
              all interactive input, as that is echoed back to the user. This
              can be useful for logging but is much less so for doing
              programmatic things after the method returns.
            - ``stderr`` will always be empty (same as `run` when
              ``pty==True``).
            - ``pty`` will always be True (because one was automatically used).
            - ``exited`` and similar attributes will only reflect the overall
              session, which may vary by shell or appliance but often has no
              useful relationship with the internally executed commands' exit
              codes.

        - This method behaves as if ``warn`` is set to ``True``: even if the
          remote shell exits uncleanly, no exception will be raised.
        - A pty is always allocated remotely, as with ``pty=True`` under `run`.
        - The ``inline_env`` setting is ignored, as there is no default shell
          command to add the parameters to (and no guarantee the remote end
          even is a shell!)

        It supports **only** the following kwargs, which behave identically to
        their counterparts in `run` unless otherwise stated:

        - ``encoding``
        - ``env``
        - ``in_stream`` (useful in niche cases, but make sure regular `run`
          with this argument isn't more suitable!)
        - ``replace_env``
        - ``watchers`` (note that due to pty echoing your stdin back to stdout,
          a watcher will see your input as well as program stdout!)

        Those keyword arguments also honor the ``run.*`` configuration tree, as
        in `run`/`sudo`.

        :returns: `~invoke.runners.Result`

        :raises:
            `~invoke.exceptions.ThreadException` (if the background I/O threads
            encountered exceptions other than
            `~invoke.exceptions.WatcherError`).

        .. versionadded:: 2.7
        """
        runner = RemoteShell(context=self, **kwargs)
        return runner.run()

    def local(self, *args, **kwargs):
        """
        Execute a shell command on the local system.

        This method is effectively a wrapper of `invoke.run`; see its docs for
        details and call signature.

        .. versionadded:: 2.0
        """
        return super().run(*args, **kwargs)

    @opens
    def sftp(self):
        """
        Return a `~paramiko.sftp_client.SFTPClient` object.

        If called more than one time, memoizes the first result; thus, any
        given `.Connection` instance will only ever have a single SFTP client,
        and state (such as that managed by
        `~paramiko.sftp_client.SFTPClient.chdir`) will be preserved.

        .. versionadded:: 2.0
        """
        if self._sftp is None:
            self._sftp = self.transport.open_sftp_client()
        return self._sftp

    def get(self, *args, **kwargs):
        """
        Get a remote file to the local filesystem or file-like object.

        Simply a wrapper for `.Transfer.get`. Please see its documentation for
        all details.

        .. versionadded:: 2.0
        """
        return Transfer(self).get(*args, **kwargs)

    def put(self, *args, **kwargs):
        """
        Put a local file (or file-like object) to the remote filesystem.

        Simply a wrapper for `.Transfer.put`. Please see its documentation for
        all details.

        .. versionadded:: 2.0
        """
        return Transfer(self).put(*args, **kwargs)

    @contextmanager
    @opens
    def forward_local(self, local_port, remote_port=None, remote_host='localhost', local_host='localhost'):
        """
        Open a tunnel connecting ``local_port`` to the server's environment.

        For example, say you want to connect to a remote PostgreSQL database
        which is locked down and only accessible via the system it's running
        on. You have SSH access to this server, so you can temporarily make
        port 5432 on your local system act like port 5432 on the server::

            import psycopg2
            from fabric import Connection

            with Connection('my-db-server').forward_local(5432):
                db = psycopg2.connect(
                    host='localhost', port=5432, database='mydb'
                )
                # Do things with 'db' here

        This method is analogous to using the ``-L`` option of OpenSSH's
        ``ssh`` program.

        :param int local_port: The local port number on which to listen.

        :param int remote_port:
            The remote port number. Defaults to the same value as
            ``local_port``.

        :param str local_host:
            The local hostname/interface on which to listen. Default:
            ``localhost``.

        :param str remote_host:
            The remote hostname serving the forwarded remote port. Default:
            ``localhost`` (i.e., the host this `.Connection` is connected to.)

        :returns:
            Nothing; this method is only useful as a context manager affecting
            local operating system state.

        .. versionadded:: 2.0
        """
        if remote_port is None:
            remote_port = local_port

        transport = self.transport
        finished = Event()
        tunnel_manager = TunnelManager(
            local_host, local_port, remote_host, remote_port, transport, finished
        )
        tunnel_manager.start()

        try:
            yield
        finally:
            finished.set()
            tunnel_manager.join()

    @contextmanager
    @opens
    def forward_remote(self, remote_port, local_port=None, remote_host='127.0.0.1', local_host='localhost'):
        """
        Open a tunnel connecting ``remote_port`` to the local environment.

        For example, say you're running a daemon in development mode on your
        workstation at port 8080, and want to funnel traffic to it from a
        production or staging environment.

        In most situations this isn't possible as your office/home network
        probably blocks inbound traffic. But you have SSH access to this
        server, so you can temporarily make port 8080 on that server act like
        port 8080 on your workstation::

            from fabric import Connection

            c = Connection('my-remote-server')
            with c.forward_remote(8080):
                c.run("remote-data-writer --port 8080")
                # Assuming remote-data-writer runs until interrupted, this will
                # stay open until you Ctrl-C...

        This method is analogous to using the ``-R`` option of OpenSSH's
        ``ssh`` program.

        :param int remote_port: The remote port number on which to listen.

        :param int local_port:
            The local port number. Defaults to the same value as
            ``remote_port``.

        :param str local_host:
            The local hostname/interface the forwarded connection talks to.
            Default: ``localhost``.

        :param str remote_host:
            The remote interface address to listen on when forwarding
            connections. Default: ``127.0.0.1`` (i.e. only listen on the remote
            localhost).

        :returns:
            Nothing; this method is only useful as a context manager affecting
            local operating system state.

        .. versionadded:: 2.0
        """
        if local_port is None:
            local_port = remote_port

        transport = self.transport
        finished = Event()
        tunnel_manager = TunnelManager(
            remote_host, remote_port, local_host, local_port, transport, finished
        )
        tunnel_manager.start()

        try:
            yield
        finally:
            finished.set()
            tunnel_manager.join()
