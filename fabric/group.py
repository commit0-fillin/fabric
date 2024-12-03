from queue import Queue
from invoke.util import ExceptionHandlingThread
from .connection import Connection
from .exceptions import GroupException

class Group(list):
    """
    A collection of `.Connection` objects whose API operates on its contents.

    .. warning::
        **This is a partially abstract class**; you need to use one of its
        concrete subclasses (such as `.SerialGroup` or `.ThreadingGroup`) or
        you'll get ``NotImplementedError`` on most of the methods.

    Most methods in this class wrap those of `.Connection` and will accept the
    same arguments; however their return values and exception-raising behavior
    differ:

    - Return values are dict-like objects (`.GroupResult`) mapping
      `.Connection` objects to the return value for the respective connections:
      `.Group.run` returns a map of `.Connection` to `.runners.Result`,
      `.Group.get` returns a map of `.Connection` to `.transfer.Result`, etc.
    - If any connections encountered exceptions, a `.GroupException` is raised,
      which is a thin wrapper around what would otherwise have been the
      `.GroupResult` returned; within that wrapped `.GroupResult`, the
      excepting connections map to the exception that was raised, in place of a
      ``Result`` (as no ``Result`` was obtained.) Any non-excepting connections
      will have a ``Result`` value, as normal.

    For example, when no exceptions occur, a session might look like this::

        >>> group = SerialGroup('host1', 'host2')
        >>> group.run("this is fine")
        {
            <Connection host='host1'>: <Result cmd='this is fine' exited=0>,
            <Connection host='host2'>: <Result cmd='this is fine' exited=0>,
        }

    With exceptions (anywhere from 1 to "all of them"), it looks like so; note
    the different exception classes, e.g. `~invoke.exceptions.UnexpectedExit`
    for a completed session whose command exited poorly, versus
    `socket.gaierror` for a host that had DNS problems::

        >>> group = SerialGroup('host1', 'host2', 'notahost')
        >>> group.run("will it blend?")
        {
            <Connection host='host1'>: <Result cmd='will it blend?' exited=0>,
            <Connection host='host2'>: <UnexpectedExit: cmd='...' exited=1>,
            <Connection host='notahost'>: gaierror(...),
        }

    As with `.Connection`, `.Group` objects may be used as context managers,
    which will automatically `.close` the object on block exit.

    .. versionadded:: 2.0
    .. versionchanged:: 2.4
        Added context manager behavior.
    """

    def __init__(self, *hosts, **kwargs):
        """
        Create a group of connections from one or more shorthand host strings.

        See `.Connection` for details on the format of these strings - they
        will be used as the first positional argument of `.Connection`
        constructors.

        Any keyword arguments given will be forwarded directly to those
        `.Connection` constructors as well. For example, to get a serially
        executing group object that connects to ``admin@host1``,
        ``admin@host2`` and ``admin@host3``, and forwards your SSH agent too::

            group = SerialGroup(
                "host1", "host2", "host3", user="admin", forward_agent=True,
            )

        .. versionchanged:: 2.3
            Added ``**kwargs`` (was previously only ``*hosts``).
        """
        self.extend([Connection(host, **kwargs) for host in hosts])

    @classmethod
    def from_connections(cls, connections):
        """
        Alternate constructor accepting `.Connection` objects.

        .. versionadded:: 2.0
        """
        group = cls()
        group.extend(connections)
        return group

    def run(self, *args, **kwargs):
        """
        Executes `.Connection.run` on all member `Connections <.Connection>`.

        :returns: a `.GroupResult`.

        .. versionadded:: 2.0
        """
        results = GroupResult()
        for connection in self:
            try:
                results[connection] = connection.run(*args, **kwargs)
            except Exception as e:
                results[connection] = e
        if any(isinstance(result, Exception) for result in results.values()):
            raise GroupException(results)
        return results

    def sudo(self, *args, **kwargs):
        """
        Executes `.Connection.sudo` on all member `Connections <.Connection>`.

        :returns: a `.GroupResult`.

        .. versionadded:: 2.6
        """
        results = GroupResult()
        for connection in self:
            try:
                results[connection] = connection.sudo(*args, **kwargs)
            except Exception as e:
                results[connection] = e
        if any(isinstance(result, Exception) for result in results.values()):
            raise GroupException(results)
        return results

    def put(self, *args, **kwargs):
        """
        Executes `.Connection.put` on all member `Connections <.Connection>`.

        This is a straightforward application: aside from whatever the concrete
        group subclass does for concurrency or lack thereof, the effective
        result is like running a loop over the connections and calling their
        ``put`` method.

        :returns:
            a `.GroupResult` whose values are `.transfer.Result` instances.

        .. versionadded:: 2.6
        """
        results = GroupResult()
        for connection in self:
            try:
                results[connection] = connection.put(*args, **kwargs)
            except Exception as e:
                results[connection] = e
        if any(isinstance(result, Exception) for result in results.values()):
            raise GroupException(results)
        return results

    def get(self, *args, **kwargs):
        """
        Executes `.Connection.get` on all member `Connections <.Connection>`.

        .. note::
            This method changes some behaviors over e.g. directly calling
            `.Connection.get` on a ``for`` loop of connections; the biggest is
            that the implied default value for the ``local`` parameter is
            ``"{host}/"``, which triggers use of local path parameterization
            based on each connection's target hostname.

            Thus, unless you override ``local`` yourself, a copy of the
            downloaded file will be stored in (relative) directories named
            after each host in the group.

        .. warning::
            Using file-like objects as the ``local`` argument is not currently
            supported, as it would be equivalent to supplying that same object
            to a series of individual ``get()`` calls.

        :returns:
            a `.GroupResult` whose values are `.transfer.Result` instances.

        .. versionadded:: 2.6
        """
        results = GroupResult()
        for connection in self:
            try:
                if 'local' not in kwargs:
                    kwargs['local'] = f"{connection.host}/"
                results[connection] = connection.get(*args, **kwargs)
            except Exception as e:
                results[connection] = e
        if any(isinstance(result, Exception) for result in results.values()):
            raise GroupException(results)
        return results

    def close(self):
        """
        Executes `.Connection.close` on all member `Connections <.Connection>`.

        .. versionadded:: 2.4
        """
        for connection in self:
            connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

