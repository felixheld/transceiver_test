#!/usr/bin/env python3
from litex.build.xilinx import VivadoProgrammer

prog = VivadoProgrammer()
prog.load_bitstream(bitstream_file="build_pcie_cpri/gateware/top.bit")