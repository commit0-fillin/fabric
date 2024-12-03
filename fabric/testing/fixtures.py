"""
`pytest <https://pytest.org>`_ fixtures for easy use of Fabric test helpers.

To get Fabric plus this module's dependencies (as well as those of the main
`fabric.testing.base` module which these fixtures wrap), ``pip install
fabric[pytest]``.

The simplest way to get these fixtures loaded into your test suite so Pytest
notices them is to import them into a ``conftest.py`` (`docs
<http://pytest.readthedocs.io/en/latest/fixture.html#conftest-py-sharing-fixture-functions>`_).
For example, if you intend to use the `remote` and `client` fixtures::

    from fabric.testing.fixtures import client, remote

.. versionadded:: 2.1
"""
from unittest.mock import patch, Mock
try:
    from pytest import fixture
except ImportError:
    import warnings
    warning = "You appear to be missing some optional test-related dependencies;please 'pip install fabric[pytest]'."
    warnings.warn(warning, ImportWarning)
    raise
from .. import Connection
from ..transfer import Transfer
from .base import MockRemote, MockSFTP

@fixture
def connection():
    """
    Yields a `.Connection` object with mocked methods.

    Specifically:

    - the hostname is set to ``"host"`` and the username to ``"user"``;
    - the primary API members (`.Connection.run`, `.Connection.local`, etc) are
      replaced with ``mock.Mock`` instances;
    - the ``run.in_stream`` config option is set to ``False`` to avoid attempts
      to read from stdin (which typically plays poorly with pytest and other
      capturing test runners);

    .. versionadded:: 2.1
    """
    with patch('fabric.connection.Connection') as mock_connection:
        conn = mock_connection.return_value
        conn.host = "host"
        conn.user = "user"
        conn.run = Mock()
        conn.local = Mock()
        conn.sudo = Mock()
        conn.put = Mock()
        conn.get = Mock()
        conn.config.run.in_stream = False
        yield conn
cxn = connection

@fixture
def remote_with_sftp():
    """
    Like `remote`, but with ``enable_sftp=True``.

    To access the internal mocked SFTP client (eg for asserting SFTP
    functionality was called), note that the returned `MockRemote` object has a
    ``.sftp`` attribute when created in this mode.
    """
    mock_remote = MockRemote(enable_sftp=True)
    yield mock_remote
    mock_remote.stop()

@fixture
def remote():
    """
    Fixture allowing setup of a mocked remote session & access to sub-mocks.

    Yields a `.MockRemote` object (which may need to be updated via
    `.MockRemote.expect`, `.MockRemote.expect_sessions`, etc; otherwise a
    default session will be used) & calls `.MockRemote.safety` and
    `.MockRemote.stop` on teardown.

    .. versionadded:: 2.1
    """
    mock_remote = MockRemote()
    yield mock_remote
    try:
        mock_remote.safety()
    finally:
        mock_remote.stop()

@fixture
def sftp():
    """
    Fixture allowing setup of a mocked remote SFTP session.

    Yields a 3-tuple of: Transfer() object, SFTPClient object, and mocked OS
    module.

    For many/most tests which only want the Transfer and/or SFTPClient objects,
    see `sftp_objs` and `transfer` which wrap this fixture.

    .. versionadded:: 2.1
    """
    with patch('fabric.transfer.os') as mock_os, \
         patch('fabric.transfer.Transfer') as mock_transfer, \
         patch('paramiko.sftp_client.SFTPClient') as mock_sftp_client:
        transfer = mock_transfer.return_value
        sftp_client = mock_sftp_client.return_value
        yield transfer, sftp_client, mock_os

@fixture
def sftp_objs(sftp):
    """
    Wrapper for `sftp` which only yields the Transfer and SFTPClient.

    .. versionadded:: 2.1
    """
    transfer, sftp_client, _ = sftp
    yield transfer, sftp_client

@fixture
def transfer(sftp):
    """
    Wrapper for `sftp` which only yields the Transfer object.

    .. versionadded:: 2.1
    """
    transfer, _, _ = sftp
    yield transfer

@fixture
def client():
    """
    Mocks `~paramiko.client.SSHClient` for testing calls to ``connect()``.

    Yields a mocked ``SSHClient`` instance.

    This fixture updates `~paramiko.client.SSHClient.get_transport` to return a
    mock that appears active on first check, then inactive after, matching most
    tests' needs by default:

    - `.Connection` instantiates, with a None ``.transport``.
    - Calls to ``.open()`` test ``.is_connected``, which returns ``False`` when
      ``.transport`` is falsey, and so the first open will call
      ``SSHClient.connect`` regardless.
    - ``.open()`` then sets ``.transport`` to ``SSHClient.get_transport()``, so
      ``Connection.transport`` is effectively
      ``client.get_transport.return_value``.
    - Subsequent activity will want to think the mocked SSHClient is
      "connected", meaning we want the mocked transport's ``.active`` to be
      ``True``.
    - This includes `.Connection.close`, which short-circuits if
      ``.is_connected``; having a statically ``True`` active flag means a full
      open -> close cycle will run without error. (Only tests that double-close
      or double-open should have issues here.)

    End result is that:

    - ``.is_connected`` behaves False after instantiation and before ``.open``,
      then True after ``.open``
    - ``.close`` will work normally on 1st call
    - ``.close`` will behave "incorrectly" on subsequent calls (since it'll
      think connection is still live.) Tests that check the idempotency of
      ``.close`` will need to tweak their mock mid-test.

    For 'full' fake remote session interaction (i.e. stdout/err
    reading/writing, channel opens, etc) see `remote`.

    .. versionadded:: 2.1
    """
    with patch('paramiko.client.SSHClient') as mock_client:
        client = mock_client.return_value
        transport = Mock()
        transport.active = PropertyMock(side_effect=[True, False])
        client.get_transport.return_value = transport
        yield client
