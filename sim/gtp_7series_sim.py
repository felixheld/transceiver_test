#!/usr/bin/env python3

import os
import sys
sys.path.append("../")

from litex.gen import *
from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform

from litex.gen.genlib.io import CRG

from transceiver.gtp_7series import GTPQuadPLL


_io = [
    ("clk100", 0, Pins("X")),
    ("gtp_refclk", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
    ("gtp_tx", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
    ("gtp_rx", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
]


class Platform(XilinxPlatform):
    def __init__(self):
        XilinxPlatform.__init__(self, "", _io)


class GTPSim(Module):
    def __init__(self, platform):
        clk_freq = 100e6
        self.submodules.crg = CRG(platform.request("clk100"))

        refclk = Signal()
        refclk_pads = platform.request("gtp_refclk")
        self.specials += [
            Instance("IBUFDS_GTE2",
                i_CEB=0,
                i_I=refclk_pads.p,
                i_IB=refclk_pads.n,
                o_O=refclk)
        ]

        qpll = GTPQuadPLL(refclk, 125e6, 1.25e9)
        print(qpll)
        self.submodules += qpll


def generate_top():
    platform = Platform()
    soc = GTPSim(platform)
    platform.build(soc, build_dir="./", run=False)

def generate_top_tb():
    f = open("top_tb.v", "w")
    f.write("""
`timescale 1ns/1ps

module top_tb();

reg clk100;
initial clk100 = 1'b1;
always #5 clk100 = ~clk100;

reg gtp_refclk;
initial gtp_refclk = 1'b1;
always #4 gtp_refclk = ~gtp_refclk;

wire gtp_p;
wire gtp_n;

top dut (
    .clk100(clk100),
    .gtp_refclk_p(gtp_refclk),
    .gtp_refclk_n(~gtp_refclk),
    .gtp_tx_p(gtp_p),
    .gtp_tx_n(gtp_n),
    .gtp_rx_p(gtp_p),
    .gtp_rx_n(gtp_n)
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
    #run_sim()

if __name__ == "__main__":
    main()
