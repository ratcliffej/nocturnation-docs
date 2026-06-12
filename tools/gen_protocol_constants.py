#!/usr/bin/env python3
"""Generate language-specific protocol-constants artifacts from the YAML SOT.

The SOT lives at Docs/protocol/constants.yaml; this script reads it and
emits a Python module (for the Tildagon firmware) or a C++ header (for
the StickC firmware) on stdout. Each firmware repo has a CI check that
re-runs this generator and fails on any drift between the SOT and the
checked-in generated artifact.

Usage:
  python3 gen_protocol_constants.py --py   > path/to/_generated.py
  python3 gen_protocol_constants.py --cpp  > path/to/protocol_constants_generated.h

Dependencies: PyYAML. Both firmware repos already use PyYAML for other
tooling (deploy scripts, generators); no new runtime dep.
"""

import argparse
import pathlib
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write("error: PyYAML not installed; pip install pyyaml\n")
    sys.exit(2)


SOT_PATH = pathlib.Path(__file__).resolve().parent.parent / "protocol" / "constants.yaml"


def load_sot():
    with SOT_PATH.open() as fh:
        return yaml.safe_load(fh)


PY_HEADER = '''\
"""AUTO-GENERATED from Docs/protocol/constants.yaml. Do not edit by hand.

To regenerate, run tools/regen_constants.sh from the firmware repo
root. The accompanying test re-runs the generator and fails CI if
this file drifts from the SOT.
"""

'''


CPP_HEADER = '''\
// AUTO-GENERATED from Docs/protocol/constants.yaml. Do not edit by hand.
//
// To regenerate, run tools/regen_constants.sh from the firmware repo
// root. The accompanying check script re-runs the generator and
// fails on any drift between this header and the SOT.

#pragma once

#include <cstdint>

namespace nocturnation {
namespace transport {
namespace espnow {

'''


CPP_FOOTER = '''\

}  // namespace espnow
}  // namespace transport
}  // namespace nocturnation
'''


def render_py(d):
    out = [PY_HEADER]
    out.append("MAGIC_0 = 0x{:02X}\n".format(d["magic"]["byte_0"]))
    out.append("MAGIC_1 = 0x{:02X}\n".format(d["magic"]["byte_1"]))
    out.append("\n")
    out.append("PROTOCOL_VERSION = 0x{:02X}\n".format(d["protocol_version"]))
    out.append("\n")
    out.append("HEADER_SIZE = {}\n".format(d["header_size"]))
    out.append("\n\n")
    out.append("class MessageType:\n")
    name_w = max(len(n) for n in d["message_types"])
    for name, value in d["message_types"].items():
        out.append("    {:{w}s} = 0x{:02X}\n".format(name, value, w=name_w))
    out.append("\n\n")
    out.append("PAYLOAD_LENGTHS = {\n")
    for name, value in d["payload_lengths"].items():
        out.append("    MessageType.{:{w}s}: {},\n".format(name, value, w=name_w))
    out.append("}\n")
    return "".join(out)


def render_cpp(d):
    out = [CPP_HEADER]
    out.append("constexpr uint8_t kMagic0          = 0x{:02X};\n".format(d["magic"]["byte_0"]))
    out.append("constexpr uint8_t kMagic1          = 0x{:02X};\n".format(d["magic"]["byte_1"]))
    out.append("constexpr uint8_t kProtocolVersion = 0x{:02X};\n".format(d["protocol_version"]))
    out.append("constexpr uint8_t kHeaderSize      = {};\n".format(d["header_size"]))
    out.append("\n")
    out.append("enum class MessageType : uint8_t {\n")
    # The C++ enum uses CamelCase names matching the existing frame.h
    # (Heartbeat, LightPulse, ...). Map SCREAMING_SNAKE_CASE -> CamelCase.
    cpp_names = {
        "HEARTBEAT":        "Heartbeat",
        "LIGHT_PULSE":      "LightPulse",
        "LIGHT_WASH":       "LightWash",
        "LIGHT_WASH_END":   "LightWashEnd",
        "LIGHT_WASH_PULSE": "LightWashPulse",
        "EXTENSION":        "Extension",
    }
    name_w = max(len(cpp_names[n]) for n in d["message_types"])
    for yaml_name, value in d["message_types"].items():
        cpp_name = cpp_names.get(yaml_name)
        if cpp_name is None:
            sys.stderr.write("error: no C++ name mapping for {}\n".format(yaml_name))
            sys.exit(2)
        out.append("    {:{w}s} = 0x{:02X},\n".format(cpp_name, value, w=name_w))
    out.append("};\n")
    out.append("\n")
    # Payload length constants - one constexpr per type, matching the
    # existing frame.h naming convention (kHeartbeatPayloadLen etc.).
    out.append("// Payload bytes per message type (excluding the {}-byte header).\n".format(
        d["header_size"]))
    pl_const_names = {
        "HEARTBEAT":        "kHeartbeatPayloadLen",
        "LIGHT_PULSE":      "kLightPulsePayloadLen",
        "LIGHT_WASH":       "kLightWashPayloadLen",
        "LIGHT_WASH_END":   "kLightWashEndPayloadLen",
        "LIGHT_WASH_PULSE": "kLightWashPulsePayloadLen",
    }
    const_w = max(len(v) for v in pl_const_names.values())
    for yaml_name, value in d["payload_lengths"].items():
        cname = pl_const_names.get(yaml_name)
        if cname is None:
            sys.stderr.write("error: no payload-length const name mapping for {}\n".format(yaml_name))
            sys.exit(2)
        out.append("constexpr uint8_t {:{w}s} = {};\n".format(cname, value, w=const_w))
    out.append(CPP_FOOTER)
    return "".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--py",  action="store_true", help="emit Python module")
    p.add_argument("--cpp", action="store_true", help="emit C++ header")
    args = p.parse_args()
    if args.py == args.cpp:
        p.error("specify exactly one of --py or --cpp")
    d = load_sot()
    sys.stdout.write(render_py(d) if args.py else render_cpp(d))


if __name__ == "__main__":
    main()
