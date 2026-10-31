"""Firmware recipes (schema 2): declarative steps from a fixed vocabulary.

Hardware describes inputs and steps (docs/firmware-recipes.md in hardware);
this module implements the steps. It does not know where inputs come from:
the host reads them from the IPSW, the in-app installer from iPadOS.
"""
import hashlib
import json
import plistlib

from orchard.apple import bluetool, d5500, smartio, z2fw, z2packet
from orchard.apple.osserialize import apple_xml


def _copy(step, data, expected):
    return data


def _bluetool_hcd(step, data, expected):
    return bluetool.hcd_from_binary(data, expected)


def _smartio(step, data, expected):
    return smartio.extract(data)[0]


def _smartio_kext(step, data, expected):
    if step['kext'] not in smartio.BUNDLES:
        raise ValueError('Unknown SmartIO kext: ' + step['kext'])
    return smartio.extract(data, step['kext'])[0]


def _vxd(step, data, expected):
    return d5500.extract(data)[0]


def _im4p_payload(step, data, expected):
    return im4p_payload(data)


def _touch_profile(data, name):
    profile = plistlib.loads(data).get(name)
    if not isinstance(profile, dict):
        raise ValueError('Touch profile not found: ' + str(name))
    return profile


def _z2fw(step, data, expected):
    profile = _touch_profile(data, step['profile'])
    output, _ = z2fw.convert(profile, apple_xml(profile['Firmware Config']),
                             packet=step['packet'], load_address=step['load_address'],
                             hbpp_version=step['hbpp_version'])
    return output


def _z2_packet(step, data, expected):
    return z2packet.convert(data, step['profile'], step['calibration_address'], step['calibration_size'])


def _z2fw_packets(step, data, expected):
    profile = _touch_profile(data, step['profile'])
    output, _ = z2fw.convert_packets(profile, apple_xml(profile['Firmware Config']), packet=step['packet'])
    return output


def _z2fw_gen1(step, data, expected):
    output, _ = z2fw.convert_gen1(_touch_profile(data, step['profile']), packet=step['packet'],
                                 touch_maximum=step['touch_maximum'], prox_maximum=step['prox_maximum'],
                                 fll_mval=step['fll_mval'], ref_clk_div=step['ref_clk_div'],
                                 clk32_clock_enable=step['clk32_clock_enable'])
    return output


def _z2fw_mtfw(step, data, expected):
    output, _ = z2fw.convert_mtfw(apple_xml(data), step['profile'], otp_preflight=step['otp_preflight'])
    return output


# step@version -> (implementation, {parameter besides 'step' and 'input': type})
STEPS = {
    'copy@1': (_copy, {}),
    'bluetool-hcd@1': (_bluetool_hcd, {}),
    # T8010 SmartIO kext (iPad 6/7, iPhone 7).
    'smartio-from-kernelcache@1': (_smartio, {}),
    # SmartIO kext named by bundle identifier (S8000 for A9).
    'smartio-from-kernelcache@2': (_smartio_kext, {'kext': str}),
    # VXD (DEVA) video-decoder firmware from AppleD5500.
    'vxd-firmware-from-kernelcache@1': (_vxd, {}),
    'im4p-payload@1': (_im4p_payload, {}),
    # mtprops with one preconstructed packet (J71b).
    'z2fw-from-mtprops@1': (_z2fw, {'profile': str, 'packet': str, 'load_address': int, 'hbpp_version': int}),
    # mtprops with one complete preconstructed Z2 boot packet (iPad 5), wrapped unmodified.
    'z2fw-from-z2-packet@1': (_z2_packet, {'profile': str, 'calibration_address': int,
                                           'calibration_size': int}),
    # mtprops with an array of preconstructed packets (iPhone 7 Plus), makez2fw's layout.
    'z2fw-from-mtprops@2': (_z2fw_packets, {'profile': str, 'packet': str}),
    # Legacy Gen1 data-form mtprops (iPhone 6), Corellium's bootstrap and board calibration bounds.
    'z2fw-from-mtprops@3': (_z2fw_gen1, {'profile': str, 'packet': str,
                                      'touch_maximum': int, 'prox_maximum': int,
                                      'fll_mval': int, 'ref_clk_div': int, 'clk32_clock_enable': int}),
    # MTFW (IM4P payload) with HBPP load items per personality (iPad 7), makez2fw's layout.
    'z2fw-from-mtfw@1': (_z2fw_mtfw, {'profile': str, 'otp_preflight': bool}),
}


