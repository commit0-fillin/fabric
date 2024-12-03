"""
File transfer via SFTP and/or SCP.
"""
import os
import posixpath
import stat
from pathlib import Path
from .util import debug

class Transfer:
    """
    `.Connection`-wrapping class responsible for managing file upload/download.

    .. versionadded:: 2.0
    """

    def __init__(self, connection):
        self.connection = connection

    def get(self, remote, local=None, preserve_mode=True):
        """
        Copy a file from wrapped connection's host to the local filesystem.

        :param str remote:
            Remote file to download.

            May be absolute, or relative to the remote working directory.

            .. note::
                Most SFTP servers set the remote working directory to the
                connecting user's home directory, and (unlike most shells) do
                *not* expand tildes (``~``).

                For example, instead of saying ``get("~/tmp/archive.tgz")``,
                say ``get("tmp/archive.tgz")``.

        :param local:
            Local path to store downloaded file in, or a file-like object.

            **If None or another 'falsey'/empty value is given** (the default),
            the remote file is downloaded to the current working directory (as
            seen by `os.getcwd`) using its remote filename. (This is equivalent
            to giving ``"{basename}"``; see the below subsection on
            interpolation.)

            **If a string is given**, it should be a path to a local directory
            or file and is subject to similar behavior as that seen by common
            Unix utilities or OpenSSH's ``sftp`` or ``scp`` tools.

            For example, if the local path is a directory, the remote path's
            base filename will be added onto it (so ``get('foo/bar/file.txt',
            '/tmp/')`` would result in creation or overwriting of
            ``/tmp/file.txt``).

            This path will be **interpolated** with some useful parameters,
            using `str.format`:

            - The `.Connection` object's ``host``, ``user`` and ``port``
              attributes.
            - The ``basename`` and ``dirname`` of the ``remote`` path, as
              derived by `os.path` (specifically, its ``posixpath`` flavor, so
              that the resulting values are useful on remote POSIX-compatible
              SFTP servers even if the local client is Windows).
            - Thus, for example, ``"/some/path/{user}@{host}/{basename}"`` will
              yield different local paths depending on the properties of both
              the connection and the remote path.

            .. note::
                If nonexistent directories are present in this path (including
                the final path component, if it ends in `os.sep`) they will be
                created automatically using `os.makedirs`.

            **If a file-like object is given**, the contents of the remote file
            are simply written into it.

        :param bool preserve_mode:
            Whether to `os.chmod` the local file so it matches the remote
            file's mode (default: ``True``).

        :returns: A `.Result` object.

        .. versionadded:: 2.0
        .. versionchanged:: 2.6
            Added ``local`` path interpolation of connection & remote file
            attributes.
        .. versionchanged:: 2.6
            Create missing ``local`` directories automatically.
        """
        sftp = self.connection.sftp()
        remote_path = posixpath.join(sftp.getcwd() or '', remote)
        remote_basename = posixpath.basename(remote_path)
        remote_dirname = posixpath.dirname(remote_path)

        if not local:
            local = os.getcwd()

        if isinstance(local, str):
            local = local.format(
                host=self.connection.host,
                user=self.connection.user,
                port=self.connection.port,
                basename=remote_basename,
                dirname=remote_dirname
            )

            if os.path.isdir(local):
                local = os.path.join(local, remote_basename)

            local_dir = os.path.dirname(local)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)

            with open(local, 'wb') as local_file:
                sftp.getfo(remote_path, local_file)

            if preserve_mode:
                remote_mode = sftp.stat(remote_path).st_mode
                os.chmod(local, remote_mode)
        else:
            sftp.getfo(remote_path, local)

        return Result(local, local, remote_path, remote, self.connection)

    def put(self, local, remote=None, preserve_mode=True):
        """
        Upload a file from the local filesystem to the current connection.

        :param local:
            Local path of file to upload, or a file-like object.

            **If a string is given**, it should be a path to a local (regular)
            file (not a directory).

            .. note::
                When dealing with nonexistent file paths, normal Python file
                handling concerns come into play - for example, trying to
                upload a nonexistent ``local`` path will typically result in an
                `OSError`.

            **If a file-like object is given**, its contents are written to the
            remote file path.

        :param str remote:
            Remote path to which the local file will be written.

            .. note::
                Most SFTP servers set the remote working directory to the
                connecting user's home directory, and (unlike most shells) do
                *not* expand tildes (``~``).

                For example, instead of saying ``put("archive.tgz",
                "~/tmp/")``, say ``put("archive.tgz", "tmp/")``.

                In addition, this means that 'falsey'/empty values (such as the
                default value, ``None``) are allowed and result in uploading to
                the remote home directory.

            .. note::
                When ``local`` is a file-like object, ``remote`` is required
                and must refer to a valid file path (not a directory).

        :param bool preserve_mode:
            Whether to ``chmod`` the remote file so it matches the local file's
            mode (default: ``True``).

        :returns: A `.Result` object.

        .. versionadded:: 2.0
        """
        sftp = self.connection.sftp()

        if isinstance(local, str):
            local_path = os.path.expanduser(local)
            local_basename = os.path.basename(local_path)
            orig_local = local_path

            if not os.path.isfile(local_path):
                raise OSError(f"Local file '{local_path}' does not exist")

            if remote is None or remote == '':
                remote = local_basename

            remote_path = posixpath.join(sftp.getcwd() or '', remote)

            with open(local_path, 'rb') as local_file:
                sftp.putfo(local_file, remote_path)

            if preserve_mode:
                local_mode = os.stat(local_path).st_mode
                sftp.chmod(remote_path, local_mode)
        else:
            if remote is None:
                raise ValueError("'remote' must be specified when 'local' is a file-like object")

            remote_path = posixpath.join(sftp.getcwd() or '', remote)
            orig_local = local
            sftp.putfo(local, remote_path)

        return Result(local_path if isinstance(local, str) else None, orig_local, remote_path, remote, self.connection)

class Result:
    """
    A container for information about the result of a file transfer.

    See individual attribute/method documentation below for details.

    .. note::
        Unlike similar classes such as `invoke.runners.Result` or
        `fabric.runners.Result` (which have a concept of "warn and return
        anyways on failure") this class has no useful truthiness behavior. If a
        file transfer fails, some exception will be raised, either an `OSError`
        or an error from within Paramiko.

    .. versionadded:: 2.0
    """

    def __init__(self, local, orig_local, remote, orig_remote, connection):
        self.local = local
        self.orig_local = orig_local
        self.remote = remote
        self.orig_remote = orig_remote
        self.connection = connection
