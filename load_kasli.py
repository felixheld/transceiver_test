#!/usr/bin/env python3
import os
import sys

if sys.argv[1] == "1":
	os.system("openocd -f /usr/local/share/openocd/scripts/board/kasli.cfg -c \"ftdi_location 3:7,3; init; pld load 0 build_kasli/gateware/top.bit; exit\"")
elif sys.argv[2] == "2":
	os.system("openocd -f /usr/local/share/openocd/scripts/board/kasli.cfg -c \"ftdi_location 3:7,1; init; pld load 0 build_kasli/gateware/top.bit; exit\"")
