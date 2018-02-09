#!/usr/bin/env python3
import sys
import os

from litex.build.xilinx import VivadoProgrammer

if len(sys.argv) == 1:
	prog = VivadoProgrammer()
	prog.load_bitstream(
    	bitstream_file="build_kasli/gateware/top.bit")
elif (len(sys.argv) == 2) and (sys.argv[1] == "remote"):
	# load the 2 kasli boards
	os.system("openocd -f /usr/local/share/openocd/scripts/board/kasli.cfg -c \"ftdi_location 3:7,3; init; pld load 0 build_kasli/gateware/top.bit; exit\"")
	os.system("openocd -f /usr/local/share/openocd/scripts/board/kasli.cfg -c \"ftdi_location 3:7,1; init; pld load 0 build_kasli/gateware/top.bit; exit\"")
else:
	raise ValueError
