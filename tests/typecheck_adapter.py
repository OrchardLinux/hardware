"""Static conformance check: the registered module must satisfy Adapter."""
from orchard.contracts import Adapter
from orchard.apple.device import Device


def check(device: Device) -> Adapter:
    return device
