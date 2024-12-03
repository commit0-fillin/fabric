import copy
import errno
import os
from invoke.config import Config as InvokeConfig, merge_dicts
from paramiko.config import SSHConfig
from .runners import Remote, RemoteShell
from .util import get_local_user, debug

class Config(InvokeConfig):
    """
    An `invoke.config.Config` subclass with extra Fabric-related behavior.

    This class behaves like `invoke.config.Config` in every way, with the
    following exceptions:

    - its `global_defaults` staticmethod has been extended to add/modify some
      default settings (see its documentation, below, for details);
    - it triggers loading of Fabric-specific env vars (e.g.
      ``FABRIC_RUN_HIDE=true`` instead of ``INVOKE_RUN_HIDE=true``) and
      filenames (e.g. ``/etc/fabric.yaml`` instead of ``/etc/invoke.yaml``).
    - it extends the API to account for loading ``ssh_config`` files (which are
      stored as additional attributes and have no direct relation to the
      regular config data/hierarchy.)
    - it adds a new optional constructor, `from_v1`, which :ref:`generates
      configuration data from Fabric 1 <from-v1>`.

    Intended for use with `.Connection`, as using vanilla
    `invoke.config.Config` objects would require users to manually define
    ``port``, ``user`` and so forth.

    .. seealso:: :doc:`/concepts/configuration`, :ref:`ssh-config`

    .. versionadded:: 2.0
    """
    prefix = 'fabric'

    @classmethod
    def from_v1(cls, env, **kwargs):
        """
        Alternate constructor which uses Fabric 1's ``env`` dict for settings.

        All keyword arguments besides ``env`` are passed unmolested into the
        primary constructor, with the exception of ``overrides``, which is used
        internally & will end up resembling the data from ``env`` with the
        user-supplied overrides on top.

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
        overrides = kwargs.pop('overrides', {})
        
        # Map Fabric 1 env vars to Fabric 2 config
        config_map = {
            'user': 'user',
            'host_string': 'host',
            'port': 'port',
            'key_filename': 'connect_kwargs.key_filename',
            'password': 'connect_kwargs.password',
            'use_ssh_config': 'ssh_config',
            'timeout': 'timeouts.connect',
            'command_timeout': 'timeouts.command',
            'forward_agent': 'forward_agent',
            'gateway': 'gateway',
            'warn_only': 'run.warn',
            'shell': 'run.shell',
            'pty': 'run.pty',
            'combine_stderr': 'run.combine_stderr',
            'echo': 'run.echo',
        }
        
        new_config = {}
        for v1_key, v2_key in config_map.items():
            if v1_key in env:
                keys = v2_key.split('.')
                current = new_config
                for key in keys[:-1]:
                    current = current.setdefault(key, {})
                current[keys[-1]] = env[v1_key]
        
        # Merge with user-supplied overrides
        merged = merge_dicts(new_config, overrides)
        
        # Create and return new Config instance
        return cls(**kwargs, overrides=merged)

    def __init__(self, *args, **kwargs):
        """
        Creates a new Fabric-specific config object.

        For most API details, see `invoke.config.Config.__init__`. Parameters
        new to this subclass are listed below.

        :param ssh_config:
            Custom/explicit `paramiko.config.SSHConfig` object. If given,
            prevents loading of any SSH config files. Default: ``None``.

        :param str runtime_ssh_path:
            Runtime SSH config path to load. Prevents loading of system/user
            files if given. Default: ``None``.

        :param str system_ssh_path:
            Location of the system-level SSH config file. Default:
            ``/etc/ssh/ssh_config``.

        :param str user_ssh_path:
            Location of the user-level SSH config file. Default:
            ``~/.ssh/config``.

        :param bool lazy:
            Has the same meaning as the parent class' ``lazy``, but
            additionally controls whether SSH config file loading is deferred
            (requires manually calling `load_ssh_config` sometime.) For
            example, one may need to wait for user input before calling
            `set_runtime_ssh_path`, which will inform exactly what
            `load_ssh_config` does.
        """
        ssh_config = kwargs.pop('ssh_config', None)
        lazy = kwargs.get('lazy', False)
        self.set_runtime_ssh_path(kwargs.pop('runtime_ssh_path', None))
        system_path = kwargs.pop('system_ssh_path', '/etc/ssh/ssh_config')
        self._set(_system_ssh_path=system_path)
        self._set(_user_ssh_path=kwargs.pop('user_ssh_path', '~/.ssh/config'))
        explicit = ssh_config is not None
        self._set(_given_explicit_object=explicit)
        if ssh_config is None:
            ssh_config = SSHConfig()
        self._set(base_ssh_config=ssh_config)
        super().__init__(*args, **kwargs)
        if not lazy:
            self.load_ssh_config()

    def set_runtime_ssh_path(self, path):
        """
        Configure a runtime-level SSH config file path.

        If set, this will cause `load_ssh_config` to skip system and user
        files, as OpenSSH does.

        .. versionadded:: 2.0
        """
        self._set(_runtime_ssh_path=path)

    def load_ssh_config(self):
        """
        Load SSH config file(s) from disk.

        Also (beforehand) ensures that Invoke-level config re: runtime SSH
        config file paths, is accounted for.

        .. versionadded:: 2.0
        """
        if self._runtime_ssh_path is not None:
            self.set_runtime_ssh_path(self._runtime_ssh_path)
        
        if not self._given_explicit_object:
            self._load_ssh_files()

    def _load_ssh_files(self):
        """
        Trigger loading of configured SSH config file paths.

        Expects that ``base_ssh_config`` has already been set to an
        `~paramiko.config.SSHConfig` object.

        :returns: ``None``.
        """
        if self._runtime_ssh_path:
            self._load_ssh_file(self._runtime_ssh_path)
        else:
            self._load_ssh_file(self._system_ssh_path)
            self._load_ssh_file(self._user_ssh_path)

    def _load_ssh_file(self, path):
        """
        Attempt to open and parse an SSH config file at ``path``.

        Does nothing if ``path`` is not a path to a valid file.

        :returns: ``None``.
        """
        from os.path import expanduser, isfile
        
        expanded_path = expanduser(path)
        if isfile(expanded_path):
            with open(expanded_path) as f:
                self.base_ssh_config.parse(f)

    @staticmethod
    def global_defaults():
        """
        Default configuration values and behavior toggles.

        Fabric only extends this method in order to make minor adjustments and
        additions to Invoke's `~invoke.config.Config.global_defaults`; see its
        documentation for the base values, such as the config subtrees
        controlling behavior of ``run`` or how ``tasks`` behave.

        For Fabric-specific modifications and additions to the Invoke-level
        defaults, see our own config docs at :ref:`default-values`.

        .. versionadded:: 2.0
        .. versionchanged:: 3.1
            Added the ``authentication`` settings section, plus sub-attributes
            such as ``authentication.strategy_class``.
        """
        from invoke.config import Config as InvokeConfig
        
        defaults = InvokeConfig.global_defaults()
        fabric_defaults = {
            'connect_kwargs': {},
            'forward_agent': False,
            'gateway': None,
            'hosts': [],
            'timeouts': {
                'connect': None,
                'command': None,
            },
            'ssh_config': True,
            'authentication': {
                'strategy_class': None,
                'identities': [],
                'key_types': ['rsa', 'dsa', 'ecdsa', 'ed25519'],
            },
        }
        merge_dicts(defaults, fabric_defaults)
        return defaults
