#!/usr/bin/env python3

import os
import sys
sys.path.append("../")

from litex.gen import *
from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform

from litex.gen.genlib.io import CRG

from transceiver.serdes_ultrascale import SERDESPLL, SERDES


_io = [
    ("clk125", 0, Pins("X")),
    ("serdes_tx", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
]


class Platform(XilinxPlatform):
    def __init__(self):
        XilinxPlatform.__init__(self, "", _io)


class SERDESSim(Module):
    def __init__(self, platform):
        clk_freq = 125e6
        self.submodules.crg = CRG(platform.request("clk125"))

        pll = SERDESPLL(ClockSignal(), 125e6, 1.25e9)
        self.submodules += pll

        tx_pads = platform.request("serdes_tx")
        serdes = SERDES(pll, tx_pads)
        self.comb += serdes.produce_square_wave.eq(1)
        self.submodules += serdes


def generate_top():
    platform = Platform()
    soc = SERDESSim(platform)
    platform.build(soc, build_dir="./", run=False)

def generate_top_tb():
    f = open("top_tb.v", "w")
    f.write("""
`timescale 1ns/1ps

module top_tb();

reg clk125;
initial clk125 = 1'b1;
always #4 clk125 = ~clk125;

wire serdes_p;
wire serdes_n;

top dut (
    .clk125(clk125),
    .serdes_tx_p(serdes_p),
    .serdes_tx_n(serdes_n)
);

endmodule""")
    f.close()

def run_sim():
    os.system("rm -rf xsim.dir")
    os.system("call xvlog glbl.v")
    os.system("call xvlog top.v")
    os.system("call xvlog top_tb.v")
    os.system("call xelab -debug typical top_tb glbl -s top_tb_sim -L unisims_ver -L unimacro_ver -L SIMPRIM_VER -L secureip -L $xsimdir/xil_defaultlib -timescale 1ns/1ps")
    os.system("call xsim top_tb_sim -gui")

def main():
    generate_top()
    generate_top_tb()
    run_sim()

if __name__ == "__main__":
    main()