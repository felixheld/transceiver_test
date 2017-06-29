#!/usr/bin/env python3
from litex.build.xilinx import VivadoProgrammer

prog = VivadoProgrammer()
prog.load_bitstream(
    bitstream_file="build_kcu105/gateware/top.bit",
    target="localhost:3121/xilinx_tcf/Digilent/210308A0E6EA")
