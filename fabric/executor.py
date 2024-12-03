import invoke
from invoke import Call, Task
from .tasks import ConnectionCall
from .exceptions import NothingToDo
from .util import debug

class Executor(invoke.Executor):
    """
    `~invoke.executor.Executor` subclass which understands Fabric concepts.

    Designed to work in tandem with Fabric's `@task
    <fabric.tasks.task>`/`~fabric.tasks.Task`, and is capable of acting on
    information stored on the resulting objects -- such as default host lists.

    This class is written to be backwards compatible with vanilla Invoke-level
    tasks, which it simply delegates to its superclass.

    Please see the parent class' `documentation <invoke.executor.Executor>` for
    details on most public API members and object lifecycle.
    """

    def normalize_hosts(self, hosts):
        """
        Normalize mixed host-strings-or-kwarg-dicts into kwarg dicts only.

        In other words, transforms data taken from the CLI (--hosts, always
        strings) or decorator arguments (may be strings or kwarg dicts) into
        kwargs suitable for creating Connection instances.

        Subclasses may wish to override or extend this to perform, for example,
        database or custom config file lookups (vs this default behavior, which
        is to simply assume that strings are 'host' kwargs).

        :param hosts:
            Potentially heterogenous list of host connection values, as per the
            ``hosts`` param to `.task`.

        :returns: Homogenous list of Connection init kwarg dicts.
        """
        normalized = []
        for host in hosts:
            if isinstance(host, str):
                normalized.append({"host": host})
            elif isinstance(host, dict):
                normalized.append(host)
            else:
                raise ValueError(f"Invalid host specification: {host}")
        return normalized

    def parameterize(self, call, connection_init_kwargs):
        """
        Parameterize a Call with its Context set to a per-host Connection.

        :param call:
            The generic `.Call` being parameterized.
        :param connection_init_kwargs:
            The dict of `.Connection` init params/kwargs to attach to the
            resulting `.ConnectionCall`.

        :returns:
            `.ConnectionCall`.
        """
        from .tasks import ConnectionCall
        
        # Create a new ConnectionCall object
        connection_call = ConnectionCall(
            task=call.task,
            args=call.args,
            kwargs=call.kwargs,
            init_kwargs=connection_init_kwargs
        )
        
        # Set the context of the ConnectionCall to a new Connection
        connection_call.context = Connection(**connection_init_kwargs)
        
        return connection_call
