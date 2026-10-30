#!/usr/bin/env python3
"""Bounded Apple device-tree parser. Callers must explicitly select safe properties.

Raw trees can contain device identities and calibration; this module intentionally
has no dump-all command. Values returned by parse() must not be logged wholesale.
"""
import struct
from dataclasses import dataclass


@dataclass
class Node:
    properties: dict
    children: list

    @property
    def name(self):
        return self.properties.get('name', b'?').split(b'\0', 1)[0].decode('ascii', errors='replace')

    def walk(self, parent=''):
        path = parent + '/' + self.name
        yield path, self
        for child in self.children:
            yield from child.walk(path)


def parse(blob):
    if len(blob) > 16 * 1024 * 1024:
        raise ValueError('ADT exceeds parser size limit')
    position = 0
    budget = 100000

    def read_node(depth):
        nonlocal position, budget
        budget -= 1
        if budget < 0 or depth > 64 or position + 8 > len(blob):
            raise ValueError('ADT node bounds/depth limit')
        prop_count, child_count = struct.unpack_from('<II', blob, position)
        position += 8
        if prop_count > 4096 or child_count > 4096:
            raise ValueError('ADT count limit')
        properties = {}
        for _ in range(prop_count):
            if position + 36 > len(blob):
                raise ValueError('Truncated property header')
            raw_name = blob[position:position+32]
            if b'\0' not in raw_name:
                raise ValueError('Unterminated property name')
            name = raw_name.split(b'\0', 1)[0].decode('ascii')
            length = struct.unpack_from('<I', blob, position+32)[0] & 0xffffff
            position += 36
            padded = (length + 3) & ~3
            if position + padded > len(blob) or name in properties:
                raise ValueError('Truncated or duplicate property')
            properties[name] = blob[position:position+length]
            position += padded
        return Node(properties, [read_node(depth+1) for _ in range(child_count)])

    return read_node(0)


def u32s(value):
    if len(value) % 4:
        raise ValueError('Not an array of 32-bit words')
    return list(struct.unpack('<' + 'I' * (len(value)//4), value))
