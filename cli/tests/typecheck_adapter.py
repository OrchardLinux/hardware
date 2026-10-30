"""Static conformance check: the registered module must satisfy Adapter."""
from orchard_cli.contracts import Adapter
from orchard_cli.apple.device import Device


def check(device: Device) -> Adapter:
    return device
