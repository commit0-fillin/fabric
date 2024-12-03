"""
CLI entrypoint & parser configuration.

Builds on top of Invoke's core functionality for same.
"""
import getpass
from pathlib import Path
from invoke import Argument, Collection, Exit, Program
from invoke import __version__ as invoke
from paramiko import __version__ as paramiko, Agent
from . import __version__ as fabric
from . import Config, Executor

class Fab(Program):
    def __init__(self, version=None, namespace=None, name=None, binary=None):
        super().__init__(version=version, namespace=namespace, name=name, binary=binary)
        self.config = Config()

def make_program():
    return Fab(
        version=f"Fabric {fabric} (Invoke {invoke}) (Paramiko {paramiko})",
        namespace=Collection.from_module(Executor),
        name="fab",
        binary="fab",
    )

program = make_program()
