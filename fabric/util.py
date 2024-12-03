import logging
import sys
log = logging.getLogger('fabric')
for x in ('debug',):
    globals()[x] = getattr(log, x)
win32 = sys.platform == 'win32'

def get_local_user():
    """
    Return the local executing username, or ``None`` if one can't be found.

    .. versionadded:: 2.0
    """
    import getpass
    try:
        return getpass.getuser()
    except Exception:
        return None
