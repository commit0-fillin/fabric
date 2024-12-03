from functools import partial
from getpass import getpass
from pathlib import Path
from paramiko import Agent, PKey
from paramiko.auth_strategy import AuthStrategy, Password, InMemoryPrivateKey, OnDiskPrivateKey
from .util import win32

class OpenSSHAuthStrategy(AuthStrategy):
    """
    Auth strategy that tries very hard to act like the OpenSSH client.

    .. warning::
        As of version 3.1, this class is **EXPERIMENTAL** and **incomplete**.
        It works best with passphraseless (eg ssh-agent) private key auth for
        now and will grow more features in future releases.

    For example, it accepts a `~paramiko.config.SSHConfig` and uses any
    relevant ``IdentityFile`` directives from that object, along with keys from
    your home directory and any local SSH agent. Keys specified at runtime are
    tried last, just as with ``ssh -i /path/to/key`` (this is one departure
    from the legacy/off-spec auth behavior observed in older Paramiko and
    Fabric versions).

    We explicitly do not document the full details here, because the point is
    to match the documented/observed behavior of OpenSSH. Please see the `ssh
    <https://man.openbsd.org/ssh>`_ and `ssh_config
    <https://man.openbsd.org/ssh_config>`_ man pages for more information.

    .. versionadded:: 3.1
    """

    def __init__(self, ssh_config, fabric_config, username):
        """
        Extends superclass with additional inputs.

        Specifically:

        - ``fabric_config``, a `fabric.Config` instance for the current
          session.
        - ``username``, which is unified by our intended caller so we don't
          have to - it's a synthesis of CLI, runtime,
          invoke/fabric-configuration, and ssh_config configuration.

        Also handles connecting to an SSH agent, if possible, for easier
        lifecycle tracking.
        """
        super().__init__(ssh_config=ssh_config)
        self.username = username
        self.config = fabric_config
        self.agent = Agent()

    def close(self):
        """
        Shut down any resources we ourselves opened up.
        """
        if self.agent:
            self.agent.close()
        super().close()