class UnsupportedRecipe(ValueError):
    """The recipe needs a step this Orchard version does not implement."""


def check(recipe):
    """Validate the recipe against the hardware schema where jsonschema is installed, then its steps (host side)."""
    from orchard.manifests import firmware_schema, schema_validator
    validator = schema_validator()
    if validator is not None:
        import jsonschema
        jsonschema.validate(recipe, firmware_schema(), cls=validator)
    return supported(recipe)


def supported(recipe):
    """Confirm every step is implemented with its parameters, before any work.

    Needs no schema or hardware package, so the in-app installer can use it.
    """
    if recipe.get('schema') != 2:
        raise UnsupportedRecipe('This firmware recipe format needs a newer Orchard')
    for output in recipe['outputs']:
        # The first step reads a recipe input; each later step, the previous result.
        if not output['steps'] or 'input' not in output['steps'][0] or \
                any('input' in step for step in output['steps'][1:]):
            raise ValueError('Only the first step of an output names an input: ' + output['destination'])
        for step in output['steps']:
            if step['step'] not in STEPS:
                raise UnsupportedRecipe(f"This firmware recipe needs step {step['step']}; "
                                        'update Orchard to a version that implements it')
            _, parameters = STEPS[step['step']]
            if set(step) - {'step', 'input'} != set(parameters):
                raise ValueError(f"Wrong parameters for {step['step']}: {sorted(set(step) - {'step', 'input'})}")
            for name, kind in parameters.items():
                # bool is an int subclass; neither stands in for the other.
                if not isinstance(step[name], kind) or isinstance(step[name], bool) != (kind is bool):
                    raise ValueError(f"Parameter {name} of {step['step']} must be {kind.__name__}")
            if 'input' in step and step['input'] not in recipe['inputs']:
                raise ValueError('Unknown recipe input: ' + step['input'])
    destinations = [output['destination'] for output in recipe['outputs']]
    if len(set(destinations)) != len(destinations):
        raise ValueError('Duplicate firmware destination')
    return recipe


def needed_inputs(recipe, outputs=None):
    outputs = recipe['outputs'] if outputs is None else outputs
    return sorted({output['steps'][0]['input'] for output in outputs})


def run(recipe, read_input, outputs=None):
    """Produce outputs as {destination: bytes}; read_input(name) returns bytes.

    Every pinned input and every output is checked against its hash.
    """
    cache = {}

    def source(name):
        if name not in cache:
            data = read_input(name)
            pinned = recipe['inputs'][name].get('sha256')
            if pinned and hashlib.sha256(data).hexdigest() != pinned:
                raise ValueError('Firmware input differs from the recipe: ' + name)
            cache[name] = data
        return cache[name]

    result = {}
    for output in recipe['outputs'] if outputs is None else outputs:
        data = source(output['steps'][0]['input'])
        for step in output['steps']:
            implementation, _ = STEPS[step['step']]
            data = implementation(step, data, output['sha256'])
        if hashlib.sha256(data).hexdigest() != output['sha256']:
            raise ValueError('Firmware output differs from the recipe: ' + output['destination'])
        result[output['destination']] = data
    return result


def im4p_payload(blob):
    """Payload of an IM4P container: SEQUENCE { "IM4P", type, description, OCTET STRING, ... }."""
    def item(pos):
        if pos + 2 > len(blob):
            raise ValueError('Truncated IM4P')
        tag, length = blob[pos], blob[pos + 1]
        pos += 2
        if length & 0x80:
            count = length & 0x7f
            if not 1 <= count <= 4 or pos + count > len(blob):
                raise ValueError('Invalid IM4P length')
            length = int.from_bytes(blob[pos:pos + count], 'big')
            pos += count
        if pos + length > len(blob):
            raise ValueError('Truncated IM4P')
        return tag, blob[pos:pos + length], pos + length

    tag, body, _ = item(0)
    if tag != 0x30:
        raise ValueError('IM4P is not a DER sequence')
    blob = body
    fields, pos = [], 0
    while pos < len(blob) and len(fields) < 4:
        tag, value, pos = item(pos)
        fields.append((tag, value))
    if len(fields) < 4 or fields[0] != (0x16, b'IM4P') or fields[3][0] != 0x04:
        raise ValueError('Not an IM4P container')
    payload = fields[3][1]
    if payload[:4] in (b'bvx2', b'bvx1', b'bvx-', b'bvxn'):
        raise ValueError('Compressed IM4P payloads are not supported by im4p-payload@1')
    return payload
