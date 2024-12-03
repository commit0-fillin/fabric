"""
Tunnel and connection forwarding internals.

If you're looking for simple, end-user-focused connection forwarding, please
see `.Connection`, e.g. `.Connection.forward_local`.
"""
import select
import socket
import time
from threading import Event
from invoke.exceptions import ThreadException
from invoke.util import ExceptionHandlingThread

class TunnelManager(ExceptionHandlingThread):
    """
    Thread subclass for tunnelling connections over SSH between two endpoints.

    Specifically, one instance of this class is sufficient to sit around
    forwarding any number of individual connections made to one end of the
    tunnel or the other. If you need to forward connections between more than
    one set of ports, you'll end up instantiating multiple TunnelManagers.

    Wraps a `~paramiko.transport.Transport`, which should already be connected
    to the remote server.

    .. versionadded:: 2.0
    """

    def __init__(self, local_host, local_port, remote_host, remote_port, transport, finished):
        super().__init__()
        self.local_address = (local_host, local_port)
        self.remote_address = (remote_host, remote_port)
        self.transport = transport
        self.finished = finished

class Tunnel(ExceptionHandlingThread):
    """
    Bidirectionally forward data between an SSH channel and local socket.

    .. versionadded:: 2.0
    """

    def __init__(self, channel, sock, finished):
        self.channel = channel
        self.sock = sock
        self.finished = finished
        self.socket_chunk_size = 1024
        self.channel_chunk_size = 1024
        super().__init__()

    def read_and_write(self, reader, writer, chunk_size):
        """
        Read ``chunk_size`` from ``reader``, writing result to ``writer``.

        Returns ``None`` if successful, or ``True`` if the read was empty.

        .. versionadded:: 2.0
        """
        data = reader.recv(chunk_size)
        if not data:
            return True
        writer.sendall(data)
        return None
