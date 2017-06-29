#!/usr/bin/env python3
from litex.build.xilinx import VivadoProgrammer

prog = VivadoProgrammer()
prog.load_bitstream(
    bitstream_file="build_pcie_radio/gateware/top.bit",
    target="localhost:3121/xilinx_tcf/Digilent/210249810959")