class SerialGroup(Group):
    """
    Subclass of `.Group` which executes in simple, serial fashion.

    .. versionadded:: 2.0
    """

class ThreadingGroup(Group):
    """
    Subclass of `.Group` which uses threading to execute concurrently.

    .. versionadded:: 2.0
    """

class GroupResult(dict):
    """
    Collection of results and/or exceptions arising from `.Group` methods.

    Acts like a dict, but adds a couple convenience methods, to wit:

    - Keys are the individual `.Connection` objects from within the `.Group`.
    - Values are either return values / results from the called method (e.g.
      `.runners.Result` objects), *or* an exception object, if one prevented
      the method from returning.
    - Subclasses `dict`, so has all dict methods.
    - Has `.succeeded` and `.failed` attributes containing sub-dicts limited to
      just those key/value pairs that succeeded or encountered exceptions,
      respectively.

      - Of note, these attributes allow high level logic, e.g. ``if
        mygroup.run('command').failed`` and so forth.

    .. versionadded:: 2.0
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._successes = {}
        self._failures = {}

    @property
    def succeeded(self):
        """
        A sub-dict containing only successful results.

        .. versionadded:: 2.0
        """
        return {k: v for k, v in self.items() if not isinstance(v, Exception)}

    @property
    def failed(self):
        """
        A sub-dict containing only failed results.

        .. versionadded:: 2.0
        """
        return {k: v for k, v in self.items() if isinstance(v, Exception)}
